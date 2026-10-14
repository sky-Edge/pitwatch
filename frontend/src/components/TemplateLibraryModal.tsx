import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { DownloadOutlined, EditOutlined, SettingOutlined, UploadOutlined } from '@ant-design/icons'
import {
  downloadBlob,
  projectApi,
  reportApi,
  type ExportTemplateInfo,
  type Project,
} from '../api/client'
import ScopePicker from './ScopePicker'

/**
 * 母版库 —— 在主界面就把所有母版管起来。
 *
 * 为什么要有它（用户 2026-10-11）：
 *   「能不能把一些操作集中主界面上？每个项目都要调单独设置有点繁琐」
 * 母版虽然早就能作用于多个项目（`template_projects`），但**管理入口**还锁在
 * 某个项目的「成果导出」页里 —— 想看"我一共有几份母版、哪份给了哪些基坑"，
 * 得先点进一个项目。这里把它提到主界面。
 *
 * 与「预警规则库」是同一套路数：东西不属于项目，由它自己声明作用于哪些项目。
 */
export default function TemplateLibraryModal({
  open,
  onClose,
  onChanged,
}: {
  open: boolean
  onClose: () => void
  /** 有增删改时通知外面（导出页的下拉要跟着刷） */
  onChanged?: () => void
}) {
  const [items, setItems] = useState<ExportTemplateInfo[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)

  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadName, setUploadName] = useState('')
  const [uploadGlobal, setUploadGlobal] = useState(false)
  const [uploadProjectIds, setUploadProjectIds] = useState<number[]>([])
  const [uploading, setUploading] = useState(false)

  const [renameTarget, setRenameTarget] = useState<ExportTemplateInfo | null>(null)
  const [renameValue, setRenameValue] = useState('')

  const [scopeTarget, setScopeTarget] = useState<ExportTemplateInfo | null>(null)
  const [scopeGlobal, setScopeGlobal] = useState(false)
  const [scopeProjectIds, setScopeProjectIds] = useState<number[]>([])
  const [savingScope, setSavingScope] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [lib, ps] = await Promise.all([reportApi.listLibrary(), projectApi.list()])
      setItems(lib)
      setProjects(ps)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  const openUpload = () => {
    setUploadName('')
    setUploadGlobal(false)
    setUploadProjectIds([])
    setUploadOpen(true)
  }

  const doUpload = async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.xlsx') && !file.name.toLowerCase().endsWith('.xlsm')) {
      message.error('仅支持 .xlsx/.xlsm 模板文件')
      return false
    }
    if (!uploadGlobal && uploadProjectIds.length === 0) {
      message.warning('请先选这份母版作用于哪些项目，或勾上「所有项目通用」')
      return false
    }
    setUploading(true)
    try {
      const r = await reportApi.uploadToLibrary(uploadName, file, {
        projectIds: uploadProjectIds,
        isGlobal: uploadGlobal,
      })
      message.success(r.message || `母版「${r.name}」已入库`)
      setUploadOpen(false)
      await load()
      onChanged?.()
    } catch (e) {
      message.error((e as Error).message, 8)
    } finally {
      setUploading(false)
    }
    return false
  }

  const openRename = (t: ExportTemplateInfo) => {
    setRenameTarget(t)
    setRenameValue(t.name)
  }

  const doRename = async () => {
    if (!renameTarget) return
    const name = renameValue.trim()
    if (!name) {
      message.warning('请输入母版名称')
      return
    }
    try {
      await reportApi.renameTemplate(renameTarget.id, name)
      message.success(`已改名为「${name}」`)
      setRenameTarget(null)
      await load()
      onChanged?.()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const openScope = (t: ExportTemplateInfo) => {
    setScopeTarget(t)
    setScopeGlobal(Boolean(t.is_global))
    setScopeProjectIds(t.project_ids ?? [])
  }

  const doSaveScope = async () => {
    if (!scopeTarget) return
    if (!scopeGlobal && scopeProjectIds.length === 0) {
      message.warning('请至少选一个项目，或者设为「所有项目通用」')
      return
    }
    setSavingScope(true)
    try {
      await reportApi.setTemplateScope(scopeTarget.id, {
        projectIds: scopeProjectIds,
        isGlobal: scopeGlobal,
      })
      message.success(`「${scopeTarget.name}」的作用范围已更新`)
      setScopeTarget(null)
      await load()
      onChanged?.()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSavingScope(false)
    }
  }

  const doDownload = async (t: ExportTemplateInfo) => {
    try {
      const blob = await reportApi.downloadTemplateFile(t.id)
      downloadBlob(blob, `${t.name}.xlsx`)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const doDelete = async (t: ExportTemplateInfo) => {
    try {
      await reportApi.deleteTemplate(t.id)
      message.success(`母版「${t.name}」已删除`)
      await load()
      onChanged?.()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const columns: ColumnsType<ExportTemplateInfo> = [
    {
      title: '母版名称',
      dataIndex: 'name',
      render: (v: string, r) => (
        <Space size={6}>
          <Typography.Text strong>{v}</Typography.Text>
          {r.is_builtin && <Tag>系统内置</Tag>}
        </Space>
      ),
    },
    {
      title: '作用于哪些项目',
      width: 320,
      render: (_, r) => {
        if (r.is_builtin) return <Typography.Text type="secondary">所有项目都能用（系统版式）</Typography.Text>
        if (r.is_global) return <Tag color="blue">所有项目通用</Tag>
        const names = r.project_names ?? []
        if (names.length === 0)
          return <Typography.Text type="warning">还没绑定任何项目 —— 点右边「设置适用项目」加上</Typography.Text>
        return (
          <Space size={[4, 4]} wrap>
            {names.map((n) => (
              <Tag key={n}>{n}</Tag>
            ))}
          </Space>
        )
      },
    },
    {
      title: '原件',
      width: 110,
      render: (_, r) => {
        if (r.is_builtin) return <Typography.Text type="secondary">—</Typography.Text>
        return r.file_ok === false ? (
          <Tag color="red">原件缺失</Tag>
        ) : (
          <Tag color="green">在</Tag>
        )
      },
    },
    {
      title: '操作',
      width: 300,
      render: (_, r) => {
        if (r.is_builtin) return <Typography.Text type="secondary">内置版式不可改</Typography.Text>
        return (
          <Space size={4} wrap>
            <Button size="small" icon={<EditOutlined />} onClick={() => openRename(r)}>
              改名
            </Button>
            <Button size="small" icon={<SettingOutlined />} onClick={() => openScope(r)}>
              设置适用项目
            </Button>
            <Tooltip title="下载这份母版原件（想直接看或改样板时用）">
              <Button
                size="small"
                icon={<DownloadOutlined />}
                disabled={r.file_ok === false}
                onClick={() => void doDownload(r)}
              />
            </Tooltip>
            <Popconfirm
              title={`删除母版「${r.name}」？`}
              description="会一并解除它与各项目的绑定。已经导出的报表不受影响。"
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => void doDelete(r)}
            >
              <Button size="small" danger type="text">
                删除
              </Button>
            </Popconfirm>
          </Space>
        )
      },
    },
  ]

  const custom = items.filter((t) => !t.is_builtin)

  return (
    <>
      <Modal
        title="母版库"
        open={open}
        onCancel={onClose}
        footer={<Button onClick={onClose}>关闭</Button>}
        width={980}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Alert
            type="info"
            showIcon
            message="母版不属于某个项目 —— 由它自己声明「作用于哪些项目」"
            description="一份母版可以同时给好几个基坑用：报表版式一样时，改表头只要重传一次同名母版，原先绑定的项目自动跟过来。新项目也能自动挂上默认母版（见主界面的「项目默认值」）。"
          />
          <Space wrap>
            <Button type="primary" icon={<UploadOutlined />} onClick={openUpload}>
              上传母版
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              共 {custom.length} 份自定义母版
            </Typography.Text>
          </Space>
          <Table
            rowKey="id"
            size="small"
            loading={loading}
            columns={columns}
            dataSource={items}
            pagination={false}
          />
        </Space>
      </Modal>

      {/* 上传：先挑文件，名字可以留空（用文件名）—— 顺序不该被起名字卡住 */}
      <Modal
        title="上传母版到母版库"
        open={uploadOpen}
        onCancel={() => setUploadOpen(false)}
        footer={null}
        width={560}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Upload beforeUpload={(f) => void doUpload(f)} accept=".xlsx,.xlsm" showUploadList={false} disabled={uploading}>
            <Button type="primary" block icon={<UploadOutlined />} loading={uploading}>
              {uploading ? '正在学习这份母版…' : '选择整卷文件上传'}
            </Button>
          </Upload>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              母版名称（可留空，留空就用文件名；事后也能改）
            </Typography.Text>
            <Input
              style={{ marginTop: 4 }}
              value={uploadName}
              onChange={(e) => setUploadName(e.target.value)}
              placeholder="如：基坑监测日报（标准版）"
            />
          </div>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              这份母版作用于哪些项目
            </Typography.Text>
            <div style={{ marginTop: 4 }}>
              <ScopePicker
                projects={projects}
                value={uploadProjectIds}
                isGlobal={uploadGlobal}
                onValueChange={setUploadProjectIds}
                onGlobalChange={setUploadGlobal}
              />
            </div>
          </div>
        </Space>
      </Modal>

      <Modal
        title="设置适用项目"
        open={scopeTarget !== null}
        onOk={() => void doSaveScope()}
        onCancel={() => setScopeTarget(null)}
        confirmLoading={savingScope}
        okText="保存"
        cancelText="取消"
        width={560}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          「{scopeTarget?.name}」作用于哪些项目 —— 只有这里勾上的项目，在导出页的母版下拉里才看得见它。
        </Typography.Paragraph>
        <ScopePicker
          projects={projects}
          value={scopeProjectIds}
          isGlobal={scopeGlobal}
          onValueChange={setScopeProjectIds}
          onGlobalChange={setScopeGlobal}
        />
      </Modal>

      <Modal
        title="母版改名"
        open={renameTarget !== null}
        onOk={() => void doRename()}
        onCancel={() => setRenameTarget(null)}
        okText="保存"
        cancelText="取消"
      >
        <Input
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          placeholder="母版名称"
          onPressEnter={() => void doRename()}
        />
      </Modal>
    </>
  )
}
