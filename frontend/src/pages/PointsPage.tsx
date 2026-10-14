import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { PlusOutlined } from '@ant-design/icons'
import { pointApi, type MeasurementPoint, type PointType, type Project } from '../api/client'
import { POINT_TYPE_LABEL, POINT_TYPE_TAG_COLOR } from '../labels'
import BatchPointsModal from '../components/BatchPointsModal'
import { WARNING_LEVEL } from '../theme/tokens'

interface FormValues {
  point_no: string
  point_type: PointType
  initial_value_mm?: number | null
  location_desc?: string
}

export default function PointsPage({
  project,
  onBack,
  onRecords,
}: {
  project: Project
  onBack: () => void
  onRecords: () => void
}) {
  const [points, setPoints] = useState<MeasurementPoint[]>([])
  const [loading, setLoading] = useState(false)
  const [includeInactive, setIncludeInactive] = useState(true)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<MeasurementPoint | null>(null)
  const [batchOpen, setBatchOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<MeasurementPoint | null>(null)
  const [deleteInput, setDeleteInput] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [form] = Form.useForm<FormValues>()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await pointApi.list(project.id, includeInactive)
      setPoints(data)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [project.id, includeInactive])

  useEffect(() => {
    void load()
  }, [load])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    setModalOpen(true)
  }

  const openEdit = (p: MeasurementPoint) => {
    setEditing(p)
    form.setFieldsValue({
      point_no: p.point_no,
      point_type: p.point_type,
      initial_value_mm: p.initial_value_mm ?? undefined,
      location_desc: p.location_desc ?? '',
    })
    setModalOpen(true)
  }

  const submit = async () => {
    const values = await form.validateFields()
    const payload = {
      point_no: values.point_no,
      point_type: values.point_type,
      initial_value_mm: values.initial_value_mm ?? null,
      location_desc: values.location_desc || null,
    }
    try {
      if (editing) {
        await pointApi.update(editing.id, payload)
        message.success('测点已更新')
      } else {
        await pointApi.create(project.id, payload)
        message.success('测点已创建')
      }
      setModalOpen(false)
      void load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const archive = async (p: MeasurementPoint) => {
    try {
      await pointApi.archive(p.id)
      message.success(`测点 ${p.point_no} 已停用`)
      void load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const reactivate = async (p: MeasurementPoint) => {
    try {
      await pointApi.update(p.id, { status: 'active' })
      message.success(`测点 ${p.point_no} 已恢复`)
      void load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  /** 物理删除测点：输入测点编号确认（服务端删除前自动备份） */
  const confirmHardDelete = async () => {
    if (!deleteTarget) return
    if (deleteInput.trim() !== deleteTarget.point_no) {
      message.error(`请输入测点编号 ${deleteTarget.point_no} 以确认删除`)
      return
    }
    setDeleting(true)
    try {
      const r = await pointApi.hardDelete(deleteTarget.id, deleteTarget.point_no)
      if (r.deleted) {
        message.success(`测点 ${deleteTarget.point_no} 及其观测数据已删除（删除前已自动备份）`)
        setDeleteTarget(null)
        setDeleteInput('')
        void load()
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  const columns: ColumnsType<MeasurementPoint> = [
    { title: 'ID', dataIndex: 'id', width: 64 },
    { title: '测点编号', dataIndex: 'point_no', width: 120 },
    {
      title: '类型',
      dataIndex: 'point_type',
      width: 110,
      render: (v: PointType) => <Tag color={POINT_TYPE_TAG_COLOR[v]}>{POINT_TYPE_LABEL[v]}</Tag>,
    },
    {
      title: '初始值(mm)',
      dataIndex: 'initial_value_mm',
      width: 110,
      render: (v: number | null) => (v === null || v === undefined ? '-' : v),
    },
    { title: '位置描述', dataIndex: 'location_desc', render: (v: string | null) => v || '-' },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (v === 'active' ? <Tag color="green">在用</Tag> : <Tag>已停用</Tag>),
    },
    {
      title: '操作',
      width: 180,
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => openEdit(record)}>
            编辑
          </Button>
          {record.status === 'active' ? (
            <Popconfirm title={`确定停用测点 ${record.point_no}？`} onConfirm={() => archive(record)}>
              <Button size="small" danger>
                停用
              </Button>
            </Popconfirm>
          ) : (
            <Button size="small" onClick={() => reactivate(record)}>
              恢复
            </Button>
          )}
          <Button size="small" type="text" danger onClick={() => { setDeleteTarget(record); setDeleteInput('') }}>
            删除…
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Button onClick={onBack}>← 返回项目列表</Button>
        <Alert
          type="info"
          showIcon
          style={{ flex: 1 }}
          message={`${project.name}（${project.code}）· 测点管理`}
          description="在这里建好监测点（例如 JJC1~JJC8），每个点填一次初始值即可。停用测点不会删除已有数据。"
        />
      </Space>
      {points.length === 0 && (
        <Alert
          type="warning"
          showIcon
          message="还没有测点"
          description="点「新建测点」逐个添加，或用「批量新建」一次粘贴多个（每行格式：编号,类型,初始值）。建好测点后就能录入观测数据了。"
        />
      )}
      <Space>
        <Button type="primary" onClick={openCreate}>
          新建测点
        </Button>
        <Button type="primary" ghost icon={<PlusOutlined />} onClick={() => setBatchOpen(true)}>
          批量新建
        </Button>
        <Button type="primary" ghost onClick={onRecords}>
          观测录入
        </Button>
        <span>显示已停用：</span>
        <Switch checked={includeInactive} onChange={setIncludeInactive} />
      </Space>
      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={points}
        pagination={{ pageSize: 10 }}
      />

      <Modal
        title={editing ? `编辑测点 ${editing.point_no}` : '新建测点'}
        open={modalOpen}
        onOk={() => void submit()}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="point_no" label="测点编号" rules={[{ required: true, message: '请输入测点编号' }]}>
            <Input placeholder="如 LZ-01" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="point_type" label="观测类型" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'settlement', label: '沉降' },
                { value: 'displacement', label: '水平位移' },
                { value: 'both', label: '沉降 + 水平位移' },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="initial_value_mm"
            label="显式初始值 V0(mm，可选)"
            tooltip="留空则由系统取首期有效读数作为 V0（推荐默认行为）"
          >
            <InputNumber style={{ width: '100%' }} placeholder="留空 = 取首期有效读数" />
          </Form.Item>
          <Form.Item name="location_desc" label="位置描述">
            <Input placeholder="如 1#钢板桩桩顶 / 桩号 K2+100" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 物理删除确认：需输入测点编号 */}
      <Modal
        title={<span style={{ color: WARNING_LEVEL.alarm.fg }}>⚠ 彻底删除测点 {deleteTarget?.point_no}</span>}
        open={!!deleteTarget}
        onOk={() => void confirmHardDelete()}
        onCancel={() => setDeleteTarget(null)}
        okText="确认彻底删除"
        okButtonProps={{ danger: true, loading: deleting, disabled: deleteInput.trim() !== deleteTarget?.point_no }}
        cancelText="取消"
        destroyOnClose
      >
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="此操作不可撤销"
          description="将同时删除该测点的全部观测数据与预警事件。删除前系统会自动备份数据库（database/backups/），误删可还原。"
        />
        <Typography.Paragraph>
          请在下方输入测点编号 <Typography.Text code strong>{deleteTarget?.point_no}</Typography.Text> 以确认：
        </Typography.Paragraph>
        <Input
          placeholder={`输入 ${deleteTarget?.point_no} 确认删除`}
          value={deleteInput}
          onChange={(e) => setDeleteInput(e.target.value)}
          onPressEnter={() => void confirmHardDelete()}
        />
      </Modal>

      <BatchPointsModal
        projectId={project.id}
        open={batchOpen}
        onClose={() => setBatchOpen(false)}
        onDone={() => void load()}
      />
    </Space>
  )
}
