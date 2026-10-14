# 基坑监测观测系统 — UX / UI 优化分析报告

> 范围：`frontend/src/` 全部 16 个源文件 + `launcher.py` 启动流程 + `index.html`
> 维度：交互流畅性 / 视觉一致性 / 易用性 / 可访问性
> 结论概览：**功能完整度高，工程侧扎实；主要欠账集中在"设计系统缺位"和"给现场用户的语言不够口语化"两块。**

---

## 0. 总体判断

| 维度 | 现状评分 | 一句话结论 |
|---|---|---|
| 交互流畅性 | ⭐⭐⭐☆☆ | 主流程（四步导航）设计得很好，但列表页无筛选、录入页串行请求会拖慢体感 |
| 视觉一致性 | ⭐⭐☆☆☆ | 没有设计令牌层，颜色/间距/圆角散落在 16 个文件里硬编码 |
| 易用性 | ⭐⭐⭐☆☆ | 防呆设计（输编号才能删）是亮点；但开发者术语漏到界面上 |
| 可访问性 | ⭐⭐☆☆☆ | 键盘可达性、aria、颜色语义冗余基本没做 |

**先说做得好的，不建议动**：

1. **四步工作流导航**（`ProjectFlowNav.tsx`）—— 把 7 个子页面收敛成 4 个语义步骤，这是整个系统最好的设计决策，现场用户不会迷路。
2. **删除防呆**（`ProjectsPage` / `PointsPage`）—— 要求手动输入编号才能彻底删除，比「二次确认弹窗」高一个段位。
3. **`ProjectsPage.nextAction()`** —— 每行直接告诉你「下一步点什么」，符合傻瓜式原则。
4. **导入九步流程 + 预览不落库** —— 工程正确性上是对的，先看后导，不污染数据库。

---

## 1. 交互流畅性

### 1.1 【高】`RecordsPage` 逐点串行请求 —— 现场录入等待明显

**现状位置**：`frontend/src/pages/RecordsPage.tsx`，每期渲染时对每个测点单独 `await recordApi.list()`。

8 个测点 = 8 次网络往返，且是**串行**。现场用的是笔记本电脑 + 本地服务，单次 ~20ms 尚可感知；若测点扩到 20+ 个（真实基坑很容易），页面会出现 0.4~1s 的白屏等待，而用户只是在切一个日期。

**优化建议**：
- 后端加一个批量端点 `GET /api/v1/records?project_id=&obs_date=&point_ids=a,b,c`，一次取回当期全部点。
- 前端把 `Promise.all` 并发化作为最小改动（不改后端也能立刻改善）。
- 进阶：录入页预取**相邻周期**数据（上一期/下一期按钮点击时无等待）。

### 1.2 【高】`ComparisonPage` / `WarningEventsPage` 无筛选、无分页

**现状位置**：`ComparisonPage.tsx` —— 每个系列一张 `Card + Table`，按观测期纵向堆叠，**无分页、无测点筛选、无指标筛选**。`WarningEventsPage.tsx` 两张表同样无分页。

21 期 × 8 测点的真实台账，页面会变成几万像素的长滚动；用户想找「JJC3 的水平位移」要滚很久。

**优化建议**：
- 顶部加一行 `Space`：测点多选 `Select`（默认全选）+ 指标多选 + 日期范围 `RangePicker`。
- 表格加 `pagination={{ pageSize: 10, showSizeChanger: true }}`，或者改成「按测点分 Tab」。
- 更进一步：把 `ComparisonPage` 的纵向卡片改成**可折叠面板**（`Collapse`），默认只展开第一个系列。

### 1.3 【中】`BackupPage` 还原操作双重确认，层级混乱

**现状位置**：`BackupPage.tsx` —— 外层 `Modal.confirm` 套内层 `Popconfirm`，同一次「还原」要点两次确认。

**优化建议**：去掉一层。保留 `Modal.confirm`（承载「当前数据将被覆盖」的说明文字），`Popconfirm` 删掉。危险度靠**文案说明后果**来体现，不是靠点两次。

### 1.4 【中】`WarningRulesModal` 保存时才报错

**现状位置**：`WarningRulesModal.tsx` —— 阈值 `InputNumber` 可以填任意值（含 0、负数、速率>累计的荒谬组合），点保存才 `message.warning`。

**优化建议**：`InputNumber` 加 `min={0}` + 表单级即时校验，非法时输入框下方红字提示、保存按钮 `disabled`。防呆应该发生在**输入时**，不是提交时。

### 1.5 【中】导入流程缺少进度反馈

**现状位置**：`ImportPage.tsx` 提交导入后只有 `Steps` 步骤跳转，没有「正在写入 N 行…」的进度。

**优化建议**：确认导入按钮点击后置 `loading`，并在步骤区显示「已写入 3/8 测点」。`client.ts` 的 `timeout: 15000` 对大批量文件偏紧，建议导入相关请求单独放宽到 60s。

### 1.6 【低】`ChartPage` 缺数据导出与全屏

**现状位置**：`ChartPage.tsx` —— 图表只能看，不能存。

**优化建议**：右上角加两个小图标按钮：导出 PNG（`echartsInstance.getDataURL()`）、全屏（`Modal` 内放大重绘或 `requestFullscreen`）。

---

## 2. 视觉一致性

### 2.1 【高】没有设计令牌层 —— 色彩硬编码散落全仓

**现状位置**：`main.tsx` 的 `<ConfigProvider locale={zhCN}>` **没有传 `theme`**。结果：
- 主色 `#185FA5` 硬编码在 `App.tsx` 的 `Header.background`；
- 报警 `#cf1322` / 背景 `#fff1f0`、预警 `#d48806` / 背景 `#fffbe6`、正常 `#52c41a`、次要 `#888` 在各个页面里重复出现；
- `ChartPage.PALETTE` 另有一套 8 色，与预警语义色**并行两套**。

这意味着：**改一次主色要改 8 个文件**，而且容易出现「这里红是 `#cf1322`，那里红是 `#ff4d4f`」的脏化。

**优化建议**（推荐，一次性根治）：
```tsx
// main.tsx
const theme = {
  token: {
    colorPrimary: '#185FA5',
    colorError:   '#cf1322',
    colorWarning: '#d48806',
    colorSuccess: '#52c41a',
    borderRadius: 6,
  },
  components: {
    // 布局壳的蓝色顶栏也走 token，别写死在 App.tsx
    Layout: { headerBg: '#185FA5', headerHeight: 56 },
  },
};
<ConfigProvider locale={zhCN} theme={theme}>
```
配套动作：
- 在 `src/theme/tokens.ts` 导出 `WARN_BG = '#fff1f0'` / `CTRL_BG = '#fffbe6'` 这类**业务语义令牌**（antd token 覆盖不到表格行底色），全仓 import 使用。
- `ChartPage.PALETTE` 改为从同一份令牌派生，保证「图上的红色」= 「表上的红色」。

### 2.2 【中】全局样式分裂在 4 个地方

**现状位置**：
- `main.tsx` 里一段 `.ant-table-row-ignore` 字符串；
- `RecordsPage.tsx` 页面内 `<style>{...}</style>` 定义 `.warning-row-alarm` / `.warning-row-control`；
- `App.tsx` 内联 style 对象；
- 各页面零散的内联 `style={{...}}`。

同一类东西（表格行态）分两处定义，且都不在样式层。

**优化建议**：新建 `frontend/src/styles/global.css`，把 `.ant-table-row-ignore` 和两个 `warning-row-*` 合并进去，在 `main.tsx` `import './styles/global.css'`。Vite 原生支持 CSS 导入，不需要额外依赖。

### 2.3 【中】页面标题三处不一致

**现状位置**：
- `index.html` `<title>基坑监测观测系统</title>`
- `App.tsx` Header 标题「基坑拉森钢板桩沉降与水平位移观测系统」
- 浏览器标签与页内标题对不上，用户开多个标签时分不清。

**优化建议**：统一为「基坑监测观测系统」（短，标签栏能显示全），Header 副标题再写全称。同时补 favicon（当前缺失，浏览器标签是空白图标）。

### 2.4 【中】标题层级混乱

**现状位置**：`App.tsx` 用 `Typography.Title level={4}` 固定；各页面内部又各自用 `level={4}` / `level={5}`，层级关系不成体系。

**优化建议**：定一套：页面主标题 `level={3}`、区块标题 `level={5}`、表格/卡片标题 `level={5}` + `type="secondary"`。

### 2.5 【低】间距与圆角不成体系

`App.tsx` 的 `Content padding: 24`、各页 `Card` 的默认 padding、`Space size` 有的默认有的 `middle`。建议纳入 2.1 的 token（`borderRadius`、`paddingLG`），并约定 `Space size="middle"` 为页面级默认。

---

## 3. 易用性（含「傻瓜式」原则的落地缺口）

### 3.1 【高】「内部 UUID」列暴露给现场用户

**现状位置**：`PointsPage.tsx` —— 表格有一列叫「内部 UUID」，`fontFamily: monospace`，占满 240px 宽。

这是**纯开发者概念**。现场技术员既看不懂也不关心，它还挤占了本来就不宽的表宽（需要横向滚动）。用户明确说过「界面文案避免开发者术语」。

**优化建议**：**直接删掉这一列**。如果确实需要（比如排查问题时对日志），放进展开行（`expandable`）或 tooltip，不要占主表列。

### 3.2 【高】`BatchPointsModal` 要求用户手写 CSV 语法

**现状位置**：`BatchPointsModal.tsx` —— 用户要在 `Input.TextArea` 里写：
```
编号,类型,初始值(mm),位置
JJC1,沉降,12345.6,北侧
```
还要记住分隔符支持 `,，;；\t`、表头可省略、类型支持中英别名。

这与「傻瓜式操作、不要让人记规则」直接冲突。

**优化建议**（择一或组合）：
- **A. 改成表格化录入**：`可编辑 Table`，点「+ 添加一行」，每列是 `Input` + `Select`（类型下拉、位置自由文本），单位标签在表头。
- **B. 保留粘贴但给模板**：提供「下载模板」按钮 + 「粘贴 Excel 内容」引导，并接受从 Excel 直接复制的 `\t` 分隔内容（这部分已支持，只需把文案从"CSV"改成"从 Excel 复制粘贴"）。
- **C. 单位统一为 m**：当前这里填 mm，其他所有页面填 m —— **这是最容易出错的地方**。全系统统一到 m，入库时转 mm（已有 `M_TO_MM`）。若坚持 mm，表头必须显式写「(mm)」并加 `Alert` 提醒。

### 3.3 【高】`ImportPage` 出现 markdown 字面量

**现状位置**：`ImportPage.tsx` 第 780 行附近，`Typography.Text` 里写了 `**初测日期**`，页面上会**原样显示两个星号**。同类问题可能还有几处。

**优化建议**：全局搜 `\*\*` 修掉；如需强调用 `<Typography.Text strong>`。

### 3.4 【中】`ImportPage` 错误文案与实现不符

**现状位置**：`ImportPage.tsx` 多处提示「需 .xlsx/.xlsm」，但实际导入支持 `.xls`（`Upload` 的 accept 里包含）。

**优化建议**：统一文案为「Excel 文件（.xlsx / .xls / .xlsm）」。文案与行为不一致会直接摧毁用户对系统的信任 —— 用户会因为这句话把 .xls 文件先手动另存一遍。

### 3.5 【中】`WarningEventsPage` 重算成功提示带内部 batch id

**现状位置**：`WarningEventsPage.tsx` 的 `recompute()` → `message.success('重算完成（批次 ${batch.slice(0,6)}）')`。

batch id 对用户零价值。

**优化建议**：改成「重算完成，共 N 条预警记录」，batch id 移到「重算历史」里可查。

### 3.6 【中】`WarningRulesModal` 阈值输入无单位

**现状位置**：`WarningRulesModal.tsx` 的两个 `InputNumber` 之间只有 `/` 分隔符，单位只在 `placeholder` 里。

**优化建议**：加 `addonAfter="mm"` 和 `addonAfter="mm/d"`，用户填的时候不用回忆单位。

### 3.7 【中】录入页 `Input` 无单位与移动端键盘

**现状位置**：`RecordsPage.tsx` 表格内的 `Input`（值 / X / Y）没标单位，也没设 `inputMode="decimal"`。

**优化建议**：表头写「本次读数 (m)」；`Input` 加 `inputMode="decimal"`（移动端直接弹数字键盘）。

### 3.8 【低】`BackupPage`「备份位置」是伪按钮

**现状位置**：`BackupPage.tsx` —— 「备份位置」按钮点了只 `message.info` 弹路径，不能复制、不能打开目录。

**优化建议**：改成可复制的路径文本（`Typography.Text copyable`）+「打开文件夹」按钮（后端加一个 `os.startfile` 端点，Windows 下体验起飞）。同时补一句「自动保留最近 30 份备份」的策略说明。

### 3.9 【低】`ImportPage` 步骤区缩进错乱

**现状位置**：`ImportPage.tsx` 第 614 行 `{step === 1 && (` 前有多余缩进，代码可读性小瑕疵（不影响运行）。建议顺手格式化。

### 3.10 【低】`ReportsPage` 导出按钮文案过长

`批量导出 Excel（5 期 → ZIP）` 这种"聪明文案"在按钮宽度上会被截断。建议按钮只写「批量导出」，把「5 期 → ZIP」放到 `Tooltip` 或按钮上方的说明文字里。

---

## 4. 可访问性

### 4.1 【高】可点击 `Tag` 没有键盘可达性

**现状位置**：`ProjectsPage.tsx` 「预警状态」列的 `Tag` 用 `onClick` 打开弹窗，配 `cursor: 'pointer'`。

- `<Tag>` 渲染成 `<span>`，**不可 Tab 聚焦、不可 Enter 触发**；
- 读屏软件完全无法感知这是可点击的；
- 纯视觉用户也不知道它能点（没有下划线/按钮形状）。

**优化建议**：换成 `<Button type="link" size="small">预警详情</Button>`，或用 `<Tag>` 外套 `<Tooltip>` + `<Button type="text">`。行为一致 + 键盘可达 + 语义正确，三合一。

### 4.2 【高】颜色是唯一的语义载体

**现状位置**：`RecordsPage` 的 `.warning-row-alarm` / `.warning-row-control`、`ChartPage` 的红/金/绿散点、各页 `Tag` 的 color。

对色觉障碍用户（男性约 8%），红/金/绿几乎不可分辨。

**优化建议**：
- 表格行态在**首列加图标/文字**（⚠ 预警 / ⛔ 报警），颜色只作辅助；
- 图表散点除颜色外加**形状区分**（circle / triangle / diamond）；
- `Tag` 本身已带文字（「预警」「报警」），这类是好的，保持。

### 4.3 【中】无 aria / 语义标签

**现状位置**：全仓未见 `aria-label`、`role`、`aria-live`。`Table` 无 `aria`、`Steps` 无当前步骤播报、`message` 提示不播报。

**优化建议**（按性价比排序）：
1. 图标按钮加 `aria-label`（如「导出 PNG」）；
2. 表单 `Input` 靠 `Form.Item label` 已自动关联，无需动；
3. 关键操作成功提示加 `role="status"`。

### 4.4 【中】`WarningEventsPage` 截断文本无 tooltip

**现状位置**：`WarningEventsPage.tsx` 「说明」列 `ellipsis: true`，**没有 tooltip**。用户只能看到半句话。

**优化建议**：`ellipsis: { showTitle: false }` + `render` 里包 `Tooltip`。同类问题在 `ComparisonPage` 及多个表格的备注列也存在，建议统一。

### 4.5 【低】对比度

`#d48806`（预警金）在白色背景上对比度约 3.0:1，低于 WCAG AA 正文要求的 4.5:1（作为大号/加粗文字勉强达标）。若作为小号文字色使用，建议加深到 `#ad6800`（antd 的 warning-7）。

### 4.6 【低】动效未考虑减弱偏好

未使用 `prefers-reduced-motion`。当前动效很少（`Spin`、`Drawer`），风险低，记录待办即可。

---

## 5. 建议的落地顺序（改动量从小到大）

| 批次 | 内容 | 影响面 | 预期收益 |
|---|---|---|---|
| **第 1 批（半天）** | 修 `**初测日期**` 字面量；删「内部 UUID」列；改错误文案 .xls；batch id 从提示移除；还原去双重确认；`InputNumber` 加单位与 min | 6 个文件小改 | 用户**立刻能感知**的脏点清零，风险极低 |
| **第 2 批（1 天）** | `ConfigProvider` 注入 theme token + `tokens.ts` + 合并全局 CSS；统一标题与 `index.html`；补 favicon | 全局 | 视觉一致性根治，后续所有改动受益 |
| **第 3 批（1~2 天）** | `ComparisonPage` / `WarningEventsPage` 加筛选 + 分页；截断列补 tooltip；可点击 Tag 改 Button | 3 个页面 | 大数据量下可用性显著提升 + 可访问性达标 |
| **第 4 批（2 天）** | `RecordsPage` 并发/批量请求；`BatchPointsModal` 改表格化录入 + 单位统一 m；导入进度反馈 | 后端 + 2 页面 | 交互流畅性质变，消除最容易填错的地方 |
| **第 5 批（按需）** | 图表导出/全屏；备份目录打开；aria 与减弱动效 | 多个 | 锦上添花 |

---

## 6. 两个需要你决策的点

1. **批量新建测点的初始值单位**：现在这里填 mm，其他页面填 m。改成统一 m（推荐，与现场读数习惯一致），还是保留 mm 但强化提示？
2. **「内部 UUID」列**：直接删，还是移进展开行保留给排障用？

---

*报告基于对 `frontend/src/` 全部 16 个源文件的逐行阅读，问题位置均标注到文件与行。所有建议不改变后端数据模型与计算引擎，属于纯表现层与交互层优化。*
