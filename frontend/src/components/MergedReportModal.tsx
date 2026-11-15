import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Divider,
  Empty,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { DeleteOutlined, EditOutlined, FileExcelOutlined, PrinterOutlined, PlusOutlined } from '@ant-design/icons'
import type { Dayjs } from 'dayjs'

import {
  downloadBlob,
  mergedReportApi,
  reportApi,
  type ExportTemplateInfo,
  type Project,
  type ReportGroup,
  type TemplateSheetOption,
} from '../api/client'

/**
 * 合并报表 —— 多个项目合出**一份**整卷日报。
 *
 * 用户 2026-11-14：「钢栈桥的数据实在太多了，打印也是很多，把南北两侧的数据总计在一起，
 * 封面和签字页和位置布置共用」。一份册子里封面/签字/概况/分析/布点图只有一份，
 * 数据表各占一张纸。
 *
 * 设计上刻意**不动项目模型**：南北两侧仍是两个项目、各存各的数据、各自导入，
 * 这里只是"出报时的装订方式"。删掉组合不影响任何观测数据。
 *
 * 现场用起来就一步：配一次 → 以后每天选日期 → 点「导出 Excel / 打印」。
 */
interface Props {
  open: boolean
  onClose: () => void
  projects: Project[]
}

interface Draft {
  id?: number
  name: string
  display_name: string
  template_id?: number
  main_project_id?: number
  /** 表名 → 项目 id */
  mapping: Record<string, number | undefined>
}

const EMPTY_DRAFT: Draft = { name: '', display_name: '', mapping: {} }

export default function MergedReportModal({ open, onClose, projects }: Props) {
  const [groups, setGroups] = useState<ReportGroup[]>([])
  const [loading, setLoading] = useState(false)
  const [templates, setTemplates] = useState<ExportTemplateInfo[]>([])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [sheetOptions, setSheetOptions] = useState<TemplateSheetOption[]>([])
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [obsDate, setObsDate] = useState<Dayjs | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [g, t] = await Promise.all([mergedReportApi.list(), reportApi.listLibrary()])
      setGroups(g)
      setTemplates(t.filter((x) => !x.is_builtin && x.file_ok !== false))
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  /** 换母版 → 重新拉数据表清单；已填的对应关系保留同名的 */
  const loadSheets = useCallback(async (templateId: number) => {
    try {
      const sheets = await mergedReportApi.templateSheets(templateId)
      setSheetOptions(sheets)
      setDraft((d) => (d ? { ...d, mapping: d.mapping } : d))
    } catch (e) {
      setSheetOptions([])
      message.error((e as Error).message)
    }
  }, [])

  const openCreate = () => {
    setDraft({ ...EMPTY_DRAFT, mapping: {} })
    setSheetOptions([])
  }

  const openEdit = async (g: ReportGroup) => {
    const mapping: Record<string, number | undefined> = {}
    g.items.forEach((it) => {
      mapping[it.sheet_name] = it.project_id
    })
    setDraft({
      id: g.id,
      name: g.name,
      display_name: g.display_name || '',
      template_id: g.template_id,
      main_project_id: g.main_project_id,
      mapping,
    })
    await loadSheets(g.template_id)
  }

  const save = async () => {
    if (!draft) return
    if (!draft.name.trim()) {
      message.warning('先给这份合并报表起个名字')
      return
    }
    if (!draft.template_id) {
      message.warning('先选一份合并母版（里面要有多张数据表）')
      return
    }
    if (!draft.main_project_id) {
      message.warning('先选主项目 —— 封面、签字页、期次都以它为准')
      return
    }
    const items = sheetOptions.map((s, idx) => ({
      sheet_name: s.sheet_name,
      project_id: draft.mapping[s.sheet_name] as number,
      sort_order: idx,
    }))
    const missing = items.filter((i) => !i.project_id).map((i) => i.sheet_name)
    if (missing.length) {
      message.warning(`这些数据表还没选项目：${missing.join('、')}`)
      return
    }
    if (!items.some((i) => i.project_id === draft.main_project_id)) {
      message.warning('主项目必须出现在数据表里（否则封面上的工程名称跟数据对不上）')
      return
    }
    setSaving(true)
    try {
      const payload = {
        name: draft.name.trim(),
        display_name: draft.display_name.trim() || null,
        template_id: draft.template_id,
        main_project_id: draft.main_project_id,
        items,
      }
      if (draft.id) await mergedReportApi.update(draft.id, payload)
      else await mergedReportApi.create(payload)
      message.success('已保存')
      setDraft(null)
      await load()
    } catch (e) {
      message.error((e as Error).message, 8)
    } finally {
      setSaving(false)
    }
  }

  const remove = (g: ReportGroup) => {
    Modal.confirm({
      title: `删除「${g.name}」？`,
      content: '只删掉这个"装订关系"，两个项目的观测数据一条都不会动。以后还能重新配一份。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          await mergedReportApi.remove(g.id)
          message.success('已删除')
          await load()
        } catch (e) {
          message.error((e as Error).message)
        }
      },
    })
  }

  const exportExcel = async (g: ReportGroup) => {
    setBusyId(g.id)
    const tip = message.loading(`正在生成「${g.name}」…`, 0)
    try {
      const blob = await mergedReportApi.exportExcel(g.id, obsDate?.format('YYYY-MM-DD'))
      const day = obsDate?.format('YYYY.MM.DD') || '最新一期'
      downloadBlob(blob, `${g.name}_${day}.xlsx`)
      message.success('已导出')
    } catch (e) {
      message.error((e as Error).message, 8)
    } finally {
      tip()
      setBusyId(null)
    }
  }

  const printPdf = async (g: ReportGroup) => {
    setBusyId(g.id)
    const tip = message.loading(`正在生成「${g.name}」PDF，约需 3 秒…`, 0)
    try {
      const blob = await mergedReportApi.pdf(g.id, obsDate?.format('YYYY-MM-DD'))
      const url = URL.createObjectURL(blob)
      // 与单项目导出同一套做法：塞进隐藏 iframe 调 print()，页面不跳走
      const iframe = document.createElement('iframe')
      iframe.setAttribute('aria-hidden', 'true')
      iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;'
      iframe.src = url
      iframe.onload = () => {
        window.setTimeout(() => {
          try {
            iframe.contentWindow?.focus()
            iframe.contentWindow?.print()
          } catch {
            window.open(url, '_blank')
          }
        }, 400)
      }
      document.body.appendChild(iframe)
      window.setTimeout(() => {
        iframe.remove()
        URL.revokeObjectURL(url)
      }, 180_000)
    } catch (e) {
      message.error((e as Error).message, 8)
    } finally {
      tip()
      setBusyId(null)
    }
  }

  const projectOptions = useMemo(
    () =>
      projects.map((p) => ({
        value: p.id,
        label: `${p.code} ${p.name}${p.is_active ? '' : '（已归档）'}`,
      })),
    [projects],
  )

  const columns: ColumnsType<ReportGroup> = [
    {
      title: '名称',
      dataIndex: 'name',
      width: 180,
      render: (v: string, r) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{v}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            封面印：{r.display_name || r.main_project_name || '—'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '数据表 ← 项目',
      render: (_v, r) => (
        <Space direction="vertical" size={2}>
          {r.items.map((it) => (
            <Space key={it.sheet_name} size={6}>
              <Tag color={it.project_id === r.main_project_id ? 'blue' : undefined}>{it.sheet_name}</Tag>
              <Typography.Text style={{ fontSize: 12 }}>← {it.project_name || `#${it.project_id}`}</Typography.Text>
              {it.project_id === r.main_project_id && (
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  主项目
                </Typography.Text>
              )}
            </Space>
          ))}
        </Space>
      ),
    },
    {
      title: '母版',
      dataIndex: 'template_name',
      width: 160,
      render: (v: string | null) => v || '—',
    },
    {
      title: '操作',
      width: 290,
      render: (_v, r) => (
        <Space size={6} wrap>
          <Button
            size="small"
            type="primary"
            ghost
            icon={<FileExcelOutlined />}
            loading={busyId === r.id}
            onClick={() => void exportExcel(r)}
          >
            导出
          </Button>
          <Button size="small" icon={<PrinterOutlined />} disabled={busyId === r.id} onClick={() => void printPdf(r)}>
            打印
          </Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => void openEdit(r)} />
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => remove(r)} />
        </Space>
      ),
    },
  ]

  return (
    <Modal
      title="合并报表 · 多个项目装订成一份"
      open={open}
      onCancel={() => {
        setDraft(null)
        onClose()
      }}
      footer={null}
      width={980}
      destroyOnClose
    >
      {!draft ? (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="一份册子：封面、签字页、概况、布点图只有一份，每个项目各占一张数据表"
            description="数据仍然各存各的项目、各导各的台账。这里只决定「打印时怎么装订」。天气、仪器、电子签名都跟单项目导出一致。"
          />
          <Space wrap size={8}>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新建合并报表
            </Button>
            <Divider type="vertical" />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              按哪天出：
            </Typography.Text>
            <DatePicker
              size="small"
              value={obsDate}
              onChange={(d) => setObsDate(d)}
              placeholder="留空 = 各项目都有数据的最近一天"
              style={{ width: 260 }}
            />
          </Space>

          <Card size="small" bodyStyle={{ padding: 0 }}>
            <Table<ReportGroup>
              rowKey="id"
              size="small"
              loading={loading}
              columns={columns}
              dataSource={groups}
              pagination={false}
              locale={{ emptyText: <Empty description="还没有合并报表，点上面「新建合并报表」配一份" /> }}
            />
          </Card>

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            注意：合并报表要求各项目**同一天都有数据**。选的那天谁没测，会直接告诉你"某项目最近一期是哪天"，不会拿旧数据凑一页。
          </Typography.Text>
        </Space>
      ) : (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space wrap size={12}>
            <Space direction="vertical" size={4}>
              <Typography.Text style={{ fontSize: 12 }}>名称（也做文件名）</Typography.Text>
              <Input
                style={{ width: 220 }}
                value={draft.name}
                placeholder="如：钢栈桥监测日报"
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </Space>
            <Space direction="vertical" size={4}>
              <Typography.Text style={{ fontSize: 12 }}>封面工程名称（可留空）</Typography.Text>
              <Input
                style={{ width: 220 }}
                value={draft.display_name}
                placeholder="如：钢栈桥"
                onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
              />
            </Space>
          </Space>

          <Space wrap size={12}>
            <Space direction="vertical" size={4}>
              <Typography.Text style={{ fontSize: 12 }}>用哪份母版</Typography.Text>
              <Select
                style={{ width: 300 }}
                value={draft.template_id}
                placeholder="选一份合并母版"
                onChange={(v: number) => {
                  setDraft({ ...draft, template_id: v })
                  void loadSheets(v)
                }}
                options={templates.map((t) => ({
                  value: t.id,
                  label: `${t.name}${t.is_global ? '（通用）' : ''}`,
                }))}
                notFoundContent="母版库里还没有可用的母版"
              />
            </Space>
            <Space direction="vertical" size={4}>
              <Typography.Text style={{ fontSize: 12 }}>主项目（封面/签字/期次跟它）</Typography.Text>
              <Select
                style={{ width: 300 }}
                value={draft.main_project_id}
                placeholder="选主项目"
                onChange={(v: number) => setDraft({ ...draft, main_project_id: v })}
                options={projectOptions}
                showSearch
                optionFilterProp="label"
              />
            </Space>
          </Space>

          <Divider style={{ margin: '4px 0' }}>每张数据表装哪个项目</Divider>

          {sheetOptions.length === 0 ? (
            <Typography.Text type="secondary">先选母版，系统会列出里面的数据表。</Typography.Text>
          ) : (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              {sheetOptions.map((s) => (
                <Space key={s.sheet_name} size={10}>
                  <Tag style={{ minWidth: 120, textAlign: 'center' }}>{s.sheet_name}</Tag>
                  <Typography.Text type="secondary">←</Typography.Text>
                  <Select
                    style={{ width: 320 }}
                    value={draft.mapping[s.sheet_name]}
                    placeholder="选这个项目的数"
                    onChange={(v: number) =>
                      setDraft({ ...draft, mapping: { ...draft.mapping, [s.sheet_name]: v } })
                    }
                    options={projectOptions}
                    showSearch
                    optionFilterProp="label"
                  />
                </Space>
              ))}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                一张表装一个项目；同一个项目不能出现在两张表里（点号会撞）。
              </Typography.Text>
            </Space>
          )}

          <Space>
            <Button type="primary" loading={saving} onClick={() => void save()}>
              保存
            </Button>
            <Button onClick={() => setDraft(null)}>取消</Button>
          </Space>
        </Space>
      )}
    </Modal>
  )
}
