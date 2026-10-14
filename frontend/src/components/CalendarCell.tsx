import { useEffect, type ReactNode } from 'react'
import type { Dayjs } from 'dayjs'
import { Divider } from 'antd'
import { markOf, sourceLabel } from '../calendar/holidays'
import { useHolidayEnsure, useHolidayTable, useHolidayYears } from './HolidayProvider'
import { BRAND, CALENDAR } from '../theme/tokens'

/**
 * 日期选择面板里的「一天」。
 *
 * 把四种信息叠在一个格子里，层次固定，避免各页面各画一套：
 *   ① 底色  —— 报警/预警优先，其次节假日（安全信息 > 放假信息）
 *   ② 右上角标 —— 「休」法定放假 / 「班」调休上班
 *   ③ 左上角蓝点 —— 初始值日（仅观测记录页）
 *   ④ 底部横条 —— 当天已录入（红=报警/金=预警/绿=正常，仅观测记录页）
 *
 * ⚠ antd 在提供 `cellRender` 时**不会**生成 `.ant-picker-cell-inner`，
 *   写探针读面板时直接读格子本身，别去找那个类名。
 */
export default function CalendarCell({
  date,
  bg,
  barColor,
  dot,
}: {
  date: Dayjs
  /** 单元格浅底：传了就用它（不与节假日底色叠加，避免出现"第三种颜色"） */
  bg?: string
  /** 底部横条颜色 */
  barColor?: string
  /** 是否画初始值蓝点 */
  dot?: boolean
}) {
  // 节假日数据按年共享（42 个格子只会触发一次请求，见 HolidayProvider 的说明）
  const year = date.year()
  const table = useHolidayTable(year)
  const ensure = useHolidayEnsure()
  useEffect(() => {
    ensure(year)
  }, [year, ensure])

  const mark = markOf(table, date)
  const holidayBg =
    mark.kind === 'holiday' ? CALENDAR.holiday.bg : mark.kind === 'makeup' ? CALENDAR.makeup.bg : undefined
  const badgeColor = mark.kind === 'holiday' ? CALENDAR.holiday.fg : CALENDAR.makeup.fg

  return (
    <div
      style={{
        position: 'relative',
        height: '100%',
        borderRadius: 4,
        background: bg ?? holidayBg,
      }}
    >
      <span style={mark.kind === 'weekend' ? { color: CALENDAR.weekendText } : undefined}>{date.date()}</span>

      {mark.label && (
        <span
          title={mark.name}
          style={{
            position: 'absolute',
            top: 0,
            right: 2,
            fontSize: 9,
            lineHeight: '11px',
            fontWeight: 600,
            color: badgeColor,
          }}
        >
          {mark.label}
        </span>
      )}

      {dot && (
        <span
          style={{
            position: 'absolute',
            top: 2,
            left: 3,
            width: 6,
            height: 6,
            borderRadius: 3,
            background: BRAND.primary,
            display: 'inline-block',
          }}
        />
      )}

      {barColor && (
        <span
          style={{
            position: 'absolute',
            bottom: 1,
            left: '30%',
            right: '30%',
            height: 3,
            borderRadius: 2,
            background: barColor,
            display: 'inline-block',
          }}
        />
      )}
    </div>
  )
}

/**
 * 日期面板的内容宽度（antd 日期面板默认就是 288px 宽）。
 *
 * 【为什么需要这个数】图例是放在面板里的（`panelRender`），若不给它设上限，
 * **图例文字会把整个下拉撑宽** —— 实测撑到 612px，而日历本体只有 288px，
 * 于是日历右边凭空多出一大片空白。用 maxWidth 约束后图例自己换行，下拉保持原宽。
 */
const PANEL_CONTENT_WIDTH = 288

/**
 * 日期面板底部的图例。
 *
 * 【为什么要它】节假日角标是"自解释"的（休/班），但蓝点与底部横条不是，
 * 用户第一次看到只会觉得"这里有个色块"。图例把话说清楚，省得猜。
 */
export function CalendarLegend({ extra = [] }: { extra?: string[] }) {
  const years = useHolidayYears()
  const withData = years.filter((y) => Object.keys(y.rest).length > 0)
  const pending = years.filter((y) => Object.keys(y.rest).length === 0)

  /**
   * 数据说明 —— 告诉用户"标的是哪一年的、数据从哪来"。
   *
   * 这行不是装饰：放假安排每年 11 月才公布，用户翻到次年如果没有标记，
   * 会怀疑"是不是坏了"。写清"国家还没公布"能省掉一次误会。
   */
  const dataNote = (() => {
    if (withData.length === 0 && pending.length === 0) return '节假日数据加载中…'
    const parts: string[] = []
    if (withData.length > 0) {
      const detail = withData.map((y) => `${y.year} 年 ${sourceLabel(y.source)}`).join(' · ')
      parts.push(`数据：${detail}`)
    }
    if (pending.length > 0) {
      parts.push(`${pending.map((y) => y.year).join('、')} 年安排国家还没公布`)
    }
    return parts.join('；')
  })()

  const swatch = (color: string, text: string, hollow = false) => (
    <span key={text} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span
        style={{
          width: 12,
          height: 12,
          borderRadius: 3,
          background: hollow ? undefined : color,
          border: hollow ? `1px solid ${color}` : undefined,
          color,
          fontSize: 9,
          lineHeight: '10px',
          textAlign: 'center',
        }}
      >
        {hollow ? '休' : ''}
      </span>
      <span>{text}</span>
    </span>
  )

  return (
    <>
      <Divider style={{ margin: '6px 0' }} />
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '2px 10px',
          padding: '0 12px 8px',
          // 关键：不设上限的话图例会把下拉撑宽（见 PANEL_CONTENT_WIDTH 的说明）
          maxWidth: PANEL_CONTENT_WIDTH,
          fontSize: 11,
          lineHeight: '16px',
          color: CALENDAR.makeup.fg,
        }}
      >
        {swatch(CALENDAR.holiday.bg, '法定节假日')}
        {swatch(CALENDAR.makeup.bg, '调休上班')}
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <span style={{ color: CALENDAR.weekendText, fontWeight: 700 }}>15</span>
          <span>周末</span>
        </span>
        {extra.map((t) => (
          <span key={t}>{t}</span>
        ))}
        <span style={{ width: '100%', opacity: 0.85 }}>{dataNote}</span>
      </div>
    </>
  )
}

/**
 * 「只带节假日标记」的面板配置 —— 给不关心观测打点的日期选择器用
 * （导出日报的日期范围、生成数据的起止日、导入时选观测日期）。
 *
 * 用法：`<DatePicker {...plainCalendarPanel} />`
 * 一处定义、多处展开，避免"记录页加了节假日、导出页忘了加"。
 */
export const plainCalendarPanel = {
  cellRender: (current: unknown, info: { type: string }) =>
    (info.type === 'date' ? <CalendarCell date={current as Dayjs} /> : (current as Dayjs).date()) as ReactNode,
  panelRender: (panel: ReactNode) => (
    <>
      {panel}
      <CalendarLegend />
    </>
  ),
}

/**
 * 「带观测日期标记」的面板配置 —— 给需要一眼看出"哪几期有数据"的选择器用
 * （导出日报的期次选择）。
 *
 * 【为什么需要它】导出页原先只有 `disabledDate`：有数据的日期能点、没数据的点不了。
 * 这是**消极的区分** —— 用户翻到某个月，看不出这个月到底测过哪几天，
 * 只能一个个去试（点不动才知道没有）。这里把"有数据"变成看得见的标记。
 *
 * 【为什么只用一种颜色】记录页那套是红/金/绿（报警/预警/正常），但**导出页的报表
 * 不含预警**（用户明确要求"导出的 Excel 不要显示预警"）—— 这里再飘出红金两色，
 * 会让人以为预警会影响导出内容。所以只用一个颜色表示"这天有观测数据"，语义单一。
 *
 * 返回的是面板配置对象；调用方请用 `useMemo` 包住，避免每次渲染都重建。
 */
export function observationCalendarPanel(
  markedDates: string[],
  markedLegend = '底部横条 = 有观测数据',
  emptyLegend = '灰色 = 这天没有数据',
) {
  const marked = new Set(markedDates)
  return {
    cellRender: (current: unknown, info: { type: string }) =>
      (info.type === 'date' ? (
        <CalendarCell
          date={current as Dayjs}
          barColor={marked.has((current as Dayjs).format('YYYY-MM-DD')) ? BRAND.primary : undefined}
        />
      ) : (
        (current as Dayjs).date()
      )) as ReactNode,
    panelRender: (panel: ReactNode) => (
      <>
        {panel}
        <CalendarLegend extra={[markedLegend, emptyLegend]} />
      </>
    ),
  }
}
