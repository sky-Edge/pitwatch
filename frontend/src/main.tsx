import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
// ⚠ 必须显式加载 dayjs 的中文包。
//   ConfigProvider 的 locale 只负责「请选择日期/确定」这类**文案**，
//   日期面板里的**月份名与星期缩写**取自 dayjs 自己的 locale（rc-picker 调
//   `dayjs().locale(...).localeData()`），不加载就回退英文 ——
//   表现为面板头部显示「2026年Sep」、星期行是 Su Mo Tu。
import 'dayjs/locale/zh-cn'
import 'antd/dist/reset.css'
// 全局样式（含设计令牌的 CSS 变量）。必须在 reset.css 之后引入，才能覆盖 antd 默认值。
import './styles/global.css'
import { antdTheme } from './theme/antd'
import { APP_NAME } from './theme/tokens'
import { HolidayProvider } from './components/HolidayProvider'
import App from './App'

// 标签页标题与顶栏标题同源，避免两处各写一遍后走偏
// （index.html 的 <title> 也必须是同一个值，有测试校验）
document.title = APP_NAME
dayjs.locale('zh-cn')

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={antdTheme}>
      <HolidayProvider>
        <App />
      </HolidayProvider>
    </ConfigProvider>
  </React.StrictMode>,
)
