import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Segmented,
  Slider,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { InputRef } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import SequenceEntry from '../components/SequenceEntry'
import CalendarCell, { CalendarLegend, plainCalendarPanel } from '../components/CalendarCell'
import {
  baselineApi,
  calcApi,
  generateApi,
  pointApi,
  recordApi,
  type BaselineInfo,
  type Component,
  type GenerateChangeSample,
  type GenerateConflictMode,
  type GeneratePreview,
  type MeasurementPoint,
  type ObservationRecord,
  type SeriesType,
  type WarningStatusRow,
} from '../api/client'
import { POINT_TYPE_LABEL, POINT_TYPE_TAG_COLOR } from '../labels'
import { levelBg, WARNING_LEVEL } from '../theme/tokens'
import type { RecordsFocus } from '../navigation'

interface PointInputs {
  value: string // 沉降读数（m）
  x: string // 位移 X（m）
  y: string // 位移 Y（m）
}
const EMPTY_INPUTS: PointInputs = { value: '', x: '', y: '' }
const M_TO_MM = 1000 // 界面读值单位 m，入库统一 mm

/** 模板口径：读值用 m 录入显示（入库 ×1000 存 mm）；V0 只在首期计入一次，各期以 V0 为基准。 */
export default function RecordsPage({
  projectId,
  onBack,
  focus,
}: {
  projectId: number
  onBack: () => void
  /** 从「需要关注」点进来时的定位目标：直接落到出问题的那一天 / 那个测点 */
  focus?: RecordsFocus
}) {
  const [points, setPoints] = useState<MeasurementPoint[]>([])
  const [records, setRecords] = useState<ObservationRecord[]>([])
  const [status, setStatus] = useState<WarningStatusRow[]>([])
  const [baselines, setBaselines] = useState<BaselineInfo[]>([])
  const [baselineDates, setBaselineDates] = useState<string[]>([])
  const [recordDates, setRecordDates] = useState<string[]>([])
  const [alarmDates, setAlarmDates] = useState<string[]>([])
  const [controlDates, setControlDates] = useState<string[]>([])
  // 初始日期 = 定位目标（若从「需要关注」进来），否则今天。
  // 早先固定用 dayjs()，于是点了"上次 2026-09-24 观测"也会落在今天 —— 定位不到问题日期。
  const [date, setDate] = useState<Dayjs>(focus?.date ? dayjs(focus.date) : dayjs())
  /** 定位提示可关闭（否则用户改日期后还挂在上面会困惑） */
  const [focusNote, setFocusNote] = useState<string | null>(focus?.note ?? null)
  const [inputs, setInputs] = useState<Record<number, PointInputs>>({})
  const [saving, setSaving] = useState(false)
  // 数据生成器弹窗状态
  const [genOpen, setGenOpen] = useState(false)
  const [genStart, setGenStart] = useState<Dayjs>(dayjs().add(-6, 'day'))
  const [genEnd, setGenEnd] = useState<Dayjs>(dayjs())
  const [genMissing, setGenMissing] = useState(0)
  const [genSeed, setGenSeed] = useState<string>('')
  /**
   * 观测模式：全站仪 —— 叠加"每次架站"的公共误差（对中平移 + 定向微旋转）。
   *
   * 为什么要这个模式：默认噪声是**每点各自独立**的，生成出来"每个点各跳各的"；
   * 而真实全站仪每次架站的对中/定向偏差是**当期所有点共同承担**的，
   * 同一期各点会一起偏一点。开这个模式才是全站仪数据的样子。
   */
  const [genTotalStation, setGenTotalStation] = useState(false)
  const [genCentering, setGenCentering] = useState(2.0)   // mm（对中+整平极限值）
  const [genDirection, setGenDirection] = useState(5.0)   // ″（后视定向中误差）
  const [genDistance, setGenDistance] = useState(30)      // m（工作距离）
  const [genLoading, setGenLoading] = useState(false)
  const [genResult, setGenResult] = useState<string | null>(null)
  /**
   * 本次被替换掉的记录（旧值 → 新值）。
   *
   * 为什么非要显示这个：替换前后读数只差 1~3mm（观测本来就是这个量级），
   * 而记录数、日期、测点数**完全不变** —— 用户盯着表格根本看不出数据被换过，
   * 只会觉得"我点了替换，怎么一点反应都没有"。
   */
  const [genChanges, setGenChanges] = useState<GenerateChangeSample[]>([])
  /**
   * 生成前的冲突预检结果。有值时弹出"这些日期怎么办"的询问框 ——
   * 原先遇到已有日期是**静默跳过**，用户根本不知道范围内哪些日期没生成。
   */
  const [conflict, setConflict] = useState<GeneratePreview | null>(null)
  const [conflictMode, setConflictMode] = useState<GenerateConflictMode>('skip')

  /** 录入模式：表格（总览/抽查）或顺序（现场一个一个敲，全键盘） */
  const [entryMode, setEntryMode] = useState<'table' | 'sequence'>('table')
  /** 顺序录入当前位置 */
  const [seqIndex, setSeqIndex] = useState(0)
  /** 当前聚焦的输入框（顺序录入自动跳转用） */
  const seqInputRef = useRef<InputRef>(null)

  const recFor = (pointId: number, series: SeriesType, comp: Component) =>
    records.find((r) => r.point_id === pointId && r.series_type === series && r.component === comp)

  const baselineFor = (pointId: number, series: SeriesType, comp: Component) =>
    baselines.find((b) => b.point_id === pointId && b.series_type === series && b.component === comp)

  const loadPoints = useCallback(async () => {
    try {
      setPoints(await pointApi.list(projectId, false))
    } catch (e) {
      message.error((e as Error).message)
    }
  }, [projectId])

  /**
   * 预警状态按**所选日期**取，不再固定取最新一期。
   * 否则会出现"定位到 09-03，表格里却显示 09-15 的预警等级"这种前后矛盾。
   */
  const loadStatus = useCallback(
    async (d: Dayjs) => {
      try {
        setStatus(await calcApi.warningStatus(projectId, d.format('YYYY-MM-DD')))
      } catch {
        console.warn('预警状态加载失败')
      }
    },
    [projectId],
  )

  const loadBaselines = useCallback(async () => {
    try {
      const [bs, dt] = await Promise.all([baselineApi.list(projectId), baselineApi.dates(projectId)])
      setBaselines(bs)
      setBaselineDates(dt.baseline_dates)
      setRecordDates(dt.dates)
      setAlarmDates(dt.alarm_dates ?? [])
      setControlDates(dt.control_dates ?? [])
    } catch {
      console.warn('初始值加载失败')
    }
  }, [projectId])

  const loadRecords = useCallback(
    async (d: Dayjs) => {
      const day = d.format('YYYY-MM-DD')
      const all: ObservationRecord[] = []
      try {
        const pts = await pointApi.list(projectId, false)
        for (const p of pts) {
          const rs = await recordApi.list(p.id, { from_date: day, to_date: day })
          // 只留生效记录。作废（void）与被更正（superseded）的不能用来回填 ——
          // 同一天同分量会有两条，下面的 find() 取第一条；作废那条 id 更小排在前面，
          // 一旦取到它（值往往是空的），输入框就显示空白，而「录入状态」列又因为
          // 存在生效记录显示"已录入" —— 界面自相矛盾（用户实测报障）。
          // 后端已按 active_only 过滤，这里再兜一层（防旧版本后端 / 缓存数据）。
          all.push(...rs.filter((r) => r.status !== 'void' && r.superseded_by_id == null))
        }
        setRecords(all)
        const next: Record<number, PointInputs> = {}
        for (const p of pts) {
          const settle = all.find((r) => r.point_id === p.id && r.series_type === 'settlement' && r.component === 'value')
          const dx = all.find((r) => r.point_id === p.id && r.series_type === 'displacement' && r.component === 'X')
          const dy = all.find((r) => r.point_id === p.id && r.series_type === 'displacement' && r.component === 'Y')
          next[p.id] = {
            value: settle && settle.value_mm !== null ? (settle.value_mm / M_TO_MM).toFixed(3) : '',
            x: dx && dx.value_mm !== null ? (dx.value_mm / M_TO_MM).toFixed(3) : '',
            y: dy && dy.value_mm !== null ? (dy.value_mm / M_TO_MM).toFixed(3) : '',
          }
        }
        setInputs(next)
      } catch (e) {
        message.error((e as Error).message)
      }
    },
    [projectId],
  )

  useEffect(() => {
    void loadPoints()
    void loadBaselines()
  }, [loadPoints, loadBaselines])

  useEffect(() => {
    void loadRecords(date)
    void loadStatus(date)
  }, [date, loadRecords, loadStatus])

  /** 定位：表格渲染完成后把高亮行滚到视野中间（否则要在长表里自己找） */
  useEffect(() => {
    if (!focus?.pointNo) return
    const t = window.setTimeout(() => {
      document.querySelector('.focus-row')?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }, 350)
    return () => window.clearTimeout(t)
  }, [focus?.pointNo, points.length, records.length])

  const save = async () => {
    const d = date.format('YYYY-MM-DD')
    const toCreate: Array<{ point: MeasurementPoint; job: { series_type: SeriesType; component: Component; val: number | null } }> = []
    const toCorrect: Array<{
      point: MeasurementPoint
      existing: ObservationRecord
      job: { series_type: SeriesType; component: Component; val: number | null }
    }> = []

    for (const p of points) {
      const inp = inputs[p.id] ?? EMPTY_INPUTS
      const jobs: Array<{ series_type: SeriesType; component: Component; raw: string }> = []
      if (p.point_type === 'settlement' || p.point_type === 'both') {
        jobs.push({ series_type: 'settlement', component: 'value', raw: inp.value })
      }
      if (p.point_type === 'displacement' || p.point_type === 'both') {
        jobs.push({ series_type: 'displacement', component: 'X', raw: inp.x })
        jobs.push({ series_type: 'displacement', component: 'Y', raw: inp.y })
      }
      for (const job of jobs) {
        const existing = recFor(p.id, job.series_type, job.component)
        const val = job.raw.trim() === '' ? null : Number(job.raw) * M_TO_MM
        if (!existing) {
          toCreate.push({ point: p, job: { ...job, val } })
        } else if (existing.value_mm !== val) {
          toCorrect.push({ point: p, existing, job: { ...job, val } })
        }
      }
    }

    if (toCreate.length === 0 && toCorrect.length === 0) {
      message.info('本期数据无变化，无需保存')
      return
    }

    if (toCorrect.length > 0) {
      const lines = toCorrect
        .map(
          (c) =>
            `${c.point.point_no} ${c.job.series_type}/${c.job.component}: ${(c.existing.value_mm ?? 0) / M_TO_MM}m → ${c.job.val === null ? '缺测' : c.job.val / M_TO_MM}m`,
        )
        .join('\n')
      const ok = await new Promise<boolean>((resolve) => {
        Modal.confirm({
          title: '确认更正以下数据？',
          content: (
            <pre style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 12 }}>
              {lines}
              {'\n\n原值将保留（可追溯），新值生效。'}
            </pre>
          ),
          okText: '确认更正',
          cancelText: '取消',
          onOk: () => resolve(true),
          onCancel: () => resolve(false),
        })
      })
      if (!ok) return
    }

    setSaving(true)
    let created = 0
    let corrected = 0
    for (const c of toCreate) {
      try {
        await recordApi.create(c.point.id, {
          series_type: c.job.series_type,
          component: c.job.component,
          obs_date: d,
          value_mm: c.job.val,
        })
        created += 1
      } catch (e) {
        message.error(`${c.point.point_no} ${c.job.series_type}/${c.job.component}: ${(e as Error).message}`)
      }
    }
    for (const c of toCorrect) {
      try {
        await recordApi.correct(c.existing.id, {
          value_mm: c.job.val,
          remark: `录入更正 ${(c.existing.value_mm ?? 0) / M_TO_MM}m → ${c.job.val === null ? '缺测' : c.job.val / M_TO_MM}m`,
        })
        corrected += 1
      } catch (e) {
        message.error(`${c.point.point_no} ${c.job.series_type}/${c.job.component}: ${(e as Error).message}`)
      }
    }
    setSaving(false)
    message.success(`保存完成：新增 ${created} 条${corrected ? `，更正 ${corrected} 条` : ''}`)
    void loadRecords(date)
    void loadStatus(date)
    void loadBaselines()
  }

  const setInp = (pointId: number, key: 'value' | 'x' | 'y', v: string) => {
    setInputs((prev) => {
      const cur = prev[pointId] ?? EMPTY_INPUTS
      return { ...prev, [pointId]: { ...cur, [key]: v } }
    })
  }

  // ---------------- 顺序录入（现场全键盘操作） ----------------
  //
  // 现场场景：一次测完 8~20 个点，手里拿着读数单。用表格录入需要在格子间来回
  // 找鼠标位置，效率低且容易串行。顺序模式一屏一个测点，敲完按回车直接跳下一个，
  // 全程不碰鼠标；进度条告诉操作者还剩几个，避免漏测。

  /** 顺序模式下该测点需要输入的字段（沉降只有 1 个，沉降+位移有 3 个）。 */
  const seqFields = (p: MeasurementPoint): Array<{ key: 'value' | 'x' | 'y'; label: string }> => {
    const f: Array<{ key: 'value' | 'x' | 'y'; label: string }> = []
    if (p.point_type === 'settlement' || p.point_type === 'both') f.push({ key: 'value', label: '沉降读数' })
    if (p.point_type === 'displacement' || p.point_type === 'both') {
      f.push({ key: 'x', label: '位移 X' })
      f.push({ key: 'y', label: '位移 Y' })
    }
    return f
  }

  /** 已经填了值的字段数 / 应有字段数 —— 用来显示录入进度。 */
  const seqFilled = points.reduce((n, p) => {
    const inp = inputs[p.id] ?? EMPTY_INPUTS
    return n + seqFields(p).filter((f) => inp[f.key].trim() !== '').length
  }, 0)
  const seqTotal = points.reduce((n, p) => n + seqFields(p).length, 0)

  /** 跳到第 i 个测点并聚焦它的第一个输入框。 */
  const goSeq = useCallback(
    (i: number) => {
      if (points.length === 0) return
      const next = Math.max(0, Math.min(i, points.length - 1))
      setSeqIndex(next)
      // 等 React 渲染完新测点再聚焦，否则拿到的是旧节点
      window.setTimeout(() => seqInputRef.current?.focus(), 0)
    },
    [points.length],
  )

  /** 顺序模式：把某个测点的字段整体更新（不看鼠标，直接改值）。 */
  const setSeqValue = (pointId: number, key: 'value' | 'x' | 'y', v: string) => setInp(pointId, key, v)

  useEffect(() => {
    if (entryMode === 'sequence' && points.length > 0) goSeq(seqIndex)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entryMode, seqIndex, points.length])

  /** 真正执行生成。mode 由用户在"这些日期怎么办"里选定 */
  const doGenerate = async (mode: GenerateConflictMode) => {
    if (!genStart || !genEnd) return
    setGenLoading(true)
    setGenResult(null)
    setGenChanges([])
    try {
      const r = await generateApi.generate(projectId, {
        start: genStart.format('YYYY-MM-DD'),
        end: genEnd.format('YYYY-MM-DD'),
        missing_rate: genMissing,
        on_conflict: mode,
        ...(genSeed.trim() ? { seed: Number(genSeed) } : {}),
        ...(genTotalStation
          ? {
              instrument_mode: 'total_station',
              station_centering_mm: genCentering,
              station_direction_sec: genDirection,
              station_distance_m: genDistance,
            }
          : {}),
      })
      const parts = [`生成完成：${r.points} 个测点 × ${r.days} 天，共 ${r.generated} 条`]
      if (r.instrument_mode === 'total_station') {
        const s = r.station
        parts.push(
          s
            ? `全站仪模式（对中 ${s.centering_mm}mm / 定向 ${s.direction_sec}″ / 工作距离 ${s.distance_m}m）` +
              `——每次架站的偏差由当期全部测点共同承担，同一期各点会一起偏`
            : '全站仪模式',
        )
      }
      if (r.replaced) {
        parts.push(
          mode === 'replace_all'
            ? `替换 ${r.replaced} 条${r.voided_real ? `（其中 ${r.voided_real} 条是真实数据，已作废保留、可追溯）` : ''}`
            : `替换原有自动生成数据 ${r.replaced} 条`,
        )
      } else if (mode !== 'skip') {
        // 选了替换却一条都没换 —— 必须说出来。替换前后的读数只差几 mm，
        // 不说清楚用户只会觉得"我点了替换，怎么没反应"。
        parts.push('范围内没有可替换的数据，这次只填补了空白日期')
      }
      if (r.kept_dates?.length) parts.push(`跳过已有数据的 ${r.kept_dates.length} 天`)
      parts.push('数据以 source=generated 标记，可随时清理。')
      setGenResult(parts.join('；'))
      setGenChanges(r.changed_samples ?? [])
      message.success(`已生成 ${r.generated} 条观测数据`)
      void loadRecords(date)
      void loadStatus(date)
      // 生成的是一批**跨多天**的数据 → 必须刷日期表打点，否则新生成的日期在
      // 日期选择面板里看不到（这正是原先"新建完数据、日期表没反应"的原因）
      void loadBaselines()
      setConflict(null)
      setGenOpen(false)
    } catch (e) {
      // 后端这句是"没配控制值 → 去预警规则填累计或速率"，七八十个字，
      // 默认 3 秒根本读不完（同 App.tsx 对版本提示的处理）。
      message.error({ content: (e as Error).message, duration: 10 })
    } finally {
      setGenLoading(false)
    }
  }

  /**
   * 自动生成每日观测数据（需求 #9）—— 点「开始生成」后的第一步。
   *
   * **先预检、再询问**：所选范围里如果已经有数据，不再默默跳过，
   * 而是把"哪些日期有数据、是真实数据还是演示数据"摆出来让用户决定怎么处理。
   */
  const runGenerate = async () => {
    if (!genStart || !genEnd) {
      message.warning('请选择日期范围')
      return
    }
    if (genEnd.isBefore(genStart, 'day')) {
      message.warning('结束日期不能早于开始日期')
      return
    }
    setGenLoading(true)
    try {
      const pv = await generateApi.preview(projectId, {
        start: genStart.format('YYYY-MM-DD'),
        end: genEnd.format('YYYY-MM-DD'),
      })
      if (pv.occupied_days.length === 0) {
        // 整个范围都是空白日期：没什么可问的，直接生成
        await doGenerate('skip')
        return
      }
      setConflict(pv)
      // 默认选项：只有演示数据时默认"替换"（那本来就是可以随时清理的）；
      // 一旦涉及真实数据，默认退回"跳过"——不替用户做危险决定。
      setConflictMode(pv.real_data_days.length > 0 ? 'skip' : 'replace_generated')
    } catch (e) {
      const msg = (e as Error).message
      // 这一步失败最常见的原因不是网断，而是**后端没重启**（vite 会自动热更新，
      // uvicorn 不会）—— 后端还是旧代码时，这个接口根本不存在，报 404。
      message.error(
        /\b40[34]\b/.test(msg)
          ? '后端服务是旧版本，不支持这一步 —— 请关掉黑窗口，重新运行「启动系统.bat」'
          : msg,
      )
    } finally {
      setGenLoading(false)
    }
  }

  /** 清理全部 generated 数据（演示数据） */
  const runCleanupGenerated = async () => {
    try {
      const r = await generateApi.cleanup(projectId)
      message.success(`已清理 ${r.deleted} 条自动生成数据（真实数据不受影响）`)
      void loadRecords(date)
      void loadStatus(date)
      // 清理后那些日期不该再显示打点 —— 同样要刷
      void loadBaselines()
      setGenResult(null)
      setGenChanges([])
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const statusOf = (p: MeasurementPoint): { el: JSX.Element; label: string } => {
    const recs = records.filter((r) => r.point_id === p.id)
    if (recs.length === 0) return { el: <Tag>未录入</Tag>, label: '未录入' }
    const order: Record<string, number> = { void: 0, normal: 1, missing: 2, suspect: 3 }
    let worst = 'void'
    for (const r of recs) if (order[r.status] > order[worst]) worst = r.status
    const map: Record<string, { el: JSX.Element; label: string }> = {
      void: { el: <Tag>作废</Tag>, label: '作废' },
      normal: { el: <Tag color="green">已录入</Tag>, label: '已录入' },
      missing: { el: <Tag color="orange">缺测</Tag>, label: '缺测' },
      suspect: { el: <Tag color="red">异常</Tag>, label: '异常' },
    }
    return map[worst]
  }

  /** 分量友好文案：value→沉降本身；X/Y→位移方向。 */
  const compLabel = (s: { series_type: SeriesType; component: Component }): string =>
    s.series_type === 'settlement' ? '沉降' : s.component === 'X' ? '位移X' : '位移Y'

  const warnOf = (p: MeasurementPoint): JSX.Element | null => {
    const rows = status.filter((s) => s.point_id === p.id)
    if (rows.length === 0) return null
    const levels = rows.map((r) => r.warning_level)
    if (levels.includes('alarm'))
      return <Tag color="red">{rows.filter((r) => r.warning_level === 'alarm').map(compLabel).join(',')} 报警</Tag>
    if (levels.includes('control'))
      return <Tag color="gold">{rows.filter((r) => r.warning_level === 'control').map(compLabel).join(',')} 预警</Tag>
    return <Tag color="green">正常</Tag>
  }

  const rowClassName = (p: MeasurementPoint) => {
    const rows = status.filter((s) => s.point_id === p.id)
    const levels = rows.map((r) => r.warning_level)
    const cls: string[] = []
    if (levels.includes('alarm')) cls.push('warning-row-alarm')
    else if (levels.includes('control')) cls.push('warning-row-control')
    // 从「需要关注」定位进来的那一条：加左侧主色竖条，满屏红/金行里也能一眼找到
    if (focus?.pointNo && focus.pointNo === p.point_no) cls.push('focus-row')
    return cls.join(' ')
  }

  /** 测点的全部观测项（按类型返回沉降/位移分量清单）。 */
  const pointItems = (p: MeasurementPoint): Array<{ series_type: SeriesType; component: Component; label: string }> => {
    const items: Array<{ series_type: SeriesType; component: Component; label: string }> = []
    if (p.point_type === 'settlement' || p.point_type === 'both')
      items.push({ series_type: 'settlement', component: 'value', label: '沉降' })
    if (p.point_type === 'displacement' || p.point_type === 'both') {
      items.push({ series_type: 'displacement', component: 'X', label: '位移X' })
      items.push({ series_type: 'displacement', component: 'Y', label: '位移Y' })
    }
    return items
  }

  /** 每测点初始值（V0）展示：沉降 value 与位移 X/Y 分别列出（#5：both 点显示全部观测项）。 */
  const baselinesShow = (p: MeasurementPoint): BaselineInfo[] =>
    pointItems(p)
      .map((it) => baselineFor(p.id, it.series_type, it.component))
      .filter((b): b is BaselineInfo => Boolean(b))

  const columns: ColumnsType<MeasurementPoint> = [
    { title: '测点', dataIndex: 'point_no', width: 90, render: (v) => <Typography.Text strong>{v}</Typography.Text> },
    {
      title: '类型',
      width: 100,
      render: (_, p) =>
        p.point_type === 'settlement' ? (
          <Tag color="blue">沉降</Tag>
        ) : p.point_type === 'displacement' ? (
          <Tag color="orange">水平位移</Tag>
        ) : (
          <Tag color="purple">沉降+位移</Tag>
        ),
    },
    {
      title: '初始值 V0 (m)',
      width: 220,
      render: (_, p) => {
        const bs = baselinesShow(p)
        if (bs.length === 0) return <Tag>未录入</Tag>
        return (
          <Space size={6} wrap>
            {bs.map((b) => (
              <Tag key={`${b.series_type}_${b.component}`} style={{ marginRight: 0 }}>
                {compLabel(b)} {(b.baseline_value_mm / M_TO_MM).toFixed(3)}
                {b.source === 'explicit' ? '·显式' : ''}
              </Tag>
            ))}
          </Space>
        )
      },
    },
    {
      title: '沉降读数 (m)',
      width: 130,
      render: (_, p) =>
        p.point_type === 'settlement' || p.point_type === 'both' ? (
          <Input
            placeholder="留空=缺测"
            value={inputs[p.id]?.value ?? ''}
            onChange={(e) => setInp(p.id, 'value', e.target.value)}
          />
        ) : (
          '-'
        ),
    },
    {
      title: '位移 X (m)',
      width: 120,
      render: (_, p) =>
        p.point_type === 'displacement' || p.point_type === 'both' ? (
          <Input
            placeholder="留空=缺测"
            value={inputs[p.id]?.x ?? ''}
            onChange={(e) => setInp(p.id, 'x', e.target.value)}
          />
        ) : (
          '-'
        ),
    },
    {
      title: '位移 Y (m)',
      width: 120,
      render: (_, p) =>
        p.point_type === 'displacement' || p.point_type === 'both' ? (
          <Input
            placeholder="留空=缺测"
            value={inputs[p.id]?.y ?? ''}
            onChange={(e) => setInp(p.id, 'y', e.target.value)}
          />
        ) : (
          '-'
        ),
    },
    {
      title: '录入状态',
      width: 90,
      render: (_, p) => statusOf(p).el,
    },
    {
      title: '预警状态',
      width: 90,
      render: (_, p) => warnOf(p) ?? <Tag>—</Tag>,
    },
  ]

  /**
   * #6 日期表打点：一天里叠四层信息（底色=预警 / 角标=节假日 / 蓝点=初始值日 / 底条=已录入）。
   *
   * 「今天有没有记录」优先信 `recordDates`（项目全部观测日），
   * 拿不到时才回退到"当前选中日期 + 当前已加载的记录"——
   * 原先写成 `recordDates.includes(key) || date === key`（无条件为真），
   * 结果是**没数据的日期也带绿条**，把"没录上"伪装成了"已录入"。
   */
  const dateCellRender = (current: Dayjs) => {
    const key = current.format('YYYY-MM-DD')
    const isAlarm = alarmDates.includes(key)
    const isControl = controlDates.includes(key)
    const hasRecord = recordDates.includes(key) || (key === date.format('YYYY-MM-DD') && records.length > 0)
    // 已录入标记的条色：报警红 / 预警金 / 正常绿。
    // 注意与 levelColor() 的区别 —— 这里 normal 也要着色，而 levelColor 的 normal
    // 表示「不着色」（记录页的预警列用不到绿色）。
    const barColor = isAlarm
      ? WARNING_LEVEL.alarm.fg
      : isControl
        ? WARNING_LEVEL.control.fg
        : WARNING_LEVEL.normal.fg
    return (
      <CalendarCell
        date={current}
        // 与表格预警行（.warning-row-*）取自同一份定义，避免日历与表格两套底色
        bg={levelBg(isAlarm ? 'alarm' : isControl ? 'control' : undefined)}
        barColor={hasRecord ? barColor : undefined}
        dot={baselineDates.includes(key)}
      />
    )
  }

  /** #7 原始数据按测点聚合：每个测点一张卡，沉降+位移 V0 显示在一起。 */
  const groupedBaselines = points
    .map((p) => ({ point: p, items: baselinesShow(p) }))
    .filter((g) => g.items.length > 0)

  const baselineCards = groupedBaselines.map((g) => (
    <Card key={g.point.id} size="small" style={{ width: 260 }}>
      <Typography.Text strong>{g.point.point_no}</Typography.Text>
      <Tag style={{ marginLeft: 6 }} color={POINT_TYPE_TAG_COLOR[g.point.point_type]}>
        {POINT_TYPE_LABEL[g.point.point_type]}
      </Tag>
      <div style={{ marginTop: 6 }}>
        {g.items.map((b) => (
          <div key={`${b.series_type}_${b.component}`} style={{ fontSize: 13, lineHeight: '22px' }}>
            <Typography.Text type="secondary" style={{ display: 'inline-block', width: 56 }}>
              {compLabel(b)}
            </Typography.Text>
            <Typography.Text strong>{(b.baseline_value_mm / M_TO_MM).toFixed(3)} m</Typography.Text>
            <Tag color={b.source === 'explicit' ? 'cyan' : 'geekblue'} style={{ marginLeft: 8, fontSize: 10 }}>
              {b.source === 'explicit' ? '显式' : '首期'}
            </Tag>
            {b.baseline_date && (
              <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 11 }}>
                {b.baseline_date}
              </Typography.Text>
            )}
          </div>
        ))}
      </div>
    </Card>
  ))

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Button onClick={onBack}>← 返回</Button>
        <Alert
          type="info"
          showIcon
          style={{ flex: 1 }}
          message="观测录入（读值单位：米）"
          description="选好日期 → 在表格里填当天读数 → 点「保存本期观测」。初始值只在第一次填一次；留空表示当天没测（缺测）。"
        />
      </Space>

      {focusNote && (
        <Alert
          type="info"
          showIcon
          closable
          onClose={() => setFocusNote(null)}
          message={`已定位到 ${date.format('YYYY-MM-DD')}`}
          description={`${focusNote}。表格中已用左侧蓝色竖条标出该测点；若读数有误可直接修改，改完点「保存本期观测」。`}
        />
      )}

      {/* 生成结果常驻在页面上（弹窗一关就看不到结果了，见组件注释） */}
      <GenerateOutcome
        result={genResult}
        changes={genChanges}
        onClose={() => {
          setGenResult(null)
          setGenChanges([])
        }}
      />

      {points.length === 0 && (
        <Alert
          type="warning"
          showIcon
          message="这个项目还没有测点"
          description="请先点上方的「① 测点与初始值」创建测点（例如 JJC1~JJC8），再回来录入数据。"
        />
      )}

      {baselineCards.length > 0 && (
        <Card
          size="small"
          title={<Typography.Text strong>原始数据（初始值 V0 · 只计一次，后续各期以此为准）</Typography.Text>}
          extra={
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              首期=首次读数自动作为 V0 · 显式=手工指定过初始值
            </Typography.Text>
          }
        >
          <Space wrap>
            {baselineCards}
          </Space>
        </Card>
      )}

      <Space wrap>
        <DatePicker
          value={date}
          onChange={(d) => d && setDate(d)}
          allowClear={false}
          disabled={points.length === 0}
          // 打开面板时拉一次最新打点 —— 兜底：任何"写完数据忘了刷新"的路径
          // 都会在这里被纠正，不必指望每个写操作都记得调 loadBaselines()
          onOpenChange={(open) => {
            if (open) void loadBaselines()
          }}
          cellRender={(current, info) =>
            info.type === 'date' ? dateCellRender(current as Dayjs) : (current as Dayjs).date()
          }
          panelRender={(panel) => (
            <>
              {panel}
              <CalendarLegend extra={['蓝点=初始值日', '底部横条=已录入（红=报警 / 金=预警 / 绿=正常）']} />
            </>
          )}
        />
        <Button type="primary" loading={saving} disabled={points.length === 0} onClick={() => void save()}>
          保存本期观测
        </Button>
        <Button icon={<ThunderboltOutlined />} disabled={points.length === 0} onClick={() => setGenOpen(true)}>
          自动生成数据
        </Button>
        <Segmented
          value={entryMode}
          onChange={(v) => setEntryMode(v as 'table' | 'sequence')}
          disabled={points.length === 0}
          options={[
            { label: '总览表格', value: 'table' },
            { label: '顺序录入', value: 'sequence' },
          ]}
        />
      </Space>

      {entryMode === 'sequence' && points.length > 0 && (
        <SequenceEntry
          points={points}
          index={seqIndex}
          inputs={inputs}
          filled={seqFilled}
          total={seqTotal}
          fieldsOf={seqFields}
          inputRef={seqInputRef}
          existingOf={(pid, st, comp) => recFor(pid, st, comp)}
          onGoto={goSeq}
          onSet={setSeqValue}
          onSave={() => void save()}
          saving={saving}
        />
      )}

      {entryMode === 'table' && (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={points}
          pagination={false}
          size="small"
          rowClassName={(p) => rowClassName(p)}
        />
      )}
      {/* 预警行底色（报警红 / 预警金）已统一收进 styles/global.css，此处不再内联 <style> */}

      {/* 数据自动生成器（需求 #9） */}
      <Modal
        title="自动生成每日观测数据"
        open={genOpen}
        onCancel={() => setGenOpen(false)}
        onOk={() => void runGenerate()}
        okText="开始生成"
        okButtonProps={{ loading: genLoading }}
        cancelText="关闭"
        width={560}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="数据范围说明"
          description="从项目已有 V0（初始值/首期读数）出发，按预警规则的累计与速率控制值，自动生成日期范围内每天的读数——累计变化始终控制在规范限值内、不触发报警。所选日期里如果已经有数据，下一步会问你该怎么处理。"
        />
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap>
            <span>
              开始：
              <DatePicker
                value={genStart}
                onChange={(d) => d && setGenStart(d)}
                style={{ marginLeft: 6 }}
                {...plainCalendarPanel}
              />
            </span>
            <span>
              结束：
              <DatePicker
                value={genEnd}
                onChange={(d) => d && setGenEnd(d)}
                style={{ marginLeft: 6 }}
                {...plainCalendarPanel}
              />
            </span>
          </Space>
          <Space wrap>
            <span style={{ width: 150 }}>每日缺测概率：{genMissing * 100}%</span>
            <Slider
              style={{ width: 260 }}
              min={0}
              max={50}
              value={genMissing * 100}
              onChange={(v) => setGenMissing(v / 100)}
              marks={{ 0: '0%', 25: '25%', 50: '50%' }}
            />
          </Space>
          <Space>
            <span>随机种子（可选，固定可复现）：</span>
            <Input
              style={{ width: 160 }}
              placeholder="如 20260801"
              value={genSeed}
              onChange={(e) => setGenSeed(e.target.value)}
            />
          </Space>

          {/* 全站仪观测模式：默认噪声是"每点各跳各的"，这个模式把"每次架站"的
              公共偏差加进去（当期所有点一起偏）—— 那才是全站仪数据的样子。 */}
          <div>
            <Space align="center">
              <Switch
                size="small"
                checked={genTotalStation}
                onChange={setGenTotalStation}
                id="gen-total-station"
              />
              <Typography.Text strong>全站仪观测模式</Typography.Text>
            </Space>
            <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0', fontSize: 12 }}>
              每次架站的对中、定向偏差是由当期全部测点共同承担的，同一期各点会一起偏一点；
              不开这个模式时，各点只是各跳各的。只影响位移（沉降是水准仪测的，不受影响）。
            </Typography.Paragraph>
            {genTotalStation && (
              <Space wrap style={{ marginTop: 8 }}>
                <span>
                  对中误差
                  <InputNumber
                    size="small"
                    style={{ width: 76, marginLeft: 6 }}
                    min={0}
                    max={20}
                    step={0.5}
                    value={genCentering}
                    onChange={(v) => setGenCentering(v ?? 0)}
                    addonAfter="mm"
                  />
                </span>
                <span>
                  定向误差
                  <InputNumber
                    size="small"
                    style={{ width: 76, marginLeft: 6 }}
                    min={0}
                    max={60}
                    step={1}
                    value={genDirection}
                    onChange={(v) => setGenDirection(v ?? 0)}
                    addonAfter="″"
                  />
                </span>
                <span>
                  工作距离
                  <InputNumber
                    size="small"
                    style={{ width: 76, marginLeft: 6 }}
                    min={1}
                    max={2000}
                    step={5}
                    value={genDistance}
                    onChange={(v) => setGenDistance(v ?? 30)}
                    addonAfter="m"
                  />
                </span>
              </Space>
            )}
            {genTotalStation && (
              <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0', fontSize: 12 }}>
                对中误差填你现场架站的极限偏差（三脚架+光学对中器一般 2mm，有强制对中装置可填 0.5）；
                定向误差是后视定向的中误差（正倒镜取中 2~3″，单盘或短后视 5″ 很常见）；
                工作距离只用于把角度误差折算成毫米。默认值按普通现场水平给，按你的实际改。
              </Typography.Paragraph>
            )}
          </div>
          {/* 生成结果**不放这里** —— 生成成功后两个弹窗都会关闭，
              放在弹窗里的结果用户一关就再也看不到。结果改到页面上常驻显示，
              见 <GenerateOutcome>。 */}
          <Popconfirm
            title="确定清理该项目的全部自动生成数据？"
            description="真实录入/导入的数据不受影响"
            onConfirm={() => void runCleanupGenerated()}
          >
            <Button size="small" danger>
              清理自动生成的数据
            </Button>
          </Popconfirm>
        </Space>
      </Modal>

      {/* 生成前的冲突询问：所选范围里已有数据时怎么处理。
          这一步是刻意加的 —— 原先遇到已有日期静默跳过，用户不知道哪些日期没生成。 */}
      <Modal
        open={!!conflict}
        title="所选日期里，有些已经有数据了"
        onCancel={() => setConflict(null)}
        onOk={() => void doGenerate(conflictMode)}
        okText={conflictMode === 'skip' ? '跳过它们，只填空白的日期' : '按我的选择生成'}
        okButtonProps={{ loading: genLoading, danger: conflictMode === 'replace_all' }}
        cancelText="取消"
        width={620}
      >
        {conflict && <ConflictDetail pv={conflict} mode={conflictMode} onModeChange={setConflictMode} />}
      </Modal>
    </Space>
  )
}

/**
 * 生成结果横幅：摘要 + 「旧值 → 新值」对照。
 *
 * 【为什么必须放在页面上、而不是生成弹窗里】
 * 生成成功后代码会关掉配置弹窗和冲突弹窗。结果若渲染在弹窗内，用户一关就再也
 * 看不到了 —— 只剩下一个 3 秒后消失的 toast。
 * 而替换前后的读数只差 1~3mm，记录数、日期、测点数**完全不变**，
 * 没有这份清单，用户只会得出"我点了替换，怎么一点反应都没有"的结论。
 */
function GenerateOutcome({
  result,
  changes,
  onClose,
}: {
  result: string | null
  changes: GenerateChangeSample[]
  onClose: () => void
}) {
  if (!result) return null
  return (
    <Card
      size="small"
      title="上次生成的结果"
      extra={
        <Button size="small" type="text" onClick={onClose}>
          收起
        </Button>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size={10}>
        <Alert type="success" showIcon message={result} />
        {changes.length > 0 && (
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              被替换掉的记录（原读数 → 新读数，单位 m）。读数变化本来就只有几毫米，
              对照这几条就能确认数据确实换过了：
            </Typography.Text>
            <Table<GenerateChangeSample>
              size="small"
              style={{ marginTop: 6 }}
              rowKey={(r) => `${r.point_no}-${r.series_type}-${r.component}-${r.obs_date}`}
              dataSource={changes}
              pagination={false}
              columns={[
                { title: '测点', dataIndex: 'point_no', width: 100 },
                {
                  title: '项目',
                  width: 110,
                  render: (_, r) =>
                    r.series_type === 'settlement' ? '沉降' : `水平位移 ${r.component}`,
                },
                { title: '日期', dataIndex: 'obs_date', width: 110 },
                {
                  title: '原读数',
                  width: 120,
                  render: (_, r) => (
                    <Typography.Text delete type="secondary">
                      {r.old_value_mm === null ? '缺测' : (r.old_value_mm / 1000).toFixed(3)}
                    </Typography.Text>
                  ),
                },
                {
                  title: '新读数',
                  width: 120,
                  render: (_, r) => (
                    <Typography.Text strong>
                      {r.new_value_mm === null ? '缺测' : (r.new_value_mm / 1000).toFixed(3)}
                    </Typography.Text>
                  ),
                },
                {
                  title: '原数据来源',
                  width: 150,
                  render: (_, r) => (
                    <Tag color={r.action === 'voided' ? 'orange' : undefined}>
                      {r.action === 'voided'
                        ? '你的真实数据（已作废保留）'
                        : r.old_source === 'excel'
                          ? 'Excel 导入（演示）'
                          : r.old_source === 'manual'
                            ? '手工录入（演示）'
                            : '自动生成'}
                    </Tag>
                  ),
                },
              ]}
            />
          </div>
        )}
      </Space>
    </Card>
  )
}

/** 把日期列表压缩成「前 8 个…等 N 天」，避免 30 天挤满弹窗 */
function compactDates(dates: string[]): string {
  if (dates.length <= 8) return dates.join('、')
  return `${dates.slice(0, 8).join('、')} … 等 ${dates.length} 天`
}

/**
 * 冲突明细 + 处理方式选择。
 *
 * 三类数据区别对待（这是本功能的核心）：
 * - 自动生成：本来就是演示数据，可以整批删掉重来
 * - 真实数据（Excel 导入 / 手工录入）：**只作废、不删除**，原值永久保留可追溯
 */
function ConflictDetail({
  pv,
  mode,
  onModeChange,
}: {
  pv: GeneratePreview
  mode: GenerateConflictMode
  onModeChange: (m: GenerateConflictMode) => void
}) {
  const genDays = pv.generated_only_days
  const realDays = pv.real_data_days
  const realRecords = pv.occupied_days
    .filter((d) => d.kind === 'real' || d.kind === 'mixed')
    .reduce((s, d) => s + d.records, 0)

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <Typography.Text>
        {pv.start_date} ~ {pv.end_date} 共 {pv.total_days} 天，其中{' '}
        <b>{pv.occupied_days.length}</b> 天已经有数据、{pv.empty_days} 天是空的。
      </Typography.Text>

      <div style={{ fontSize: 12, lineHeight: '20px' }}>
        {genDays.length > 0 && (
          <div>
            <Typography.Text type="secondary">自动生成（{genDays.length} 天）：</Typography.Text>
            {compactDates(genDays)}
          </div>
        )}
        {realDays.length > 0 && (
          <div>
            <Typography.Text type="secondary">真实数据（{realDays.length} 天）：</Typography.Text>
            {compactDates(realDays)}
          </div>
        )}
      </div>

      <div>
        <Typography.Text strong>这些日期的数据怎么处理？</Typography.Text>
      </div>

      <Radio.Group value={mode} onChange={(e) => onModeChange(e.target.value)}>
        <Space direction="vertical" size={6}>
          <Radio value="skip">
            跳过它们，只填空白的那 {pv.empty_days} 天
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              （不动任何已有数据）
            </Typography.Text>
          </Radio>
          {genDays.length > 0 && (
            <Radio value="replace_generated">
              替换掉自动生成的那 {genDays.length} 天
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                （这些本来就是演示数据，删掉重来没有影响
                {realDays.length > 0 ? '；真实数据仍然跳过' : ''}）
              </Typography.Text>
            </Radio>
          )}
          {realDays.length > 0 && (
            <Radio value="replace_all">
              连真实数据一起替换（{realDays.length} 天 / 约 {realRecords} 条）
            </Radio>
          )}
        </Space>
      </Radio.Group>

      {mode === 'replace_all' && (
        <Alert
          type="warning"
          showIcon
          message="这些天里有你导入或手工录入的真实读数，会被这次生成的数据顶替"
          description="原来的读数不会被删除，只会标记为「作废」——在数据里仍能查到原值。但计算用的就是新生成的数据了，日报里的数字也会跟着变。真要这么做，建议先去「数据备份」做一次快照。"
        />
      )}
      {mode === 'replace_generated' && genDays.length > 0 && (
        <Alert
          type="info"
          showIcon
          message={`只会重做那 ${genDays.length} 天的演示数据，真实数据一条都不会动`}
        />
      )}
    </Space>
  )
}
