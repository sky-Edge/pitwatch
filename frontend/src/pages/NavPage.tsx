import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'

import { Alert, Empty, Select, Space, Tag, Typography, message } from 'antd'
import {
  AppstoreOutlined,
  AuditOutlined,
  DatabaseOutlined,
  EditOutlined,
  FileExcelOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  ImportOutlined,
  LineChartOutlined,
  MergeCellsOutlined,
  SettingOutlined,
  TableOutlined,
  TagsOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons'

import MergedReportModal from '../components/MergedReportModal'
import ProjectDefaultsModal from '../components/ProjectDefaultsModal'
import TemplateLibraryModal from '../components/TemplateLibraryModal'
import WarningRulesModal from '../components/WarningRulesModal'
import type { Project } from '../api/client'
import { calcApi, projectApi } from '../api/client'
import type { ProjectAttention, ProjectWarningSummary } from '../api/client'

/**
 * 总导航 —— 把全站入口摊在一页上。
 *
 * 【为什么要这一页】
 * 顶栏只有 4 个按钮、项目内还有 7 个页面，加上 4 个只存在于弹窗里的配置项
 * （母版库 / 预警规则 / 项目默认值 / 合并报表），加起来十几个入口散在三处。
 * 现场同事培训时最常问的两句就是「母版在哪改」「合并报表从哪进」——
 * 说明入口本身就该有一张地图。
 *
 * 【分两段而不是平铺】
 * 入口分两类：**不依赖项目的**（导入、建项目、配置、维护）和**必须挂在某个项目上的**
 * （测点、录入、曲线、导出）。混在一起平铺会变成"按钮墙"，还得让用户自己去猜
 * "这个按钮点了会不会先问我选项目"。所以上半段直接进，下半段先选项目。
 */

type NavView =
  | { kind: 'projects' }
  | { kind: 'backup' }
  | { kind: 'audit' }
  | { kind: 'daily-import' }
  | { kind: 'batch-projects' }
  | { kind: 'points' | 'records' | 'import' | 'chart' | 'comparison' | 'warning-events' | 'reports'; project: Project }

interface Props {
  onNavigate: (v: NavView) => void
}

/** 一个入口卡片：图标 + 名字 + 一句话。整块可点。 */
function NavCard({
  icon,
  title,
  desc,
  onClick,
  href,
  muted,
}: {
  icon: ReactNode
  title: string
  desc: string
  onClick?: () => void
  href?: string
  muted?: boolean
}) {
  const body = (
    <>
      <span
        style={{
          fontSize: 17,
          lineHeight: 1,
          marginTop: 2,
          color: muted ? 'var(--wm-neutral-muted)' : 'var(--wm-brand-primary)',
        }}
      >
        {icon}
      </span>
      <span style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 2 }}>{title}</div>
        <div style={{ fontSize: 12, color: 'var(--wm-neutral-muted)', lineHeight: 1.5 }}>{desc}</div>
      </span>
    </>
  )

  const style: CSSProperties = {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    width: '100%',
    textAlign: 'left',
    padding: '12px 13px',
    background: 'var(--wm-neutral-white)',
    border: '0.5px solid var(--wm-grid-line)',
    borderRadius: 10,
    cursor: 'pointer',
    color: 'inherit',
    font: 'inherit',
  }

  if (href) {
    return (
      <a className="nav-card" href={href} download="使用说明书.pdf" style={{ ...style, textDecoration: 'none' }}>
        {body}
      </a>
    )
  }
  return (
    <button type="button" className="nav-card" onClick={onClick} style={style}>
      {body}
    </button>
  )
}

export default function NavPage({ onNavigate }: Props) {
  const [projects, setProjects] = useState<Project[]>([])
  const [summaries, setSummaries] = useState<Record<number, ProjectWarningSummary>>({})
  const [attentions, setAttentions] = useState<Record<number, ProjectAttention>>({})
  const [pickedId, setPickedId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [tplOpen, setTplOpen] = useState(false)
  const [rulesOpen, setRulesOpen] = useState(false)
  const [defaultsOpen, setDefaultsOpen] = useState(false)
  const [mergedOpen, setMergedOpen] = useState(false)

  useEffect(() => {
    void (async () => {
      setLoading(true)
      setLoadError(null)
      try {
        const [ps, ws, at] = await Promise.all([
          projectApi.list(),
          calcApi.warningSummary().catch(() => [] as ProjectWarningSummary[]),
          projectApi.attention().catch(() => [] as ProjectAttention[]),
        ])
        setProjects(ps)
        setSummaries(Object.fromEntries(ws.map((w) => [w.project_id, w])))
        setAttentions(Object.fromEntries(at.map((a) => [a.project_id, a])))

        // 默认选中：上次用过的项目 → 第一个在用项目。不自动跳到"有报警的那个"——
        // 那是替用户做决定，反而会让人以为系统只认那一个项目。
        const saved = Number(localStorage.getItem('monitor.nav.project') ?? '')
        const alive = ps.filter((p) => p.is_active)
        const initial =
          (saved && alive.find((p) => p.id === saved)?.id) || alive[0]?.id || ps[0]?.id || null
        setPickedId(initial)
      } catch (e) {
        setLoadError((e as Error).message)
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const picked = useMemo(() => projects.find((p) => p.id === pickedId) ?? null, [projects, pickedId])

  /** 选中项目的状态徽标：报警 > 预警 > 该测了 > 数据可疑 > 正常 */
  const badgesOf = (p: Project): { color?: string; text: string }[] => {
    // 归档项目不挂状态徽标（用户 2026-11-16）：它还能被选中去查历史，
    // 但不该在下拉里继续飘一个"10 项报警"，把在用的项目挤到后面。
    if (!p.is_active) return []
    const s = summaries[p.id]
    const a = attentions[p.id]
    const out: { color?: string; text: string }[] = []
    if (s?.has_alarm) out.push({ color: 'red', text: `${s.alarm_points.length} 项报警` })
    else if (s?.has_control) out.push({ color: 'gold', text: `${s.control_points.length} 项预警` })
    if (a?.needs_observation) out.push({ color: 'blue', text: `${a.stale_days ?? '多'} 天没测` })
    if (a?.suspect_count) out.push({ color: 'orange', text: `${a.suspect_count} 条可疑` })
    return out
  }

  const pick = (id: number) => {
    setPickedId(id)
    localStorage.setItem('monitor.nav.project', String(id))
  }

  const openProject = (kind: 'points' | 'records' | 'import' | 'chart' | 'comparison' | 'warning-events' | 'reports') => {
    if (!picked) {
      message.warning('先在上面选一个项目')
      return
    }
    onNavigate({ kind, project: picked })
  }

  const globalCards: { icon: ReactNode; title: string; desc: string; go?: () => void; href?: string; muted?: boolean }[] = [
    { icon: <ImportOutlined />, title: '每日导入', desc: '当天所有台账拖进来', go: () => onNavigate({ kind: 'daily-import' }) },
    { icon: <TableOutlined />, title: '批量建项目', desc: '一张点位清单建多个', go: () => onNavigate({ kind: 'batch-projects' }) },
    { icon: <MergeCellsOutlined />, title: '合并报表', desc: '南北两侧装订成一份', go: () => setMergedOpen(true) },
    { icon: <FolderOpenOutlined />, title: '母版库', desc: '报表样板与适用范围', go: () => setTplOpen(true) },
    { icon: <TagsOutlined />, title: '预警规则', desc: '一套规则给多个项目', go: () => setRulesOpen(true) },
    { icon: <SettingOutlined />, title: '项目默认值', desc: '新建项目自动套上', go: () => setDefaultsOpen(true) },
    { icon: <DatabaseOutlined />, title: '数据备份', desc: '快照与还原', go: () => onNavigate({ kind: 'backup' }), muted: true },
    { icon: <AuditOutlined />, title: '文件检查', desc: '已导出日报对账', go: () => onNavigate({ kind: 'audit' }), muted: true },
    { icon: <FileTextOutlined />, title: '使用说明', desc: '下载 PDF（逐按钮说明）', href: '/manual.pdf', muted: true },
  ]

  const projectCards: { icon: ReactNode; title: string; desc: string; kind: 'points' | 'records' | 'import' | 'chart' | 'comparison' | 'warning-events' | 'reports' }[] = [
    { icon: <AppstoreOutlined />, title: '测点管理', desc: '建点 · 初始值', kind: 'points' },
    { icon: <EditOutlined />, title: '逐日录入', desc: '填今天的读数', kind: 'records' },
    { icon: <FileExcelOutlined />, title: 'Excel 导入', desc: '一期一个文件', kind: 'import' },
    { icon: <LineChartOutlined />, title: '曲线图', desc: '趋势 · 拖点修数', kind: 'chart' },
    { icon: <TableOutlined />, title: '逐期对比', desc: '每期 ΔV 清单', kind: 'comparison' },
    { icon: <WarningOutlined />, title: '预警事件', desc: '超限明细', kind: 'warning-events' },
    { icon: <FileTextOutlined />, title: '成果导出', desc: 'Excel · 打印', kind: 'reports' },
  ]

  return (
    <>
      <Space direction="vertical" size={14} style={{ width: '100%' }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            总导航
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            所有入口都在这一页。上半段直接进，下半段先选项目。
          </Typography.Text>
        </div>

        {loadError && (
          <Alert
            type="error"
            showIcon
            message="项目列表没读到"
            description={`${loadError} —— 刷新页面重试；仍不行请确认后端是否正常。`}
          />
        )}

        <div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
            <Typography.Text strong>不用挑项目</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              全站共用
            </Typography.Text>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 10 }}>
            {globalCards.map((c) => (
              <NavCard key={c.title} icon={c.icon} title={c.title} desc={c.desc} onClick={c.go} href={c.href} muted={c.muted} />
            ))}
          </div>
        </div>

        <div style={{ height: 0.5, background: 'var(--wm-grid-line)' }} />

        <div>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
              marginBottom: 10,
              flexWrap: 'wrap',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <Typography.Text strong>进某个项目</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                先选项目，再挑功能
              </Typography.Text>
            </div>
            <Select
              style={{ minWidth: 300 }}
              value={pickedId ?? undefined}
              onChange={pick}
              loading={loading}
              placeholder="选一个项目"
              showSearch
              optionFilterProp="label"
              options={projects.map((p) => {
                const bs = badgesOf(p)
                const tail = p.is_active ? '' : ' · 已归档'
                // 名称和编号常常一模一样（用户习惯直接拿编号当名字），重复写两遍只会把下拉撑长
                const title = p.name === p.code ? p.name : `${p.name}（${p.code}）`
                return {
                  value: p.id,
                  label: `${title}${bs.length ? ' · ' + bs.map((b) => b.text).join(' / ') : ''}${tail}`,
                }
              })}
            />
          </div>

          {!picked && !loading ? (
            <Empty description="还没有项目 —— 先用上面的「批量建项目」或回项目列表建一个" />
          ) : (
            <>
              {picked && (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    flexWrap: 'wrap',
                    marginBottom: 10,
                  }}
                >
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {picked.name} 当前状态：
                  </Typography.Text>
                  {badgesOf(picked).length === 0 ? (
                    <Tag>正常</Tag>
                  ) : (
                    badgesOf(picked).map((b) => (
                      <Tag key={b.text} color={b.color}>
                        {b.text}
                      </Tag>
                    ))
                  )}
                  {attentions[picked.id]?.last_obs_date && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      最近观测 {attentions[picked.id].last_obs_date}
                    </Typography.Text>
                  )}
                </div>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 10 }}>
                {projectCards.map((c) => (
                  <NavCard
                    key={c.title}
                    icon={c.icon}
                    title={c.title}
                    desc={c.desc}
                    onClick={() => openProject(c.kind)}
                  />
                ))}
                <NavCard
                  icon={<ThunderboltOutlined />}
                  title="项目列表"
                  desc="全部项目 · 批量操作"
                  onClick={() => onNavigate({ kind: 'projects' })}
                />
              </div>
            </>
          )}
        </div>
      </Space>

      <TemplateLibraryModal open={tplOpen} onClose={() => setTplOpen(false)} />
      <WarningRulesModal open={rulesOpen} onClose={() => setRulesOpen(false)} />
      <ProjectDefaultsModal open={defaultsOpen} onClose={() => setDefaultsOpen(false)} />
      <MergedReportModal open={mergedOpen} onClose={() => setMergedOpen(false)} projects={projects} />
    </>
  )
}
