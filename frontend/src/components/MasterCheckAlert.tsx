import { Alert, Button, Space, Spin, Typography } from 'antd'
import type { TemplateCheckIssue, TemplateCheckResult } from '../api/client'

/**
 * 母版体检结果 —— 导出**前**就把话说清楚。
 *
 * 为什么要它（用户 2026-10-16）：「做个模版检查吧」。
 * 报表导出只会重写它**认得**的那些行：母版上多出来的页、第二块表头、数据区界线以下的旧值，
 * 导出一个字节都不会碰 —— 发出去的就是别的基坑的旧数。这类问题在 Excel 里人眼很难发现
 * （2026-10-16 那次是「控制值」一列上半跟着规则、下半还是母版的 30/10）。
 *
 * 三级各干什么：
 * · error —— 这份文件一定有问题，**拦住导出按钮**，改好了再来；
 * · warn —— 能导，但有些地方不会被更新；
 * · info —— 只是把"将要发生什么"讲明白（补几行、换掉哪个基坑名）。
 *
 * 判据全部在后端（`template_check_service`），而且复用导出端那几个函数 ——
 * 体检和导出各写一套判据，就又会回到"两头各说各话"。
 */

/** 一条发现：标题 + 为什么 + 具体是哪些格子 */
function IssueLine({ issue }: { issue: TemplateCheckIssue }) {
  return (
    <div style={{ marginBottom: 6 }}>
      <Typography.Text strong style={{ fontSize: 13 }}>
        {issue.title}
      </Typography.Text>
      <div style={{ fontSize: 12, lineHeight: 1.6 }}>{issue.detail}</div>
      {issue.cells.length > 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          涉及：{issue.cells.slice(0, 8).join('、')}
          {issue.cells.length > 8 ? ` 等 ${issue.cells.length} 处` : ''}
        </Typography.Text>
      )}
    </div>
  )
}

export default function MasterCheckAlert({
  result,
  loading,
  onRecheck,
  onConfigRules,
}: {
  /** null = 还没查（内置标准版式不需要查） */
  result: TemplateCheckResult | null
  loading: boolean
  onRecheck: () => void
  /** 点「去配预警规则」—— 没配控制值时导出本来就会被后端拦 422，与其撞墙不如直接给入口 */
  onConfigRules: () => void
}) {
  if (loading) {
    return (
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        <Spin size="small" style={{ marginRight: 6 }} />
        正在体检这份母版套到本项目上会怎样…
      </Typography.Text>
    )
  }
  if (!result) return null

  const errors = result.issues.filter((i) => i.level === 'error')
  const warns = result.issues.filter((i) => i.level === 'warn')
  const infos = result.issues.filter((i) => i.level === 'info')
  const needRules = errors.some((i) => i.code === 'no_control_rule' || i.code === 'half_control_rule')

  if (result.issues.length === 0) {
    return (
      <Alert
        type="success"
        showIcon
        message="体检通过"
        description="这份母版的页、表头、数据区和本项目都对得上，「控制值」也能按现行预警规则逐格重写。"
      />
    )
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="small">
      {errors.length > 0 && (
        <Alert
          type="error"
          showIcon
          message={`先别导出 —— 有 ${errors.length} 处会让这份报表出错`}
          description={
            <>
              {errors.map((i) => (
                <IssueLine key={i.code + i.title} issue={i} />
              ))}
              <Button size="small" onClick={onRecheck} style={{ marginTop: 4 }}>
                改好了，重新体检
              </Button>
            </>
          }
          action={needRules ? <Button size="small" danger onClick={onConfigRules}>去配预警规则</Button> : undefined}
        />
      )}
      {warns.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`可以导出，但有 ${warns.length} 处不会被更新`}
          description={warns.map((i) => (
            <IssueLine key={i.code + i.title} issue={i} />
          ))}
        />
      )}
      {infos.length > 0 && (
        <Alert
          type="info"
          showIcon
          message="导出时会自动做这些事"
          description={infos.map((i) => (
            <IssueLine key={i.code + i.title} issue={i} />
          ))}
        />
      )}
    </Space>
  )
}
