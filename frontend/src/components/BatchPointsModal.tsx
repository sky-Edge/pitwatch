import { useEffect, useState } from 'react'
import { Alert, Button, Input, Modal, Space, Table, Tag, Typography, message } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { pointApi, type PointInput, type PointType } from '../api/client'

interface Props {
  projectId: number
  open: boolean
  onClose: () => void
  onDone: () => void
}

interface PreviewRaw {
  index: number
  point_no: string
  message: string
  point?: PointInput | null
  reason?: string
}

interface PreviewRow {
  key: number
  index: number
  point_no: string
  point_type: PointType
  initial_value_mm?: number | null
  location_desc?: string | null
  status: 'valid' | 'conflict' | 'error'
  message: string
}

const TYPE_ALIAS: Record<string, PointType> = {
  settlement: 'settlement',
  '沉降': 'settlement',
  displacement: 'displacement',
  '水平': 'displacement',
  '水平位移': 'displacement',
  both: 'both',
  '沉降位移': 'both',
  '沉降+位移': 'both',
}

const TYPE_LABEL: Record<PointType, string> = { settlement: '沉降', displacement: '水平位移', both: '沉降+位移' }

/** 解析多行文本：每行 `编号,类型[,初始值][,位置]`；兼容 Excel 粘贴与表头行。 */
function parseText(text: string): PointInput[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== '')
  const out: PointInput[] = []
  for (const line of lines) {
    const parts = line.split(/[,\t，;；]+/).map((s) => s.trim())
    const point_no = parts[0] ?? ''
    if (!point_no) continue
    // 跳过表头（含"编号/点号/类型"字样）
    if (/编号|点号|point/i.test(point_no) && parts.length <= 2) continue
    const typeRaw = (parts[1] ?? 'both').toLowerCase()
    const point_type = TYPE_ALIAS[typeRaw] ?? TYPE_ALIAS[parts[1] ?? ''] ?? 'both'
    const item: PointInput = { point_no, point_type }
    if (parts[2] && parts[2] !== '' && !isNaN(Number(parts[2]))) item.initial_value_mm = Number(parts[2])
    if (parts[3]) item.location_desc = parts[3]
    out.push(item)
  }
  return out
}

/** 测点批量新建：文本粘贴 → 预览校验（valid/conflict/error）→ 确认落库 */
export default function BatchPointsModal({ projectId, open, onClose, onDone }: Props) {
  const [text, setText] = useState('')
  const [rows, setRows] = useState<PreviewRow[]>([])
  const [phase, setPhase] = useState<'input' | 'preview'>('input')
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    if (open) {
      setText('')
      setRows([])
      setPhase('input')
    }
  }, [open])

  /** 从文本生成行并预览校验 */
  const preview = async () => {
    const items = parseText(text)
    if (items.length === 0) {
      message.warning('未解析到有效行。每行格式：编号,类型[,初始值][,位置]，如 JJC1,both,4907000,1#桩')
      return
    }
    setLoading(true)
    try {
      const res = await pointApi.batchPreview(projectId, items)
      const mk = (arr: PreviewRaw[], status: PreviewRow['status']): PreviewRow[] =>
        arr.map((r) => ({
          key: r.index,
          index: r.index,
          point_no: r.point_no,
          point_type: r.point?.point_type ?? 'both',
          initial_value_mm: r.point?.initial_value_mm,
          location_desc: r.point?.location_desc,
          status,
          message: r.message,
        }))
      setRows([...mk(res.valid, 'valid'), ...mk(res.conflict, 'conflict'), ...mk(res.error, 'error')].sort((a, b) => a.index - b.index))
      setPhase('preview')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const back = () => {
    setPhase('input')
    setRows([])
  }

  const create = async () => {
    const validItems = rows.filter((r) => r.status === 'valid')
    if (validItems.length === 0) {
      message.warning('没有可创建的测点（全部冲突/错误）')
      return
    }
    setCreating(true)
    try {
      const res = await pointApi.batchCreate(
        projectId,
        validItems.map((r) => ({
          point_no: r.point_no,
          point_type: r.point_type,
          initial_value_mm: r.initial_value_mm ?? null,
          location_desc: r.location_desc ?? null,
        })),
      )
      message.success(`创建 ${res.created} 个测点${res.skipped.length ? `，跳过 ${res.skipped.length}（已存在）` : ''}`)
      onDone()
      onClose()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setCreating(false)
    }
  }

  const removeRow = (index: number) => setRows((rs) => rs.filter((r) => r.index !== index))

  const columns: ColumnsType<PreviewRow> = [
    { title: '编号', dataIndex: 'point_no', width: 110, render: (v) => <Typography.Text strong>{v}</Typography.Text> },
    {
      title: '类型',
      dataIndex: 'point_type',
      width: 120,
      render: (v: PointType, r) =>
        r.status === 'valid' ? (
          <Tag color={v === 'both' ? 'purple' : v === 'settlement' ? 'blue' : 'orange'}>{TYPE_LABEL[v]}</Tag>
        ) : (
          '-'
        ),
    },
    {
      title: '初始值 V0(mm)',
      dataIndex: 'initial_value_mm',
      width: 120,
      render: (v: number | null | undefined) => (v == null ? '-' : v),
    },
    { title: '位置描述', dataIndex: 'location_desc', render: (v: string | null | undefined) => v || '-' },
    {
      title: '校验结果',
      width: 220,
      render: (_, r) =>
        r.status === 'valid' ? (
          <Tag color="green">可创建</Tag>
        ) : r.status === 'conflict' ? (
          <Tag color="orange">跳过（{r.message}）</Tag>
        ) : (
          <Tag color="red">错误（{r.message}）</Tag>
        ),
    },
    ...(phase === 'preview'
      ? ([
          {
            title: '操作',
            width: 70,
            render: (_: unknown, r: PreviewRow) =>
              r.status !== 'valid' ? (
                <Button size="small" icon={<DeleteOutlined />} onClick={() => removeRow(r.index)} />
              ) : null,
          },
        ] as ColumnsType<PreviewRow>)
      : []),
  ]

  return (
    <Modal
      title="批量新建测点"
      open={open}
      onCancel={() => (phase === 'preview' ? back() : onClose())}
      width={860}
      footer={
        phase === 'input' ? (
          <>
            <Button onClick={onClose}>取消</Button>
            <Button type="primary" loading={loading} onClick={() => void preview()}>
              校验并预览
            </Button>
          </>
        ) : (
          <>
            <Button onClick={back}>← 返回编辑</Button>
            <Button danger onClick={onClose}>
              取消
            </Button>
            <Button type="primary" loading={creating} onClick={() => void create()}>
              确认创建 {rows.filter((r) => r.status === 'valid').length} 个测点
            </Button>
          </>
        )
      }
    >
      {phase === 'input' ? (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="从 Excel 或文本粘贴，每行一个测点"
            description={
              <div style={{ fontSize: 12 }}>
                格式：<Typography.Text code>编号,类型,初始值(mm，可空),位置(可空)</Typography.Text>
                <br />
                示例：<Typography.Text code>JJC1,both,4907000,1#钢板桩</Typography.Text>
                <br />
                类型：settlement(沉降) / displacement(水平位移) / both(沉降+位移)，中文亦可
              </div>
            }
          />
          <Input.TextArea
            rows={10}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={'JJC1,both,4907000,1#钢板桩\nJJC2,settlement,,2#钢板桩\nJJC3,displacement,3413055112,3#钢板桩'}
          />
          <Typography.Text type="secondary">小批量也可直接用手工录入方式（单点新建）</Typography.Text>
        </Space>
      ) : (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Alert
            type="success"
            showIcon
            message={`解析到 ${rows.filter((r) => r.status === 'valid').length} 个可创建 / ${rows.filter((r) => r.status === 'conflict').length} 个跳过 / ${rows.filter((r) => r.status === 'error').length} 个错误`}
            description="可创建行可删除不需要的；冲突/错误行仅展示，点击删除可移除。确认后一次性创建。"
          />
          <Table<PreviewRow>
            size="small"
            rowKey="key"
            columns={columns}
            dataSource={rows}
            pagination={false}
            scroll={{ y: 320 }}
          />
        </Space>
      )}
    </Modal>
  )
}
