import axios from 'axios'

/**
 * 超时分级 —— 不再用一个数包打天下。
 *
 * 起因（2026-10-09 用户报「timeout of 15000ms exceeded」）：
 * 导出 24 期日报 —— 直连后端只要 8.9 秒（看着富余），但**经 vite 代理**要走 15.2 秒
 * （28 MB 的 ZIP，浏览器走的正是这条路），刚好越过 15 秒那条线。
 * 日志里那次批量导出被点了 5 遍、**每一次后端都算完了（全 200）**——
 * 前端在 15 秒时把请求掐了，用户什么也没拿到，还以为是系统坏了。
 *
 * 分档的道理：快接口应该快点失败（真卡住了就得报错，别让人干等），
 * 慢接口得给够时间。按"这个接口通常要多久"分，不把慢活的风险摊到全部请求上。
 */
export const FAST_TIMEOUT = 15_000
/** 生成 / 导入 / 导出 / 备份这类重活：几十秒到几分钟都属正常 */
export const LONG_TIMEOUT = 5 * 60_000

declare module 'axios' {
  export interface AxiosRequestConfig {
    /** 超时后给用户看的那句话（不写就走通用提示）。见 timeoutMessage()。 */
    timeoutHint?: string
  }
}

/**
 * 把「超时」翻译成一句人话，并带上"这次等了多久、下一步怎么办"。
 *
 * 之前直接把 axios 的英文原文抛给用户（`timeout of 15000ms exceeded`），
 * 现场没人能看懂它是什么意思。超时不是错误码，是**一件要解释的事**。
 */
function timeoutMessage(err: {
  code?: string
  message?: string
  config?: { timeout?: number; timeoutHint?: string }
}): string | null {
  const isTimeout = err?.code === 'ECONNABORTED' || /timeout/i.test(String(err?.message ?? ''))
  if (!isTimeout) return null
  const sec = Math.round(Number(err?.config?.timeout ?? FAST_TIMEOUT) / 1000)
  const hint = err?.config?.timeoutHint
  if (hint) return `${hint}（已等待 ${sec} 秒仍未返回，本次已中断）`
  return `等待超过 ${sec} 秒仍未返回，已中断。数据量较大时，缩小范围分几次做会更快。`
}

// 统一 API 客户端：走 Vite 代理 → FastAPI(8000)
export const http = axios.create({
  baseURL: '/api/v1',
  timeout: FAST_TIMEOUT,
})

http.interceptors.response.use(
  (res) => res,
  async (err) => {
    let detail = err.response?.data?.detail
    if (detail == null && err.response?.data instanceof Blob) {
      // **blob 下载失败时后端给的仍然是 JSON**。不把它读出来，用户看到的就只有
      // "Request failed with status code 422" —— 而 422 里装的是"这个项目没配控制值，
      // 日报不能生成"这种必须让人读懂的话（2026-10-14 的导出拦截全靠这句话）。
      try {
        detail = JSON.parse(await err.response.data.text())?.detail
      } catch {
        // 不是 JSON（网关给的 HTML 之类）→ 当作没有 detail，走下面的兜底
      }
    }
    if (Array.isArray(detail)) {
      // Pydantic 校验错误：挑出第一条 msg 给人看。整串丢掉只留"参数校验失败"，
      // 等于把后端唯一一句解释吞了 —— 用户只能猜是哪个字段、为什么不行。
      const first = detail.find((d) => typeof d?.msg === 'string')
      const msg = first ? String(first.msg).replace(/^Value error,\s*/, '') : ''
      const loc = Array.isArray(first?.loc) ? first.loc.filter((x: unknown) => x !== 'body').join('.') : ''
      return Promise.reject(new Error(msg ? `${msg}${loc ? `（字段 ${loc}）` : ''}` : '参数校验失败'))
    }
    if (detail != null) {
      // 后端已经把话说清楚了（HTTPException 的 detail），原样透传。
      // 非字符串的 detail 不能直接塞给 Error（会变成 "[object Object]"）。
      return Promise.reject(new Error(typeof detail === 'string' ? detail : JSON.stringify(detail)))
    }
    const timeout = timeoutMessage(err)
    if (timeout) return Promise.reject(new Error(timeout))
    return Promise.reject(new Error(err.message || '请求失败'))
  },
)

export interface Project {
  id: number
  code: string
  name: string
  location: string | null
  sign_config: Record<string, unknown>
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface ProjectInput {
  code: string
  name: string
  location?: string | null
  sign_config?: Record<string, unknown>
}

/** 一键示例项目的创建结果 */
export interface SampleProjectResult {
  project_id: number
  project_code: string
  project_name: string
  preset: string | null
  points_created: number
  records_generated: number
  start_date: string
  end_date: string
  periods: number
  warning_events: number
  note: string
}

/** 项目「一句结论」速览 */
export interface ProjectBrief {
  project_id: number
  headline: string | null
  severity: 'none' | WarningLevel
  obs_date: string | null
  detail: WarningStatusRow | null
  counts: { alarm: number; control: number; normal: number }
  points_with_data?: number
}

/** 项目关注项（数据质量 + 是否该观测） */
export interface ProjectAttention {
  project_id: number
  project_code: string
  project_name: string
  suspects: Array<{
    point_no: string
    series_type: SeriesType
    component: Component
    obs_date: string
    delta_mm: number
    rate_mm_per_day: number | null
    note: string
  }>
  suspect_count: number
  missing_count: number
  last_obs_date: string | null
  obs_days: number
  stale_days: number | null
  needs_observation: boolean
}

export const projectApi = {
  list: (includeInactive = true) =>
    http.get<Project[]>('/projects', { params: { include_inactive: includeInactive } }).then((r) => r.data),
  create: (data: ProjectInput) => http.post<Project>('/projects', data).then((r) => r.data),
  /** 一键创建示例项目（项目 + 规则 + 测点 + 演示数据 + 预警事件） */
  createSample: (params?: { periods?: number; point_count?: number; start_date?: string }) =>
    http.post<SampleProjectResult>('/projects/sample', null, { params, timeout: LONG_TIMEOUT }).then((r) => r.data),
  update: (id: number, data: Partial<ProjectInput & { is_active?: boolean }>) =>
    http.put<Project>(`/projects/${id}`, data).then((r) => r.data),
  archive: (id: number) => http.delete<Project>(`/projects/${id}`).then((r) => r.data),
  /** 物理删除（force=项目编号，删除前自动备份） */
  hardDelete: (id: number, force: string) =>
    http
      .delete<{
        deleted: boolean
        /** 被别的项目共用、因此**没跟着删**的母版名（模板独立化之后才有的字段） */
        templates_kept?: string[]
      }>(`/projects/${id}/hard`, { params: { force }, timeout: LONG_TIMEOUT })
      .then((r) => r.data),
  dataCount: (id: number) =>
    http.get<{ points: number; records: number; has_data: boolean }>(`/projects/${id}/data-count`).then((r) => r.data),
  /** 全部项目的一句话结论（列表页卡片用） */
  briefs: () => http.get<ProjectBrief[]>('/project-briefs').then((r) => r.data),
  /** 全部项目的「需要关注」汇总（只读，不触发重算） */
  attention: () => http.get<ProjectAttention[]>('/project-attention').then((r) => r.data),
}

export type PointType = 'settlement' | 'displacement' | 'both'

/** 平面布置图里的一个点（坐标单位 m，大地坐标） */
export interface LayoutPoint {
  point_no: string
  point_type: PointType
  x_m: number
  y_m: number
}

/** 相邻两点（按点号顺序连） */
export interface LayoutSegment {
  from: string
  to: string
  length_m: number
}

/** 测点平面布置 —— 看一个基坑里各点的位置关系 */
export interface PointLayout {
  project_id: number
  points: LayoutPoint[]
  /** 画不上图的点 + 原因（绝不静默少点） */
  missing: Array<{ point_no: string; why: string }>
  segments: LayoutSegment[]
  /** 所有点的包络范围；没有点时是 null */
  bbox: {
    min_x_m: number
    max_x_m: number
    min_y_m: number
    max_y_m: number
    width_m: number
    height_m: number
  } | null
  placed_count: number
  missing_count: number
}

export interface MeasurementPoint {
  id: number
  uuid: string
  project_id: number
  point_no: string
  point_type: PointType
  initial_value_mm: number | null
  location_desc: string | null
  status: 'active' | 'inactive'
  created_at: string
  updated_at: string
}

export interface PointInput {
  point_no: string
  point_type: PointType
  initial_value_mm?: number | null
  location_desc?: string | null
}

export const pointApi = {
  /** 平面布置：每个测点的初始位置（水平位移 X/Y 首期读数，已换算成米）
   *
   *  为什么坐标在读数里：测点表只存高程初值，没有平面坐标；
   *  而位移的 X/Y 读数本身就是大地坐标（3412987154 mm → 3412987.154 m）。
   */
  layout: (projectId: number) =>
    http.get<PointLayout>(`/projects/${projectId}/points/layout`).then((r) => r.data),
  list: (projectId: number, includeInactive = true) =>
    http
      .get<MeasurementPoint[]>(`/projects/${projectId}/points`, { params: { include_inactive: includeInactive } })
      .then((r) => r.data),
  create: (projectId: number, data: PointInput) =>
    http.post<MeasurementPoint>(`/projects/${projectId}/points`, data).then((r) => r.data),
  update: (id: number, data: Partial<PointInput & { status: 'active' | 'inactive' }>) =>
    http.put<MeasurementPoint>(`/points/${id}`, data).then((r) => r.data),
  archive: (id: number) => http.delete<MeasurementPoint>(`/points/${id}`).then((r) => r.data),
  /** 物理删除（force=测点编号，删除前自动备份） */
  hardDelete: (id: number, force: string) =>
    http
      .delete<{ deleted: boolean }>(`/points/${id}/hard`, { params: { force }, timeout: LONG_TIMEOUT })
      .then((r) => r.data),
  /** 批量新建 · 预览校验（不落库） */
  batchPreview: (projectId: number, items: PointInput[]) =>
    http
      .post<{
        valid: Array<{ index: number; point_no: string; message: string; point: PointInput | null }>
        conflict: Array<{ index: number; point_no: string; message: string; reason?: string }>
        error: Array<{ index: number; point_no: string; message: string; reason?: string }>
      }>(`/projects/${projectId}/points/batch-preview`, { items })
      .then((r) => r.data),
  /** 批量新建 · 落库（仅收 valid 行） */
  batchCreate: (projectId: number, items: PointInput[]) =>
    http
      .post<{
        created: number
        created_rows: Array<{ index: number; point_no: string; success: boolean; message: string; point_id: number | null }>
        skipped: Array<{ index: number; point_no: string; success: boolean; message: string; point_id: number | null }>
        failed: Array<{ index: number; point_no: string; success: boolean; message: string; point_id: number | null }>
      }>(`/projects/${projectId}/points/batch`, { items })
      .then((r) => r.data),
}

export type SeriesType = 'settlement' | 'displacement'
export type Component = 'value' | 'X' | 'Y'
export type RecordStatus = 'normal' | 'missing' | 'suspect' | 'void'
/** 预警等级：正常 / 预警（触发控制值）/ 报警。配色见 theme/tokens.ts 的 WARNING_LEVEL。 */
export type WarningLevel = 'normal' | 'control' | 'alarm'

export interface ObservationRecord {
  id: number
  point_id: number
  series_type: SeriesType
  component: Component
  obs_date: string
  value_mm: number | null
  status: RecordStatus
  source: string
  /** 非空 = 已被该记录更正替代（原值永久保留，但不再生效） */
  superseded_by_id?: number | null
  remark: string | null
  created_at: string
  updated_at: string
}

export interface RecordInput {
  series_type: SeriesType
  component: Component
  obs_date: string
  value_mm?: number | null
  remark?: string | null
}

export const recordApi = {
  list: (pointId: number, params?: { series_type?: SeriesType; from_date?: string; to_date?: string; active_only?: boolean }) =>
    http.get<ObservationRecord[]>(`/points/${pointId}/records`, { params }).then((r) => r.data),
  create: (pointId: number, data: RecordInput) =>
    http.post<ObservationRecord>(`/points/${pointId}/records`, data).then((r) => r.data),
  update: (id: number, data: { remark?: string | null; status?: RecordStatus }) =>
    http.put<ObservationRecord>(`/records/${id}`, data).then((r) => r.data),
  correct: (id: number, data: { value_mm: number | null; remark?: string | null }) =>
    http.post<ObservationRecord>(`/records/${id}/correct`, data).then((r) => r.data),
  void: (id: number) => http.delete<ObservationRecord>(`/records/${id}`).then((r) => r.data),
}

// ---------- 预警规则 ----------

export type Indicator = 'settlement' | 'displacement'
export type RuleLevel = 'control' | 'alarm'

export interface WarningRule {
  id: number
  project_id: number | null
  indicator: Indicator
  level: RuleLevel
  threshold_cumulative_mm: number | null
  /** 本次变化量 ΔV 上限(mm)：不看观测间隔，隔几天测都按这一个数卡 */
  threshold_delta_mm: number | null
  threshold_rate_mm_per_day: number | null
  is_active: boolean
  source_note: string | null
  created_at: string
  updated_at: string
}

export interface RuleInput {
  indicator: Indicator
  level: RuleLevel
  threshold_cumulative_mm?: number | null
  threshold_delta_mm?: number | null
  threshold_rate_mm_per_day?: number | null
  is_active?: boolean
  source_note?: string | null
}

export interface StandardPreset {
  key: string
  name: string
  description: string
  rules: RuleInput[]
}

/** 一套预警规则 + 它作用于哪些项目（规则独立于项目的载体）。 */
export interface WarningRuleSetInfo {
  id: number
  name: string
  source_note: string | null
  /** 通用规则：没绑任何规则的项目由它兜底 */
  is_global: boolean
  project_ids: number[]
  project_names: string[]
  rules: WarningRule[]
  created_at: string
  updated_at: string
}

/** 某项目当前用的是哪套规则（>1 表示共用，改了会影响别的项目）。 */
export interface ProjectRuleSetInfo {
  set_id: number | null
  name: string | null
  is_global: boolean
  shared_project_count: number
}

export const warningRuleApi = {
  standards: () => http.get<StandardPreset[]>('/warning-rules/standards').then((r) => r.data),
  /** 该项目**当前生效**的规则（绑定套 / 通用套） */
  list: (projectId: number) =>
    http.get<WarningRule[]>(`/projects/${projectId}/warning-rules`).then((r) => r.data),
  /** 该项目用的是哪套规则、被几个项目共用 */
  projectSet: (projectId: number) =>
    http.get<ProjectRuleSetInfo>(`/projects/${projectId}/warning-rules/set`).then((r) => r.data),
  /** 规则库：全部规则（含通用、含暂时没绑项目的） */
  listSets: () => http.get<WarningRuleSetInfo[]>('/warning-rule-sets').then((r) => r.data),
  createSet: (body: {
    name: string
    rules: RuleInput[]
    source_note?: string | null
    project_ids?: number[]
    is_global?: boolean
  }) => http.post<WarningRuleSetInfo>('/warning-rule-sets', body).then((r) => r.data),
  updateSet: (
    setId: number,
    body: { name?: string; source_note?: string | null; rules?: RuleInput[] },
  ) => http.put<WarningRuleSetInfo>(`/warning-rule-sets/${setId}`, body).then((r) => r.data),
  /** 设置这套规则作用于哪些项目（多选 + 通用开关） */
  setSetProjects: (setId: number, body: { project_ids?: number[]; is_global?: boolean }) =>
    http.put<WarningRuleSetInfo>(`/warning-rule-sets/${setId}/projects`, body).then((r) => r.data),
  deleteSet: (setId: number) =>
    http.delete<{ deleted: boolean }>(`/warning-rule-sets/${setId}`).then((r) => r.data),
}

// ---------- 计算 / 预警状态 ----------

export interface ComputedRow {
  obs_date: string
  is_missing: boolean
  value_mm: number | null
  delta_mm: number | null
  cumulative_mm: number | null
  rate_mm_per_day: number | null
  prev_valid_date: string | null
  skipped_days: number | null
  warning_level: WarningLevel
  triggers: string[]
}

export interface WarningStatusRow {
  point_id: number
  point_no: string
  series_type: SeriesType
  component: Component
  obs_date: string | null
  value_mm: number | null
  delta_mm: number | null
  cumulative_mm: number | null
  rate_mm_per_day: number | null
  warning_level: WarningLevel
  triggers: string[]
}

export interface ProjectWarningSummary {
  project_id: number
  project_code: string
  project_name: string
  has_alarm: boolean
  has_control: boolean
  alarm_points: WarningStatusRow[]
  control_points: WarningStatusRow[]
  total_points: number
}

export const calcApi = {
  warningStatus: (projectId: number, obsDate?: string) =>
    http
      .get<WarningStatusRow[]>(`/projects/${projectId}/warning-status`, {
        params: obsDate ? { obs_date: obsDate } : undefined,
      })
      .then((r) => r.data),
  warningSummary: () => http.get<ProjectWarningSummary[]>('/warning-summary').then((r) => r.data),
}

// ---------- 逐期对比 ----------

export interface ComparisonRow {
  period: number
  obs_date: string
  compare_to_date: string | null
  compare_kind: 'first' | 'adjacent' | 'skip' | 'missing'
  /** 与对比基准相隔的天数 —— 速率的除数。缺测跨期时 >1，界面要显示出来让人看懂速率怎么来的 */
  skipped_days: number | null
  value_mm: number | null
  prev_value_mm: number | null
  delta_mm: number | null
  cumulative_mm: number | null
  rate_mm_per_day: number | null
  direction: string
  direction_label: string
  warning_level: WarningLevel
  triggers: string[]
}

export interface ComparisonSeries {
  point_id: number
  point_no: string
  series_type: SeriesType
  component: Component
  unit_note: string
  periods: ComparisonRow[]
}

export const comparisonApi = {
  project: (projectId: number) =>
    http
      .get<ComparisonSeries[]>(`/projects/${projectId}/comparison`, { timeout: LONG_TIMEOUT })
      .then((r) => r.data),
}

// ---------- 初始值（V0）独立处理 ----------

export interface BaselineInfo {
  point_id: number
  point_no: string
  series_type: SeriesType
  component: Component
  baseline_value_mm: number
  baseline_date: string | null
  source: 'explicit' | 'first_observation'
  note: string
}

export interface RecordDatesInfo {
  /** 全部观测日期（**含缺测日**：那天录了记录、只是值为空）。记录页的"已录入"用它。 */
  dates: string[]
  /** 只含**有实际读数**的日期。导出日报用它 —— 缺测日导出来是空表。 */
  dates_with_values?: string[]
  baseline_dates: string[]
  alarm_dates: string[]
  control_dates: string[]
}

export const baselineApi = {
  list: (projectId: number) =>
    http.get<BaselineInfo[]>(`/projects/${projectId}/baselines`).then((r) => r.data),
  dates: (projectId: number) =>
    http.get<RecordDatesInfo>(`/projects/${projectId}/record-dates`).then((r) => r.data),
}

// ---------- Excel 导入（九步流程） ----------

export interface ImportSheetInfo {
  sheet_name: string
  series_type: SeriesType
  /** 这张表的形态：ledger=逐期观测台账 / point_list=「点号+坐标/高程」点位清单。
   *  点位清单该走「批量建项目」，前端据此提醒用户别在这个入口继续导。 */
  kind?: 'ledger' | 'point_list'
  obs_date: string | null
  /** 表里原本写的是哪天（用户改过日期后，用它显示"表里写的是 X"） */
  original_obs_date?: string | null
  row_count: number
  column_map: Record<string, number>
  /** 该表还没有观测日期（表里没写、用户也没指定），不指定这些行无法入库 */
  needs_date?: boolean
}

/** 用户为某张数据表指定的观测日期（改完按新日期重新核对，冲突判定会重算） */
export interface SheetDateOverride {
  sheet_name: string
  obs_date: string | null
}

export interface ImportPreviewRow {
  point_no: string
  series_type: SeriesType
  component: Component
  obs_date: string | null
  value_mm: number | null
  initial_mm: number | null
  remark: string | null
  warning_level: string | null
  control_cum_mm: number | null
  /** 控制值·变化速率（「控制值」合并表头第二列，如 10 / 3 mm/d） */
  control_rate_mm_d?: number | null
  error: string | null
  error_kind?: string | null // unknown_point=测点不存在 / invalid=其他
  suggested?: Array<{ point_no: string; confidence: 'exact' | 'strong' | 'weak' }>
  existing_record_id: number | null
  existing_value_mm: number | null
  /** 该行所属数据表没有观测日期：需用户指定观测日期后才能导入 */
  needs_date?: boolean
  /** 该行来自哪张数据表（按表改观测日期时用它回填） */
  sheet_name?: string | null
}

/** 未知点号聚合（需求②：初始点 Excel 导入匹配） */
export interface PointIssue {
  point_no: string
  row_count: number
  series_types: SeriesType[]
  components: Component[]
  infer_point_type: PointType
  suggested: Array<{ point_no: string; confidence: 'exact' | 'strong' | 'weak' }>
}

/** 台账里写着的「控制值」 vs 项目当前预警规则（报表「控制值」两列与规则必须是同一个数） */
export interface ControlValueSuggest {
  indicator: SeriesType
  /** 台账里写的累计控制值 / 速率控制值 */
  control_cum_mm: number | null
  control_rate_mm_d: number | null
  row_count: number
  matched_rows: number
  /** 项目当前规则（未配为 null） */
  current_cum_mm: number | null
  current_rate_mm_d: number | null
  /** 两边不一致（含"还没配规则"） */
  differs: boolean
  has_rule: boolean
}

export interface ImportPreviewResponse {
  batch_id: number
  filename: string
  sheets: ImportSheetInfo[]
  valid_rows: ImportPreviewRow[]
  /** 与「系统自动生成」数据同日：导入后以用户数据为准覆盖 */
  override_rows: ImportPreviewRow[]
  conflict_rows: ImportPreviewRow[]
  error_rows: ImportPreviewRow[]
  /** 未识别观测日期的行：预览时补填日期后可导入（否则默认按今天） */
  no_date_rows: ImportPreviewRow[]
  point_issues: PointIssue[]
  /** 台账里的控制值 vs 当前预警规则（可勾选"导入时同步到规则"） */
  control_suggest?: ControlValueSuggest[]
}

export interface ImportBatchInfo {
  id: number
  project_id: number
  filename: string
  sheet_name: string | null
  status: string
  total_rows: number
  valid_rows: number
  imported_rows: number
  skipped_rows: number
  conflict_rows: number
  error_summary: Record<string, unknown>
  created_at: string
}

/** 未知点号处理动作：auto_create 建点 或 map_to 映射到已有测点 */
export interface PointResolveAction {
  excel_no: string
  auto_create: boolean
  infer_point_type: PointType
  map_to: string | null
}

// ---------- 跨项目导入（一次上传、自动分到各项目）----------

/** 认项目时摆给用户挑的候选 */
export interface MultiImportCandidate {
  project_id: number | null
  project_code: string
  project_name: string
  score?: number
  how?: string
  weak?: boolean
}

/** 一张数据表认到了哪个项目（project_id 为空 = 没认出来，要用户手选） */
export interface MultiImportSheet {
  sheet_name: string
  series_type: SeriesType | ''
  kind: 'ledger' | 'point_list'
  obs_date: string | null
  row_count: number
  /** 表里读到的工程名称（界面上要能回答"你凭什么认成它"） */
  name_texts: string[]
  project_id: number | null
  project_code: string
  project_name: string
  project_archived: boolean
  how: string
  score: number
  /** 只是"相近"（不是完全一致）→ 提醒核一眼 */
  close: boolean
  /** 表里没写名称、只按文件名认的 → 更要提醒核一眼 */
  from_filename: boolean
  ambiguous: boolean
  reason: string
  candidates: MultiImportCandidate[]
}

/** 认到同一个项目的一批表 = 一个批次（「全部导入」里的一项） */
export interface MultiImportGroup {
  batch_id: number
  project_id: number
  project_code: string
  project_name: string
  project_archived: boolean
  filename: string
  sheets: MultiImportSheet[]
  preview: ImportPreviewResponse
  /** 有哪张表是"按文件名认的"或只是"相近"→ 提醒核一眼 */
  needs_eyeball: boolean
}

/** 认不出的表：不建批次，等用户手选项目 */
export interface MultiImportPending {
  /** 暂存目录里的文件名（回传给 assign 用；不是完整路径） */
  upload_name: string
  filename: string
  sheet: MultiImportSheet
}

export interface MultiImportUploadResponse {
  groups: MultiImportGroup[]
  pending: MultiImportPending[]
  /** 点位清单（「点号+坐标/高程」）：那是「批量建项目」的活，这里只挑出来说明 */
  point_lists: Array<{ filename: string; sheet_name: string; row_count: number }>
  /** 解析失败的文件（一份坏了不拖住其余的） */
  errors: string[]
}

export const importApi = {
  /** Step 1-7：上传 → 解析 → 校验 → 重复检查，返回预览 */
  upload: (projectId: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return http
      .post<ImportPreviewResponse>(`/projects/${projectId}/import/upload`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: LONG_TIMEOUT,
        timeoutHint: '台账解析超时。文件很大时可以拆成几个小文件分开导',
      })
      .then((r) => r.data)
  },
  /** 按用户指定的观测日期重新核对（不落库）。
   *  改日期必须重算：新日期可能已有记录（变冲突），也可能把原冲突解开（变可导入）。 */
  recheck: (batchId: number, dates: SheetDateOverride[]) =>
    http
      .post<ImportPreviewResponse>(`/import/batches/${batchId}/recheck`, { dates }, { timeout: LONG_TIMEOUT })
      .then((r) => r.data),
  /** Step 8-9：确认导入（含未知点号动作、可选的"控制值同步到预警规则"） */
  confirm: (
    batchId: number,
    validRows: ImportPreviewRow[],
    pointActions: PointResolveAction[] = [],
    syncControlValues = false,
  ) =>
    http
      .post<{
        batch_id: number
        imported_rows: number
        replaced_generated: number
        /** 因缺观测日期未入库的行数（正常流程应为 0） */
        skipped_no_date?: number
        /** 同日已有真实记录、跳过未覆盖的条数 */
        skipped_dup_existing?: number
        points_auto_created: number
        status: string
        /** 同步进预警规则的变更（勾了才有） */
        control_changes?: Array<{
          indicator: SeriesType
          old_cum: number | null
          old_rate: number | null
          new_cum: number | null
          new_rate: number | null
          created: boolean
        }>
        /** 没能同步的原因（台账控制值比系统报警值还大 = 倒挂）；数据照常入库 */
        control_sync_error?: string | null
      }>(`/import/batches/${batchId}/confirm`, {
        valid_rows: validRows,
        point_actions: pointActions,
        sync_control_values: syncControlValues,
      }, { timeout: LONG_TIMEOUT, timeoutHint: '导入超时。行数很多时等久一点是正常的，可以重试（重复的行会跳过、不会写两遍）' })
      .then((r) => r.data),
  /** 一次上传多份台账 → 后端按表内「工程名称」自动分到各项目（**不落库**，只出预览） */
  multiUpload: (files: File[]) => {
    const form = new FormData()
    for (const f of files) form.append('files', f)
    return http
      .post<MultiImportUploadResponse>('/import/multi/upload', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: LONG_TIMEOUT,
        timeoutHint: '台账太多、解析超时。可以分两批传',
      })
      .then((r) => r.data)
  },
  /** 认不出是哪个基坑的表 → 由用户指定项目（**不落库**，只出预览） */
  multiAssign: (uploadName: string, sheetNames: string[], projectId: number, filename?: string) =>
    http
      .post<MultiImportGroup>('/import/multi/assign', {
        upload_name: uploadName,
        sheet_names: sheetNames,
        project_id: projectId,
        filename: filename ?? null,
      }, { timeout: LONG_TIMEOUT })
      .then((r) => r.data),
}

// ---------- 批量建项目（一份「点号 + X/Y/Z」清单 → 拆多个基坑项目） ----------

/** 清单里的一个测点（x/y/z 单位 mm，解析时已从米换算） */
export interface BatchPoint {
  point_no: string
  x: number | null
  y: number | null
  z: number | null
}

export interface BatchSuggestedGroup {
  name: string
  point_count: number
  point_nos: string[]
}

export interface BatchPreview {
  sheet_name: string
  /** 字段 → 列号（1-based）。X/Y 是按数值量级判的，界面上要写出来让用户核对 */
  column_map: Record<string, number>
  has_date: boolean
  obs_date: string | null
  points: BatchPoint[]
  /** 按点号尾部序号猜的分组；猜得没意义时为空数组（让用户自己分） */
  suggested_groups: BatchSuggestedGroup[]
}

/** 用户在界面上分好的一组 = 一个基坑 = 一个项目 */
export interface BatchGroupPayload {
  group_name: string
  project_code: string
  project_name: string
  obs_date: string
  points: BatchPoint[]
}

export interface BatchGroupResult {
  group_name: string
  project_id?: number
  project_code: string
  project_name: string
  obs_date: string | null
  created_project: boolean
  points_created: number
  records_created: number
  skipped: Array<{ point_no: string; reason: string }>
  error?: string
}

export interface BatchCommitResult {
  groups: BatchGroupResult[]
  totals: {
    projects_created: number
    projects_reused: number
    points_created: number
    points_skipped: number
    records_created: number
  }
}

export const batchImportApi = {
  /** 上传清单 → 点列表 + 列映射 + 建议分组（不落库） */
  preview: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return http
      .post<BatchPreview>('/batch-import/preview', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: LONG_TIMEOUT,
        timeoutHint: '清单解析超时。文件很大时可以拆成几个小文件分开导',
      })
      .then((r) => r.data)
  },
  /** 按用户分好的组落库：建项目 / 建测点 / 写初始值 */
  commit: (groups: BatchGroupPayload[]) =>
    http
      .post<BatchCommitResult>('/batch-import/commit', { groups }, {
        timeout: LONG_TIMEOUT,
        timeoutHint: '建项目超时。测点很多时等久一点是正常的，可以重试（同名项目/测点会跳过）',
      })
      .then((r) => r.data),
}

// ---------- 图表数据（Chart Data Builder） ----------

/** 曲线上的一个点。
 *
 *  `record_id` / `value_mm` 是给"拖拽调整"用的：
 *  曲线画的是累计变化量，但落库改的是**读数**，需要这条记录的主键和原读数。
 */
export interface ChartPoint {
  date: string
  cumulative_mm: number
  value_mm: number | null
  record_id: number | null
}

export interface ChartSeries {
  point_id: number
  point_no: string
  series_type: SeriesType
  component: Component
  data: ChartPoint[]
  /** V0 的来源日（没填显式初始值时为最早一期）。这一天是整条曲线的基准，不参与拖拽 */
  baseline_date: string | null
}

export interface ChartWarningPoint {
  point_no: string
  series_type: SeriesType
  component: Component
  date: string
  cumulative_mm: number
  rate_mm_per_day: number | null
  warning_level: WarningLevel
  triggers: string[]
}

export interface ChartDataResponse {
  indicator: SeriesType
  component: Component
  dates: string[]
  series: ChartSeries[]
  warning_points: ChartWarningPoint[]
  unit: string
  /** 拖拽调整的边界（累计变化量绝对值，mm）。None = 该维没配规则，不允许拖拽 */
  limits: {
    control_cumulative_mm: number | null
    alarm_cumulative_mm: number | null
  }
}

export const chartApi = {
  data: (projectId: number, indicator: SeriesType, component?: Component) =>
    http
      .get<ChartDataResponse>(`/projects/${projectId}/chart-data`, {
        params: { indicator, component },
      })
      .then((r) => r.data),
}

// ---------- 观测数据自动生成（需求 #9） ----------

export interface GenerateResult {
  project_id: number
  start_date: string
  end_date: string
  days: number
  generated: number
  /** 被替换掉的记录数（删除的演示数据 + 作废的真实数据） */
  replaced?: number
  /** 其中属于真实数据、被作废的条数 */
  voided_real?: number
  /** 因用户选择"跳过"而保持原样的日期 */
  kept_dates?: string[]
  points: number
  on_conflict?: GenerateConflictMode
  /** 观测模式：standard=每点独立噪声 / total_station=全站仪（含每次架站的公共误差） */
  instrument_mode?: string
  /** 全站仪模式的实际参数与误差预算（standard 模式为 null） */
  station?: {
    centering_mm: number
    direction_sec: number
    distance_m: number
    /** 每期架站公共误差的标准差（mm） */
    sigma_common_mm: number
    /** 独立噪声强度：开模式前 → 开模式后（公共项是从这里让出来的，总散布没变） */
    noise_scale_before_mm: number
    noise_scale_after_mm: number
    points_with_xy: number
  } | null
  /** 「旧值 → 新值」对照样例（最多 12 条）。替换前后读数只差几 mm，靠它才能证明真换了。 */
  changed_samples?: GenerateChangeSample[]
  note: string
}

/** 一条被替换掉的记录：旧值、旧来源、新值 */
export interface GenerateChangeSample {
  point_no: string
  series_type: SeriesType
  component: Component
  obs_date: string
  old_value_mm: number | null
  old_source: string | null
  new_value_mm: number | null
  /** deleted=删掉的演示数据；voided=作废保留的真实数据 */
  action: 'deleted' | 'voided'
}

/** 遇到「这天已有数据」时的处理方式 */
export type GenerateConflictMode = 'skip' | 'replace_generated' | 'replace_all'

/** 生成前预检结果：范围内哪些日期已有数据、是真实数据还是之前自动生成的 */
export interface GeneratePreview {
  start_date: string
  end_date: string
  total_days: number
  empty_days: number
  occupied_days: Array<{
    date: string
    records: number
    sources: Record<string, number>
    /** generated=只有自动生成的数据（可删）；real=真实数据；mixed=两者都有 */
    kind: 'generated' | 'real' | 'mixed'
  }>
  /** 只含自动生成数据的日期（可整批重来） */
  generated_only_days: string[]
  /** 含真实数据的日期（不能删，只能作废） */
  real_data_days: string[]
}

export const generateApi = {
  /** 生成前预检：范围内已有数据的分布（只读，不落库） */
  preview: (projectId: number, params: { start: string; end: string }) =>
    http
      .get<GeneratePreview>(`/projects/${projectId}/generate-data/preview`, { params, timeout: LONG_TIMEOUT })
      .then((r) => r.data),
  /**
   * 自动生成规范限值内的每日观测数据（source=generated）。
   * `on_conflict` 决定遇到已有数据怎么办：跳过 / 只替换自动生成的 / 连真实数据一起替换。
   * `instrument_mode=total_station` 时叠加"每次架站"的公共误差（全站仪观测模式）。
   */
  generate: (
    projectId: number,
    params: {
      start: string
      end: string
      seed?: number
      missing_rate?: number
      on_conflict?: GenerateConflictMode
      instrument_mode?: string
      station_centering_mm?: number
      station_direction_sec?: number
      station_distance_m?: number
    },
  ) =>
    http
      .post<GenerateResult>(`/projects/${projectId}/generate-data`, null, {
        params,
        timeout: LONG_TIMEOUT,
        timeoutHint: '生成数据超时。天数×测点很多时会慢，可以缩小日期范围分几次生成',
      })
      .then((r) => r.data),
  cleanup: (projectId: number) =>
    http
      .post<{ deleted: number }>(`/projects/${projectId}/generate-data/cleanup`, null, { timeout: LONG_TIMEOUT })
      .then((r) => r.data),
}

// ---------- 预警事件 / 数据质量 ----------

export interface WarningEventRow {
  id: number
  project_id: number
  point_no: string
  series_type: SeriesType
  component: Component
  obs_date: string
  indicator: SeriesType
  level: 'control' | 'alarm'
  trigger_type: 'cumulative' | 'delta' | 'rate'
  value_mm: number | null
  rate_mm_per_day: number | null
  message: string
  recompute_batch: string
  created_at: string
}

export interface QualitySuspectRow {
  point_no: string
  series_type: SeriesType
  component: Component
  obs_date: string
  delta_mm: number
  rate_mm_per_day: number | null
  note: string
}

export interface QualityMissingRow {
  point_no: string
  series_type: SeriesType
  component: Component
  obs_date: string
  note: string
}

export interface QualityReport {
  project_id: number
  mutation_threshold_mm: number
  suspects: QualitySuspectRow[]
  missing: QualityMissingRow[]
  suspect_count: number
  missing_count: number
}

/**
 * 「修正最新一期超限值」：只把**最新一期**触发预警的读数更正回限值以内，历史期不动。
 *
 * ⚠ 这会**改观测数据**（走更正流程：旧值置 superseded 保留、新值 source=correct），
 * 所以后端把 `dry_run` 默认设成 true —— 先看预检、用户确认后再执行。
 */
export interface FixWarningItem {
  point_no: string
  series_type: SeriesType
  component: Component
  obs_date: string
  old_value_mm: number
  new_value_mm?: number
  level: 'control' | 'alarm'
  old_rate_mm_per_day: number | null
  new_rate_mm_per_day?: number | null
  old_cumulative_mm: number | null
  reason?: string
}

export interface FixWarningResult {
  project_id: number
  dry_run: boolean
  series_scanned: number
  /** 可修正的读数条数。注意它可能**多于**预警事件条数：改了前一期，后一期的变化量会变大，要连带修 */
  fixable: number
  unfixable: number
  by_level: { control: number; alarm: number }
  samples: FixWarningItem[]
  unfixable_samples: FixWarningItem[]
  changed: number
  recompute?: { recompute_batch: string; events_generated: number; points: number }
}

export const warningEventApi = {
  recompute: (projectId: number) =>
    http
      .post<{ recompute_batch: string; events_generated: number; points: number }>(
        `/projects/${projectId}/warning-events/recompute`,
        null,
        { timeout: LONG_TIMEOUT },
      )
      .then((r) => r.data),
  list: (projectId: number, params?: { level?: 'control' | 'alarm'; from_date?: string; to_date?: string }) =>
    http.get<WarningEventRow[]>(`/projects/${projectId}/warning-events`, { params }).then((r) => r.data),
  quality: (projectId: number) =>
    http.get<QualityReport>(`/projects/${projectId}/quality-issues`).then((r) => r.data),
  /** dryRun=true 只预检；执行必须显式传 false */
  fixValues: (projectId: number, opts: { dryRun: boolean; includeControl?: boolean }) =>
    http
      .post<FixWarningResult>(`/projects/${projectId}/warning-events/fix-values`, null, {
        params: { dry_run: opts.dryRun, include_control: opts.includeControl ?? true },
        timeout: LONG_TIMEOUT,
        // 修正是迭代到收敛的（改一期会牵动后一期），比一次普通的保存慢得多
        timeoutHint: '修正超时。它要反复复算直到不再触发预警，数据多时会慢一些',
      })
      .then((r) => r.data),
}

// ---------- 报表导出 ----------

export interface ExportTemplateInfo {
  id: number
  project_id: number | null
  name: string
  template_type: 'excel' | 'word'
  is_builtin: boolean
  /** 通用母版：所有项目都能用（模板独立化之后，适用范围由它 + projectIds 决定） */
  is_global?: boolean
  /** 这份母版绑定了哪些项目（下拉里要标出来"来自哪个项目"） */
  project_ids?: number[]
  project_names?: string[]
  file_path: string | null
  layout_json: Record<string, unknown> | null
  /** 项目模板原件是否存在（false=文件缺失需重新上传） */
  file_ok?: boolean | null
  created_at: string
}

/** 导出时的「母版 vs 项目测点」差异摘要（后端放在 X-Export-Summary 响应头里）。
 *
 * 为什么要带出来：母版常是从别的基坑拿来的，点位行数（8）少于项目测点数（12）。
 * 导出时会自动补行，界面得能说清"为什么比母版多了几行、哪几个点母版根本放不下"。
 */
export interface ExportSummary {
  template_points: Record<string, number>
  added_points: Record<string, string[]>
  shifted_rows: Record<string, number>
  /** 原始数据区（X/Y/Z 坐标小表）网格放不下、真没导出去的点 */
  missing_raw_points: Record<string, string[]>
  /** 原始数据区补进网格的点（母版右列下面的空格） */
  added_raw_points: Record<string, string[]>
  /** 母版自带的「××统计表」里清掉的旧期数据格数（那是别的基坑的点位与预警等级） */
  cleared_stats?: Record<string, number>
  /** 按本期数据自动重写的文字页：{页名: 写入格数}（概况/分析/成果提交） */
  text_generated?: Record<string, number>
  /** 母版多出来的旧文字格被清空的格数（**不清就是在发别的工程的内容**） */
  text_cleared?: Record<string, number>
  /** 生成的内容母版格子装不下的部分（绝不静默丢） */
  text_overflow?: Record<string, string[]>
}

function readExportSummary(headers: unknown): ExportSummary | null {
  const raw = (headers as Record<string, string> | undefined)?.['x-export-summary']
  if (!raw) return null
  try {
    return JSON.parse(decodeURIComponent(raw)) as ExportSummary
  } catch {
    return null
  }
}

/** 母版体检（导出**前**查）的一条发现 —— 后端 `template_check_service` 的 issue。 */
export interface TemplateCheckIssue {
  /** 机器码（no_control_rule / stale_control_out_of_range / sheet_not_learned …）。
   *  用 code 区分动作，别拿文案匹配：说明话术会改，码不会。 */
  code: string
  /** error = 这份文件发出去一定有问题；warn = 有东西不会被更新；info = 只是把将要发生的事说清楚 */
  level: 'error' | 'warn' | 'info'
  title: string
  detail: string
  /** 涉及的格子（`竖向位移!I7` 形态） */
  cells: string[]
}

export interface TemplateCheckResult {
  template_id: number
  template_name: string
  project_id: number
  issues: TemplateCheckIssue[]
  errors: number
  warnings: number
}

/** 预览网格里有内容的格子（后端只回非空格，界面按 r/c 摆） */
export interface PreviewCell {
  r: number
  c: number
  /** 已经按单元格的显示格式换算好的文本：2.0 就是 "2.0"，日期格是 "2026-08-21" */
  v: string
  /** 数值（右对齐） */
  n?: boolean
  /** 公式格：文件里没有算好的值，Excel 打开后才算得出来 */
  f?: boolean
  b?: boolean
  /** 母版自带的底色（六位 RGB，不带 #） */
  bg?: string
  ha?: string
  wrap?: boolean
  /** 文本过长被截断 */
  cut?: boolean
}

export interface PreviewMerge {
  ref: string
  r: number
  c: number
  rs: number
  cs: number
}

export interface PreviewSheet {
  name: string
  rows: number
  cols: number
  cells: PreviewCell[]
  merges: PreviewMerge[]
  /** 列宽（Excel 字符宽度，键是 A/B/C…） */
  widths: Record<string, number>
  /** visible / hidden —— 隐藏页在 Excel 里看不见，但文件里带着 */
  state: string
  /** 这张表超出预览上限，只画了左上角那一段（文件本身完整） */
  truncated: boolean
}

/** 一个 Sheet 的「控制值」逐格核对结论（预览顺带做的，规则是唯一的数从哪儿来） */
export interface ControlCheck {
  sheet: string
  kind: 'settlement' | 'displacement'
  /** 系统预警规则里 control 级的那对数（= 报表上应该印的数） */
  expected: { control_cum: number | null; control_rate: number | null }
  /** 导出会重写的数据区行范围 [首, 尾]（含尾） */
  area: [number, number]
  control_cols: Record<string, number>
  checked_rows: number
  ok: boolean
  /** 数据区内与规则不符的格子 */
  mismatch: string[]
  /** 数据区内该有值却空着的格子 */
  blank: string[]
  /** 数据区**之外**还印着数的格子（母版遗留，导出够不着） */
  residual: string[]
}

export interface ReportPreviewDoc {
  filename: string
  /** 补点/清行摘要（与下载响应头同一份清单，缺项不出现） */
  summary: Partial<ExportSummary>
  /** 内置标准版式没有母版可比 → 空数组 */
  control_checks: ControlCheck[]
  sheets: PreviewSheet[]
  truncated: boolean
}

export const reportApi = {
  /** 下载整卷监测日报（templateId 缺省=系统标准；weather 天气可选） */
  downloadExcel: (projectId: number, obsDate?: string, templateId?: number, weather?: string) => {
    const params = new URLSearchParams()
    if (obsDate) params.set('obs_date', obsDate)
    if (templateId) params.set('template_id', String(templateId))
    if (weather) params.set('weather', weather)
    const qs = params.toString()
    return http
      .get<Blob>(`/projects/${projectId}/reports/excel${qs ? `?${qs}` : ''}`, {
        responseType: 'blob',
        timeout: LONG_TIMEOUT,
        timeoutHint: '导出超时。母版较大时生成会慢一些',
      })
      .then((r) => ({ blob: r.data, summary: readExportSummary(r.headers) }))
  },
  /** 批量下载多期日报（ZIP 打包，obsDates 逗号连接） */
  downloadExcelBatch: (projectId: number, obsDates: string[], templateId?: number, weather?: string) => {
    const params = new URLSearchParams()
    if (obsDates.length > 0) params.set('obs_dates', obsDates.join(','))
    if (templateId) params.set('template_id', String(templateId))
    if (weather) params.set('weather', weather)
    const qs = params.toString()
    return http
      .get<Blob>(`/projects/${projectId}/reports/excel/batch${qs ? `?${qs}` : ''}`, {
        responseType: 'blob',
        // 批量是**逐期生成**再打包：后端实测约 0.4 秒/期（12 个测点），每期产物 1 MB 上下。
        // 24 期 ≈ 9 秒、28 MB —— 15 秒的通用超时刚好压线（2026-10-09 就是这么被掐断的）。
        timeout: LONG_TIMEOUT,
        timeoutHint: `导出超时（共 ${obsDates.length} 期）。期数多、母版大时会慢，可以缩小日期范围分几次导`,
      })
      .then((r) => r.data)
  },
  /** 下载 Word 日报 */
  downloadDaily: (projectId: number, obsDate?: string) => {
    const params = obsDate ? `?obs_date=${obsDate}` : ''
    return http
      .get<Blob>(`/projects/${projectId}/reports/daily${params}`, { responseType: 'blob', timeout: LONG_TIMEOUT })
      .then((r) => r.data)
  },
  /** 在界面里预览这一期将导出的 Excel（不下载文件）。
   *
   *  后端与 `/reports/excel` 共用同一条生成路径，所以预览所见 == 下载所得；
   *  回来的不只是网格，还有「控制值」逐格核对的结论。*/
  preview: (projectId: number, obsDate?: string, templateId?: number, weather?: string) => {
    const params = new URLSearchParams()
    if (obsDate) params.set('obs_date', obsDate)
    if (templateId) params.set('template_id', String(templateId))
    if (weather) params.set('weather', weather)
    const qs = params.toString()
    return http
      .get<ReportPreviewDoc>(`/projects/${projectId}/reports/preview${qs ? `?${qs}` : ''}`, {
        // 整卷母版的生成成本和导出一样（还要再解析一遍），15 秒的通用档会被掐
        timeout: LONG_TIMEOUT,
        timeoutHint: '预览生成超时。母版较大时可以先下载文件再看',
      })
      .then((r) => r.data)
  },
  /** 母版体检（导出**前**）：只读地查这份母版套到本项目上会出什么问题。 */
  checkTemplate: (projectId: number, templateId: number) =>
    http
      .get<TemplateCheckResult>(`/projects/${projectId}/export-templates/${templateId}/check`, {
        // 大母版整卷扫一遍要几秒（与学习同一量级），走慢档
        timeout: LONG_TIMEOUT,
        timeoutHint: '母版体检超时。母版很大时会慢一些，可以重试',
      })
      .then((r) => r.data),
  /** 项目可用模板列表（内置 + 自定义） */
  listTemplates: (projectId: number) =>
    http.get<ExportTemplateInfo[]>(`/projects/${projectId}/export-templates`).then((r) => r.data),
  /** 上传母版（整卷样板）→ 自动学习变量槽。
   *  `name` 可传空串：留空就用文件名 —— 传文件与起名字没有先后要求。
   *  `scope` 指定这份母版作用于哪些项目（不传 = 只绑当前项目）*/
  uploadTemplate: (
    projectId: number,
    name: string,
    file: File,
    scope?: { projectIds?: number[]; isGlobal?: boolean },
  ) => {
    const form = new FormData()
    form.append('name', name)
    if (scope?.projectIds) form.append('project_ids', scope.projectIds.join(','))
    if (scope?.isGlobal) form.append('is_global', 'true')
    form.append('file', file)
    return http
      .post<{
        id: number
        name: string
        detected_layout: Record<string, unknown> | null
        master_mode: boolean
        is_global?: boolean
        project_ids?: number[]
        message: string
      }>(`/projects/${projectId}/export-templates/upload`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: LONG_TIMEOUT,
        timeoutHint: '母版学习超时。整卷文件较大时会慢一些，可以重试',
      })
      .then((r) => r.data)
  },
  /** 重命名母版（内置不可改）。上传时没想好名字也没关系，事后在这里改。 */
  renameTemplate: (templateId: number, name: string) =>
    http.put<ExportTemplateInfo>(`/export-templates/${templateId}`, { name }).then((r) => r.data),
  /** 设置这份母版**作用于哪些项目**（多选 + 通用开关）。
   *
   *  为什么要能改：一份母版往往好几个基坑共用同一个版式。改一次表头只要重传一次，
   *  原先绑定的项目自动继承 —— 不必每个项目各传一遍（用户 2026-10-08 提出）。*/
  setTemplateScope: (templateId: number, scope: { projectIds?: number[]; isGlobal?: boolean }) =>
    http
      .put<ExportTemplateInfo>(`/export-templates/${templateId}/projects`, scope)
      .then((r) => r.data),

  // ---------- 母版库（主界面「母版库」用，不依赖具体项目） ----------

  /** 全部母版（含通用、含暂时没绑项目的）—— 管理视角 */
  listLibrary: () => http.get<ExportTemplateInfo[]>('/export-templates').then((r) => r.data),
  /** 传进母版库并指定适用范围（不挂在某个项目下 —— 母版本来就不属于项目） */
  uploadToLibrary: (name: string, file: File, scope: { projectIds?: number[]; isGlobal?: boolean }) => {
    const form = new FormData()
    form.append('name', name)
    if (scope.projectIds?.length) form.append('project_ids', scope.projectIds.join(','))
    if (scope.isGlobal) form.append('is_global', 'true')
    form.append('file', file)
    return http
      .post<{ id: number; name: string; master_mode: boolean; message: string }>(
        '/export-templates/upload',
        form,
        {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: LONG_TIMEOUT,
          timeoutHint: '母版学习超时。整卷文件较大时会慢一些，可以重试',
        },
      )
      .then((r) => r.data)
  },
  deleteTemplate: (templateId: number) =>
    http.delete<{ deleted: boolean }>(`/export-templates/${templateId}`, { timeout: LONG_TIMEOUT }).then((r) => r.data),
  /** 下载母版原件（想直接看/改那份样板时用） */
  downloadTemplateFile: (templateId: number) =>
    http
      .get<Blob>(`/export-templates/${templateId}/file`, { responseType: 'blob', timeout: LONG_TIMEOUT })
      .then((r) => r.data),
}

/** 触发浏览器下载 Blob */
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ---------- 备份 / 还原 ----------

export interface BackupRecordInfo {
  id: number
  filename: string
  size_bytes: number
  backup_type: 'manual' | 'auto'
  note: string | null
  created_at: string
}

export const backupApi = {
  create: () => http.post<BackupRecordInfo>('/backup', null, { timeout: LONG_TIMEOUT }).then((r) => r.data),
  list: () => http.get<BackupRecordInfo[]>('/backup/list').then((r) => r.data),
  restore: (id: number) =>
    http
      .post<{ restored: string; safety_backup: string; restored_at: string }>(`/backup/${id}/restore`, null, {
        params: { confirm: true },
        timeout: LONG_TIMEOUT,
      })
      .then((r) => r.data),
}

// ---------- 节假日安排 ----------

/** 服务端返回的节假日条目 */
export interface HolidayDay {
  date: string
  name: string
}

/**
 * 某一年的法定节假日与调休上班日。
 *
 * 数据在服务端获取（联网 → 本地缓存 → 内置保底），前端不再自带年份数据 ——
 * 免得联网拿到新年份了、前端还写着旧的这种自相矛盾。
 *  表示这一年官方还没公布安排（服务端也拿不到）。
 */
export interface HolidayYearResponse {
  year: number
  source: 'network' | 'cache' | 'builtin' | 'none'
  fetched_at: string | null
  has_data: boolean
  rest: HolidayDay[]
  makeup: HolidayDay[]
}

export const holidayApi = {
  /** 取某年安排（服务端会缓存，重复调用很快） */
  get: (year: number) => http.get<HolidayYearResponse>(`/holidays/${year}`).then((r) => r.data),
}

// ---------- 全站设置（主界面「项目默认值」） ----------

/**
 * 新建项目时自动套用的东西。
 *
 * 用户 2026-10-11：「每个项目都要调单独设置有点繁琐」—— 把"每个新基坑都要配一遍"的
 * 三件事（规则套、母版、天气）提到全站一份，建项目就自动带上。
 *
 * ⚠ 改这里**只影响以后新建的项目**，不动已经存在的项目。
 */
export interface AppSettings {
  /** 新建项目自动绑定的预警规则套（null = 不绑，靠通用规则兜底） */
  default_rule_set_id: number | null
  /** 新建项目自动加入适用范围的 Excel 母版（null = 不挂） */
  default_template_id: number | null
  /** 新建项目的默认导出天气 */
  default_weather: string
  /** 「文件检查」扫的那个共同文件夹（导出日报统一存放处） */
  audit_folder?: string | null
  /** 上次检查完成时间（ISO 字符串） */
  audit_last_run?: string | null
  /** 自动生成数据时是否允许产生预警/报警读数（主界面开关） */
  generate_allow_warning?: boolean
}

export const settingsApi = {
  get: () => http.get<AppSettings>('/settings').then((r) => r.data),
  update: (body: Partial<AppSettings>) =>
    http.put<AppSettings>('/settings', body, { timeout: LONG_TIMEOUT }).then((r) => r.data),
}

// ---------- 项目批量操作（项目列表上勾选多个项目后一次做完） ----------

export interface BulkArchiveResult {
  changed: number
  already: number
  names: string[]
  already_names: string[]
  message: string
}

export interface BulkRuleSetResult {
  changed: number
  set_name: string | null
  /** 改绑前每个项目用的是哪套规则（"从哪套换到了哪套"要说清楚） */
  before: Record<string, string>
  message: string
}

export interface BulkTemplateResult {
  changed: number
  template_name: string
  is_global: boolean
  message: string
}

export const bulkApi = {
  /** 批量归档 / 恢复（只改在用状态，观测数据一条不动） */
  archive: (projectIds: number[], active: boolean) =>
    http
      .post<BulkArchiveResult>('/bulk/projects/archive', { project_ids: projectIds, active }, { timeout: LONG_TIMEOUT })
      .then((r) => r.data),
  /** 把这几个项目**改绑**到同一套规则（setId=null → 解绑，改回吃通用规则） */
  ruleSet: (projectIds: number[], setId: number | null) =>
    http
      .post<BulkRuleSetResult>('/bulk/projects/rule-set', { project_ids: projectIds, set_id: setId }, { timeout: LONG_TIMEOUT })
      .then((r) => r.data),
  /** 把这几个项目加进同一份母版的适用范围（"加上去"，不会替换掉它们原有的母版） */
  template: (projectIds: number[], templateId: number) =>
    http
      .post<BulkTemplateResult>('/bulk/projects/template', { project_ids: projectIds, template_id: templateId }, { timeout: LONG_TIMEOUT })
      .then((r) => r.data),
  /** 批量导出各项目**最新一期**日报 → ZIP */
  exportLatest: (projectIds: number[], opts?: { templateId?: number; weather?: string }) =>
    http
      .post<Blob>(
        '/bulk/projects/export',
        { project_ids: projectIds, template_id: opts?.templateId ?? null, weather: opts?.weather ?? null },
        {
          responseType: 'blob',
          timeout: LONG_TIMEOUT,
          timeoutHint: `导出超时（共 ${projectIds.length} 个项目）。项目多时会慢，可以少选几个分批导`,
        },
      )
      .then((r) => r.data),
}

// ---------- 导出文件检查（扫一个文件夹，把导出的日报和系统逐项比） ----------

/** 一条比对明细里的问题 */
export interface AuditProblem {
  /** rate / delta / cum / prev_cum / control_cum / control_rate / rate_over / cum_over /
   *  level_diff / unknown_point / report_empty */
  kind: string
  report?: number | string | null
  system?: number | string | null
  level?: WarningLevel
  diff?: number
}

export interface AuditItem {
  file: string
  path: string
  project_id: number
  project_code: string
  project_name: string
  obs_date: string
  sheet: string
  excel_row: number
  point_no: string
  component: Component
  component_label: string
  problems: AuditProblem[]
  report: Record<string, number | null>
  system: Record<string, number | null>
  limits: Record<string, number | null>
  levels: { report?: WarningLevel; system?: WarningLevel }
}

export interface AuditResult {
  folder: string
  /** 文件夹里扫到的日报份数 */
  scanned: number
  /** 真正认出来并比对了的份数 */
  checked: number
  rows_total: number
  problem_rows: number
  /** 明细条数到上限被截断了（结果页要说明） */
  truncated: boolean
  /** 问题类型 → 条数 */
  counters: Record<string, number>
  /** 问题类型 → 中文说明（后端给，前端不再抄一份） */
  problem_labels: Record<string, string>
  projects: Array<{
    project_id: number
    project_code: string
    project_name: string
    files: number
    rows: number
    problem_rows: number
  }>
  items: AuditItem[]
  /** 认不出/读不了的文件（绝不静默丢） */
  skipped: Array<{
    file: string
    why: string
    /** 认不出时"最像的"几个项目 —— weak 的只是沾点边，别当成推荐（用户 2026-10-13） */
    candidates?: Array<{
      project_id: number | null
      project_code: string
      project_name: string
      score: number
      text: string
      how: string
      weak: boolean
    }>
    /** 几个项目都像、分不清是哪份 —— 要用户自己定 */
    ambiguous?: boolean
  }>
  /** 名字不是完全一致、按"相近"认出来的（让用户扫一眼确认没认歪） */
  fuzzy: Array<{
    file: string
    project_id: number
    project_code: string
    project_name: string
    /** 报表里写的那句名字 */
    text: string
    how: string
    score: number
  }>
  fuzzy_files: number
  /** 属于**已归档**项目、按你的要求跳过没查的（按项目聚合） */
  archived: Array<{
    project_id: number
    project_code: string
    project_name: string
    count: number
    /** 前若干个文件名，够看清"是哪些" */
    files: string[]
  }>
  archived_files: number
}

export interface AuditStatus {
  folder: string | null
  last_run: string | null
  /** 配了文件夹且超过 7 天没查 → 界面提醒补扫 */
  due: boolean
  exists: boolean
}

export const auditApi = {
  status: () => http.get<AuditStatus>('/audit/status').then((r) => r.data),
  /** 开始检查。folder 传 undefined = 用上次存的那个 */
  run: (folder?: string) =>
    http
      .post<AuditResult>('/audit/run', { folder: folder ?? null, save: Boolean(folder) }, {
        timeout: LONG_TIMEOUT,
        timeoutHint: '检查超时（文件夹里文件太多时正常）。可以先把不看的文件挪走再扫',
      })
      .then((r) => r.data),
}

/** 服务端目录浏览（只列目录）—— 浏览器拿不到绝对路径，只能由后端提供这一层 */
export interface DirEntry {
  name: string
  path: string
}

export const fsApi = {
  dirs: (path?: string) =>
    http.get<{ path: string | null; parent: string | null; dirs: DirEntry[]; is_drive_list: boolean }>(
      '/fs/dirs',
      { params: path ? { path } : undefined },
    ).then((r) => r.data),
}

// ---------- 系统自检 ----------

/**
 * 后端能力自检。
 *
 * 【为什么需要】前端 vite 是热更新的，后端 uvicorn 不是 —— 改了 Python 必须重启。
 * 不重启就会出现"界面是新的、行为是旧的"：用户点了新按钮，后端照旧逻辑跑，
 * 表现是**提示变了但数据没动**，靠肉眼根本查不出来。
 * 让后端报出自己有哪些能力，前端一比对就能把话说清楚。
 *
 * 注意这里走 `/api/health`（不带 /v1），所以用 fetch 而不是 http 实例
 * （http 实例的 baseURL 是 /api/v1）。vite 的代理包含 /api，开发时同样能转发。
 */
export const systemApi = {
  health: async (): Promise<{ status: string; version?: string; features?: string[] }> => {
    const r = await fetch('/api/health')
    if (!r.ok) throw new Error(`后端返回 ${r.status}`)
    return r.json()
  },
}
