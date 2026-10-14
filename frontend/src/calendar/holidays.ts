import type { Dayjs } from 'dayjs'

/**
 * 节假日的**判断逻辑**（数据本身在服务端）。
 *
 * 【为什么数据不放在前端】
 * 放假安排每年由国务院办公厅在上一年 11 月才公布。原先这里内置了一份 2026 的区间表，
 * 但那样就有两个数据源：前端一份、服务端一份，早晚会出现"联网拿到 2027 了、前端还写着 2026"
 * 这种自相矛盾。现在统一由服务端提供（联网 → 缓存 → 内置保底），前端只负责判断与绘制。
 *
 * 【服务端拿不到数据时怎么办】
 * 不显示节假日标记即可 —— 日期选择器的本职是选日期，标记只是锦上添花。
 * 绝不能因为"节假日接口挂了"让整个面板报错。
 */

/** 服务端返回的某一年节假日安排（已按日期建索引，便于查表） */
export interface HolidayTable {
  year: number
  /** 放假日：{'YYYY-MM-DD': 节日名} */
  rest: Record<string, string>
  /** 调休上班日：{'YYYY-MM-DD': 说明} */
  makeup: Record<string, string>
  /** 数据来源：联网 / 本地缓存 / 内置保底 / 无 */
  source: 'network' | 'cache' | 'builtin' | 'none'
  fetchedAt?: string | null
}

/** 一天的"日子类型" */
export type DayKind =
  /** 法定放假日 */
  | 'holiday'
  /** 调休上班日（周末补班） */
  | 'makeup'
  /** 普通周末 */
  | 'weekend'
  /** 普通工作日 */
  | 'workday'

export interface DayMark {
  kind: DayKind
  /** 角标文字：放假=「休」、调休上班=「班」；周末与工作日不标字 */
  label?: string
  /** 节日名（如"国庆节"），用于 tooltip */
  name?: string
}

/**
 * 判断某天是什么日子。
 *
 * 顺序不能反：**调休上班日优先于周末** —— 2026-02-14 是周六但要上班，
 * 若先判周末就会标成休息日，与官方安排相反（而界面上只是少个「班」字，看不出来）。
 *
 * @param table 该年份的节假日表；没拿到（未加载 / 该年无数据）时传 undefined
 */
export function markOf(table: HolidayTable | undefined, date: Dayjs): DayMark {
  const key = date.format('YYYY-MM-DD')
  if (table) {
    const makeup = table.makeup[key]
    if (makeup) return { kind: 'makeup', label: '班', name: makeup }
    const rest = table.rest[key]
    if (rest) return { kind: 'holiday', label: '休', name: rest }
  }
  const wd = date.day() // 0=周日 6=周六
  if (wd === 0 || wd === 6) return { kind: 'weekend' }
  return { kind: 'workday' }
}

/** 数据来源的中文说明（图例里给用户看） */
export function sourceLabel(source: HolidayTable['source']): string {
  switch (source) {
    case 'network':
      return '已联网更新'
    case 'cache':
      return '本地缓存'
    case 'builtin':
      return '内置数据'
    default:
      return '暂无安排'
  }
}
