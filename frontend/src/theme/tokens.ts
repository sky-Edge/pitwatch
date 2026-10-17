import type { WarningLevel } from '../api/client'

/**
 * 设计令牌 —— 全站颜色与常用尺寸的唯一来源。
 *
 * 【为什么要有这个文件】
 * 收口前：主色 `#185FA5` 写在 App.tsx，报警红 `#CF1322` 散在 6 个页面，
 * 曲线图另有一套 8 色调色板与预警语义色并行，`alarm ? 红 : 金` 这种三元判断
 * 在 4 个文件里各写了一遍。结果是「改一处颜色要翻遍全仓」，而且必然漏掉某一处，
 * 漏掉的那处就是界面上看着不对劲的地方。
 *
 * 【使用规则】
 * 1. 页面与组件里**不允许**出现颜色字面量（`#xxx` / `rgb()`）。
 *    需要新颜色时在这个文件的 `RAW` 里加一条，并写清它代表什么语义。
 *    —— `tests/test_frontend_theme.py` 会扫描全仓，违反即测试失败。
 * 2. 预警相关配色一律走 `WARNING_LEVEL` / `levelColor()` / `levelBg()`，
 *    不要在页面里重新判断等级。新增等级时 `Record<WarningLevel, ...>` 会直接编译报错，
 *    这是刻意的（防止后端加了等级、前端忘了配色）。
 * 3. CSS 里用 `var(--wm-*)`，变量值在 `styles/global.css` 的 `:root` 中；
 *    两边一致性同样由上述测试强制校验。
 */

/**
 * 应用名 —— 顶栏与浏览器标签共用。
 * `index.html` 的 `<title>` 必须与此一致（有测试校验，避免两处各写一遍后走偏）。
 */
export const APP_NAME = '基坑拉森钢板桩沉降与水平位移观测系统'

/**
 * 原始色值 —— **全仓唯一出现颜色字面量的地方**。
 *
 * 键名 → CSS 变量名的换算规则：camelCase → kebab-case 加 `--wm-` 前缀。
 * 例如 `levelAlarmFg` → `--wm-level-alarm-fg`。
 *
 * ⚠ 需要暴露给 CSS 的键必须同时登记在 `CSS_VAR_KEYS` 里，否则测试会失败。
 */
const RAW = {
  brandPrimary: '#185FA5',
  brandOnPrimary: '#FFFFFF',

  levelNormalFg: '#52C41A',
  levelNormalBg: '#F6FFED',
  levelNormalText: '#389E0D',
  levelControlFg: '#D48806',
  levelControlBg: '#FFFBE6',
  levelControlText: '#AD6800',
  levelAlarmFg: '#CF1322',
  levelAlarmBg: '#FFF1F0',
  levelAlarmText: '#CF1322',

  neutralWhite: '#FFFFFF',
  neutralMuted: '#888888',
  neutralIgnoredBg: '#FAFAFA',
  /** 导出预览网格的细线：比 antd 表格线更淡，让 Excel 自带的底色/文字成为主角 */
  gridLine: '#DDE3EA',

  /** 「定位到某条记录」时该行的浅底（比预警底色更弱，不抢报警/预警的视觉） */
  focusBg: '#EAF2FA',

  /**
   * 日历上的节假日标记。
   * 刻意避开预警金与报警红 —— 日历格子里已经有「报警红底/预警金底」，
   * 放假再用金/红就分不清"那天放假"还是"那天超限"了，故取青绿。
   */
  holidayFg: '#08979C',
  holidayBg: '#E6FFFB',
  /** 调休上班日：灰，低调表示"这天得上班" */
  makeupFg: '#8C8C8C',
  makeupBg: '#F5F5F5',
  /** 周末的日期数字淡化（只淡数字，不铺底色，否则整张日历一半是灰的） */
  neutralFaint: '#BFBFBF',

  // 测点曲线调色板（第 1 个即主色，其余为拉开色相的补充色）
  series2: '#2F8F4E',
  series3: '#E08A00',
  series4: '#B73A2F',
  series5: '#6A4FA3',
  series6: '#0E8A8A',
  series7: '#C77A9B',
  series8: '#5A6B8C',
} as const

/** 需要同步到 `styles/global.css` 的 `:root` 的键（其余仅 TS 侧使用） */
const CSS_VAR_KEYS = [
  'brandPrimary',
  'brandOnPrimary',
  'levelNormalFg',
  'levelNormalBg',
  'levelControlFg',
  'levelControlBg',
  'levelAlarmFg',
  'levelAlarmBg',
  'neutralWhite',
  'neutralMuted',
  'neutralIgnoredBg',
  'gridLine',
  'focusBg',
] as const

/** 主色系 */
export const BRAND = {
  /** 顶栏底色、曲线首个测点色 */
  primary: RAW.brandPrimary,
  /** 铺在主色之上的文字与图标 */
  onPrimary: RAW.brandOnPrimary,
} as const

/** 中性色 */
export const NEUTRAL = {
  /** 纯白：散点描边等需要与背景拉开的地方 */
  white: RAW.neutralWhite,
  /** 次要说明文字 / 空值占位符 */
  muted: RAW.neutralMuted,
  /** 导入预览中「已排除」行的底色 */
  ignoredBg: RAW.neutralIgnoredBg,
} as const

/** 单个预警等级的配色三件套 */
export interface LevelStyle {
  /** 主色：文字、图标、图表散点 */
  fg: string
  /** 行/单元格浅底色 */
  bg: string
  /** 铺在 `bg` 上的深色文字（浅底上直接用 `fg` 对比度不够） */
  textOnBg: string
}

/**
 * 预警等级配色。键与后端 `warning_level` 严格对应。
 * `normal.bg` 目前未被使用（正常状态不上底色），保留是为了让三档结构一致。
 */
export const WARNING_LEVEL: Record<WarningLevel, LevelStyle> = {
  normal: { fg: RAW.levelNormalFg, bg: RAW.levelNormalBg, textOnBg: RAW.levelNormalText },
  control: { fg: RAW.levelControlFg, bg: RAW.levelControlBg, textOnBg: RAW.levelControlText },
  alarm: { fg: RAW.levelAlarmFg, bg: RAW.levelAlarmBg, textOnBg: RAW.levelAlarmText },
}

/**
 * 取某等级的主色。
 *
 * 只有 `control` / `alarm` 着色；`normal`、ProjectBrief 的 `none`、
 * 以及任何未知值都返回 `undefined`（沿用默认文字色，不额外着色）——
 * 与原先把 `normal` 落到 `undefined` 的行为完全一致。
 *
 * 参数类型放宽为 `string | null | undefined`：调用方拿到的可能是
 * `severity`（多一个 `none`）或可空字段，让它们不必先做类型收窄。
 */
export function levelColor(level: string | null | undefined): string | undefined {
  if (level !== 'control' && level !== 'alarm') return undefined
  return WARNING_LEVEL[level].fg
}

/** 取某等级的浅底色，语义同 `levelColor`（仅 control/alarm 有底色） */
export function levelBg(level: string | null | undefined): string | undefined {
  if (level !== 'control' && level !== 'alarm') return undefined
  return WARNING_LEVEL[level].bg
}

/**
 * 测点曲线调色板：8 色，超出后循环使用（保证多测点可区分）。
 *
 * 注：3 号（橙）与 4 号（砖红）在色相上接近预警金 / 报警红，
 * 但预警点是用 symbolSize 14 的大散点 + 图例单独标注的，实际不会混淆。
 */
export const SERIES_PALETTE: string[] = [
  RAW.brandPrimary,
  RAW.series2,
  RAW.series3,
  RAW.series4,
  RAW.series5,
  RAW.series6,
  RAW.series7,
  RAW.series8,
]

/**
 * 「定位」类高亮（从"需要关注"点进来时，标出到底是哪一行）
 * 底色刻意取得比预警底色更弱 —— 报警/预警行的底色优先级更高，不会被它盖掉。
 */
export const HIGHLIGHT = {
  rowBg: RAW.focusBg,
} as const

/**
 * 日历（日期选择面板）的节假日语义色。
 *
 * 优先级：**报警/预警底色 > 节假日底色**。同一天既放假又超限时，
 * 底色让给预警（安全信息优先），但"休/班"角标照常显示，两者不互相顶掉。
 */
export const CALENDAR = {
  /** 法定放假日 */
  holiday: { fg: RAW.holidayFg, bg: RAW.holidayBg },
  /** 调休上班日（周末补班） */
  makeup: { fg: RAW.makeupFg, bg: RAW.makeupBg },
  /** 周末日期数字的淡化色 */
  weekendText: RAW.neutralFaint,
} as const

/** 常用尺寸（避免每个页面各写一个数） */
export const LAYOUT = {
  /** 主内容区内边距 */
  pagePadding: 24,
  /** 图表绘图区高度（曲线页） */
  chartHeight: 460,
} as const

/** 供测试校验用：导出原始表与 CSS 变量键清单（业务代码请勿使用） */
export const __INTERNAL__ = { RAW, CSS_VAR_KEYS }
