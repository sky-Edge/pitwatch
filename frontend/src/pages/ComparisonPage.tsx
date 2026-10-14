import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Space, Table, Tag, Tooltip, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { comparisonApi, type ComparisonRow, type ComparisonSeries } from '../api/client'
import { levelColor } from '../theme/tokens'

const KIND_LABEL: Record<ComparisonRow['compare_kind'], { text: string; color: string }> = {
  first: { text: '首期(相对初始值)', color: 'blue' },
  adjacent: { text: '相邻上期', color: 'green' },
  skip: { text: '跨期(中间缺测)', color: 'orange' },
  missing: { text: '缺测', color: 'default' },
}

const WARNING_LABEL: Record<string, { text: string; color: string }> = {
  normal: { text: '正常', color: 'green' },
  control: { text: '预警', color: 'gold' },
  alarm: { text: '报警', color: 'red' },
}

const SERIES_NAME: Record<string, string> = {
  settlement: '沉降',
  displacement_X: '水平位移 X',
  displacement_Y: '水平位移 Y',
}

/**
 * «本次变化 ΔV» 与 «累计变化 C» 两列共用同一渲染：
 * 按预警等级着色，缺测显示 `-`。
 * （原先这两列各写了一遍 `alarm ? 红 : control ? 金 : 默认` 的三元判断）
 */
const levelValueCell = (v: number | null, r: ComparisonRow) =>
  v === null ? (
    '-'
  ) : (
    <span style={{ color: levelColor(r.warning_level) }}>
      {v > 0 ? '+' : ''}
      {v.toFixed(1)}
    </span>
  )

/** 逐期对比分析：每期与上一期依次对比（首期相对初始值期），后续各期持续沿用。 */
export default function ComparisonPage({ projectId, onBack }: { projectId: number; onBack: () => void }) {
  const [series, setSeries] = useState<ComparisonSeries[]>([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setSeries(await comparisonApi.project(projectId))
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  const columns: ColumnsType<ComparisonRow> = [
    {
      title: '期次',
      dataIndex: 'period',
      width: 80,
      render: (v: number) => (v === 0 ? '初始值' : `第${v}期`),
    },
    { title: '观测日期', dataIndex: 'obs_date', width: 110 },
    {
      title: '对比基准',
      width: 110,
      render: (_, r) => r.compare_to_date ?? '(初始值)',
    },
    {
      title: '对比类型',
      width: 140,
      render: (_, r) => <Tag color={KIND_LABEL[r.compare_kind].color}>{KIND_LABEL[r.compare_kind].text}</Tag>,
    },
    {
      title: '读值(m)',
      dataIndex: 'value_mm',
      width: 90,
      render: (v: number | null) => (v === null ? '-' : (v / 1000).toFixed(3)),
    },
    {
      title: '本次变化 ΔV(mm)',
      dataIndex: 'delta_mm',
      width: 130,
      render: levelValueCell,
    },
    {
      title: '累计变化 C(mm)',
      dataIndex: 'cumulative_mm',
      width: 130,
      render: levelValueCell,
    },
    {
      // 把速率的"除数"摆出来：看到 -1.00mm/d 时，能立刻分清是
      // "隔 1 天掉了 1mm" 还是 "隔 7 天掉了 7mm" —— 后者才是真危险。
      title: '间隔(天)',
      dataIndex: 'skipped_days',
      width: 90,
      render: (v: number | null | undefined, r) =>
        v == null ? (
          '-'
        ) : (
          <Tooltip title={`速率 = 本次变化 ${r.delta_mm ?? '-'}mm ÷ ${v} 天`}>
            <span style={{ borderBottom: '1px dashed currentColor', cursor: 'help' }}>{v}</span>
          </Tooltip>
        ),
    },
    {
      title: '速率(mm/d)',
      dataIndex: 'rate_mm_per_day',
      width: 100,
      render: (v: number | null) => (v === null ? '-' : v.toFixed(2)),
    },
    { title: '变化方向', dataIndex: 'direction_label', width: 90 },
    {
      title: '预警状态',
      width: 90,
      render: (_, r) => <Tag color={WARNING_LABEL[r.warning_level].color}>{WARNING_LABEL[r.warning_level].text}</Tag>,
    },
  ]

  if (!loading && series.length === 0) {
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Button onClick={onBack}>← 返回</Button>
        <Alert type="info" showIcon message="暂无观测数据" description="请先在「观测录入」中录入至少两期数据，即可生成逐期对比。" />
      </Space>
    )
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Button onClick={onBack}>← 返回</Button>
        <Alert
          type="info"
          showIcon
          style={{ flex: 1 }}
          message="逐期对比分析（报告数据源）"
          description="每期与上一期依次对比：首期相对初始值期；中间缺测时与最近有效读数对比并标注跨期天数。关注维度：ΔV / 累计C / 速率 / 方向 / 预警。"
        />
      </Space>
      {series.map((s) => (
        <Card
          key={`${s.point_id}_${s.series_type}_${s.component}`}
          size="small"
          title={
            <Typography.Text strong>
              测点 {s.point_no} · {SERIES_NAME[`${s.series_type}_${s.component}`] ?? `${s.series_type}/${s.component}`}
            </Typography.Text>
          }
        >
          <Table rowKey="period" columns={columns} dataSource={s.periods} pagination={false} size="small" />
        </Card>
      ))}
    </Space>
  )
}
