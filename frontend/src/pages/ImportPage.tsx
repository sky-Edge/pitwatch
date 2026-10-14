import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  DatePicker,
  Modal,
  Select,
  Space,
  Steps,
  Switch,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { plainCalendarPanel } from '../components/CalendarCell'
import {
  importApi,
  type ControlValueSuggest,
  type ImportPreviewResponse,
  type ImportPreviewRow,
  type ImportSheetInfo,
  type PointIssue,
  type PointResolveAction,
  type PointType,
  type Project,
  type SheetDateOverride,
} from '../api/client'
import { WARNING_LEVEL } from '../theme/tokens'

const { Dragger } = Upload

interface Props {
  project: Project
  onBack: () => void
}

/** 录入单位显示：预览统一显示 m 便于核对 */
const fmtM = (mm: number | null): string => (mm == null ? '缺测' : `${(mm / 1000).toFixed(4)} m`)

const TYPE_LABEL: Record<PointType, string> = { settlement: '沉降', displacement: '水平位移', both: '沉降+位移' }

/** 列号 → Excel 列字母（1→A） */
const colLetter = (n: number): string => {
  let s = ''
  let x = n
  while (x > 0) {
    const m = (x - 1) % 26
    s = String.fromCharCode(65 + m) + s
    x = Math.floor((x - 1) / 26)
  }
  return s
}

/** 字段中文名（展示"系统把哪列认成了什么"，换模板时便于核对） */
const FIELD_LABEL: Record<string, string> = {
  point_no: '点号',
  initial_m: '初始值',
  prev_m: '上次测值',
  current_m: '本次测值',
  prev_cum_mm: '上次累计',
  cum_mm: '本次累计',
  delta_mm: '本次变化',
  rate_mm_d: '速率',
  control_cum_mm: '控制值',
  control_rate_mm_d: '速率控制值',
  warning_level: '预警等级',
  remark: '备注',
  // 「名字 + X/Y/Z」简易初始值清单
  x: 'X',
  y: 'Y',
  z: 'Z(高程)',
}

const colMapText = (m?: Record<string, number>): string => {
  if (!m) return ''
  return Object.keys(FIELD_LABEL)
    .filter((k) => m[k])
    .map((k) => `${FIELD_LABEL[k]}=${colLetter(m[k])}列`)
    .join(' · ')
}

/** 数据表类型标签（简易 X/Y/Z 表同时含沉降与位移） */
const sheetKindLabel = (t: string): string =>
  t === 'settlement' ? '沉降' : t === 'displacement' ? '位移' : '沉降+位移'

type ResolutionMode = 'auto_create' | 'map' | 'skip'

interface Resolution {
  mode: ResolutionMode
  mapTo?: string
}

/** 默认决议：有 exact/strong 候选 → 映射到第一个候选；否则 → 自动建点 */
const defaultResolution = (issue: PointIssue): Resolution => {
  const best = issue.suggested.find((s) => s.confidence === 'exact' || s.confidence === 'strong')
  if (best) return { mode: 'map', mapTo: best.point_no }
  return { mode: 'auto_create' }
}

/** 单个文件的解析/导入状态（多文件 = 多个 FilePreview） */
interface FilePreview {
  key: string
  filename: string
  status: 'uploading' | 'ready' | 'error' | 'imported'
  error?: string
  preview?: ImportPreviewResponse
  /** 有效行排除集合（key: valid-<idx>） */
  excludeIds: Set<string>
  /** 未知点号处理决议：excel_no → 动作 */
  resolutions: Record<string, Resolution>
  /** 用户为该文件各数据表指定的观测日期（sheet_name → YYYY-MM-DD）。
   *  表里日期识别错了、或表里根本没写日期，都在这里改/补。 */
  sheetDates: Record<string, string>
  /** 正在按新日期重新核对（换日期要重算有效/冲突分组） */
  rechecking?: boolean
  importedRows?: number
}

/** 某张数据表当前生效的观测日期：用户指定的优先，其次用表里识别到的 */
const sheetDate = (fp: FilePreview, sheetName?: string | null): string | null => {
  if (!sheetName) return null
  const picked = fp.sheetDates[sheetName]
  if (picked) return picked
  return fp.preview?.sheets.find((s) => s.sheet_name === sheetName)?.obs_date ?? null
}

/** 该文件里「表里没写日期、用户也还没指定」的行（这些行无法入库）。
 *  只统计"纯缺日期"的行 —— 测点对不上的行另有专门的匹配表处理，
 *  两边都列会把同一条数据说两遍，反而看不清到底还差什么。 */
const undatedRows = (fp: FilePreview): ImportPreviewRow[] =>
  (fp.preview?.no_date_rows ?? []).filter((r) => !sheetDate(fp, r.sheet_name))

const undatedCount = (fp: FilePreview): number => undatedRows(fp).length

/**
 * 控制值显示：整数不带小数点（30，不是 30.0），没值写「—」。
 * 报表和预警规则两边用的都是这个格式，看着才像"同一对数"。
 */
const fmtCtl = (v: number | null | undefined): string =>
  v === null || v === undefined ? '—' : String(Number.isInteger(v) ? v : Math.round(v * 100) / 100)

export default function ImportPage({ project, onBack }: Props) {
  const [step, setStep] = useState(0)
  const [files, setFiles] = useState<FilePreview[]>([])
  const [activeKey, setActiveKey] = useState('')
  const [parsing, setParsing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  /** 导入时把台账里的「控制值」同步进项目预警规则（默认不勾：改判定口径要用户点头） */
  const [syncControl, setSyncControl] = useState(false)
  const [summary, setSummary] = useState<{ ok: number; fail: number; rows: number; replaced: number; details: string[] }>({
    ok: 0,
    fail: 0,
    rows: 0,
    replaced: 0,
    details: [],
  })
  /** antd 多选会逐个调用 beforeUpload：用队列 + 防抖收集成一批 */
  const pendingRef = useRef<File[]>([])
  const timerRef = useRef<number | undefined>(undefined)
  /** 改日期 → 重新核对的防抖计时器（按文件 key） */
  const recheckTimers = useRef<Record<string, number>>({})
  /** files 的最新镜像：防抖回调里要读当次最新状态，闭包里的 files 可能已过期 */
  const filesRef = useRef<FilePreview[]>([])

  useEffect(() => {
    filesRef.current = files
  }, [files])

  const updateFile = useCallback((key: string, patch: Partial<FilePreview>) => {
    setFiles((prev) => prev.map((f) => (f.key === key ? { ...f, ...patch } : f)))
  }, [])

  /** 解析一批文件（可 1 个，也可多个）：每个文件独立预览 */
  const parseFiles = useCallback(
    async (list: File[]) => {
      if (list.length === 0) return
      setParsing(true)
      const stamp = Date.now()
      const items: FilePreview[] = list.map((f, i) => ({
        key: `${stamp}-${i}`,
        filename: f.name,
        status: 'uploading',
        excludeIds: new Set<string>(),
        resolutions: {},
        sheetDates: {},
      }))
      setFiles((prev) => [...prev, ...items])
      setActiveKey((prev) => prev || items[0].key)
      setStep(1)

      for (let i = 0; i < list.length; i++) {
        const key = items[i].key
        try {
          const res = await importApi.upload(project.id, list[i])
          const issues = res.point_issues ?? []
          updateFile(key, {
            status: 'ready',
            preview: res,
            resolutions: Object.fromEntries(issues.map((it) => [it.point_no, defaultResolution(it)])) as Record<string, Resolution>,
          })
        } catch (e) {
          updateFile(key, { status: 'error', error: (e as Error).message })
        }
      }
      setParsing(false)
    },
    [project.id, updateFile],
  )

  const handleBeforeUpload = useCallback(
    (file: File) => {
      const lower = file.name.toLowerCase()
      if (!lower.endsWith('.xlsx') && !lower.endsWith('.xlsm') && !lower.endsWith('.xls')) {
        message.error(`仅支持 .xlsx / .xlsm / .xls：${file.name}`)
        return Upload.LIST_IGNORE
      }
      pendingRef.current.push(file)
      window.clearTimeout(timerRef.current)
      timerRef.current = window.setTimeout(() => {
        const batch = [...pendingRef.current]
        pendingRef.current = []
        void parseFiles(batch)
      }, 200)
      return false
    },
    [parseFiles],
  )

  /** 给没有日期的行补上该数据表当前生效的观测日期（用户指定的优先） */
  const withDate =
    (fp: FilePreview) =>
    (r: ImportPreviewRow): ImportPreviewRow => {
      if (r.obs_date) return r
      const d = sheetDate(fp, r.sheet_name)
      return d ? { ...r, obs_date: d } : r
    }

  /** 某文件实际将导入的行（有效 + 覆盖系统生成 + 已处理的未知点 + 已指定日期的行 − 排除） */
  const buildRows = (fp: FilePreview): ImportPreviewRow[] => {
    const p = fp.preview
    if (!p) return []
    const fill = withDate(fp)
    const valid = p.valid_rows.filter((_, i) => !fp.excludeIds.has(`valid-${i}`)).map(fill)
    const override = (p.override_rows ?? []).map(fill)
    const resolved = p.error_rows
      .filter((r) => r.error_kind === 'unknown_point')
      .filter((r) => {
        const res = fp.resolutions[r.point_no]
        return !!res && res.mode !== 'skip'
      })
      .map((r) => {
        const res = fp.resolutions[r.point_no]
        return { ...r, point_no: res?.mode === 'map' && res.mapTo ? res.mapTo : r.point_no }
      })
      .map(fill)
    const fromNoDate = p.no_date_rows.map(fill)
    // 观测记录必须有日期：没有生效日期的行不参与导入（界面上会明示"还有 N 条未指定日期"），
    // 绝不能默默塞一条日期进去了事 —— 那等于系统替用户编日期。
    return [...valid, ...override, ...resolved, ...fromNoDate].filter((r) => !!r.obs_date)
  }

  /** 用户改/指定某数据表的观测日期 → 按新日期重新核对（防抖）。
   *  必须重算：新日期上可能已有记录（要变成"跳过"），也可能把原来的冲突解开（要变成"可导入"）；
   *  沿用旧预览会让用户看到错的条数。 */
  const applySheetDate = (key: string, sheetName: string, value: string | null) => {
    const fp = filesRef.current.find((f) => f.key === key)
    if (!fp) return
    const next = { ...fp.sheetDates }
    if (value) next[sheetName] = value
    else delete next[sheetName]
    updateFile(key, { sheetDates: next, rechecking: true })
    window.clearTimeout(recheckTimers.current[key])
    recheckTimers.current[key] = window.setTimeout(() => void runRecheck(key), 450)
  }

  const runRecheck = async (key: string) => {
    const fp = filesRef.current.find((f) => f.key === key)
    const p = fp?.preview
    if (!fp || !p) return
    const dates: SheetDateOverride[] = p.sheets.map((s) => ({
      sheet_name: s.sheet_name,
      obs_date: sheetDate(fp, s.sheet_name),
    }))
    try {
      const res = await importApi.recheck(p.batch_id, dates)
      const hadExclusions = fp.excludeIds.size > 0
      updateFile(key, {
        preview: res,
        rechecking: false,
        // 重新分组后 valid_rows 的序号变了，按序号记的"排除"会错位到别的行 —— 必须重置，
        // 不能静静地把另一行排除掉。
        excludeIds: new Set<string>(),
      })
      if (hadExclusions) message.info('已按新日期重新核对，之前的「排除」选择已重置')
    } catch (e) {
      updateFile(key, { rechecking: false })
      message.error(`按新日期核对失败：${(e as Error).message}`)
    }
  }

  /** 数据表日期选择器（文件清单列与详情面板共用同一套行为）。
   *  compact=文件清单里用（列窄，只留选择器 + 已改标记）；详情面板里另附"表里写的是 X"。 */
  const renderSheetDate = (fp: FilePreview, s: ImportSheetInfo, compact = false) => {
    const v = sheetDate(fp, s.sheet_name)
    const original = s.original_obs_date ?? null
    const changed = !!original && !!v && v !== original
    const picker = (
      <DatePicker
        size="small"
        style={{ width: 132 }}
        value={v ? dayjs(v) : null}
        onChange={(d) => applySheetDate(fp.key, s.sheet_name, d ? d.format('YYYY-MM-DD') : null)}
        placeholder="选择观测日期"
        allowClear
        disabled={fp.status !== 'ready' || !!fp.rechecking}
        // 选观测日期时常要避开放假 / 注意调休上班的周六，所以面板也带节假日标记
        {...plainCalendarPanel}
      />
    )
    if (compact) {
      return (
        <Space size={4}>
          {picker}
          {changed && <Tag color="blue">已改</Tag>}
        </Space>
      )
    }
    return (
      <Space size={6} wrap>
        {picker}
        {!v && (
          <Typography.Text type="warning" style={{ fontSize: 12 }}>
            表里没写日期，请选初测日期
          </Typography.Text>
        )}
        {v && changed && <Tag color="blue">已改（表里写的是 {original}）</Tag>}
        {v && !original && <Tag color="blue">表里没写日期，已指定</Tag>}
        {v && original && !changed && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            表里写的是 {original}
          </Typography.Text>
        )}
      </Space>
    )
  }


  const buildActions = (fp: FilePreview): PointResolveAction[] =>
    (fp.preview?.point_issues ?? [])
      // 该点号的行若全是"没有生效日期"的，就不必建点/映射（建了也没有数据要落进去）
      .filter((issue) => {
        const rows = (fp.preview?.error_rows ?? []).filter((r) => r.point_no === issue.point_no)
        return rows.some((r) => !r.needs_date || !!sheetDate(fp, r.sheet_name))
      })
      .map((issue) => {
        const res = fp.resolutions[issue.point_no]
        if (!res || res.mode === 'skip') return null
        if (res.mode === 'map' && res.mapTo) {
          return { excel_no: issue.point_no, auto_create: false, infer_point_type: issue.infer_point_type, map_to: res.mapTo }
        }
        return { excel_no: issue.point_no, auto_create: true, infer_point_type: issue.infer_point_type, map_to: null }
      })
      .filter((x): x is PointResolveAction => x !== null)

  const fileValidCount = (fp: FilePreview): number => buildRows(fp).length

  const removeFile = (key: string) => {
    setFiles((prev) => prev.filter((f) => f.key !== key))
    setActiveKey((prev) => (prev === key ? '' : prev))
  }

  const confirmAll = async () => {
    const ready = files.filter((f) => f.status === 'ready' && f.preview)
    if (ready.length === 0) {
      message.warning('没有可导入的文件')
      return
    }
    // 还有没确定日期的行：这些行无法入库（观测记录必须有日期，系统不会替你猜）。
    // 不硬拦（用户可能就想先导能用的一部分），但必须显式确认一次，别让人以为全导进去了。
    const noDateTotal = ready.reduce((s, f) => s + undatedCount(f), 0)
    if (noDateTotal > 0) {
      const goOn = await new Promise<boolean>((resolve) => {
        Modal.confirm({
          title: `还有 ${noDateTotal} 条没确定观测日期`,
          content: '这些行不会导入。可以先在上方「观测日期」列给对应的数据表选好日期再导，也可以先导入其余数据。',
          okText: '继续导入其余数据',
          cancelText: '我先去选日期',
          onOk: () => resolve(true),
          onCancel: () => resolve(false),
        })
      })
      if (!goOn) return
    }
    setConfirming(true)
    let ok = 0
    let fail = 0
    let rows = 0
    let replaced = 0
    let skippedNoDate = 0
    let skippedDup = 0
    const ruleChanges: string[] = []
    const details: string[] = []
    for (const fp of ready) {
      const rowsToImport = buildRows(fp)
      if (rowsToImport.length === 0) {
        details.push(`${fp.filename}：没有可导入的数据行，已跳过`)
        continue
      }
      try {
        const res = await importApi.confirm(
          fp.preview!.batch_id,
          rowsToImport,
          buildActions(fp),
          syncControl,
        )
        ok += 1
        rows += res.imported_rows
        replaced += res.replaced_generated ?? 0
        skippedNoDate += res.skipped_no_date ?? 0
        skippedDup += res.skipped_dup_existing ?? 0
        for (const c of res.control_changes ?? []) {
          const label = c.indicator === 'settlement' ? '沉降' : '水平位移'
          const before = c.created ? '（原来没配）' : `（原 ${fmtCtl(c.old_cum)} / ${fmtCtl(c.old_rate)}）`
          ruleChanges.push(`${label} 控制值 → ${fmtCtl(c.new_cum)} / ${fmtCtl(c.new_rate)} mm·d⁻¹${before}`)
        }
        updateFile(fp.key, { status: 'imported', importedRows: res.imported_rows })
        details.push(
          `${fp.filename}：成功 ${res.imported_rows} 条${
            res.replaced_generated ? `（其中覆盖系统生成数据 ${res.replaced_generated} 条）` : ''
          }${res.points_auto_created ? `，自动新建测点 ${res.points_auto_created} 个` : ''}`,
        )
      } catch (e) {
        fail += 1
        details.push(`${fp.filename}：失败（${(e as Error).message}）`)
      }
    }
    if (skippedNoDate > 0) details.push(`另有 ${skippedNoDate} 条因缺观测日期未入库，请指定日期后重新导入`)
    if (skippedDup > 0) details.push(`另有 ${skippedDup} 条当天已有记录，自动跳过未覆盖`)
    for (const c of ruleChanges) details.push(`预警规则已同步：${c}`)
    setSummary({ ok, fail, rows, replaced, details })
    setConfirming(false)
    setStep(2)
    if (fail === 0) message.success(`导入完成：${ok} 个文件、共 ${rows} 条`)
    else message.warning(`导入结束：成功 ${ok} 个文件、失败 ${fail} 个`)
  }

  const statusTag = (fp: FilePreview) => {
    if (fp.status === 'uploading') return <Tag color="processing">解析中…</Tag>
    if (fp.status === 'error') return <Tag color="red">解析失败</Tag>
    if (fp.status === 'imported') return <Tag color="green">已导入 {fp.importedRows ?? 0} 条</Tag>
    if (fp.rechecking) return <Tag color="processing">按新日期核对中…</Tag>
    const n = fileValidCount(fp)
    if (n > 0) return <Tag color="blue">待导入 {n} 条</Tag>
    if (undatedCount(fp) > 0) return <Tag color="orange">待指定日期</Tag>
    return <Tag>无可导入数据</Tag>
  }

  const totalToImport = files.filter((f) => f.status === 'ready').reduce((s, f) => s + fileValidCount(f), 0)
  const readyCount = files.filter((f) => f.status === 'ready' && fileValidCount(f) > 0).length
  /** 还有行没确定观测日期的文件（这些行不会入库） */
  const pendingDateFiles = files.filter((f) => f.status === 'ready' && undatedCount(f) > 0)
  /** 全部文件里没确定日期的行数 */
  const pendingDateRows = files.reduce((s, f) => s + (f.status === 'ready' ? undatedCount(f) : 0), 0)

  /**
   * 传进来的其实是「点号 + 坐标/高程」点位清单（整份文件没有一张逐期台账）。
   *
   * 这类文件不是"某一天的观测数据"，而是用来**建项目/测点**的 —— 走「批量建项目」。
   * 传错入口不会报错（清单里的坐标会被当成初始读数存下来），所以这里主动提醒一句。
   */
  const pointListFiles = files.filter(
    (f) =>
      f.status === 'ready' &&
      (f.preview?.sheets.length ?? 0) > 0 &&
      (f.preview?.sheets ?? []).every((s) => s.kind === 'point_list'),
  )

  /**
   * 台账里写着的「控制值」和项目预警规则的对不上情况（按指标去重，多文件时取先出现的）。
   *
   * 报表上的「控制值」两列和规则里的「控制值」必须是同一个数 —— 这里把它摆出来，
   * 不一致时给一个"导入时顺带同步"的勾选框（默认不勾：改判定口径要用户点头）。
   */
  const ctlSuggest = (() => {
    const seen = new Map<string, ControlValueSuggest>()
    for (const f of files) {
      if (f.status !== 'ready') continue
      for (const s of f.preview?.control_suggest ?? []) {
        if (!seen.has(s.indicator)) seen.set(s.indicator, s)
      }
    }
    return [...seen.values()]
  })()
  const ctlDiffers = ctlSuggest.some((s) => s.differs)

  const cur = files.find((f) => f.key === activeKey) ?? files[0]
  const curPreview = cur?.preview
  const curValidRows = curPreview?.valid_rows ?? []
  const curOverrideRows = curPreview?.override_rows ?? []
  const curConflictRows = curPreview?.conflict_rows ?? []
  const curErrorRows = curPreview?.error_rows ?? []
  const curPointIssues = curPreview?.point_issues ?? []
  const curExclude = cur?.excludeIds ?? new Set<string>()
  const curResolutions = cur?.resolutions ?? {}
  const curResolvedCount = curErrorRows
    .filter((r) => r.error_kind === 'unknown_point' && curResolutions[r.point_no] && curResolutions[r.point_no].mode !== 'skip')
    .length
  /** 该文件里"没有生效日期"的行 */
  const curUndatedCount = cur ? undatedCount(cur) : 0
  const curNoDateRows = cur ? undatedRows(cur) : []

  const fileColumns: ColumnsType<FilePreview> = [
    {
      title: '文件',
      dataIndex: 'filename',
      render: (v: string, fp) => (
        <Space size={4}>
          <Typography.Text strong={fp.key === cur?.key}>{v}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '观测日期（可改）',
      width: 290,
      render: (_, fp) => (
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          {(fp.preview?.sheets ?? []).map((s) => (
            <Space key={s.sheet_name} size={4} wrap>
              <Tag>{sheetKindLabel(s.series_type)}</Tag>
              {renderSheetDate(fp, s, true)}
            </Space>
          ))}
          {!fp.preview && <Typography.Text type="secondary">—</Typography.Text>}
        </Space>
      ),
    },
    {
      title: '可导入',
      width: 90,
      render: (_, fp) => (fp.status === 'ready' ? <Tag color="blue">{fileValidCount(fp)} 条</Tag> : '—'),
    },
    {
      title: '冲突/错误',
      width: 110,
      render: (_, fp) =>
        fp.preview ? (
          <Typography.Text type="secondary">
            {fp.preview.conflict_rows.length} / {fp.preview.error_rows.length}
          </Typography.Text>
        ) : (
          '—'
        ),
    },
    { title: '状态', width: 130, render: (_, fp) => statusTag(fp) },
    {
      title: '操作',
      width: 130,
      render: (_, fp) => (
        <Space size={4}>
          <Button size="small" type={fp.key === cur?.key ? 'primary' : 'default'} onClick={() => setActiveKey(fp.key)}>
            查看
          </Button>
          <Button size="small" type="text" danger onClick={() => removeFile(fp.key)}>
            移除
          </Button>
        </Space>
      ),
    },
  ]

  const validColumns: ColumnsType<ImportPreviewRow & { _idx: number }> = [
    {
      title: '测点',
      dataIndex: 'point_no',
      width: 110,
      render: (v: string, r) => (
        <Space size={4}>
          {v}
          {r.initial_mm != null && r.value_mm === r.initial_mm && <Tag color="blue">V0</Tag>}
        </Space>
      ),
    },
    {
      title: '观测项',
      width: 120,
      render: (_, r) => (
        <Tag>
          {r.series_type === 'settlement' ? '沉降' : '水平位移'} / {r.component}
        </Tag>
      ),
    },
    { title: '日期', dataIndex: 'obs_date', width: 110 },
    { title: '读数(m)', dataIndex: 'value_mm', width: 145, render: (v: number | null) => fmtM(v) },
    {
      title: '控制值(mm)',
      dataIndex: 'control_cum_mm',
      width: 100,
      render: (v: number | null) => (v == null ? '-' : v),
    },
    {
      title: '速率控制值(mm/d)',
      dataIndex: 'control_rate_mm_d',
      width: 130,
      render: (v: number | null | undefined) => (v == null ? '-' : v),
    },
    { title: '备注', dataIndex: 'remark', render: (v: string | null) => v ?? '' },
  ]

  const conflictColumns: ColumnsType<ImportPreviewRow> = [
    { title: '测点', dataIndex: 'point_no', width: 90 },
    { title: '观测项', width: 130, render: (_, r) => <Tag color="orange">{r.series_type} / {r.component}</Tag> },
    { title: '日期', dataIndex: 'obs_date', width: 110 },
    { title: '模板值(m)', width: 110, render: (_, r) => fmtM(r.value_mm) },
    { title: '已有值(m)', width: 110, render: (_, r) => fmtM(r.existing_value_mm) },
    { title: '处理', render: () => <Tag color="red">跳过（不覆盖原始数据）</Tag> },
  ]

  /** 与"系统自动生成"数据同日：导入后以用户数据为准覆盖 */
  const overrideColumns: ColumnsType<ImportPreviewRow> = [
    { title: '测点', dataIndex: 'point_no', width: 90 },
    { title: '观测项', width: 130, render: (_, r) => <Tag color="blue">{r.series_type} / {r.component}</Tag> },
    { title: '日期', dataIndex: 'obs_date', width: 110 },
    { title: '系统生成值(m)', width: 130, render: (_, r) => fmtM(r.existing_value_mm) },
    { title: '你的数据(m)', width: 120, render: (_, r) => fmtM(r.value_mm) },
    { title: '处理', render: () => <Tag color="green">以你的数据为准（覆盖生成数据）</Tag> },
  ]

  const errorColumns: ColumnsType<ImportPreviewRow & { _idx: number }> = [
    { title: '测点', dataIndex: 'point_no', width: 110 },
    { title: '观测项', width: 130, render: (_, r) => <Tag>{r.series_type} / {r.component}</Tag> },
    { title: '日期', dataIndex: 'obs_date', width: 110 },
    {
      title: '错误原因',
      dataIndex: 'error',
      render: (v: string | null, r) => (
        <span style={{ color: r.error_kind === 'unknown_point' && curResolutions[r.point_no]?.mode !== 'skip' ? WARNING_LEVEL.control.fg : WARNING_LEVEL.alarm.fg }}>{v}</span>
      ),
    },
  ]

  /** 待指定日期的行（表里没写日期）—— 明示"哪些行不会导入"，不让人猜 */
  const noDateColumns: ColumnsType<ImportPreviewRow> = [
    { title: '测点', dataIndex: 'point_no', width: 110 },
    {
      title: '观测项',
      width: 130,
      render: (_, r) => <Tag>{r.series_type === 'settlement' ? '沉降' : '水平位移'} / {r.component}</Tag>,
    },
    {
      title: '所属数据表',
      dataIndex: 'sheet_name',
      width: 190,
      render: (v: string | null | undefined) => v ?? '—',
    },
    { title: '读数(m)', width: 110, render: (_, r) => fmtM(r.value_mm) },
    { title: '备注', dataIndex: 'remark', render: (v: string | null) => v ?? '' },
  ]

  const issueColumns: ColumnsType<PointIssue> = [
    {
      title: 'Excel 点号',
      dataIndex: 'point_no',
      width: 120,
      render: (v: string) => <Typography.Text strong>{v}</Typography.Text>,
    },
    { title: '行数', dataIndex: 'row_count', width: 60, render: (v: number) => <Tag color="blue">{v}</Tag> },
    {
      title: '推断类型',
      dataIndex: 'infer_point_type',
      width: 110,
      render: (v: PointType) => <Tag color="purple">{TYPE_LABEL[v]}</Tag>,
    },
    {
      title: '候选已有测点',
      width: 180,
      render: (_, issue) =>
        issue.suggested.length > 0 ? (
          issue.suggested.map((s) => (
            <Tag key={s.point_no} color={s.confidence === 'exact' ? 'green' : s.confidence === 'strong' ? 'cyan' : 'default'}>
              {s.point_no}
              {s.confidence === 'exact' ? '(精确)' : s.confidence === 'strong' ? '(相似)' : ''}
            </Tag>
          ))
        ) : (
          <Typography.Text type="secondary">无候选</Typography.Text>
        ),
    },
    {
      title: '处理方式',
      width: 300,
      render: (_, issue) => {
        const res = curResolutions[issue.point_no] ?? defaultResolution(issue)
        const skipped = res.mode === 'skip'
        const k = cur?.key ?? ''
        return (
          <Space>
            <Select
              size="small"
              style={{ width: 190 }}
              disabled={skipped}
              placeholder={skipped ? '已跳过，不导入' : '请选择处理方式'}
              value={skipped ? undefined : res.mode === 'map' ? (res.mapTo ?? undefined) : '__create__'}
              onChange={(v) => {
                if (!cur) return
                if (v === '__create__') updateFile(k, { resolutions: { ...curResolutions, [issue.point_no]: { mode: 'auto_create' } } })
                else updateFile(k, { resolutions: { ...curResolutions, [issue.point_no]: { mode: 'map', mapTo: v } } })
              }}
              options={[
                { value: '__create__', label: `自动建点（${TYPE_LABEL[issue.infer_point_type]}）` },
                ...issue.suggested.map((s) => ({ value: s.point_no, label: `映射到 ${s.point_no}` })),
              ]}
            />
            <Switch
              size="small"
              checked={!skipped}
              checkedChildren="处理"
              unCheckedChildren="跳过"
              onChange={(on) => {
                if (!cur) return
                updateFile(k, { resolutions: { ...curResolutions, [issue.point_no]: on ? defaultResolution(issue) : { mode: 'skip' } } })
              }}
            />
          </Space>
        )
      },
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
          message={`${project.name}（${project.code}）· Excel 导入`}
          description="一次可选 1 个或多个文件（每期一个文件）。系统逐个核对后列成清单，确认无误点「全部导入」即可；当天已有数据的行会自动跳过，绝不覆盖。"
        />
      </Space>

      <Steps current={step} items={[{ title: '选择文件' }, { title: '核对并导入' }, { title: '完成' }]} />

      {step === 0 && (
        <Dragger
          accept=".xlsx,.xlsm,.xls"
          multiple
          showUploadList={false}
          beforeUpload={handleBeforeUpload}
          disabled={parsing}
          style={{ padding: 24 }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">{parsing ? '解析中…' : '点击或拖拽 Excel 文件到此处（可一次选多个）'}</p>
          <p className="ant-upload-hint">
            支持两类：① 基坑台账（自动识别竖向位移 / 水平位移 sheet）；② 初始值清单（「名字 + X/Y/Z」表）。
            支持 .xlsx / .xlsm / .xls；一期一个文件，可多选一次导入
          </p>
          <p className="ant-upload-hint">
            ⚠ 这个页面是「把某一天的观测数据录进某个项目」。
            <b>要新建基坑、批量建测点</b>（一张表里有好几个基坑的点），请回项目列表用「批量建项目」。
          </p>
        </Dragger>
      )}

          {step === 1 && (
        <>
          {pointListFiles.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`这 ${pointListFiles.length} 个文件是「点号 + 坐标/高程」点位清单，不是某一天的观测数据`}
              description={
                <span style={{ fontSize: 12 }}>
                  {pointListFiles.map((f) => f.filename).join('、')}。
                  <b>要新建基坑、批量建测点</b>（一张表里混了好几个基坑的点）：回项目列表点「批量建项目」，
                  在那边传这份清单。如果只是想把它当作<b>某个项目的初始值（第 0 期）</b>导入，
                  忽略这条提醒、继续往下走即可。
                </span>
              }
            />
          )}

          {ctlSuggest.length > 0 && (
            <Alert
              type={ctlDiffers ? 'warning' : 'info'}
              showIcon
              message={
                ctlDiffers
                  ? '台账里的「控制值」和本项目预警规则对不上'
                  : '台账里的「控制值」与预警规则一致'
              }
              description={
                <Space direction="vertical" size={4} style={{ fontSize: 12 }}>
                  {ctlSuggest.map((s) => (
                    <span key={s.indicator}>
                      {s.indicator === 'settlement' ? '沉降' : '水平位移'}：台账写{' '}
                      <b>{fmtCtl(s.control_cum_mm)}</b> mm / <b>{fmtCtl(s.control_rate_mm_d)}</b> mm·d⁻¹
                      {'；'}
                      {s.has_rule
                        ? `当前规则 ${fmtCtl(s.current_cum_mm)} / ${fmtCtl(s.current_rate_mm_d)}`
                        : '本项目还没配规则'}
                      {s.differs ? ' ← 对不上' : ' ✓'}
                    </span>
                  ))}
                  {ctlDiffers && (
                    <Checkbox checked={syncControl} onChange={(e) => setSyncControl(e.target.checked)}>
                      导入时把台账里的控制值同步到本项目预警规则
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        （只改「控制值」，报警值不动 —— 台账里没有报警值的依据）
                      </Typography.Text>
                    </Checkbox>
                  )}
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    报表里「控制值」两列以后就按这个数写，和系统判定用的是同一对数。
                  </Typography.Text>
                </Space>
              }
            />
          )}

          {pendingDateFiles.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`有 ${pendingDateRows} 条数据还没确定观测日期`}
              description={
                <span style={{ fontSize: 12 }}>
                  涉及 {pendingDateFiles.length} 个文件（{pendingDateFiles.map((f) => f.filename).join('、')}）。
                  在下方「观测日期（可改）」列给对应的数据表选日期即可 —— 如果是初始值清单，就选初测那一天。
                  <b>没确定日期的行不会导入</b>（系统不会替你猜日期）。
                </span>
              }
            />
          )}

          <Table<FilePreview>
            size="small"
            rowKey="key"
            title={() => (
              <Space>
                <Typography.Text strong>文件清单（{files.length}）</Typography.Text>
                {parsing && <Tag color="processing">解析中…</Tag>}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  点「查看」核对明细；确认后一次全部导入
                </Typography.Text>
              </Space>
            )}
            columns={fileColumns}
            dataSource={files}
            pagination={false}
            scroll={{ x: 1080 }}
          />

          {cur?.status === 'error' && (
            <Alert
              type="error"
              showIcon
              message={`文件解析失败：${cur.filename}`}
              description={cur.error ?? '请检查文件格式（支持 .xlsx / .xlsm / .xls，且含竖向位移 / 水平位移 Sheet）'}
            />
          )}

          {cur?.status === 'uploading' && <Alert type="info" showIcon message={`正在解析 ${cur.filename} …`} />}

          {curPreview && (
            <>
              <Alert
                type={curPreview.sheets.length > 0 ? 'success' : 'warning'}
                showIcon
                message={`当前查看：${cur?.filename} ｜ 识别 ${curPreview.sheets.length} 个数据 Sheet`}
                description={
                  <Space direction="vertical" size={2} style={{ fontSize: 12 }}>
                    {curPreview.sheets.length === 0 ? (
                      <span>
                        没有识别到数据表。常见原因：表头不在前 15 行；表头缺少「点号/测点」与「测值/读数/累计」字样；Sheet 名不含「沉降/竖向位移/水平位移」。
                        可换个写法重传，或把表头文字发我加识别规则。
                      </span>
                    ) : (
                      curPreview.sheets.map((s) => (
                        <span key={s.sheet_name}>
                          【{sheetKindLabel(s.series_type)}】{s.sheet_name}（{s.row_count} 行）
                          {colMapText(s.column_map) && ` — 已识别列：${colMapText(s.column_map)}`}
                        </span>
                      ))
                    )}
                  </Space>
                }
              />

              {curPreview.sheets.length > 0 && cur && (
                <Card
                  size="small"
                  title={
                    <Space>
                      <Typography.Text strong>观测日期</Typography.Text>
                      {cur.rechecking && <Tag color="processing">按新日期核对中…</Tag>}
                    </Space>
                  }
                >
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      表里读到的日期不对、或这份表本来就没写日期，都可以在这里改。改完系统会立刻按新日期重新核对：
                      那天已经有记录的行会自动跳过、绝不覆盖；原来撞车的日期改开后，那些行就变成可导入。
                    </Typography.Text>
                    {curPreview.sheets.map((s) => (
                      <Space key={s.sheet_name} size={8} wrap>
                        <Tag>{sheetKindLabel(s.series_type)}</Tag>
                        <Typography.Text style={{ minWidth: 150 }}>{s.sheet_name}</Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {s.row_count} 行
                        </Typography.Text>
                        {renderSheetDate(cur, s)}
                      </Space>
                    ))}
                    {curUndatedCount > 0 && (
                      <Typography.Text type="warning" style={{ fontSize: 12 }}>
                        这个文件还有 {curUndatedCount} 条没有观测日期，这些行不会导入 —— 在上面给对应的数据表选个日期即可。
                      </Typography.Text>
                    )}
                  </Space>
                </Card>
              )}

              {curPreview.sheets.length > 0 &&
                curValidRows.length === 0 &&
                curConflictRows.length === 0 &&
                curErrorRows.length === 0 && (
                  <Alert
                    type="warning"
                    showIcon
                    message="这个文件没有解析出数据行"
                    description="系统认出了数据表，但没读到有效行——可能是表头或列名与预期不同，或数据区为空。把该文件的表头文字发我，我加对应的识别规则。"
                  />
                )}

              {curPointIssues.length > 0 && (
                <Table<PointIssue>
                  size="small"
                  rowKey="point_no"
                  title={() => (
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Typography.Text type="warning">⚠ 测点匹配（{curPointIssues.length} 个点号在项目中不存在）</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        默认已替你选好：能对上已有测点的自动映射，对不上的自动新建。不用改也能直接导入。
                      </Typography.Text>
                    </Space>
                  )}
                  columns={issueColumns}
                  dataSource={curPointIssues}
                  pagination={false}
                />
              )}

              {curValidRows.length > 0 && (
                <Table<ImportPreviewRow & { _idx: number }>
                  size="small"
                  rowKey={(_, i) => `v-${i ?? 0}`}
                  title={() => (
                    <Space>
                      <Typography.Text strong>
                        ✓ 可导入（{curValidRows.length - curExclude.size} 条{curResolvedCount ? ` + 已处理点 ${curResolvedCount} 条` : ''}）
                      </Typography.Text>
                      <Button
                        size="small"
                        type="link"
                        onClick={() => {
                          if (!cur) return
                          Modal.confirm({
                            title: '确认全部排除',
                            content: '将不导入该文件的有效行（冲突/错误行本就不导入）。',
                            onOk: () => updateFile(cur.key, { excludeIds: new Set(curValidRows.map((_, i) => `valid-${i}`)) }),
                          })
                        }}
                      >
                        全部排除
                      </Button>
                    </Space>
                  )}
                  columns={validColumns}
                  dataSource={curValidRows.map((r, i) => ({ ...r, _idx: i }))}
                  pagination={false}
                  rowClassName={(r) => (curExclude.has(`valid-${r._idx}`) ? 'ant-table-row-ignore' : '')}
                />
              )}

              {curOverrideRows.length > 0 && (
                <Table
                  size="small"
                  rowKey={(_, i) => `o-${i ?? 0}`}
                  title={() => (
                    <Typography.Text type="warning">
                      ⚠ 将覆盖系统自动生成的数据（{curOverrideRows.length} 条）— 这些日期系统自动生成过数据，导入后以你导入的为准
                    </Typography.Text>
                  )}
                  columns={overrideColumns}
                  dataSource={curOverrideRows}
                  pagination={false}
                />
              )}

              {curConflictRows.length > 0 && (
                <Table
                  size="small"
                  rowKey={(_, i) => `c-${i ?? 0}`}
                  title={() => <Typography.Text type="warning">⚠ 冲突（{curConflictRows.length} 条）— 当日已有记录，自动跳过</Typography.Text>}
                  columns={conflictColumns}
                  dataSource={curConflictRows}
                  pagination={false}
                />
              )}

              {curUndatedCount > 0 && (
                <Table<ImportPreviewRow>
                  size="small"
                  rowKey={(_, i) => `nd-${i ?? 0}`}
                  title={() => (
                    <Typography.Text type="warning">
                      ⚠ 待指定观测日期（{curUndatedCount} 条）— 这些行不会导入，在上方「观测日期」里给对应的数据表选好日期即可
                    </Typography.Text>
                  )}
                  columns={noDateColumns}
                  dataSource={curNoDateRows}
                  pagination={false}
                />
              )}

              {curErrorRows.filter((r) => !(r.error_kind === 'unknown_point' && curResolutions[r.point_no] && curResolutions[r.point_no].mode !== 'skip')).length > 0 && (
                <Table<ImportPreviewRow & { _idx: number }>
                  size="small"
                  rowKey={(_, i) => `e-${i ?? 0}`}
                  title={() => (
                    <Typography.Text type="danger">
                      ✗ 校验失败（
                      {curErrorRows.filter((r) => !(r.error_kind === 'unknown_point' && curResolutions[r.point_no] && curResolutions[r.point_no].mode !== 'skip')).length} 条）— 不导入
                    </Typography.Text>
                  )}
                  columns={errorColumns}
                  dataSource={curErrorRows
                    .filter((r) => !(r.error_kind === 'unknown_point' && curResolutions[r.point_no] && curResolutions[r.point_no].mode !== 'skip'))
                    .map((r, i) => ({ ...r, _idx: i }))}
                  pagination={false}
                />
              )}
            </>
          )}

          <Space wrap>
            <Button
              onClick={() => {
                setFiles([])
                setActiveKey('')
                setStep(0)
              }}
            >
              重新选择
            </Button>
            <Button
              type="primary"
              loading={confirming}
              disabled={files.filter((f) => f.status === 'ready').length === 0}
              onClick={() => void confirmAll()}
            >
              {pendingDateFiles.length > 0
                ? `全部导入（还有 ${pendingDateRows} 条待选日期）`
                : `全部导入${readyCount > 0 ? `（${readyCount} 个文件 / ${totalToImport} 条）` : ''}`}
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              冲突行与校验失败行不会导入，原始数据不会被覆盖
            </Typography.Text>
          </Space>
        </>
      )}

      {step === 2 && (
        <>
          <Alert
            type="success"
            showIcon
            message={`导入完成：成功 ${summary.ok} 个文件、共 ${summary.rows} 条${
              summary.replaced ? `（其中覆盖系统生成数据 ${summary.replaced} 条）` : ''
            }${summary.fail ? `；失败 ${summary.fail} 个` : ''}`}
            description={
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {summary.details.map((d, i) => (
                  <li key={i}>{d}</li>
                ))}
              </ul>
            }
          />
          <Space>
            <Button
              onClick={() => {
                setFiles([])
                setActiveKey('')
                setSummary({ ok: 0, fail: 0, rows: 0, replaced: 0, details: [] })
                setStep(0)
              }}
            >
              继续导入下一批
            </Button>
            <Button onClick={onBack}>返回项目列表</Button>
          </Space>
        </>
      )}
    </Space>
  )
}
