import { useEffect, useState } from 'react'
import { Button, Layout, message, Space, Typography } from 'antd'
import { DatabaseOutlined, HomeOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import ProjectsPage from './pages/ProjectsPage'
import PointsPage from './pages/PointsPage'
import RecordsPage from './pages/RecordsPage'
import ComparisonPage from './pages/ComparisonPage'
import ImportPage from './pages/ImportPage'
import ChartPage from './pages/ChartPage'
import WarningEventsPage from './pages/WarningEventsPage'
import ReportsPage from './pages/ReportsPage'
import BackupPage from './pages/BackupPage'
import BatchProjectsPage from './pages/BatchProjectsPage'
import AuditPage from './pages/AuditPage'
import ProjectFlowNav, { type ProjectViewKind } from './components/ProjectFlowNav'
import { systemApi, type Project } from './api/client'
import { APP_NAME, BRAND, LAYOUT } from './theme/tokens'
import type { RecordsFocus } from './navigation'

/**
 * 界面依赖的后端能力。少了任何一项，说明**后端还是旧代码**。
 *
 * 前端改了 vite 会自动热更新，后端改了必须重启 —— 两者不同步时，
 * 用户会看到"按钮是新的、点下去还是老行为"，最难自查。启动时对一次就知道。
 */
const REQUIRED_BACKEND_FEATURES = [
  'generate_preview',
  'generate_on_conflict',
  // 生成数据：无趋势、围绕初值涨跌交替（旧后端会生成一条单调爬升/下降的曲线）
  'generate_realistic_v4',
  // 生成数据支持「全站仪观测模式」（旧后端没有这个参数）
  'generate_total_station',
  // 日期接口区分「有读数」与「仅缺测记录」（旧后端只有前者，日期标记会不准）
  'record_dates_with_values',
  // 预警页的「一键修正报警值」（旧后端没有这个接口，点了会 404）
  'warning_fix_values',
  // 母版点位不够时导出会自动补行（旧后端会静默丢掉多出来的测点）
  'export_expand_points',
  // 原始数据区（坐标小表）也会自动补块（旧后端那一区会整条丢掉多出来的点）
  'export_expand_raw_points',
  // 导出报表不显示预警（旧后端会写预警等级列、并带出母版里别的基坑的预警统计）
  'export_no_warning',
  // 导入预览带"台账 / 点位清单"标记，两个上传入口互相指路（旧后端没有 kind 字段）
  'import_sheet_kind',
  // 批量建项目会挡下逐期观测台账并指路（旧后端会把几毫米的读数当成高程建点）
  'batch_projects_reject_ledger',
  // 上传母版：名称可留空（用文件名兜底）、传完还能改名（旧后端会把空名字存进去）
  'template_name_optional',
  // 报表「控制值」与预警规则同源（旧后端：母版套打照抄母版、导入的控制值不进规则）
  'control_values_single_source',
  // 导出时把母版里"别的基坑名"换成本项目名（旧后端会原样留着，报表串名）
  'master_project_name_sync',
  // 模板独立成"模板库"：一份母版可作用于多个项目（旧后端一个模板只属于一个项目）
  'template_library',
  // 概况/分析/成果提交三页按本期数据自动生成（旧后端会原样带出别工程的整段文字）
  'auto_text_pages',
  // 预警规则独立成"规则库"：一套规则可作用于多个项目（旧后端一个项目一套）
  'warning_rule_library',
  // 主界面集中管理：全站默认值（新建项目自动套规则/母版/天气）+ 项目列表批量操作
  // （旧后端没有 /settings 与 /bulk/projects/*，界面上那几个按钮点了会 404）
  'app_settings',
  'bulk_project_ops',
  // 导出文件检查：扫一个文件夹，把导出的日报和系统逐项比（旧后端没有 /audit/*）
  'export_file_audit',
]

/** focus：从「需要关注」跳进来时携带的定位目标（哪一天 / 哪个测点） */
type View =
  | { kind: 'projects' }
  | { kind: 'backup' }
  /** 导出文件检查：项目无关（扫的是一个文件夹），所以挂在顶栏 */
  | { kind: 'audit' }
  /** 项目列表级的批量建项目（不属于任何单个项目，所以不挂 ProjectViewKind） */
  | { kind: 'batch-projects' }
  | { kind: ProjectViewKind; project: Project; focus?: RecordsFocus }

export default function App() {
  const [view, setView] = useState<View>({ kind: 'projects' })

  // ⚠ 必须写成"逐个排除"而不是 `ARRAY.includes(view.kind)` ——
  // 只有这种形式 TypeScript 才会把它当类型守卫，进而把 view 收窄成"项目内视图"。
  // 换成 includes() 后 `view.project` 会直接编译不过（踩过）。
  const isProjectView =
    view.kind !== 'projects' && view.kind !== 'backup' && view.kind !== 'audit' && view.kind !== 'batch-projects'
  const goProjects = () => setView({ kind: 'projects' })

  // 启动自检：后端是不是最新代码 / 到底有没有起来。
  // 宁可开机时说一句，也别让用户对着"点了没反应"的功能猜半天。
  useEffect(() => {
    void (async () => {
      try {
        const h = await systemApi.health()
        const missing = REQUIRED_BACKEND_FEATURES.filter((f) => !(h.features ?? []).includes(f))
        if (missing.length > 0) {
          message.warning({
            content: '后端服务是旧版本，新功能不会生效 —— 请关掉黑窗口，重新运行「启动系统.bat」',
            duration: 10,
          })
        }
      } catch {
        message.error({
          content: '连不上后端服务 —— 请确认「启动系统.bat」还在运行',
          duration: 10,
        })
      }
    })()
  }, [])

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* 顶栏底色/文字色由 antd 主题提供（theme/antd.ts 的 Layout.headerBg / headerColor），此处只保留布局 */}
      <Layout.Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Typography.Title level={4} style={{ color: BRAND.onPrimary, margin: 0 }}>
          {APP_NAME}
        </Typography.Title>
        <Space>
          <Button type="text" icon={<HomeOutlined />} style={{ color: BRAND.onPrimary }} onClick={goProjects}>
            项目列表
          </Button>
          <Button
            type="text"
            icon={<DatabaseOutlined />}
            style={{ color: BRAND.onPrimary }}
            onClick={() => setView({ kind: 'backup' })}
          >
            数据备份
          </Button>
          <Button
            type="text"
            icon={<SafetyCertificateOutlined />}
            style={{ color: BRAND.onPrimary }}
            onClick={() => setView({ kind: 'audit' })}
          >
            文件检查
          </Button>
        </Space>
      </Layout.Header>
      <Layout.Content style={{ padding: LAYOUT.pagePadding }}>
        {view.kind === 'projects' && (
          <ProjectsPage
            onManagePoints={(p) => setView({ kind: 'points', project: p })}
            onRecords={(p, focus) => setView({ kind: 'records', project: p, focus })}
            onComparison={(p) => setView({ kind: 'comparison', project: p })}
            onImport={(p) => setView({ kind: 'import', project: p })}
            onChart={(p) => setView({ kind: 'chart', project: p })}
            onWarningEvents={(p) => setView({ kind: 'warning-events', project: p })}
            onReports={(p) => setView({ kind: 'reports', project: p })}
            onBatchProjects={() => setView({ kind: 'batch-projects' })}
            onAudit={() => setView({ kind: 'audit' })}
          />
        )}
        {isProjectView && (
          <ProjectFlowNav
            project={view.project}
            current={view.kind}
            onChange={(k) => setView({ kind: k, project: view.project })}
            onBack={goProjects}
          />
        )}
        {view.kind === 'points' && <PointsPage project={view.project} onBack={goProjects} onRecords={() => setView({ kind: 'records', project: view.project })} />}
        {view.kind === 'records' && <RecordsPage projectId={view.project.id} focus={view.focus} onBack={goProjects} />}
        {view.kind === 'import' && <ImportPage project={view.project} onBack={goProjects} />}
        {view.kind === 'chart' && <ChartPage projectId={view.project.id} onBack={goProjects} />}
        {view.kind === 'comparison' && <ComparisonPage projectId={view.project.id} onBack={goProjects} />}
        {view.kind === 'warning-events' && <WarningEventsPage projectId={view.project.id} onBack={goProjects} />}
        {view.kind === 'reports' && <ReportsPage projectId={view.project.id} onBack={goProjects} />}
        {view.kind === 'backup' && <BackupPage />}
        {view.kind === 'audit' && <AuditPage onBack={goProjects} />}
        {view.kind === 'batch-projects' && <BatchProjectsPage onBack={goProjects} />}
      </Layout.Content>
    </Layout>
  )
}
