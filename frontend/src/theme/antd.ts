import type { ThemeConfig } from 'antd'
import { BRAND, WARNING_LEVEL } from './tokens'

/**
 * antd 主题 —— 把设计令牌灌进 antd。
 *
 * 【为什么必须做这一步】
 * antd 有它自己的默认主色（比项目蓝更亮的一支蓝）。收口前顶栏用项目蓝，
 * 而按钮、链接、Segmented 选中态、Spin 全走 antd 默认蓝 —— 两种蓝并排出现，
 * 界面上会显得「脏」，换个页面又是另一种蓝。
 * 把 `colorPrimary` 指到项目主色后，所有 antd 组件自动统一，且**不需要逐个页面改**。
 *
 * 【提示色也一并接上令牌】
 * `colorError` / `colorWarning` / `colorSuccess` 对齐 `WARNING_LEVEL`，
 * 这样 antd 的 Alert、表单校验红与业务里的报警红是同一个红，
 * 不会出现「表单报错红」和「报警红」两种红。
 *
 * ⚠ 已知边界：antd 的**预设色板**（`<Tag color="red">` 这类）不受 theme token 控制，
 * 它与 `WARNING_LEVEL` 是两支相近但不完全相同的红/金。当前项目里 Tag 只用于
 * 「正常/预警/报警」这类一眼可辨的标签，视觉上可接受，故未强行统一。
 */
export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: BRAND.primary,
    colorInfo: BRAND.primary,
    colorError: WARNING_LEVEL.alarm.fg,
    colorWarning: WARNING_LEVEL.control.fg,
    colorSuccess: WARNING_LEVEL.normal.fg,
  },
  components: {
    Layout: {
      // 顶栏底色改为令牌控制，App.tsx 里不再写内联 background
      headerBg: BRAND.primary,
      headerColor: BRAND.onPrimary,
    },
  },
}
