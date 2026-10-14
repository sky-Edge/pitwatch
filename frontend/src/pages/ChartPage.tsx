import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Empty, Radio, Segmented, Space, Spin, Tag, Typography } from 'antd'
import * as echarts from 'echarts'
import { chartApi, type ChartDataResponse, type Component, type SeriesType } from '../api/client'
import { LAYOUT, NEUTRAL, SERIES_PALETTE, WARNING_LEVEL } from '../theme/tokens'

interface Props {
  projectId: number
  onBack: () => void
}

/** 当前指标/分量是否有可绘制数据（所有测点序列均为空视为无数据） */
const hasAnyData = (d: ChartDataResponse | null): boolean => !!d && d.series.some((s) => s.data.length > 0)

export default function ChartPage({ projectId, onBack }: Props) {
  const [indicator, setIndicator] = useState<SeriesType>('settlement')
  const [component, setComponent] = useState<Component>('value')
  const [loading, setLoading] = useState(false)
  const [errMsg, setErrMsg] = useState<string | null>(null)
  const [chartData, setChartData] = useState<ChartDataResponse | null>(null)
  const chartRef = useRef<HTMLDivElement | null>(null)
  const chartInst = useRef<echarts.ECharts | null>(null)
  /** 请求序号：快速切换指标/分量时丢弃过期响应，防止旧结果覆盖新图 */
  const seqRef = useRef(0)

  const load = useCallback(async () => {
    const seq = ++seqRef.current
    setLoading(true)
    setErrMsg(null)
    try {
      const data = await chartApi.data(projectId, indicator, component)
      if (seq !== seqRef.current) return // 已有更新的请求在途，丢弃本次结果
      setChartData(data)
    } catch (e) {
      if (seq !== seqRef.current) return
      setErrMsg((e as Error).message)
      setChartData(null)
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [projectId, indicator, component])

  useEffect(() => {
    void load()
  }, [load])

  // ── 关键修复：ECharts 的 init / setOption 全部放进 effect（在 DOM commit 之后执行）。
  //    图表容器 div 常驻不卸载（loading/空态用遮罩叠加，不再卸载容器），
  //    因此绘制时容器必然已挂载，异步请求的返回时机不再影响能否出图。 ──
  useEffect(() => {
    const el = chartRef.current
    const data = chartData
    if (!el || !data || !hasAnyData(data)) return

    // 复用同一实例，仅切换数据（notMerge 全量替换），不再每次 dispose 重建
    if (!chartInst.current) {
      chartInst.current = echarts.init(el)
    }
    const inst = chartInst.current

    const colorIdx = new Map<string, number>()
    data.series.forEach((s) => {
      if (s.data.length > 0 && !colorIdx.has(s.point_no)) colorIdx.set(s.point_no, colorIdx.size)
    })

    const seriesList = data.series
      .filter((s) => s.data.length > 0)
      .map((s) => ({
        name: `${s.point_no}${s.component !== 'value' ? `(${s.component})` : ''}`,
        type: 'line' as const,
        smooth: true,
        symbolSize: 5,
        lineStyle: { width: 2 },
        itemStyle: { color: SERIES_PALETTE[(colorIdx.get(s.point_no) ?? 0) % SERIES_PALETTE.length] },
        data: s.data.map((p) => [p.date, p.cumulative_mm]),
      }))

    // 预警叠加：报警红 / 预警金（配色取自令牌，页面不再写死颜色）
    const alarmPts = data.warning_points.filter((w) => w.warning_level === 'alarm')
    const controlPts = data.warning_points.filter((w) => w.warning_level === 'control')
    const scatter = (pts: typeof alarmPts, color: string, name: string) => ({
      name,
      type: 'scatter' as const,
      symbolSize: 14,
      itemStyle: { color, borderColor: NEUTRAL.white, borderWidth: 1 },
      data: pts.map((w) => ({
        value: [w.date, w.cumulative_mm],
        label: {
          show: true,
          formatter: w.point_no,
          position: 'top' as const,
          fontSize: 10,
          color,
        },
      })),
    })

    const option: echarts.EChartsOption = {
      color: SERIES_PALETTE,
      tooltip: { trigger: 'axis' },
      // legend 放顶部，避免与 x 轴旋转日期标签重叠（修复 UI 冲突）
      legend: { type: 'scroll', top: 0, left: 'center' },
      grid: { left: 60, right: 40, top: 50, bottom: 70 },
      xAxis: {
        type: 'category',
        data: data.dates,
        name: '日期',
        nameLocation: 'middle',
        nameGap: 40,
        axisLabel: { rotate: 45 },
      },
      yAxis: {
        type: 'value',
        name: '累计变化量 (mm)',
        nameLocation: 'middle',
        nameGap: 45,
      },
      series: [
        ...seriesList,
        ...(alarmPts.length ? [scatter(alarmPts, WARNING_LEVEL.alarm.fg, '报警点')] : []),
        ...(controlPts.length ? [scatter(controlPts, WARNING_LEVEL.control.fg, '预警点')] : []),
      ],
    }
    inst.setOption(option, true)
    inst.resize()
  }, [chartData])

  // 组件卸载时释放实例
  useEffect(() => {
    return () => {
      if (chartInst.current) {
        chartInst.current.dispose()
        chartInst.current = null
      }
    }
  }, [])

  // 窗口大小变化自适应
  useEffect(() => {
    const onResize = () => chartInst.current?.resize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const isDisplacement = indicator === 'displacement'
  const showEmpty = !loading && chartData !== null && !hasAnyData(chartData)

  const switchIndicator = (v: SeriesType) => {
    setIndicator(v)
    setComponent(v === 'settlement' ? 'value' : 'X')
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Button onClick={onBack}>← 返回项目列表</Button>
        <Alert
          type="info"
          showIcon
          style={{ flex: 1 }}
          message="沉降 / 水平位移曲线"
          description="多测点累计变化量曲线（单位 mm），报警点红标、预警点黄标；切换指标与位移分量可分别查看。"
        />
      </Space>

      <Card size="small">
        <Space wrap>
          <Segmented
            value={indicator}
            onChange={(v) => switchIndicator(v as SeriesType)}
            options={[
              { label: '沉降曲线', value: 'settlement' },
              { label: '水平位移曲线', value: 'displacement' },
            ]}
          />
          {isDisplacement && (
            <Radio.Group value={component} onChange={(e) => setComponent(e.target.value)} optionType="button">
              <Radio.Button value="X">X 方向</Radio.Button>
              <Radio.Button value="Y">Y 方向</Radio.Button>
            </Radio.Group>
          )}
          <Typography.Text type="secondary">
            {isDisplacement ? `水平位移 · ${component} 方向` : '沉降（累计变化量 mm）'}
          </Typography.Text>
          <Tag color="red">● 报警点</Tag>
          <Tag color="gold">● 预警点</Tag>
        </Space>
      </Card>

      {errMsg && <Alert type="error" showIcon message={errMsg} />}

      {/* 容器常驻：loading / 空态均以遮罩叠加在固定高度的容器上，保证 ECharts 初始化时容器必然已挂载 */}
      <Card size="small" style={{ minHeight: LAYOUT.chartHeight + 20 }}>
        <Spin spinning={loading}>
          <div style={{ position: 'relative', height: LAYOUT.chartHeight, width: '100%' }}>
            <div ref={chartRef} style={{ position: 'absolute', inset: 0 }} />
            {showEmpty && (
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Empty description="该项目暂无此观测数据" />
              </div>
            )}
          </div>
        </Spin>
      </Card>
    </Space>
  )
}
