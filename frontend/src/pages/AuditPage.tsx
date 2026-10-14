import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { FolderOpenOutlined, PlayCircleOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { auditApi, downloadBlob, fsApi, settingsApi, type AuditItem, type AuditResult, type DirEntry } from '../api/client'
import { NEUTRAL, WARNING_LEVEL } from '../theme/tokens'

/** 问题类型 → 颜色（后端给中文说明，这里只配颜色） */
const KIND_COLOR: Record<string, string> = {
  rate: 'red',
  delta: 'red',
  cum: 'red',
  prev_cum: 'orange',
  control_cum: 'gold',
  control_rate: 'gold',
  rate_over: 'red',
  level_diff: 'purple',
  unknown_point: 'magenta',
  report_empty: 'cyan',
  no_system_data: 'magenta',
}

const LEVEL_TEXT: Record<string, string> = { normal: '正常', control: '预警', alarm: '报警' }

/** 一条明细里"报表值 / 系统值"单元格的展示。
 *
 *  不直接铺 JSON —— 用户看的是"这个数对不对"，不是字段名。
 */
function ValueCell({ item, field }: { item: AuditItem; field: string }) {
  const rep = item.report?.[field]
  const sys = item.system?.[field]
  const bad = item.problems.some((p) => p.kind === field)
  const fmt = (v: number | null | undefined) => (v === null || v === undefined ? '—' : String(v))
  return (
    <Space size={4}>
      <span style={bad ? { color: WARNING_LEVEL.alarm.fg, fontWeight: 600 } : undefined}>{fmt(rep)}</span>
      {bad && <span style={{ color: WARNING_LEVEL.alarm.fg }}>≠</span>}
      {bad && <span style={{ color: NEUTRAL.muted }}>{fmt(sys)}</span>}
    </Space>
  )
}


export default function AuditPage({ onBack }: { onBack: () => void }) {
  const [folder, setFolder] = useState<string>('')
  const [status, setStatus] = useState<{ last_run: string | null; due: boolean } | null>(null)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<AuditResult | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [pickOpen, setPickOpen] = useState(false)
  const [kindFilter, setKindFilter] = useState<string | 'all'>('all')

  const loadStatus = useCallback(async () => {
    try {
      const st = await auditApi.status()
      setStatus(st)
      setFolder(st.folder ?? '')
    } catch (e) {
      message.error((e as Error).message)
    }
  }, [])

  useEffect(() => {
    void loadStatus()
  }, [loadStatus])

  const run = async () => {
    if (running) return // 防连点：几百份文件会跑一会儿
    setRunning(true)
    setResult(null)
    const t0 = Date.now()
    setElapsed(0)
    const tick = window.setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000)
    try {
      const r = await auditApi.run(folder.trim() || undefined)
      setResult(r)
      await loadStatus()
      if (r.problem_rows === 0) {
        message.success(`检查完成：比对了 ${r.checked} 份日报，没有发现问题`)
      } else {
        message.warning(`检查完成：${r.checked} 份里有 ${r.problem_rows} 行对不上或超限`, 8)
      }
    } catch (e) {
      message.error((e as Error).message, 8)
    } finally {
      window.clearInterval(tick)
      setRunning(false)
    }
  }

  const saveFolder = async () => {
    try {
      await settingsApi.update({ audit_folder: folder.trim() })
      message.success('文件夹已记住，以后打开这页直接用')
      await loadStatus()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const exportCsv = () => {
    if (!result) return
    const head = ['项目', '观测日期', '文件', '数据表', '行号', '测点', '观测项', '问题', '报表值', '系统值']
    const lines = [head.join(',')]
    for (const it of result.items) {
      const probs = it.problems.map((p) => result.problem_labels[p.kind] ?? p.kind).join('；')
      const kv = (o: Record<string, number | null>) =>
        Object.entries(o ?? {})
          .map(([k, v]) => `${k}=${v ?? ''}`)
          .join(' ')
      lines.push(
        [
          it.project_name,
          it.obs_date,
          it.file,
          it.sheet,
          String(it.excel_row),
          it.point_no,
          it.component_label,
          probs,
          kv(it.report),
          kv(it.system),
        ]
          .map((s) => `"${String(s).replace(/"/g, '""')}"`)
          .join(','),
      )
    }
    // 加 BOM，Excel 打开中文不乱码
    downloadBlob(new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' }), '导出文件检查结果.csv')
  }

  const items = useMemo(() => {
    if (!result) return []
    let out = result.items
    if (kindFilter !== 'all') out = out.filter((it) => it.problems.some((p) => p.kind === kindFilter))
    return out
  }, [result, kindFilter])

  const columns: ColumnsType<AuditItem> = [
    {
      title: '项目',
      width: 150,
      render: (_, r) => (
        <span>
          {r.project_name}
          {/* 编号和名字不一样时把编号也带上：导出的文件名里用的是**编号**，
              只显示名字的话，用户会以为"这份报表认错项目了"（实测踩过） */}
          {r.project_code && r.project_code !== r.project_name && (
            <>
              <br />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                编号 {r.project_code}
              </Typography.Text>
            </>
          )}
        </span>
      ),
    },
    { title: '观测日期', dataIndex: 'obs_date', width: 110 },
    { title: '测点', dataIndex: 'point_no', width: 90 },
    { title: '观测项', dataIndex: 'component_label', width: 80 },
    {
      title: '变化速率(mm/d)',
      width: 130,
      render: (_, r) => <ValueCell item={r} field="rate" />,
    },
    { title: '本次变化量', width: 120, render: (_, r) => <ValueCell item={r} field="delta" /> },
    { title: '本次累计', width: 120, render: (_, r) => <ValueCell item={r} field="cum" /> },
    {
      title: '问题',
      render: (_, r) => (
        <Space size={[4, 4]} wrap>
          {r.problems.map((p) => (
            <Tag key={p.kind} color={KIND_COLOR[p.kind] ?? 'default'}>
              {result?.problem_labels[p.kind] ?? p.kind}
              {p.kind === 'rate_over' && p.level ? `（${LEVEL_TEXT[String(p.level)] ?? p.level}）` : ''}
              {p.kind === 'level_diff' ? `（报表判${LEVEL_TEXT[String(p.report)] ?? r.report}／系统判${LEVEL_TEXT[String(p.system)] ?? r.system}）` : ''}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '来源',
      width: 200,
      render: (_, r) => (
        <Tooltip title={r.path}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {r.file}
            <br />
            {r.sheet} 第 {r.excel_row} 行
          </Typography.Text>
        </Tooltip>
      ),
    },
  ]

  const counters = Object.entries(result?.counters ?? {}).sort((a, b) => b[1] - a[1])
  // 名字不是完全一致、按相近认出来的（旧后端没有这两个字段，兜一下）
  const fuzzy = result?.fuzzy ?? []
  const fuzzyFiles = result?.fuzzy_files ?? fuzzy.length

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Card size="small">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap>
            <Button onClick={onBack}>← 返回项目列表</Button>
            <Typography.Text strong>文件检查</Typography.Text>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => void loadStatus()}>
              刷新状态
            </Button>
          </Space>
          <Alert
            type="info"
            showIcon
            message="把导出的日报和系统里的数比一遍"
            description="从文件夹里的「工程名称」和「本次监测时间」认出是哪个基坑哪一期，逐行比对：变化速率 / 本次变化量 / 本次累计变化量 是否和系统一致、变化速率有没有超过预警规则、报表上的「控制值」和现行规则是不是一个数。基坑名和系统里的项目名不完全一样也能认（带地名前缀、中文数字、括号限定都行），实在认不出的会单独列出来并告诉你最接近的是哪个；已归档项目的日报不查（归档了就不再出报表，查了只会淹没真问题）。"
          />
          <Space wrap>
            <Input
              style={{ width: 460 }}
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
              placeholder="导出日报统一存放的那个文件夹，如 D:\文件\测量台账\导出日报"
              onPressEnter={() => void run()}
            />
            <Button icon={<FolderOpenOutlined />} onClick={() => setPickOpen(true)}>
              浏览…
            </Button>
            <Button icon={<SaveOutlined />} disabled={!folder.trim()} onClick={() => void saveFolder()}>
              记住这个文件夹
            </Button>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={running}
              disabled={!folder.trim()}
              onClick={() => void run()}
            >
              开始检查
            </Button>
            {running && (
              <Typography.Text type="secondary">
                正在扫描并比对…已用 {elapsed} 秒（文件多时会慢，请不要重复点击）
              </Typography.Text>
            )}
          </Space>
          {status?.last_run && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              上次检查：{status.last_run.replace('T', ' ')}
              {status.due && ' —— 已经超过 7 天没查了'}
            </Typography.Text>
          )}
        </Space>
      </Card>

      {result && (
        <>
          <Card size="small">
            <Space wrap size={16}>
              <span>
                扫到 <b>{result.scanned}</b> 个文件，比对 <b>{result.checked}</b> 份、共 <b>{result.rows_total}</b> 行
              </span>
              {result.archived_files > 0 && (
                <Tooltip
                  title={result.archived
                    .map((a) => `${a.project_name}（${a.count} 份）：${a.files.join('、')}`)
                    .join('\n')}
                >
                  <Typography.Text type="secondary">
                    另有 {result.archived_files} 份属于已归档项目，没查
                  </Typography.Text>
                </Tooltip>
              )}
              <span>
                有问题的行：<b style={{ color: result.problem_rows ? WARNING_LEVEL.alarm.fg : WARNING_LEVEL.normal.fg }}>{result.problem_rows}</b>
              </span>
              {counters.map(([k, n]) => (
                <Tag
                  key={k}
                  color={KIND_COLOR[k] ?? 'default'}
                  style={{ cursor: 'pointer' }}
                  onClick={() => setKindFilter((prev) => (prev === k ? 'all' : k))}
                >
                  {result.problem_labels[k] ?? k} {n}
                  {kindFilter === k ? ' ×' : ''}
                </Tag>
              ))}
              {kindFilter !== 'all' && (
                <Button size="small" type="link" onClick={() => setKindFilter('all')}>
                  清除筛选
                </Button>
              )}
              <Button size="small" disabled={result.items.length === 0} onClick={exportCsv}>
                导出结果 CSV
              </Button>
            </Space>
          </Card>

          {result.truncated && (
            <Alert
              type="warning"
              showIcon
              message={`明细太多，只显示了前 ${result.items.length} 条`}
              description="上面的计数是完整的。先按类型点上面的标签筛一筛，或者缩小文件夹范围再查。"
            />
          )}

          {fuzzyFiles > 0 && (
            <Alert
              type="info"
              showIcon
              message={`有 ${fuzzyFiles} 份日报的基坑名和系统里的项目名不是完全一样，是按「相近」认的`}
              description={
                <>
                  <div>
                    名字对不上是常事（报表里常带地名前缀、换成中文数字、括号里加限定），
                    系统会自动认。下面这些是<b>看着像</b>就认的，扫一眼确认没认歪 ——
                    认错了会比较出一堆"对不上"，白忙。
                  </div>
                  <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                    {fuzzy.slice(0, 8).map((f) => (
                      <li key={f.file}>
                        {f.file} —— 认成「{f.project_name}」
                        {f.text ? `（报表里写的是「${f.text}」，${f.how.replace(/（\d+%）$/, '')}）` : `（${f.how}）`}
                      </li>
                    ))}
                    {fuzzyFiles > 8 && <li>……还有 {fuzzyFiles - 8} 份</li>}
                  </ul>
                </>
              }
            />
          )}

          {result.skipped.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`有 ${result.skipped.length} 个文件没能检查`}
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {result.skipped.slice(0, 10).map((s) => (
                    <li key={s.file}>
                      {s.file} —— {s.why}
                      {s.candidates && s.candidates.length > 0 && (
                        <div>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            最像的是：
                            {s.candidates
                              .map(
                                (c) =>
                                  `${c.project_name}（${Math.round(c.score * 100)}%${
                                    c.weak ? '，只是沾点边' : ''
                                  }）`,
                              )
                              .join('、')}
                            {s.ambiguous ? ' —— 都像，得你自己定' : ''}
                          </Typography.Text>
                        </div>
                      )}
                    </li>
                  ))}
                  {result.skipped.length > 10 && <li>……还有 {result.skipped.length - 10} 个</li>}
                </ul>
              }
            />
          )}

          {result.archived_files > 0 && (
            <Alert
              type="info"
              showIcon
              message={`有 ${result.archived_files} 份属于已归档项目，本次没查`}
              description={
                <>
                  {result.archived.map((a) => `${a.project_name}（${a.count} 份）`).join('、')}
                  <br />
                  归档项目的旧报表再报"对不上"也没有意义，所以默认跳过。
                  要把它们也查一遍，先在项目列表里把项目「恢复在用」，再跑一次即可。
                </>
              }
            />
          )}

          {result.items.length === 0 ? (
            <Card>
              <Empty
                description={
                  result.checked === 0
                    ? result.archived_files > 0
                      ? '这次没有可查的文件 —— 扫到的都属于已归档项目（按你的要求跳过了）'
                      : '没扫到可查的日报'
                    : '没有发现问题 —— 文件夹里的日报和系统里的数一一对得上'
                }
              />
            </Card>
          ) : (
            <Table
              rowKey={(r) => `${r.path}-${r.sheet}-${r.excel_row}`}
              size="small"
              columns={columns}
              dataSource={items}
              pagination={{ pageSize: 20, showSizeChanger: true }}
              scroll={{ x: 1400 }}
            />
          )}
        </>
      )}

      <FolderPicker
        open={pickOpen}
        initial={folder}
        onCancel={() => setPickOpen(false)}
        onPick={(p) => {
          setFolder(p)
          setPickOpen(false)
        }}
      />
    </Space>
  )
}

/** 服务端目录浏览（浏览器拿不到绝对路径，所以由后端列目录）。
 *
 *  只列文件夹，不列文件 —— 用户要选的是"放日报的那个文件夹"。
 */
function FolderPicker({
  open,
  initial,
  onCancel,
  onPick,
}: {
  open: boolean
  initial: string
  onCancel: () => void
  onPick: (path: string) => void
}) {
  const [cur, setCur] = useState<string | null>(null)
  const [entries, setEntries] = useState<DirEntry[]>([])
  const [parent, setParent] = useState<string | null>(null)
  const [isDrives, setIsDrives] = useState(true)
  const [loading, setLoading] = useState(false)

  const go = useCallback(async (path?: string) => {
    setLoading(true)
    try {
      const r = await fsApi.dirs(path)
      setCur(r.path)
      setParent(r.parent)
      setEntries(r.dirs)
      setIsDrives(r.is_drive_list)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void go(initial.trim() || undefined)
  }, [open, initial, go])

  return (
    <Modal
      title="选择文件夹"
      open={open}
      onCancel={onCancel}
      onOk={() => cur && onPick(cur)}
      okText={cur ? `就用这个：${cur}` : '请先选一个文件夹'}
      okButtonProps={{ disabled: !cur }}
      cancelText="取消"
      width={640}
    >
      <Space direction="vertical" style={{ width: '100%' }} size="small">
        <Space wrap size={6}>
          <Button size="small" onClick={() => void go(undefined)}>
            盘符
          </Button>
          <Button size="small" disabled={!parent} onClick={() => void go(parent ?? undefined)}>
            上一层
          </Button>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            当前：{cur ?? '（选一个盘）'}
          </Typography.Text>
        </Space>
        <div
          style={{
            maxHeight: 320,
            overflow: 'auto',
            border: `1px solid ${NEUTRAL.ignoredBg}`,
            borderRadius: 4,
            padding: 4,
          }}
        >
          {entries.length === 0 && !loading && (
            <Typography.Text type="secondary" style={{ padding: 8, display: 'block' }}>
              这里没有子文件夹
            </Typography.Text>
          )}
          {entries.map((d) => (
            <div
              key={d.path}
              style={{ padding: '4px 8px', cursor: 'pointer' }}
              onClick={() => void go(d.path)}
              onDoubleClick={() => onPick(d.path)}
            >
              <FolderOpenOutlined /> {d.name}
            </div>
          ))}
        </div>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          点一下进文件夹；到了就按下面的「就用这个」。也可以直接在上面输入框里粘路径。
          {isDrives && '（当前是盘符列表）'}
        </Typography.Text>
      </Space>
    </Modal>
  )
}
