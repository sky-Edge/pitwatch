import { Button, Card, Segmented, Space, Steps, Tag, Typography } from 'antd'
import { LeftOutlined } from '@ant-design/icons'
import type { Project } from '../api/client'

/** 项目内功能页（与 App 的 View 一一对应） */
export type ProjectViewKind =
  | 'points'
  | 'records'
  | 'import'
  | 'chart'
  | 'comparison'
  | 'warning-events'
  | 'reports'

interface StepDef {
  title: string
  hint: string
  views: { key: ProjectViewKind; label: string }[]
}

/** 四步工作流：按顺序做就不会错（傻瓜式引导） */
export const PROJECT_STEPS: StepDef[] = [
  {
    title: '① 测点与初始值',
    hint: '建好测点，录一次初始值',
    views: [{ key: 'points', label: '测点管理' }],
  },
  {
    title: '② 观测数据',
    hint: '每天录入，或 Excel 批量导入',
    views: [
      { key: 'records', label: '逐日录入' },
      { key: 'import', label: 'Excel 导入' },
    ],
  },
  {
    title: '③ 分析与预警',
    hint: '看曲线、逐期对比、预警提示',
    views: [
      { key: 'chart', label: '曲线图' },
      { key: 'comparison', label: '逐期对比' },
      { key: 'warning-events', label: '预警事件' },
    ],
  },
  {
    title: '④ 成果导出',
    hint: '一键导出日报',
    views: [{ key: 'reports', label: '成果导出' }],
  },
]

/** 步骤序号（0 起）：当前视图属于第几步 */
export function stepIndexOfView(kind: ProjectViewKind): number {
  const i = PROJECT_STEPS.findIndex((s) => s.views.some((v) => v.key === kind))
  return i < 0 ? 0 : i
}

/** 项目流程导航条：告诉用户"在做什么、下一步是什么"，点击可跳转 */
export default function ProjectFlowNav({
  project,
  current,
  onChange,
  onBack,
}: {
  project: Project
  current: ProjectViewKind
  onChange: (v: ProjectViewKind) => void
  onBack: () => void
}) {
  const stepIndex = stepIndexOfView(current)
  const step = PROJECT_STEPS[stepIndex]

  return (
    <Card size="small" style={{ marginBottom: 16 }}>
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Space wrap>
          <Button icon={<LeftOutlined />} onClick={onBack}>
            项目列表
          </Button>
          <Typography.Text strong style={{ fontSize: 16 }}>
            {project.name}
          </Typography.Text>
          <Tag>{project.code}</Tag>
        </Space>
        <Steps
          size="small"
          current={stepIndex}
          onChange={(i) => onChange(PROJECT_STEPS[i].views[0].key)}
          items={PROJECT_STEPS.map((s) => ({ title: s.title, description: s.hint }))}
        />
        {step.views.length > 1 && (
          <Segmented
            value={current}
            onChange={(v) => onChange(v as ProjectViewKind)}
            options={step.views.map((v) => ({ label: v.label, value: v.key }))}
          />
        )}
      </Space>
    </Card>
  )
}
