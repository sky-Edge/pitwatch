import type { CSSProperties, ReactNode } from 'react'
import { Alert, Empty, Modal, Spin, Tabs, Tag, Typography } from 'antd'
import type { ControlCheck, PreviewCell, PreviewMerge, PreviewSheet, ReportPreviewDoc } from '../api/client'
import { HIGHLIGHT, NEUTRAL, WARNING_LEVEL } from '../theme/tokens'

/**
 * 导出 Excel 的界面预览 —— 点导出之前，先在这儿把这一期报表看完。
 *
 * 为什么做（用户 2026-10-16）：「把导出Excel可视化 做进系统中吧」。
 * 成果文件是发给甲方的，原先只能"下载 → 打开 Excel → 对着看"，一处不对就来回好几趟。
 * 现在界面里直接画出这张表，**并且**把「控制值」逐格与系统预警规则比对：
 * 不符的格子标红，鼠标停上去说明差在哪。
 *
 * 为什么用后端算好的网格、而不是前端塞一个 SheetJS/Univer：
 * 前端解析文件既不知道系统的规则、也不知道哪些列是控制值 —— 画得出表，核不了数。
 *
 * 【三种"看着对、其实不是"的格子】必须在界面上注明，否则不预览更坏：
 * · 公式格（斜体 fx）：文件里没有算好的值，Excel 打开后才计算；
 * · 隐藏页：Excel 里看不见，但文件里确实带着；
 * · 被截断的长文本：预览只显前一段（末尾 …），文件里是完整的。
 */

/** 与后端 `preview_model.MAX_ROWS / MAX_COLS` 同一个上限（写死是为了把话说明白，别让人以为少画了是丢了数据） */
const MAX_PREVIEW_ROWS = 220
const MAX_PREVIEW_COLS = 30

/** 列号（1 起）→ Excel 列名 */
function colLetter(i: number): string {
  let s = ''
  let n = i
  while (n > 0) {
    s = String.fromCharCode(65 + ((n - 1) % 26)) + s
    n = Math.floor((n - 1) / 26)
  }
  return s
}

/** `竖向位移!I20=30（规则是 25）` → {r:20, c:9}；解析不出来返回 null（宁可少标一格，不能标错格） */
function refToRC(item: string): { r: number; c: number } | null {
  const m = /!\$?([A-Z]{1,3})(\d{1,5})/.exec(item)
  if (!m) return null
  let c = 0
  for (const ch of m[1]) c = c * 26 + (ch.charCodeAt(0) - 64)
  return { r: Number(m[2]), c }
}

/** 核对结论里的格子 → 「为什么被标红」的说明（悬浮在格子上看） */
function badReasons(check: ControlCheck): Map<string, string> {
  const out = new Map<string, string>()
  const put = (items: string[], prefix: string) => {
    for (const item of items) {
      const rc = refToRC(item)
      if (rc) out.set(`${rc.r}:${rc.c}`, `${prefix}：${item.replace(/^[^!]*!/, '')}`)
    }
  }
  put(check.mismatch, '这一格与系统预警规则对不上')
  put(check.blank, '规则里有这一项，表上却是空的')
  put(check.residual, '数据区之外的母版遗留值，导出不会更新它')
  return out
}

const headStyle: CSSProperties = {
  padding: '2px 6px',
  fontSize: 11,
  fontWeight: 400,
  color: NEUTRAL.muted,
  background: NEUTRAL.ignoredBg,
  textAlign: 'center',
  whiteSpace: 'nowrap',
}

function SheetGrid({ sheet, check }: { sheet: PreviewSheet; check?: ControlCheck }) {
  const byPos = new Map<string, PreviewCell>()
  for (const c of sheet.cells) byPos.set(`${c.r}:${c.c}`, c)

  // 合并区只在左上角那一格画内容，跨出去的格子直接不输出（colSpan/rowSpan 已经占位）
  const anchors = new Map<string, PreviewMerge>()
  const covered = new Set<string>()
  for (const m of sheet.merges) {
    anchors.set(`${m.r}:${m.c}`, m)
    for (let r = m.r; r < m.r + m.rs; r++) {
      for (let c = m.c; c < m.c + m.cs; c++) {
        if (r !== m.r || c !== m.c) covered.add(`${r}:${c}`)
      }
    }
  }

  const bad = check ? badReasons(check) : new Map<string, string>()
  const controlCols = new Set<number>(check ? Object.values(check.control_cols) : [])
  const areaLo = check?.area[0] ?? 0
  const areaHi = check?.area[1] ?? -1

  const rows: ReactNode[] = []
  for (let r = 1; r <= sheet.rows; r++) {
    const inArea = r >= areaLo && r <= areaHi
    const cells: ReactNode[] = []
    for (let c = 1; c <= sheet.cols; c++) {
      const k = `${r}:${c}`
      if (covered.has(k)) continue
      const cell = byPos.get(k)
      const merge = anchors.get(k)
      const why = bad.get(k)
      const tint = controlCols.has(c) || inArea ? HIGHLIGHT.rowBg : undefined
      const style: CSSProperties = {
        padding: '2px 6px',
        height: 20,
        maxWidth: 320,
        overflow: 'hidden',
        verticalAlign: 'middle',
        background: why ? WARNING_LEVEL.alarm.bg : cell?.bg ? `#${cell.bg}` : tint,
        color: why ? WARNING_LEVEL.alarm.textOnBg : undefined,
        fontWeight: cell?.b ? 600 : undefined,
        whiteSpace: cell?.wrap || why ? 'normal' : 'nowrap',
        // Excel 的 horizontal 还有 general/justify/fill 等取值，CSS 不认 —— 只认 center/right，其余按数值右对齐
        textAlign: cell?.ha === 'center' ? 'center' : cell?.ha === 'right' ? 'right' : cell?.n ? 'right' : 'left',
      }
      let content: ReactNode = cell ? cell.v : undefined
      let title: string | undefined = why
      if (cell?.f) {
        content = <i style={{ color: NEUTRAL.muted }}>fx {cell.v}</i>
        title = title ?? '这一格是公式：文件里没有算好的值，Excel 打开后才会算出来'
      } else if (cell?.cut) {
        title = title ?? cell.v
      }
      cells.push(
        <td key={c} colSpan={merge?.cs} rowSpan={merge?.rs} title={title} style={style}>
          {content}
        </td>,
      )
    }
    rows.push(
      <tr key={r}>
        <th style={{ ...headStyle, background: inArea ? HIGHLIGHT.rowBg : NEUTRAL.ignoredBg }}>{r}</th>
        {cells}
      </tr>,
    )
  }

  return (
    <div style={{ overflow: 'auto', maxHeight: '58vh' }}>
      <table className="excel-preview-grid" style={{ borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            <th style={headStyle}>#</th>
            {Array.from({ length: sheet.cols }, (_, i) => i + 1).map((c) => (
              <th key={c} style={{ ...headStyle, background: controlCols.has(c) ? HIGHLIGHT.rowBg : NEUTRAL.ignoredBg }}>
                {colLetter(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  )
}

/** 一个 Sheet 的核对结论 —— 说清"核过了"，也说清"哪里不对" */
function checkLine(check: ControlCheck): { text: string; danger: boolean } {
  const want = `${num(check.expected.control_cum)} / ${num(check.expected.control_rate)}`
  if (check.ok) {
    return {
      text: `「控制值」已逐格核对：第 ${check.area[0]}~${check.area[1]} 行共 ${check.checked_rows} 行，两格都等于预警规则里的 ${want}。`,
      danger: false,
    }
  }
  const parts = [
    check.mismatch.length > 0 ? `${check.mismatch.length} 格与规则不符` : '',
    check.blank.length > 0 ? `${check.blank.length} 格规则里有值、表上却空着` : '',
    check.residual.length > 0 ? `${check.residual.length} 格是数据区之外的母版遗留值（导出碰不到它）` : '',
  ].filter(Boolean)
  return { text: `「控制值」有问题：${parts.join('，')}。规则里的数是 ${want}。`, danger: true }
}

/** 下载响应头 `X-Export-Summary` 里那批键的中文说法 */
const SUMMARY_LABELS: Record<string, string> = {
  template_points: '母版点位行数',
  added_points: '自动补的测点',
  shifted_rows: '为补行而下移的行',
  missing_raw_points: '网格放不下、没导出的点',
  added_raw_points: '原始数据区补进的点',
  cleared_stats: '清掉的旧统计表格',
  text_generated: '按本期重写的文字格',
  text_cleared: '清空的母版旧文字格',
  text_overflow: '装不下的文字',
}

function num(v: number | null): string {
  return v === null ? '（规则里没配）' : String(v)
}

function summaryText(v: unknown): string {
  if (Array.isArray(v)) return `${v.length} 个（${v.join('、')}）`
  if (typeof v === 'number') return String(v)
  if (v && typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${k} ${Array.isArray(x) ? x.join('、') : String(x)}`)
      .join('，')
  }
  return String(v)
}

export default function ExcelPreviewModal({
  open,
  onClose,
  doc,
  loading,
}: {
  open: boolean
  onClose: () => void
  /** 上一次请求拿到的预览；null = 还没预览过 */
  doc: ReportPreviewDoc | null
  loading: boolean
}) {
  const checks = new Map<string, ControlCheck>()
  for (const c of doc?.control_checks ?? []) checks.set(c.sheet, c)
  const summary = Object.entries(doc?.summary ?? {})

  return (
    <Modal
      title={`导出预览 —— ${doc?.filename ?? ''}`}
      open={open}
      onCancel={onClose}
      footer={null}
      width="82%"
      destroyOnClose
    >
      {loading ? (
        <div style={{ padding: 48, textAlign: 'center' }}>
          <Spin />
          <div style={{ marginTop: 12 }}>正在按当前模板生成这一期的报表…</div>
        </div>
      ) : !doc ? (
        <Empty description="还没有预览内容" />
      ) : (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="这就是点「导出 Excel」会拿到的那份文件"
            description={
              <>
                预览与下载在后端走同一条生成路径，界面上的每一格就是文件里的那一格。
                淡蓝底是本次会重写的数据区与「控制值」列；标红的格子是控制值与系统预警规则
                对不上的地方（鼠标停在格子上看原因）；斜体 <i>fx</i> 是公式，要等 Excel 打开后才算得出值。
              </>
            }
          />
          {summary.length > 0 && (
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
              本次生成对母版做了这些调整：
              {summary.map(([k, v]) => `${SUMMARY_LABELS[k] ?? k} ${summaryText(v)}`).join('；')}
            </Typography.Paragraph>
          )}
          <Tabs
            items={doc.sheets.map((s) => {
              const check = checks.get(s.name)
              const line = check ? checkLine(check) : null
              return {
                key: s.name,
                label: (
                  <span>
                    {s.name}
                    {s.state !== 'visible' && <Tag style={{ marginLeft: 6 }}>Excel 里隐藏</Tag>}
                    {s.truncated && <Tag style={{ marginLeft: 6 }}>只画了前面一部分</Tag>}
                    {check && !check.ok && <Tag color="error">控制值对不上</Tag>}
                  </span>
                ),
                children: (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {s.truncated && (
                      <Typography.Text type="warning" style={{ fontSize: 12 }}>
                        这张表超出预览上限（约 {MAX_PREVIEW_ROWS} 行 × {MAX_PREVIEW_COLS} 列），只画了左上角那一段 ——
                        文件本身是完整的。
                      </Typography.Text>
                    )}
                    {line && (
                      <Typography.Text type={line.danger ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
                        {line.text}
                      </Typography.Text>
                    )}
                    <SheetGrid sheet={s} check={check} />
                  </div>
                ),
              }
            })}
          />
        </>
      )}
    </Modal>
  )
}
