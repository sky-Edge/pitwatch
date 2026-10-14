import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Modal, Space, Table, Typography, message } from 'antd'
import { CloudDownloadOutlined, DatabaseOutlined, ReloadOutlined, UndoOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { backupApi, type BackupRecordInfo } from '../api/client'

const fmtSize = (b: number): string => {
  if (b > 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`
  if (b > 1024) return `${(b / 1024).toFixed(0)} KB`
  return `${b} B`
}

export default function BackupPage() {
  const [records, setRecords] = useState<BackupRecordInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setRecords(await backupApi.list())
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const create = async () => {
    setCreating(true)
    try {
      const rec = await backupApi.create()
      message.success(`备份完成：${rec.filename}`)
      void load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setCreating(false)
    }
  }

  const restore = async (rec: BackupRecordInfo) => {
    // 二次确认：还原是破坏性操作（覆盖当前数据库）
    Modal.confirm({
      title: '⚠️ 确认还原？此操作将用备份覆盖当前全部数据',
      content: (
        <div>
          <p>备份文件：<Typography.Text code>{rec.filename}</Typography.Text></p>
          <Typography.Text type="danger">
            还原会覆盖当前数据库中的所有项目、测点、观测数据。系统会在还原前自动对当前数据做一次快照备份（可回退），但请务必确认你操作正确。
          </Typography.Text>
        </div>
      ),
      okText: '确认还原',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          const r = await backupApi.restore(rec.id)
          message.success(`还原成功（自动快照：${r.safety_backup}）`)
          void load()
        } catch (e) {
          message.error((e as Error).message)
        }
      },
    })
  }

  const columns: ColumnsType<BackupRecordInfo> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '备份文件', dataIndex: 'filename' },
    {
      title: '大小',
      dataIndex: 'size_bytes',
      width: 90,
      render: (v: number) => fmtSize(v),
    },
    {
      title: '类型',
      dataIndex: 'backup_type',
      width: 80,
      render: (v: string) => (v === 'manual' ? <span>手动</span> : <span>自动</span>),
    },
    {
      title: '说明',
      dataIndex: 'note',
      render: (v: string | null) => v ?? '',
    },
    { title: '创建时间', dataIndex: 'created_at', width: 170 },
    {
      title: '操作',
      width: 120,
      render: (_, rec) => (
        // 危险度靠 Modal.confirm 里的后果说明体现，不再叠一层 Popconfirm 让用户点两次
        <Button size="small" danger icon={<UndoOutlined />} onClick={() => void restore(rec)}>
          还原
        </Button>
      ),
    },
  ]

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="info"
        showIcon
        message="数据备份与还原"
        description="备份生成数据库完整快照（含全部项目/测点/观测/预警/规则数据）。还原为破坏性操作，需二次确认；还原前系统自动对当前数据再做一次快照备份，可随时回退。"
      />
      <Card
        size="small"
        title="备份列表"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            <Button type="primary" icon={<DatabaseOutlined />} loading={creating} onClick={() => void create()}>
              立即备份
            </Button>
            <Button icon={<CloudDownloadOutlined />} onClick={() => message.info('备份文件位于 database/backups/ 目录，可直接拷贝保存')}>
              备份位置
            </Button>
          </Space>
        }
      >
        <Table<BackupRecordInfo>
          size="small"
          rowKey="id"
          columns={columns}
          dataSource={records}
          loading={loading}
          pagination={false}
          locale={{ emptyText: '暂无备份记录，点击「立即备份」创建第一份快照' }}
        />
      </Card>
    </Space>
  )
}
