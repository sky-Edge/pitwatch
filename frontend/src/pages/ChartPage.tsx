import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Empty, Modal, Radio, Segmented, Space, Spin, Tag, Typography, message } from 'antd'
import * as echarts from 'echarts'
import { chartApi, recordApi, type ChartDataResponse, type ChartPoint, type Component, type SeriesType } from '../api/client'
import { LAYOUT, NEUTRAL, SERIES_PALETTE, WARNING_LEVEL } from '../theme/tokens'

interface Props {
  projectId: number
  onBack: () => void
}

/** 当前指标/分量是否有可绘制数据（所有测点序列均为空视为无数据） */
const hasAnyData = (d: ChartDataResponse | null): boolean => !!d && d.series.some((s) => s.data.length > 0)

/** 拖拽中/待确认的一次调整 */
interface PendingAdjust {
  pointNo: string
  date: string
  color: string
  /** 原来的累计变化量（曲线上的值） */
  oldCum: number
  /** 原来的读数（落库值） */
  oldValue: number
  recordId: number
  /** 拖到的目标累计变化量（已夹在 ±预警值 内） */
  target: number
  /** 鼠标 y（图容器内相对坐标），用来放参考线 */
  yPx: number
  /** 拖的时候有没有到过边界（到过就提示"已到预警线"） */
  atLimit: boolean
}

export default function ChartPage({ projectId, onBack }: Props) {
  const [indicator, setIndicator] = useState<SeriesType>('settlement')
  const [component, setComponent] = useState<Component>('value')
  const [loading, setLoading] = useState(false)
  const [errMsg, setErrMsg] = useState<string | null>(null)
  const [chartData, setChartData] = useState<ChartDataResponse | null>(null)
  /** 正在拖拽时的实时显示（松手即清空） */
  const [drag, setDrag] = useState<PendingAdjust | null>(null)
  /** 已松手、等用户确认的那一次调整 */
  const [pending, setPending] = useState<PendingAdjust | null>(null)
  const [saving, setSaving] = useState(false)
  /** 拖拽 session 的可变状态（mousemove 高频触发，不能靠 state 闭包） */
  const dragRef = useRef<PendingAdjust | null>(null)
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

    // 预警线 / 报警线：拖拽调整时，用户得先看见"还能拖到哪"
    const ctrlLimit = data.limits?.control_cumulative_mm
    const alarmLimit = data.limits?.alarm_cumulative_mm
    if (seriesList.length > 0 && (ctrlLimit != null || alarmLimit != null)) {
      const lines: Array<{ yAxis: number }> = []
      if (ctrlLimit != null) lines.push({ yAxis: ctrlLimit }, { yAxis: -ctrlLimit })
      if (alarmLimit != null) lines.push({ yAxis: alarmLimit }, { yAxis: -alarmLimit })
      // 只挂在第一条线上（markLine 是系列级配置），不额外造空系列去污染图例
      ;(seriesList[0] as Record<string, unknown>).markLine = {
        silent: true,
        symbol: 'none',
        lineStyle: { type: 'dashed', width: 1, color: WARNING_LEVEL.control.fg, opacity: 0.85 },
        label: {
          position: 'insideEndTop',
          fontSize: 10,
          color: WARNING_LEVEL.control.fg,
          formatter: (p: { value: number }) => `${p.value > 0 ? '+' : ''}${p.value} 预警`,
        },
        data: lines,
      }
    }

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

  // ── 拖拽调整：按住曲线上的点上下拖，改那一天**读数** ──
  //    用户 2026-10-19：「有些数值看着很夸张，我想手动调整；超过预警就不能动」
  //    边界 = 预警规则里 control 级的累计变化量。**没配规则就不给拖** ——
  //    没有边界的"限制"是假的，宁可不让动，也不能让人拖出一个系统无法判定的数。
  useEffect(() => {
    const inst = chartInst.current
    const data = chartData
    if (!inst || !data || !hasAnyData(data)) return
    const limit = data.limits?.control_cumulative_mm
    if (limit == null) return

    const zr = inst.getZr()
    // 鼠标样式挂在图表容器上（zr.dom 在类型上可能是 undefined）
    const host = chartRef.current
    const HIT_PX = 16
    const visible = data.series.filter((s) => s.data.length > 0)

    let active: PendingAdjust | null = null
    let moved = false

    /** 找鼠标最近的可拖点（16px 内算命中） */
    const nearest = (x: number, y: number): { d: number; pointNo: string; pt: ChartPoint } | null => {
      let best: { d: number; pointNo: string; pt: ChartPoint } | null = null
      visible.forEach((s, si) => {
        s.data.forEach((pt) => {
          if (pt.record_id == null || pt.value_mm == null) return
          const px = inst.convertToPixel({ seriesIndex: si }, [pt.date, pt.cumulative_mm])
          if (!Array.isArray(px)) return
          const d = Math.hypot(px[0] - x, px[1] - y)
          if (best === null || d < best.d) best = { d, pointNo: s.point_no, pt }
        })
      })
      const hit = best as { d: number; pointNo: string; pt: ChartPoint } | null
      return hit && hit.d <= HIT_PX ? hit : null
    }

    const onDown = (e: { offsetX: number; offsetY: number }) => {
      const hit = nearest(e.offsetX, e.offsetY)
      if (!hit) return
      const idx = visible.findIndex((s) => s.point_no === hit.pointNo)
      active = {
        pointNo: hit.pointNo,
        date: hit.pt.date,
        color: SERIES_PALETTE[Math.max(0, idx) % SERIES_PALETTE.length],
        oldCum: hit.pt.cumulative_mm,
        oldValue: hit.pt.value_mm as number,
        recordId: hit.pt.record_id as number,
        target: hit.pt.cumulative_mm,
        yPx: e.offsetY,
        atLimit: false,
      }
      moved = false
      dragRef.current = active
    }

    const onMove = (e: { offsetX: number; offsetY: number }) => {
      if (!active) {
        if (host) host.style.cursor = nearest(e.offsetX, e.offsetY) ? 'ns-resize' : ''
        return
      }
      const pair = inst.convertFromPixel({ gridIndex: 0 }, [e.offsetX, e.offsetY])
      const raw = Array.isArray(pair) ? Number(pair[1]) : NaN
      if (!Number.isFinite(raw)) return
      // 卡在 ±预警值：超出就停在边界，且记下"碰到边界了"用于提示
      const clamped = Math.max(-limit, Math.min(limit, raw))
      const next: PendingAdjust = {
        ...active,
        target: Math.round(clamped * 100) / 100,
        yPx: e.offsetY,
        atLimit: Math.abs(raw) > limit + 1e-9,
      }
      active = next
      dragRef.current = next
      if (Math.abs(next.target - next.oldCum) > 0.01) moved = true
      setDrag(next)
    }

    const onUp = () => {
      const cur = dragRef.current
      active = null
      dragRef.current = null
      setDrag(null)
      if (cur && moved) setPending(cur) // 没拖动就不弹框（只是点了一下）
    }

    zr.on('mousedown', onDown)
    zr.on('mousemove', onMove)
    zr.on('mouseup', onUp)
    return () => {
      zr.off('mousedown', onDown)
      zr.off('mousemove', onMove)
      zr.off('mouseup', onUp)
      if (host) host.style.cursor = ''
      dragRef.current = null
      setDrag(null)
    }
  }, [chartData])

  /** 确认后落库：走"更正"流程（旧记录留痕，不改原值） */
  const applyAdjust = async () => {
    if (!pending) return
    setSaving(true)
    try {
      // 累计 = 读数 − V0（V0 不变），所以新读数 = 原读数 − 原累计 + 目标累计
      const newValue = pending.oldValue - pending.oldCum + pending.target
      await recordApi.correct(pending.recordId, {
        value_mm: Math.round(newValue * 1000) / 1000,
        remark: '曲线图手动调整',
      })
      message.success(`${pending.pointNo} ${pending.date} 的读数已更正`)
      setPending(null)
      await load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

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
          description="多测点累计变化量曲线（单位 mm），报警点红标、预警点黄标。觉得某个数太离谱，可以按住那个点上下拖动来调整（最多拖到预警线，拖不动就是到边界了）。"
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
            {/* 拖拽时的水平参考线 + 数值浮标（不拦截鼠标，只做提示） */}
            {drag && (
              <>
                <div
                  style={{
                    position: 'absolute',
                    left: 60,
                    right: 40,
                    top: drag.yPx,
                    borderTop: `1px dashed ${drag.color}`,
                    pointerEvents: 'none',
                  }}
                />
                <div
                  style={{
                    position: 'absolute',
                    right: 44,
                    top: Math.max(0, drag.yPx - 28),
                    background: NEUTRAL.white,
                    border: `1px solid ${drag.color}`,
                    borderRadius: 4,
                    padding: '2px 8px',
                    fontSize: 12,
                    color: drag.color,
                    pointerEvents: 'none',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {drag.pointNo} · {drag.date}　{drag.oldCum.toFixed(1)} → <b>{drag.target.toFixed(1)}</b> mm
                  {drag.atLimit && <span style={{ marginLeft: 6 }}>（已到预警线）</span>}
                </div>
              </>
            )}
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

      {/* 松手后确认：改的是真实观测数据，不能拖完就静默生效 */}
      <Modal
        open={!!pending}
        title="确认调整这个读数？"
        onCancel={() => setPending(null)}
        onOk={() => void applyAdjust()}
        okText="确认调整"
        cancelText="取消"
        confirmLoading={saving}
      >
        {pending && (
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            <div>
              <b>{pending.pointNo}</b>　·　{pending.date}
            </div>
            <div>
              累计变化量：<b>{pending.oldCum.toFixed(2)}</b> → <b>{pending.target.toFixed(2)}</b> mm
            </div>
            <div>
              读数：{pending.oldValue.toFixed(1)} →{' '}
              <b>{(pending.oldValue - pending.oldCum + pending.target).toFixed(1)}</b> mm
            </div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              原记录会保留并标记为「已被更正」（可追溯、可查回原值），不会真的删掉。
            </Typography.Text>
          </Space>
        )}
      </Modal>
    </Space>
  )
}
