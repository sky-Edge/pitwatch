import { Select, Space, Switch, Typography } from 'antd'
import type { Project } from '../api/client'

/**
 * 「这份东西作用于哪些项目」选择器 —— 母版与预警规则共用同一个。
 *
 * 为什么要抽出来：母版库、预警规则库、导出页的「设置适用项目」三处都要这一套
 * （通用开关 + 多选项目）。三份各写一遍的话，早晚会出现"这处能设通用、那处不能"，
 * 或者提示文案不一致 —— 用户会以为它们不是一回事。
 */
export default function ScopePicker({
  projects,
  value,
  isGlobal,
  onValueChange,
  onGlobalChange,
  /** 东西的叫法：'母版' / '规则'，用于提示文案 */
  noun = '母版',
  globalHint = '（推荐给"各基坑版式一样"的母版）',
}: {
  projects: Project[]
  value: number[]
  isGlobal: boolean
  onValueChange: (ids: number[]) => void
  onGlobalChange: (v: boolean) => void
  noun?: string
  globalHint?: string
}) {
  return (
    <Space direction="vertical" style={{ width: '100%' }} size={6}>
      <Space size={8}>
        <Switch size="small" checked={isGlobal} onChange={onGlobalChange} />
        <Typography.Text>所有项目通用</Typography.Text>
        {globalHint && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {globalHint}
          </Typography.Text>
        )}
      </Space>
      <Select
        mode="multiple"
        style={{ width: '100%' }}
        placeholder={
          isGlobal ? '已设为通用，无需逐个勾选' : `选择这份${noun}作用于哪些项目（至少 1 个）`
        }
        disabled={isGlobal}
        value={value}
        onChange={onValueChange}
        optionFilterProp="label"
        options={projects.map((p) => ({ value: p.id, label: `${p.name}${p.is_active ? '' : '（已归档）'}` }))}
      />
      {!isGlobal && value.length === 0 && (
        <Typography.Text type="warning" style={{ fontSize: 12 }}>
          请至少选一个项目，或勾上「所有项目通用」—— 否则它在导出页里就找不到了。
        </Typography.Text>
      )}
    </Space>
  )
}
