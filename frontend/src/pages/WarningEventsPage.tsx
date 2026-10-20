import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Modal, Radio, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  warningEventApi,
  type FixWarningItem,
  type FixWarningResult,
  type QualityReport,
  type WarningEventRow,
} from '../api/client'

interface Props {
  projectId: number
  onBack: () => void
}

export default function WarningEventsPage({ projectId, onBack }: Props) {
  const [events, setEvents] = useState<WarningEventRow[]>([])
  const [quality, setQuality] = useState<QualityReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [recomputing, setRecomputing] = useState(false)

  // 一键修正报警值
  const [fixOpen, setFixOpen] = useState(false)
  const [scope, setScope] = useState<'all' | 'alarm'>('all')
  const [preview, setPreview] = useState<FixWarningResult | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [outcome, setOutcome] = useState<FixWarningResult | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [ev, q] = await Promise.all([warningEventApi.list(projectId), warningEventApi.quality(projectId)])
      setEvents(ev)
      setQuality(q)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  const recompute = async () => {
    setRecomputing(true)
    try {
      const r = await warningEventApi.recompute(projectId)
      message.success(`重算完成：共生成 ${r.events_generated} 条预警事件`)
      void load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setRecomputing(false)
    }
  }

  const loadPreview = useCallback(
    async (next: 'all' | 'alarm') => {
      setPreviewLoading(true)
      try {
        setPreview(await warningEventApi.fixValues(projectId, { dryRun: true, includeControl: next === 'all' }))
      } catch (e) {
        message.error((e as Error).message)
      } finally {
        setPreviewLoading(false)
      }
    },
    [projectId],
  )

  const openFix = () => {
    setOutcome(null)
    setScope('all')
    setFixOpen(true)
    void loadPreview('all')
  }

  const changeScope = (next: 'all' | 'alarm') => {
    setScope(next)
    void loadPreview(next)
  }

  const applyFix = async () => {
    setApplying(true)
    try {
      const r = await warningEventApi.fixValues(projectId, { dryRun: false, includeControl: scope === 'all' })
      // 结果放在**页面上**，不留在弹窗里 —— 弹窗一关就什么都没了（这是踩过的坑）
      setOutcome(r)
      setFixOpen(false)
      await load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setApplying(false)
    }
  }

  const columns: ColumnsType<WarningEventRow> = [
    {
      title: '等级',
      dataIndex: 'level',
      width: 70,
      render: (v: string) => (v === 'alarm' ? <Tag color="red">报警</Tag> : <Tag color="gold">预警</Tag>),
    },
    { title: '测点', dataIndex: 'point_no', width: 90 },
    {
      title: '观测项',
      width: 110,
      render: (_, r) => <Tag>{r.series_type === 'settlement' ? '沉降' : '位移'} / {r.component}</Tag>,
    },
    { title: '日期', dataIndex: 'obs_date', width: 110 },
    {
      title: '触发项',
      dataIndex: 'trigger_type',
      width: 110,
      render: (v: string) =>
        ({ cumulative: '累计变化量', delta: '本次变化量', rate: '变化速率' })[v] ?? v,
    },
    {
      title: '值(mm)',
      dataIndex: 'value_mm',
      width: 90,
      render: (v: number | null) => (v == null ? '-' : v),
    },
    { title: '说明', dataIndex: 'message', ellipsis: true },
  ]

  const fixColumns: ColumnsType<FixWarningItem> = [
    { title: '测点', dataIndex: 'point_no', width: 90 },
    {
      title: '观测项',
      width: 110,
      render: (_, r) => <Tag>{r.series_type === 'settlement' ? '沉降' : '位移'} / {r.component}</Tag>,
    },
    { title: '日期', dataIndex: 'obs_date', width: 110 },
    {
      title: '读数修正',
      width: 190,
      render: (_, r) => (
        <span>
          <Typography.Text type="secondary">{r.old_value_mm}</Typography.Text>
          {' → '}
          <Typography.Text strong>{r.new_value_mm ?? r.old_value_mm}</Typography.Text>
          <Typography.Text type="secondary"> mm</Typography.Text>
        </span>
      ),
    },
    {
      title: '触发等级',
      dataIndex: 'level',
      width: 90,
      render: (v: string) => (v === 'alarm' ? <Tag color="red">报警</Tag> : <Tag color="gold">预警</Tag>),
    },
  ]

  const qualityColumns: ColumnsType<QualityReport['suspects'][number]> = [
    { title: '测点', dataIndex: 'point_no', width: 90 },
    {
      title: '观测项',
      width: 110,
      render: (_, r) => <Tag>{r.series_type === 'settlement' ? '沉降' : '位移'} / {r.component}</Tag>,
    },
    { title: '日期', dataIndex: 'obs_date', width: 110 },
    { title: '单期变化(mm)', dataIndex: 'delta_mm', width: 110 },
    { title: '提示', dataIndex: 'note' },
  ]

  const fixable = preview?.fixable ?? 0

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Button onClick={onBack}>← 返回项目列表</Button>
        <Alert
          type="info"
          showIcon
          style={{ flex: 1 }}
          message="预警事件 · 数据质量"
          description="预警事件为派生数据，可随时重算（整体重建、幂等、批次留痕）；质量检查只标记提示，不删除任何原始数据。"
        />
        <Space>
          <Button onClick={openFix} disabled={events.length === 0}>
            修正最新一期超限值
          </Button>
          <Button type="primary" loading={recomputing} onClick={() => void recompute()}>
            重新计算预警
          </Button>
        </Space>
      </Space>

      {outcome && (
        <Card
          size="small"
          title={`修正完成：改了 ${outcome.changed} 条读数`}
          extra={<Button type="link" onClick={() => setOutcome(null)}>收起</Button>}
        >
          <Space direction="vertical" style={{ width: '100%' }}>
            <Typography.Text>
              其中报警 <Typography.Text strong>{outcome.by_level.alarm}</Typography.Text> 条、
              预警 <Typography.Text strong>{outcome.by_level.control}</Typography.Text> 条；
              修正后重新计算预警事件，当前剩余{' '}
              <Typography.Text strong>{outcome.recompute?.events_generated ?? 0}</Typography.Text> 条。
            </Typography.Text>
            <Typography.Text type="secondary">
              原读数已作废保留、可追溯（每条都写了「旧值 → 新值」说明），没有删除任何数据。
            </Typography.Text>
            {outcome.unfixable > 0 && (
              <Alert
                type="warning"
                showIcon
                message={`有 ${outcome.unfixable} 条改不动`}
                description={outcome.unfixable_samples.map((u) => `${u.point_no} ${u.obs_date}：${u.reason}`).join('；')}
              />
            )}
            {outcome.samples.length > 0 && (
              <Table<FixWarningItem>
                size="small"
                rowKey={(r) => `${r.point_no}-${r.series_type}-${r.component}-${r.obs_date}`}
                columns={fixColumns}
                dataSource={outcome.samples}
                pagination={false}
              />
            )}
          </Space>
        </Card>
      )}

      <Card size="small" title="数据质量检查">
        {quality && (
          <Space wrap style={{ marginBottom: 12 }}>
            <Tag color={quality.suspect_count > 0 ? 'red' : 'green'}>
              突变疑似 {quality.suspect_count} 条（阈值 {quality.mutation_threshold_mm}mm）
            </Tag>
            <Tag color={quality.missing_count > 0 ? 'orange' : 'green'}>缺测 {quality.missing_count} 条</Tag>
          </Space>
        )}
        {quality && quality.suspects.length > 0 && (
          <Table size="small" rowKey={(_, i) => `s-${i ?? 0}`} columns={qualityColumns} dataSource={quality.suspects} pagination={false} />
        )}
        {quality && quality.missing.length > 0 && (
          <Table
            size="small"
            rowKey={(_, i) => `m-${i ?? 0}`}
            columns={[
              { title: '测点', dataIndex: 'point_no', width: 90 },
              { title: '观测项', width: 110, render: (_, r) => <Tag>{r.series_type} / {r.component}</Tag> },
              { title: '日期', dataIndex: 'obs_date', width: 110 },
            ]}
            dataSource={quality.missing}
            pagination={false}
          />
        )}
        {quality && quality.suspects.length === 0 && quality.missing.length === 0 && (
          <Typography.Text type="success">✓ 未发现突变疑似或缺测，数据质量正常</Typography.Text>
        )}
      </Card>

      <Card size="small" title={`预警事件（${events.length} 条）`}>
        <Table<WarningEventRow>
          size="small"
          rowKey="id"
          columns={columns}
          dataSource={events}
          loading={loading}
          pagination={{ pageSize: 10, showSizeChanger: true }}
        />
      </Card>

      <Modal
        open={fixOpen}
        title="修正最新一期超限值"
        width={720}
        onCancel={() => setFixOpen(false)}
        onOk={() => void applyFix()}
        okText={fixable > 0 ? `确认修正 ${fixable} 条` : '没有需要修正的读数'}
        okButtonProps={{ disabled: fixable === 0 || previewLoading, loading: applying }}
        cancelText="取消"
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Alert
            type="warning"
            showIcon
            message="只修最新一期，历史期一个数都不动"
            description="历史期可能已经出了日报、报了监理，改它就是篡改记录，所以系统只调整每个测点「最新一期」里超出限值的读数。修正走「更正」流程：原读数作废保留、可追溯，不会删除任何数据。"
          />
          <Radio.Group value={scope} onChange={(e) => changeScope(e.target.value)} disabled={applying}>
            <Radio value="all">预警 + 报警一起修（推荐）</Radio>
            <Radio value="alarm">只修报警</Radio>
          </Radio.Group>

          {previewLoading && <Typography.Text type="secondary">正在检查…</Typography.Text>}

          {!previewLoading && preview && fixable === 0 && (
            <Typography.Text type="success">✓ 当前没有需要修正的读数</Typography.Text>
          )}

          {!previewLoading && preview && fixable > 0 && (
            <>
              <Typography.Text>
                将修正 <Typography.Text strong>{fixable}</Typography.Text> 条读数：
                报警 {preview.by_level.alarm} 条、预警 {preview.by_level.control} 条。
              </Typography.Text>
              <Typography.Text type="secondary">
                （条数可能多于上面的事件条数 —— 改了前一期，后一期的变化量会跟着变大，要连带一起修。）
              </Typography.Text>
              <Table<FixWarningItem>
                size="small"
                rowKey={(r) => `${r.point_no}-${r.series_type}-${r.component}-${r.obs_date}`}
                columns={fixColumns}
                dataSource={preview.samples}
                pagination={false}
                scroll={{ y: 240 }}
              />
              {preview.unfixable > 0 && (
                <Alert
                  type="info"
                  showIcon
                  message={`另有 ${preview.unfixable} 条改不动`}
                  description={preview.unfixable_samples.map((u) => `${u.point_no} ${u.obs_date}：${u.reason}`).join('；')}
                />
              )}
            </>
          )}
        </Space>
      </Modal>
    </Space>
  )
}
