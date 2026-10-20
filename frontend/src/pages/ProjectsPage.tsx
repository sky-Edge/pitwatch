import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Divider,
  Dropdown,
  Form,
  Input,
  Modal,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  AppstoreOutlined,
  DownOutlined,
  ExperimentOutlined,
  FileExcelOutlined,
  ImportOutlined,
  ReloadOutlined,
  SettingOutlined,
  TagsOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { MenuProps } from 'antd'
import {
  auditApi,
  bulkApi,
  calcApi,
  downloadBlob,
  projectApi,
  reportApi,
  settingsApi,
  warningRuleApi,
  type ExportTemplateInfo,
  type Project,
  type ProjectAttention,
  type ProjectBrief,
  type ProjectWarningSummary,
  type WarningRuleSetInfo,
  type WarningStatusRow,
} from '../api/client'
import WarningRulesModal from '../components/WarningRulesModal'
import TemplateLibraryModal from '../components/TemplateLibraryModal'
import ProjectDefaultsModal from '../components/ProjectDefaultsModal'
import { levelColor, WARNING_LEVEL } from '../theme/tokens'
import type { RecordsFocus } from '../navigation'

interface FormValues {
  code: string
  name: string
  location?: string
  settlement_sign?: 'down' | 'up'
  displacement_sign?: 'toward_pit' | 'away'
}

/** 项目进度（用于"下一步做什么"引导） */
interface Progress {
  points: number
  records: number
  has_data: boolean
}

export default function ProjectsPage({
  onManagePoints,
  onRecords,
  onComparison,
  onImport,
  onChart,
  onWarningEvents,
  onReports,
  onBatchProjects,
  onAudit,
}: {
  onManagePoints: (p: Project) => void
  /** focus：带着"哪一天/哪个测点"跳过去，让用户直接落在出问题的记录上 */
  onRecords: (p: Project, focus?: RecordsFocus) => void
  onComparison: (p: Project) => void
  onImport: (p: Project) => void
  onChart: (p: Project) => void
  onWarningEvents: (p: Project) => void
  onReports: (p: Project) => void
  /** 一次测了好几个基坑时用：一张「点号+X/Y/Z」清单拆成多个项目 */
  onBatchProjects: () => void
  /** 导出文件检查（周检提醒里的「去检查」跳过去） */
  onAudit: () => void
}) {
  const [projects, setProjects] = useState<Project[]>([])
  /** 含已归档的全部项目：仅用于把关注项/预警汇总解析成可跳转的项目。
   *  为什么需要它 —— 预警汇总（/warning-summary）**包含已归档项目**，
   *  而列表默认只显示在用项目；早先只用列表去 find，归档项目找不到就被静默丢弃，
   *  结果是"数据里有报警、界面上一条都看不到"。 */
  const [allProjects, setAllProjects] = useState<Project[]>([])
  const [summaries, setSummaries] = useState<ProjectWarningSummary[]>([])
  const [progress, setProgress] = useState<Record<number, Progress>>({})
  /** 每个项目的一句话结论（"现在怎么样"） */
  const [briefs, setBriefs] = useState<Record<number, ProjectBrief>>({})
  /** 每个项目的关注项（"要不要管"） */
  const [attention, setAttention] = useState<Record<number, ProjectAttention>>({})
  const [loading, setLoading] = useState(false)
  /** 列表范围：false = 仅进行中项目；true = 含已归档 */
  const [includeInactive, setIncludeInactive] = useState(false)
  /** 全站开关：自动生成数据时要不要带预警（存 app_settings，影响以后所有生成） */
  const [allowWarning, setAllowWarning] = useState(true)
  const [savingAllowWarning, setSavingAllowWarning] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)
  const [ruleModalProject, setRuleModalProject] = useState<Project | null>(null)
  /** 纯规则库视角（不针对某个项目）—— 从工具栏「预警规则」进来 */
  const [ruleLibraryOpen, setRuleLibraryOpen] = useState(false)
  const [detailRows, setDetailRows] = useState<WarningStatusRow[] | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null)
  const [deleteInput, setDeleteInput] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [creatingSample, setCreatingSample] = useState(false)
  /** 主界面「母版库」「项目默认值」入口（用户 2026-10-11：别每个项目各配一遍） */
  const [tplLibraryOpen, setTplLibraryOpen] = useState(false)
  const [defaultsOpen, setDefaultsOpen] = useState(false)
  /** 表格勾选的项目 —— 批量操作的入口 */
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [bulkBusy, setBulkBusy] = useState(false)
  /** 批量套用母版 / 规则的弹窗 */
  const [bulkTplOpen, setBulkTplOpen] = useState(false)
  const [bulkTplId, setBulkTplId] = useState<number | undefined>()
  const [bulkTplList, setBulkTplList] = useState<ExportTemplateInfo[]>([])
  const [bulkRuleOpen, setBulkRuleOpen] = useState(false)
  const [bulkRuleSetId, setBulkRuleSetId] = useState<number | null>(null)
  const [bulkRuleSets, setBulkRuleSets] = useState<WarningRuleSetInfo[]>([])
  /** 批量导出可选的天气（沿用"每个项目最后一次用的"太绕，直接给个下拉） */
  const [bulkWeather, setBulkWeather] = useState('晴')
  /** 批量导出用哪份母版：0 = 各项目自己那份（默认，与平时导出长得一样） */
  const [bulkExportTplId, setBulkExportTplId] = useState(0)
  /** 导出日报是不是超过 7 天没检查过了（用户 2026-10-12 选了「每周自动」） */
  const [auditDue, setAuditDue] = useState<{ last_run: string | null } | null>(null)
  const [form] = Form.useForm<FormValues>()

  // 读一次全站设置（这个开关是全局的，不属于某个项目）
  useEffect(() => {
    settingsApi
      .get()
      .then((s) => setAllowWarning(s.generate_allow_warning ?? true))
      .catch(() => {})
  }, [])

  const toggleAllowWarning = async (v: boolean) => {
    const prev = allowWarning
    setAllowWarning(v) // 先动，失败再回滚 —— 开关卡半秒不动会让人以为没点上
    setSavingAllowWarning(true)
    try {
      await settingsApi.update({ generate_allow_warning: v })
      message.success(v ? '以后生成的数据会带预警，和真实台账一样' : '以后生成的数据只会在预警线以内波动')
    } catch (e) {
      setAllowWarning(prev)
      message.error((e as Error).message)
    } finally {
      setSavingAllowWarning(false)
    }
  }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await projectApi.list(includeInactive)
      setProjects(data)
      // 另取一份含归档的项目表，供关注项解析（见 allProjects 的说明）
      void projectApi.list(true).then(setAllProjects).catch(() => {})
      // 并发取各项目进度（测点数/记录数）——失败不影响列表展示
      const entries = await Promise.all(
        data.map(async (p) => {
          try {
            return [p.id, await projectApi.dataCount(p.id)] as const
          } catch {
            return [p.id, { points: 0, records: 0, has_data: false }] as const
          }
        }),
      )
      setProgress(Object.fromEntries(entries))
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [includeInactive])

  const loadSummary = useCallback(async () => {
    try {
      setSummaries(await calcApi.warningSummary())
    } catch {
      /* 预警汇总为附加信息，失败不阻塞 */
    }
  }, [])

  /** 结论与关注项都是附加信息：任一失败都不应影响主列表 */
  const loadBriefsAndAttention = useCallback(async () => {
    const [b, a] = await Promise.allSettled([projectApi.briefs(), projectApi.attention()])
    if (b.status === 'fulfilled') {
      setBriefs(Object.fromEntries(b.value.map((x) => [x.project_id, x])))
    }
    if (a.status === 'fulfilled') {
      setAttention(Object.fromEntries(a.value.map((x) => [x.project_id, x])))
    }
  }, [])

  useEffect(() => {
    void load()
    void loadSummary()
    void loadBriefsAndAttention()
  }, [load, loadSummary, loadBriefsAndAttention])

  // 批量导出的天气默认值跟着「项目默认值」走（取不到就保持页面里的初始值）；
  // 顺带把母版库拉一份，供批量导出时挑母版（一个项目一份 / 统一用某一份）
  useEffect(() => {
    void settingsApi
      .get()
      .then((c) => {
        if (c.default_weather) setBulkWeather(c.default_weather)
      })
      .catch(() => {})
    void reportApi
      .listLibrary()
      .then((lib) => setBulkTplList(lib.filter((t) => !t.is_builtin)))
      .catch(() => {})
    // 「每周自动检查」没做成常驻定时器（这系统是手动双击启动的，关了就不会跑），
    // 改成「打开系统时补扫」：这里只读一下状态，超过 7 天就在页面顶部提一句。
    void auditApi
      .status()
      .then((s) => setAuditDue(s.due ? { last_run: s.last_run } : null))
      .catch(() => {})
  }, [])

  /** 一键创建示例项目：让新用户几秒内看到完整效果 */
  const createSample = async () => {
    setCreatingSample(true)
    try {
      const r = await projectApi.createSample({ periods: 3, point_count: 8 })
      message.success(
        `已创建示例项目：${r.points_created} 个测点 · ${r.records_generated} 条演示数据（${r.periods} 期）`,
        5,
      )
      await load()
      await loadBriefsAndAttention()
      void loadSummary()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setCreatingSample(false)
    }
  }

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    setModalOpen(true)
  }

  const openEdit = (p: Project) => {
    setEditing(p)
    const sc = (p.sign_config ?? {}) as Record<string, { positive_means?: string }>
    form.setFieldsValue({
      code: p.code,
      name: p.name,
      location: p.location ?? '',
      settlement_sign: (sc.settlement?.positive_means as 'down' | 'up') ?? 'down',
      displacement_sign: (sc.displacement?.positive_means as 'toward_pit' | 'away') ?? 'toward_pit',
    })
    setModalOpen(true)
  }

  const submit = async () => {
    const values = await form.validateFields()
    // 可视化选择 → sign_config（正值代表什么方向），无需用户手写 JSON
    const sign_config = {
      settlement: { positive_means: values.settlement_sign ?? 'down' },
      displacement: { positive_means: values.displacement_sign ?? 'toward_pit' },
    }
    const payload = {
      code: values.code,
      name: values.name,
      location: values.location || null,
      sign_config,
    }
    try {
      if (editing) {
        await projectApi.update(editing.id, payload)
        message.success('项目已更新')
      } else {
        await projectApi.create(payload)
        message.success('项目已创建')
      }
      setModalOpen(false)
      void load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const archive = async (p: Project) => {
    try {
      await projectApi.update(p.id, { is_active: false })
      message.success(`项目 ${p.code} 已归档`)
      void load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const reactivate = async (p: Project) => {
    try {
      await projectApi.update(p.id, { is_active: true })
      message.success(`项目 ${p.code} 已恢复在用`)
      void load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  /** 物理删除：需输入项目编号确认（防止误删），删除前服务端自动备份 */
  const confirmHardDelete = async () => {
    if (!deleteTarget) return
    if (deleteInput.trim() !== deleteTarget.code) {
      message.error(`请输入项目编号 ${deleteTarget.code} 以确认删除`)
      return
    }
    setDeleting(true)
    try {
      const r = await projectApi.hardDelete(deleteTarget.id, deleteTarget.code)
      if (r.deleted) {
        message.success(`项目 ${deleteTarget.code} 及其数据已删除（删除前已自动备份）`)
        // 模板独立成"模板库"之后，被别的项目共用的母版不会跟着删 —— 必须说出来，
        // 否则用户以为母版也一起没了。
        if (r.templates_kept?.length) {
          message.info(
            `母版「${r.templates_kept.join('、')}」还被其他项目使用，只解除了本项目关联，母版保留`,
            6,
          )
        }
        setDeleteTarget(null)
        setDeleteInput('')
        void load()
        void loadSummary()
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  /** 根据项目进度推荐"下一步"——傻瓜式引导：不用记流程，跟着按钮走 */
  const nextAction = (p: Project): { text: string; go: () => void } => {
    const c = progress[p.id]
    if (!c || c.points === 0) return { text: '先创建测点', go: () => onManagePoints(p) }
    if (c.records === 0) return { text: '录入初始值', go: () => onRecords(p) }
    return { text: '录入本期数据', go: () => onRecords(p) }
  }

  /** 全部项目的关注项汇总成一条待办清单（报警 → 数据可疑 → 该观测了） */
  const attentionItems = (() => {
    const items: Array<{
      key: string
      type: 'alarm' | 'suspect' | 'stale'
      project: Project
      text: string
      go: () => void
    }> = []

    // ① 报警（最紧急）
    summaries
      .filter((s) => s.has_alarm)
      .forEach((s) => {
        // ⚠ 必须用 allProjects 解析：预警汇总包含已归档项目，
        //   而列表默认不显示归档项目 —— 只用 projects 找会让归档项目的报警被静默丢弃。
        const p = allProjects.find((x) => x.id === s.project_id) ?? projects.find((x) => x.id === s.project_id)
        if (!p) return
        const first = s.alarm_points[0]
        // 只有当确实知道"哪一天"时才带定位；否则退回预警事件页（那里能重算）
        const focus: RecordsFocus | undefined =
          first && first.obs_date
            ? {
                date: first.obs_date,
                pointNo: first.point_no,
                note: `${first.point_no} 累计变化 ${first.cumulative_mm}mm，已达报警值`,
              }
            : undefined
        items.push({
          key: `alarm-${s.project_id}`,
          type: 'alarm',
          project: p,
          text: first
            ? `${s.alarm_points.length} 项达到报警值，如 ${first.point_no} 累计 ${first.cumulative_mm}mm`
            : `${s.alarm_points.length} 项达到报警值`,
          // 带 focus → 直接落到报警日的那个测点上（原先只跳到预警事件页，那里可能是空的）
          go: () => (focus ? onRecords(p, focus) : onWarningEvents(p)),
        })
      })

    // ② 数据可疑（突变）
    projects.forEach((p) => {
      const a = attention[p.id]
      if (!a || a.suspect_count === 0) return
      const first = a.suspects[0]
      const focus: RecordsFocus | undefined = first
        ? {
            date: first.obs_date,
            pointNo: first.point_no,
            note: `${first.point_no} 该期变化 ${first.delta_mm.toFixed(1)}mm，变化异常请复核`,
          }
        : undefined
      items.push({
        key: `suspect-${p.id}`,
        type: 'suspect',
        project: p,
        text: first
          ? `${a.suspect_count} 条数据变化异常，如 ${first.point_no} 单期 ${first.delta_mm.toFixed(1)}mm 请复核`
          : `${a.suspect_count} 条数据变化异常`,
        go: () => onRecords(p, focus),
      })
    })

    // ③ 该观测了
    projects.forEach((p) => {
      const a = attention[p.id]
      if (!a || !a.needs_observation) return
      const focus: RecordsFocus | undefined = a.last_obs_date
        ? { date: a.last_obs_date, note: `上次观测是 ${a.last_obs_date}，已 ${a.stale_days} 天没测` }
        : undefined
      items.push({
        key: `stale-${p.id}`,
        type: 'stale',
        project: p,
        text: `已有 ${a.stale_days} 天没观测（上次 ${a.last_obs_date}）`,
        go: () => onRecords(p, focus),
      })
    })

    return items
  })()

  /** 「更多」菜单：低频与危险操作收纳于此，主界面只留最常用动作 */
  const moreMenu = (p: Project): MenuProps['items'] => [
    { key: 'points', label: '测点管理' },
    { key: 'import', label: 'Excel 导入' },
    { type: 'divider' },
    { key: 'chart', label: '曲线图' },
    { key: 'comparison', label: '逐期对比' },
    { key: 'warning-events', label: '预警事件' },
    { key: 'rules', label: '预警规则' },
    { type: 'divider' },
    { key: 'edit', label: '编辑项目' },
    p.is_active ? { key: 'archive', label: '归档项目' } : { key: 'reactivate', label: '恢复在用' },
    { key: 'delete', label: '彻底删除…', danger: true },
  ]

  const onMoreClick = (p: Project, key: string) => {
    switch (key) {
      case 'points':
        onManagePoints(p)
        break
      case 'import':
        onImport(p)
        break
      case 'chart':
        onChart(p)
        break
      case 'comparison':
        onComparison(p)
        break
      case 'warning-events':
        onWarningEvents(p)
        break
      case 'rules':
        setRuleModalProject(p)
        break
      case 'edit':
        openEdit(p)
        break
      case 'archive':
        Modal.confirm({
          title: `确定归档项目 ${p.code}？`,
          content: '归档后不再出现在「进行中」列表，数据完整保留，可随时恢复。',
          okText: '归档',
          cancelText: '取消',
          onOk: () => archive(p),
        })
        break
      case 'reactivate':
        void reactivate(p)
        break
      case 'delete':
        setDeleteTarget(p)
        setDeleteInput('')
        break
    }
  }

  // ───────────────── 批量操作（勾选多个项目后一次做完） ─────────────────
  //
  // 用户 2026-10-11：「每个项目都要调单独设置有点繁琐」。
  // 下面这些都是"本来要一个个点进去"的事：换规则、换母版、归档、导日报。

  const clearSelection = () => setSelectedIds([])

  /** 选中的项目对象（按表格当前顺序，避免"选了几个"和"做了几个"对不上） */
  const selected = projects.filter((p) => selectedIds.includes(p.id))

  /** 批量导出各项目最新一期日报 → ZIP */
  const bulkExport = async () => {
    if (bulkBusy || selected.length === 0) return
    const msgKey = 'bulk-export'
    const t0 = Date.now()
    setBulkBusy(true)
    // 项目越多越慢（每个项目都要按母版生成一份），给出已用时长才不会让人以为点坏了
    message.loading({ content: `正在导出 ${selected.length} 个项目的最新一期日报…`, key: msgKey, duration: 0 })
    const tick = window.setInterval(() => {
      message.loading({
        content: `正在导出 ${selected.length} 个项目的最新一期日报…已用 ${Math.round(
          (Date.now() - t0) / 1000,
        )} 秒（请不要重复点击）`,
        key: msgKey,
        duration: 0,
      })
    }, 1000)
    try {
      const blob = await bulkApi.exportLatest(
        selected.map((p) => p.id),
        { weather: bulkWeather, templateId: bulkExportTplId || undefined },
      )
      downloadBlob(blob, `批量日报_最新一期_共${selected.length}个项目.zip`)
      message.success(`已导出 ${selected.length} 个项目（用了 ${Math.round((Date.now() - t0) / 1000)} 秒）`)
    } catch (e) {
      message.error((e as Error).message, 8)
    } finally {
      window.clearInterval(tick)
      message.destroy(msgKey)
      setBulkBusy(false)
    }
  }

  const bulkArchive = async (active: boolean) => {
    const verb = active ? '恢复' : '归档'
    const list = selected
    const ok = await new Promise<boolean>((resolve) => {
      Modal.confirm({
        title: `把选中的 ${list.length} 个项目${verb}？`,
        content: `${list.map((p) => p.code).join('、')}\n\n${verb}只改"在用/归档"这个状态，观测数据一条都不会动。`,
        okText: `确认${verb}`,
        cancelText: '取消',
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      })
    })
    if (!ok) return
    setBulkBusy(true)
    try {
      const r = await bulkApi.archive(
        list.map((p) => p.id),
        active,
      )
      message.success(r.message)
      clearSelection()
      await load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBulkBusy(false)
    }
  }

  const openBulkTemplate = async () => {
    try {
      const lib = await reportApi.listLibrary()
      setBulkTplList(lib.filter((t) => !t.is_builtin))
      setBulkTplId(undefined)
      setBulkTplOpen(true)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const doBulkTemplate = async () => {
    if (!bulkTplId) {
      message.warning('请选择一份母版')
      return
    }
    setBulkBusy(true)
    try {
      const r = await bulkApi.template(
        selected.map((p) => p.id),
        bulkTplId,
      )
      message.success(r.message, 6)
      setBulkTplOpen(false)
      clearSelection()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBulkBusy(false)
    }
  }

  const openBulkRule = async () => {
    try {
      const sets = await warningRuleApi.listSets()
      setBulkRuleSets(sets)
      setBulkRuleSetId(null)
      setBulkRuleOpen(true)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const doBulkRule = async () => {
    setBulkBusy(true)
    try {
      const r = await bulkApi.ruleSet(
        selected.map((p) => p.id),
        bulkRuleSetId,
      )
      // 说清"从哪套换到了哪套" —— 改绑是会影响判定口径的动作，只报"成功"不够
      const from = Array.from(new Set(Object.values(r.before))).join('、')
      message.success(`${r.message}（原来是：${from}）`, 8)
      setBulkRuleOpen(false)
      clearSelection()
      await load()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBulkBusy(false)
    }
  }

  const columns: ColumnsType<Project> = [
    {
      // 编号宽 110 装不下「6号左边墩」这类长编号，会被折成两行；给到 130 并允许省略。
      title: '项目编号',
      dataIndex: 'code',
      width: 130,
      render: (v: string) => (
        <Tooltip title={v} placement="topLeft">
          <span className="cell-ellipsis">{v}</span>
        </Tooltip>
      ),
    },
    {
      // ⚠ 这一列必须给固定宽度：其余列全是固定宽，`scroll.x` 又刚好等于"固定宽之和"，
      // 于是这唯一的弹性列被压到几像素宽 —— 表现是「六号左边墩基坑」一个字一行地竖排。
      // 名字太长时用省略号 + 悬停看全名，别让它撑破版面。
      title: '项目名称',
      dataIndex: 'name',
      width: 200,
      render: (v: string) => (
        <Tooltip title={v} placement="topLeft">
          <span className="cell-ellipsis">{v}</span>
        </Tooltip>
      ),
    },
    { title: '地点', dataIndex: 'location', width: 110, render: (v: string | null) => v || '-' },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 90,
      render: (v: boolean) => (v ? <Tag color="green">进行中</Tag> : <Tag>已归档</Tag>),
    },
    {
      title: '进度',
      width: 190,
      render: (_, record) => {
        const c = progress[record.id]
        if (!c) return <Tag>—</Tag>
        if (c.points === 0) return <Tag>还没建测点</Tag>
        if (c.records === 0) return <Tag color="orange">已有测点 · 待录数据</Tag>
        return (
          <Tag color="green">
            {c.points} 个测点 · {c.records} 条数据
          </Tag>
        )
      },
    },
    {
      title: '本期概况',
      width: 280,
      render: (_, record) => {
        const b = briefs[record.id]
        if (!b || !b.headline) {
          const c = progress[record.id]
          if (!c || c.points === 0) return <Typography.Text type="secondary">还没建测点</Typography.Text>
          if (c.records === 0) return <Typography.Text type="secondary">还没有观测数据</Typography.Text>
          return <Typography.Text type="secondary">本期暂无有效数据</Typography.Text>
        }
        const color = levelColor(b.severity)
        return (
          <Tooltip title={b.obs_date ? `最近观测：${b.obs_date}` : undefined}>
            <span style={{ color }}>{b.headline}</span>
          </Tooltip>
        )
      },
    },
    {
      title: '预警状态',
      width: 120,
      render: (_, record) => {
        const s = summaries.find((x) => x.project_id === record.id)
        if (!s || s.total_points === 0) return <Tag>无数据</Tag>
        if (s.has_alarm)
          return (
            <Tag color="red" style={{ cursor: 'pointer' }} onClick={() => setDetailRows([...s.alarm_points, ...s.control_points])}>
              报警 {s.alarm_points.length}
            </Tag>
          )
        if (s.has_control)
          return (
            <Tag color="gold" style={{ cursor: 'pointer' }} onClick={() => setDetailRows(s.control_points)}>
              预警 {s.control_points.length}
            </Tag>
          )
        return <Tag color="green">正常</Tag>
      },
    },
    {
      title: '下一步',
      width: 170,
      render: (_, record) => {
        const na = nextAction(record)
        return (
          <Button type="primary" onClick={na.go}>
            {na.text}
          </Button>
        )
      },
    },
    {
      title: '操作',
      width: 260,
      render: (_, record) => {
        // 导出日报是用户每天最想要的"成品"，提到与「下一步」同级的主按钮，
        // 不再藏在操作列里。有数据才可点，避免空项目点了没结果。
        const c = progress[record.id]
        const hasData = Boolean(c?.records)
        return (
          <Space>
            <Tooltip title={hasData ? '用上次的模板直接导出最新一期' : '还没有观测数据，先录入或导入'}>
              <Button type="primary" ghost size="small" disabled={!hasData} onClick={() => onReports(record)}>
                导出日报
              </Button>
            </Tooltip>
            <Dropdown
              menu={{ items: moreMenu(record), onClick: ({ key }) => onMoreClick(record, key) }}
              trigger={['click']}
            >
              <Button size="small">
                更多 <DownOutlined />
              </Button>
            </Dropdown>
          </Space>
        )
      },
    },
  ]

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      {/* 周检提醒：导出日报超过 7 天没查过就提一句（用户 2026-10-12 选的「每周自动」） */}
      {auditDue && (
        <Alert
          type="warning"
          showIcon
          message="该检查一下导出的日报了"
          description={`${
            auditDue.last_run ? `上次检查是 ${auditDue.last_run.slice(0, 10)}，` : ''
          }已经超过 7 天。系统可以扫一遍导出文件夹，逐份比对变化速率是否和系统同步、有没有超过预警规则。`}
          action={
            <Button size="small" type="primary" onClick={onAudit}>
              去检查
            </Button>
          }
        />
      )}

      {/* 一、需要关注：把"该干什么"直接摆出来，不用逐个项目点进去看 */}
      {attentionItems.length > 0 && (
        <Card
          size="small"
          title={
            <Space>
              <WarningOutlined style={{ color: WARNING_LEVEL.control.fg }} />
              <span>需要关注（{attentionItems.length}）</span>
            </Space>
          }
          extra={
            <Button size="small" type="text" icon={<ReloadOutlined />} onClick={() => void loadBriefsAndAttention()}>
              刷新
            </Button>
          }
        >
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            {attentionItems.map((it) => (
              <div key={it.key} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <Tag color={it.type === 'alarm' ? 'red' : it.type === 'suspect' ? 'orange' : 'blue'}>
                  {it.type === 'alarm' ? '报警' : it.type === 'suspect' ? '数据可疑' : '该观测了'}
                </Tag>
                <Typography.Text strong style={{ minWidth: 120 }}>
                  {it.project.code}
                </Typography.Text>
                <Typography.Text style={{ flex: 1 }}>{it.text}</Typography.Text>
                <Button size="small" onClick={it.go}>
                  去处理
                </Button>
              </div>
            ))}
          </Space>
        </Card>
      )}

      {summaries.filter((s) => s.has_alarm).length > 0 && (
        <Alert
          type="error"
          showIcon
          banner
          message="存在报警项目"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {summaries
                .filter((s) => s.has_alarm)
                .map((s) => (
                  <li key={s.project_id}>
                    <a onClick={() => setDetailRows([...s.alarm_points, ...s.control_points])}>
                      {s.project_code} {s.project_name}：{s.alarm_points.map((p) => `${p.point_no}(${p.series_type === 'settlement' ? '沉降' : '位移'}${p.component === 'X' ? 'X' : p.component === 'Y' ? 'Y' : ''}) 累计${p.cumulative_mm}mm`).join('、')}
                    </a>
                  </li>
                ))}
            </ul>
          }
        />
      )}
      <Alert
        type="info"
        showIcon
        message="怎么用？跟着每行的「下一步」按钮走就行"
        description="建项目 → 建测点并录初始值 → 录入或导入观测数据 → 导出日报。归档不删数据；「彻底删除」需输入编号确认，且删除前会自动备份。"
      />
      {/* 主界面把所有"和单个项目无关"的入口都收在这里 —— 不必点进某个项目再找 */}
      <Card size="small">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap size={8}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              建项目：
            </Typography.Text>
            <Button type="primary" onClick={openCreate}>
              新建项目
            </Button>
            <Tooltip title="一次测了好几个基坑？传一张「点号 + X/Y/Z」清单，你分好组后一次把项目和测点全建好">
              <Button icon={<ImportOutlined />} onClick={onBatchProjects}>
                批量建项目
              </Button>
            </Tooltip>
            <Tooltip title="自动建好测点、预警规则和 3 期演示数据，直接就能看曲线和日报">
              <Button icon={<ExperimentOutlined />} loading={creatingSample} onClick={() => void createSample()}>
                先看个示例
              </Button>
            </Tooltip>
            <Divider type="vertical" />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              集中管理：
            </Typography.Text>
            <Tooltip title="一套规则可以给好几个基坑用：改一次它们一起跟着变，不用每个项目单独配一遍">
              <Button icon={<SettingOutlined />} onClick={() => setRuleLibraryOpen(true)}>
                预警规则
              </Button>
            </Tooltip>
            <Tooltip title="上传、改名、指定「这份母版作用于哪些项目」—— 母版不属于某个项目，所以在这里管">
              <Button icon={<AppstoreOutlined />} onClick={() => setTplLibraryOpen(true)}>
                母版库
              </Button>
            </Tooltip>
            <Tooltip title="新建项目自动带的规则套 / 母版 / 天气，建完就能用，不用再配一遍">
              <Button icon={<TagsOutlined />} onClick={() => setDefaultsOpen(true)}>
                项目默认值
              </Button>
            </Tooltip>
            <Divider type="vertical" />
            <Divider type="vertical" />
            <Tooltip title="开着：生成的数据会像真实台账一样偶尔触发预警。关掉：生成的数据只在预警线以内波动，不会出现预警/报警点。">
              <Space size={6}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  生成数据带预警
                </Typography.Text>
                <Switch
                  size="small"
                  checked={allowWarning}
                  loading={savingAllowWarning}
                  onChange={(v) => void toggleAllowWarning(v)}
                />
              </Space>
            </Tooltip>
            <Segmented
              value={includeInactive ? 'all' : 'active'}
              onChange={(v) => {
                setIncludeInactive(v === 'all')
                clearSelection()
              }}
              options={[
                { label: '进行中项目', value: 'active' },
                { label: '全部（含已归档）', value: 'all' },
              ]}
            />
            {includeInactive && <Tag>含 {projects.filter((p) => !p.is_active).length} 个已归档项目</Tag>}
          </Space>

          {/* 勾选后才出现：一次对多个项目做同一件事（本来要一个个点进去） */}
          {selected.length > 0 && (
            <Alert
              type="info"
              showIcon
              message={
                <Space wrap size={8}>
                  <Typography.Text strong>已勾选 {selected.length} 个项目</Typography.Text>
                  <Button size="small" type="primary" ghost icon={<FileExcelOutlined />} disabled={bulkBusy} onClick={() => void bulkExport()}>
                    导出最新一期日报
                  </Button>
                  <Button size="small" disabled={bulkBusy} onClick={() => void openBulkTemplate()}>
                    套用母版…
                  </Button>
                  <Button size="small" disabled={bulkBusy} onClick={() => void openBulkRule()}>
                    套用规则…
                  </Button>
                  <Button size="small" disabled={bulkBusy} onClick={() => void bulkArchive(false)}>
                    归档
                  </Button>
                  <Button size="small" disabled={bulkBusy} onClick={() => void bulkArchive(true)}>
                    恢复在用
                  </Button>
                  <Button size="small" type="text" disabled={bulkBusy} onClick={clearSelection}>
                    取消勾选
                  </Button>
                  <Tooltip title="批量导出各项目最新一期日报。母版默认「各项目自己那份」，出来的和平时单项目导的一样">
                    <Select
                      size="small"
                      style={{ width: 170 }}
                      value={bulkExportTplId}
                      onChange={setBulkExportTplId}
                      options={[
                        { value: 0, label: '各项目自己的母版' },
                        ...bulkTplList.map((t) => ({ value: t.id, label: t.name })),
                      ]}
                    />
                  </Tooltip>
                  <Select
                    size="small"
                    style={{ width: 90 }}
                    value={bulkWeather}
                    onChange={setBulkWeather}
                    options={['晴', '多云', '阴', '小雨', '中雨', '大雨'].map((w) => ({ value: w, label: `${w}天` }))}
                  />
                </Space>
              }
              description="批量操作只作用于勾选的这几个项目。套用母版是「加上去」（项目原有的母版不会被踢掉）；套用规则是「改绑」（一个项目只能有一套规则）。"
            />
          )}
        </Space>
      </Card>
      {!loading && projects.length === 0 && (
        <Alert
          type="success"
          showIcon
          message="还没有项目？点「先看个示例」最快"
          description="「先看个示例」会自动建好 8 个测点、预警规则和 3 期演示数据，你可以直接看曲线和导出日报，熟悉之后再「新建项目」录真实数据。"
        />
      )}
      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={projects}
        // 勾选是批量操作的入口。跨页保留（selectedIds 记在状态里），
        // 否则"翻到第 2 页勾一个"会把第 1 页勾的全丢掉。
        rowSelection={{
          selectedRowKeys: selectedIds,
          onChange: (keys) => setSelectedIds(keys as number[]),
          preserveSelectedRowKeys: true,
        }}
        pagination={{ pageSize: 10 }}
        // 这张表**每一列都给固定宽**（含勾选列），所以 scroll.x 必须 ≥ 各列宽之和，
        // 否则没有弹性列可压，antd 会把最宽的那列挤成一条竖线。
        // 现在各列合计 1550 + 勾选列 ≈ 1598，取 1600。加列/改宽时记得同步这个数。
        // （1600 是照着"1680 宽窗口下不出现横向滚动"定的；屏幕更窄时才会滚，属正常）
        scroll={{ x: 1600 }}
      />

      <Modal
        title={editing ? `编辑项目 ${editing.code}` : '新建项目'}
        open={modalOpen}
        onOk={() => void submit()}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="code" label="项目编号" rules={[{ required: true, message: '请输入项目编号' }]}>
            <Input placeholder="如 PRJ001" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入项目名称' }]}>
            <Input placeholder="如 示例公路基坑监测" />
          </Form.Item>
          <Form.Item name="location" label="地点">
            <Input placeholder="工程位置 / 标段" />
          </Form.Item>
          <Form.Item
            name="settlement_sign"
            label="沉降读数：正值代表"
            initialValue="down"
            tooltip="决定报表里变化方向显示为「下沉」还是「隆起」。不确定就保持默认。"
          >
            <Select
              options={[
                { label: '下沉（多数基坑习惯）', value: 'down' },
                { label: '隆起 / 上升', value: 'up' },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="displacement_sign"
            label="水平位移读数：正值代表"
            initialValue="toward_pit"
            tooltip="决定报表里位移方向显示为「向基坑内」还是「向基坑外」。不确定就保持默认。"
          >
            <Select
              options={[
                { label: '向基坑内（多数基坑习惯）', value: 'toward_pit' },
                { label: '向基坑外', value: 'away' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      {ruleModalProject && (
        <WarningRulesModal
          projectId={ruleModalProject.id}
          open={!!ruleModalProject}
          onClose={() => setRuleModalProject(null)}
        />
      )}

      {ruleLibraryOpen && (
        <WarningRulesModal open onClose={() => setRuleLibraryOpen(false)} />
      )}

      {/* 物理删除确认：需输入项目编号 */}
      <Modal
        title={<span style={{ color: WARNING_LEVEL.alarm.fg }}>⚠ 彻底删除项目 {deleteTarget?.code}</span>}
        open={!!deleteTarget}
        onOk={() => void confirmHardDelete()}
        onCancel={() => setDeleteTarget(null)}
        okText="确认彻底删除"
        okButtonProps={{ danger: true, loading: deleting, disabled: deleteInput.trim() !== deleteTarget?.code }}
        cancelText="取消"
        destroyOnClose
      >
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="此操作不可撤销"
          description="将同时删除该项目下的全部测点、观测数据、预警事件与规则。删除前系统会自动备份数据库（database/backups/），误删可还原。"
        />
        <Typography.Paragraph>
          请在下方输入项目编号 <Typography.Text code strong>{deleteTarget?.code}</Typography.Text> 以确认：
        </Typography.Paragraph>
        <Input
          placeholder={`输入 ${deleteTarget?.code} 确认删除`}
          value={deleteInput}
          onChange={(e) => setDeleteInput(e.target.value)}
          onPressEnter={() => void confirmHardDelete()}
        />
      </Modal>

      <Modal
        title="预警/报警详情（最新一期）"
        open={!!detailRows}
        onCancel={() => setDetailRows(null)}
        footer={<Button onClick={() => setDetailRows(null)}>关闭</Button>}
        width={640}
      >
        <Table
          rowKey={(r) => `${r.point_id}_${r.series_type}_${r.component}`}
          size="small"
          dataSource={detailRows ?? []}
          pagination={false}
          columns={[
            { title: '测点', dataIndex: 'point_no', width: 90 },
            {
              title: '观测项',
              width: 110,
              render: (_, r) => (r.series_type === 'settlement' ? '沉降' : `位移${r.component}`),
            },
            { title: '观测日期', dataIndex: 'obs_date', width: 110 },
            { title: '累计变化(mm)', dataIndex: 'cumulative_mm', width: 120 },
            {
              title: '速率(mm/d)',
              dataIndex: 'rate_mm_per_day',
              width: 110,
              render: (v: number | null) => (v === null ? '-' : v.toFixed(2)),
            },
            {
              title: '级别',
              width: 90,
              render: (_, r) =>
                r.warning_level === 'alarm' ? <Tag color="red">报警</Tag> : <Tag color="gold">预警</Tag>,
            },
            {
              title: '触发原因',
              render: (_, r) => (r.triggers.length ? r.triggers.join('；') : '-'),
            },
          ]}
        />
      </Modal>

      {/* 主界面「母版库」：上传 / 改名 / 设置适用项目，不用再进某个项目 */}
      <TemplateLibraryModal open={tplLibraryOpen} onClose={() => setTplLibraryOpen(false)} />

      {/* 主界面「项目默认值」：新建项目自动带上规则套 / 母版 / 天气 */}
      <ProjectDefaultsModal open={defaultsOpen} onClose={() => setDefaultsOpen(false)} />

      {/* 批量套用母版：把勾选的项目加进这份母版的适用范围 */}
      <Modal
        title={`套用母版到 ${selected.length} 个项目`}
        open={bulkTplOpen}
        onOk={() => void doBulkTemplate()}
        onCancel={() => setBulkTplOpen(false)}
        confirmLoading={bulkBusy}
        okText="套用"
        cancelText="取消"
        width={560}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Alert
            type="info"
            showIcon
            message="这是「加上去」，不会替换项目原有的母版"
            description="一个项目可以有好几份可用母版（导出时在下拉里选），所以这里只是把选中的项目加进这份母版的适用范围。"
          />
          <Select
            style={{ width: '100%' }}
            placeholder="选择一份母版"
            value={bulkTplId}
            onChange={setBulkTplId}
            options={bulkTplList.map((t) => ({
              value: t.id,
              label: `${t.name}${t.is_global ? '（通用）' : ` · 现作用于 ${t.project_ids?.length ?? 0} 个项目`}${
                t.file_ok === false ? ' —— 原件缺失' : ''
              }`,
            }))}
            notFoundContent="还没有自定义母版，请先去「母版库」上传一份"
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            将套用到：{selected.map((p) => p.code).join('、') || '（未勾选）'}
          </Typography.Text>
        </Space>
      </Modal>

      {/* 批量套用规则：一个项目只能有一套，所以这是"改绑" */}
      <Modal
        title={`套用预警规则到 ${selected.length} 个项目`}
        open={bulkRuleOpen}
        onOk={() => void doBulkRule()}
        onCancel={() => setBulkRuleOpen(false)}
        confirmLoading={bulkBusy}
        okText="改绑"
        cancelText="取消"
        width={560}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Alert
            type="warning"
            showIcon
            message="一个项目只能有一套规则，所以这是「改绑」"
            description="保存后这几个项目的预警判定口径会按所选规则执行。若目标是共用规则，以后改它会连带影响所有用它的项目。"
          />
          <Select
            style={{ width: '100%' }}
            value={bulkRuleSetId ?? 0}
            onChange={(v) => setBulkRuleSetId(v === 0 ? null : v)}
            options={[
              { value: 0, label: '解绑 —— 改回套用「通用规则」（没有通用规则则不判预警）' },
              ...bulkRuleSets.map((s) => ({
                value: s.id,
                label: `${s.name}${s.is_global ? '（通用）' : ''} · 现作用于 ${s.project_ids.length} 个项目`,
              })),
            ]}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            将改绑：{selected.map((p) => p.code).join('、') || '（未勾选）'}
          </Typography.Text>
        </Space>
      </Modal>
    </Space>
  )
}
