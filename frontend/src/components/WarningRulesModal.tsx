import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Divider,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  projectApi,
  warningRuleApi,
  type Indicator,
  type Project,
  type RuleInput,
  type RuleLevel,
  type StandardPreset,
  type WarningRuleSetInfo,
} from '../api/client'
import { NEUTRAL } from '../theme/tokens'

const INDICATOR_LABEL: Record<Indicator, string> = { settlement: '沉降', displacement: '水平位移' }
const LEVEL_LABEL: Record<RuleLevel, string> = { control: '控制值(预警)', alarm: '报警值' }

const ROWS: { indicator: Indicator; level: RuleLevel }[] = [
  { indicator: 'settlement', level: 'control' },
  { indicator: 'settlement', level: 'alarm' },
  { indicator: 'displacement', level: 'control' },
  { indicator: 'displacement', level: 'alarm' },
]

/**
 * 预警规则（双控：累计值 + 变化速率）。
 *
 * 规则**不再属于单个项目**：一套规则自己声明"作用于哪些项目"（多选 + 通用开关），
 * 几个基坑用同一套限值时，改一次它们一起跟随 —— 与「母版库」同一套逻辑。
 * 从项目里打开也照样能切换/新建其它规则（等于把规则库带在身边）。
 */
export default function WarningRulesModal({
  projectId,
  open,
  onClose,
}: {
  /** 从项目里打开时传入；从项目列表的「预警规则」进来则为空（纯规则库视角） */
  projectId?: number
  open: boolean
  onClose: () => void
}) {
  const [presets, setPresets] = useState<StandardPreset[]>([])
  const [sets, setSets] = useState<WarningRuleSetInfo[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  /** 正在编辑的规则 id；undefined = 正在新建 */
  const [setId, setSetId] = useState<number | undefined>()
  const [name, setName] = useState('')
  const [sourceNote, setSourceNote] = useState('')
  const [isGlobal, setIsGlobal] = useState(false)
  const [projectIds, setProjectIds] = useState<number[]>([])
  const [rules, setRules] = useState<Record<string, RuleInput>>({})
  /** 这套规则还被几个项目用着（>1 → 改限值会影响它们） */
  const [sharedCount, setSharedCount] = useState(0)
  /** 本项目没有自己的规则、正借用通用规则（保存后会为本项目单独建一套） */
  const [borrowing, setBorrowing] = useState(false)
  const [selectedPreset, setSelectedPreset] = useState<string | undefined>()
  const [saving, setSaving] = useState(false)

  /**
   * 「各项目现在用哪套规则」总览。
   *
   * 为什么要它（用户 2026-10-11「有些操作该集中到主界面」）：
   * 规则独立成库之后，下拉框里只能看到"这套作用于 3 个项目"，
   * 想知道**某个项目到底吃的是哪套**，得逐个点进去看 —— 那正是用户嫌繁琐的地方。
   * 这里在库视角下直接把"项目 → 规则"一次列出来。
   */
  const usage = useMemo(() => {
    const globalSet = sets.find((s) => s.is_global) ?? null
    return projects.map((p) => {
      const own = sets.find((s) => s.project_ids.includes(p.id)) ?? null
      return { project: p, own, fallback: own ? null : globalSet }
    })
  }, [projects, sets])

  const fillFromSet = (s: WarningRuleSetInfo) => {
    setSetId(s.id)
    setName(s.name)
    setSourceNote(s.source_note ?? '')
    setIsGlobal(s.is_global)
    setProjectIds(s.project_ids)
    setSharedCount(s.project_ids.length)
    setRules(byKeyOf(s.rules))
  }

  const load = useCallback(async () => {
    try {
      const [presetList, setList, projectList, info, effRules] = await Promise.all([
        warningRuleApi.standards(),
        warningRuleApi.listSets(),
        projectApi.list(),
        projectId === undefined
          ? Promise.resolve({ set_id: null, name: null, is_global: false, shared_project_count: 0 })
          : warningRuleApi.projectSet(projectId),
        projectId === undefined ? Promise.resolve([]) : warningRuleApi.list(projectId),
      ])
      setPresets(presetList)
      setSets(setList)
      setProjects(projectList)
      setSelectedPreset(undefined)

      if (projectId === undefined) {
        // 纯规则库视角：不预设哪一套，让用户从下拉里选或新建
        setBorrowing(false)
        setSetId(undefined)
        setName('')
        setSourceNote('')
        setIsGlobal(false)
        setProjectIds([])
        setSharedCount(0)
        setRules({})
        return
      }

      // 本项目**自己绑的**那套（不是"借用通用规则"那种兜底）
      const own = setList.find((s) => s.project_ids.includes(projectId))
      if (own) {
        setBorrowing(false)
        fillFromSet(own)
      } else {
        // 没绑 → 草稿模式：数值先把当前生效的带出来，保存时为它单独建一套
        setBorrowing(info.set_id !== null)
        setSetId(undefined)
        setName('')
        setProjectIds([projectId])
        setIsGlobal(false)
        setSharedCount(0)
        setSourceNote(effRules[0]?.source_note ?? '')
        setRules(byKeyOf(effRules))
      }
    } catch (e) {
      message.error((e as Error).message)
    }
  }, [projectId])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  const applyPreset = (key: string) => {
    setSelectedPreset(key)
    const preset = presets.find((p) => p.key === key)
    if (!preset) return
    setRules(byKeyOf(preset.rules))
    setSourceNote(preset.rules[0]?.source_note ?? '')
  }

  const setVal = (
    key: string,
    field: 'threshold_cumulative_mm' | 'threshold_rate_mm_per_day',
    v: number | null,
  ) => {
    setRules((prev) => ({ ...prev, [key]: { ...prev[key], [field]: v } }))
  }

  const collect = (): RuleInput[] => {
    const list: RuleInput[] = []
    for (const { indicator, level } of ROWS) {
      const r = rules[`${indicator}_${level}`]
      if (!r) continue
      list.push({
        indicator,
        level,
        threshold_cumulative_mm: r.threshold_cumulative_mm ?? null,
        threshold_rate_mm_per_day: r.threshold_rate_mm_per_day ?? null,
        is_active: true,
        source_note: sourceNote || undefined,
      })
    }
    return list
  }

  const save = async () => {
    const list = collect()
    if (list.length === 0) {
      message.warning('至少配置一条规则')
      return
    }
    if (!isGlobal && projectIds.length === 0) {
      message.warning('请选这套规则作用于哪些项目，或者勾上「所有项目通用」')
      return
    }
    const current = sets.find((s) => s.id === setId)
    const willAffect = (current?.project_names ?? []).length
    if (setId !== undefined && current && willAffect > 1 && _limitsChanged(current.rules, list)) {
      // 改共用规则的**限值**会一次性改变多个基坑的判定口径 —— 必须让用户看清影响谁，
      // 而不是只显示一个数字（2026-10-09 就是这么把 6 个项目的报警值改掉的）。
      const ok = await new Promise<boolean>((resolve) => {
        Modal.confirm({
          title: '改限值会影响这几个项目',
          content: (
            <>
              「{current.name}」正被 <b>{willAffect}</b> 个项目使用：
              <br />
              {current.project_names.join('、')}
              <br />
              保存后它们的预警判定口径会一起改变。
            </>
          ),
          okText: '确认修改',
          cancelText: '再想想',
          onOk: () => resolve(true),
          onCancel: () => resolve(false),
        })
      })
      if (!ok) return
    }
    setSaving(true)
    try {
      if (setId !== undefined) {
        await warningRuleApi.updateSet(setId, {
          name: name.trim() || undefined,
          source_note: sourceNote,
          rules: list,
        })
        await warningRuleApi.setSetProjects(setId, { project_ids: projectIds, is_global: isGlobal })
        message.success(
          sharedCount > 1
            ? `规则已保存 —— 这套规则作用于 ${projectIds.length} 个项目，它们的限值同步更新`
            : '预警规则已保存',
        )
      } else {
        if (!name.trim()) {
          message.warning('给这套规则起个名字（例如"八号基坑监测方案"）')
          setSaving(false)
          return
        }
        await warningRuleApi.createSet({
          name: name.trim(),
          rules: list,
          source_note: sourceNote,
          project_ids: projectIds,
          is_global: isGlobal,
        })
        message.success(
          isGlobal
            ? `已新建通用规则「${name.trim()}」——没单独配规则的项目都会用它`
            : `已新建规则「${name.trim()}」，作用于 ${projectIds.length} 个项目`,
        )
      }
      onClose()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const removeSet = async () => {
    if (setId === undefined) return
    try {
      await warningRuleApi.deleteSet(setId)
      message.success('规则已删除')
      void load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const renderRule = (indicator: Indicator, level: RuleLevel) => {
    const key = `${indicator}_${level}`
    const r = rules[key]
    return (
      <Space key={key} size={8}>
        <InputNumber
          style={{ width: 130 }}
          placeholder="累计限值"
          suffix="mm"
          value={r?.threshold_cumulative_mm ?? undefined}
          onChange={(v) => setVal(key, 'threshold_cumulative_mm', v)}
          min={0}
        />
        <span style={{ color: NEUTRAL.muted }}>/</span>
        <InputNumber
          style={{ width: 150 }}
          placeholder="速率限值"
          suffix="mm/d"
          value={r?.threshold_rate_mm_per_day ?? undefined}
          onChange={(v) => setVal(key, 'threshold_rate_mm_per_day', v)}
          min={0}
        />
      </Space>
    )
  }

  const columns: ColumnsType<{ indicator: Indicator; level: RuleLevel }> = [
    {
      title: '指标',
      dataIndex: 'indicator',
      width: 100,
      render: (v: Indicator) => (
        <Tag color={v === 'settlement' ? 'blue' : 'orange'}>{INDICATOR_LABEL[v]}</Tag>
      ),
    },
    {
      title: '级别',
      dataIndex: 'level',
      width: 120,
      render: (v: RuleLevel) => <Tag color={v === 'alarm' ? 'red' : 'gold'}>{LEVEL_LABEL[v]}</Tag>,
    },
    {
      title: '累计变化限值(mm) / 变化速率限值(mm/d)',
      render: (_, row) => renderRule(row.indicator, row.level),
    },
  ]

  return (
    <Modal
      title="预警规则（作用于哪些项目）"
      open={open}
      onOk={() => void save()}
      onCancel={onClose}
      width={760}
      okText="保存"
      cancelText="取消"
      confirmLoading={saving}
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Space wrap>
          <span>规则：</span>
          <Select
            style={{ width: 330 }}
            placeholder="新建一套规则"
            value={setId}
            onChange={(v) => {
              const s = sets.find((x) => x.id === v)
              if (s) {
                setSelectedPreset(undefined)
                setBorrowing(false)
                fillFromSet(s)
              }
            }}
            options={sets.map((s) => ({
              value: s.id,
              label: `${s.name}${s.is_global ? '（通用）' : ` · ${s.project_ids.length} 个项目`}`,
            }))}
          />
          <Button
            onClick={() => {
              setSetId(undefined)
              setName('')
              setSourceNote('')
              setIsGlobal(false)
              setProjectIds(projectId === undefined ? [] : [projectId])
              setRules({})
              setSharedCount(0)
              setBorrowing(false)
              setSelectedPreset(undefined)
            }}
          >
            新建一套
          </Button>
          {setId !== undefined && (
            <Popconfirm
              title="删除这套规则？"
              description="会解除它与各项目的绑定，项目将改为套用通用规则（如有）。"
              okText="删除"
              cancelText="取消"
              onConfirm={() => void removeSet()}
            >
              <Button danger type="text">
                删除这套
              </Button>
            </Popconfirm>
          )}
        </Space>

        {borrowing && (
          <Alert
            type="warning"
            showIcon
            message="本项目目前借用的是通用规则"
            description={
              <>
                下面带出的是那些限值。保存后会为<b>本项目单独建一套</b>，不会改动通用规则、
                也不影响别的项目。
              </>
            }
          />
        )}
        {setId !== undefined && sharedCount > 1 && (
          <Alert
            type="warning"
            showIcon
            message={`这套规则还被另外 ${Math.max(0, sharedCount - (projectIds.includes(projectId ?? -1) ? 1 : 0))} 个项目使用`}
            description={
              <>
                在这里改限值，用它的项目会<b>一起生效</b>（这正是共用规则的意思）。
                只想改某一个项目，请点「新建一套」并只勾那个项目。
              </>
            }
          />
        )}

        <Space>
          <span>规则名称：</span>
          <Input
            style={{ width: 330 }}
            placeholder="如：八号基坑监测方案"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <span>来源标注：</span>
          <Input
            style={{ width: 240 }}
            placeholder="可选，如 GB 50497-2019"
            value={sourceNote}
            onChange={(e) => setSourceNote(e.target.value)}
          />
        </Space>

        <div>
          <Space size={8} style={{ marginBottom: 4 }}>
            <Switch size="small" checked={isGlobal} onChange={setIsGlobal} />
            <Typography.Text>所有项目通用</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              （没单独配规则的项目由它兜底）
            </Typography.Text>
          </Space>
          <Select
            mode="multiple"
            style={{ width: '100%' }}
            placeholder={isGlobal ? '已设为通用，可再指定个别项目' : '这套规则作用于哪些项目（至少 1 个）'}
            value={projectIds}
            onChange={setProjectIds}
            optionFilterProp="label"
            options={projects.map((p) => ({
              value: p.id,
              label: `${p.name}${p.is_active ? '' : '（已归档）'}`,
            }))}
          />
        </div>

        <Space>
          <span>选择规范预设：</span>
          <Select
            style={{ width: 340 }}
            placeholder="作为起点，可再改"
            value={selectedPreset}
            onChange={applyPreset}
            options={presets.map((p) => ({ value: p.key, label: p.name }))}
          />
        </Space>

        <Divider style={{ margin: '4px 0' }} />
        <Table
          rowKey={(r) => `${r.indicator}_${r.level}`}
          columns={columns}
          dataSource={ROWS}
          pagination={false}
          size="small"
        />
        {_alarmEqualsControl(rules) && (
          <Typography.Text type="warning" style={{ fontSize: 12 }}>
            提示：报警值和控制值填成一样，实际只有一级判定（到值就报"报警"，不会先出"预警"）。
            规范的双控做法一般是报警值比控制值宽一档。
          </Typography.Text>
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          规则含义：|累计变化量| ≥ 报警值 → 报警；≥ 控制值 → 预警；变化速率同理，任一超标即触发。
          <br />
          这里填的「控制值」就是导出报表里「控制值」那两列 —— 改这里，报表跟着变，两处永远是一个数。
        </Typography.Text>

        {/* 纯库视角（从项目列表进来）才显示总览：在项目里打开时只看本项目就够了 */}
        {projectId === undefined && (
          <Collapse
            size="small"
            items={[
              {
                key: 'usage',
                label: `各项目现在用哪套规则（共 ${usage.length} 个项目）`,
                children: (
                  <Table
                    rowKey={(r) => r.project.id}
                    size="small"
                    pagination={false}
                    dataSource={usage}
                    columns={[
                      { title: '项目编号', width: 110, render: (_, r) => r.project.code },
                      {
                        title: '项目名称',
                        render: (_, r) => (
                          <Space size={6}>
                            <span>{r.project.name}</span>
                            {!r.project.is_active && <Tag>已归档</Tag>}
                          </Space>
                        ),
                      },
                      {
                        title: '当前生效的规则',
                        width: 260,
                        render: (_, r) => {
                          if (r.own)
                            return (
                              <Tag color={r.own.is_global ? 'blue' : 'green'}>
                                {r.own.name}
                                {r.own.is_global ? '（通用）' : ''}
                              </Tag>
                            )
                          if (r.fallback) return <Tag>{r.fallback.name}（通用规则兜底）</Tag>
                          return <Tag color="red">没有规则 —— 不判预警</Tag>
                        },
                      },
                      {
                        title: '共用',
                        width: 90,
                        render: (_, r) =>
                          r.own ? (
                            <Tooltip title={r.own.project_names.join('、')}>
                              {r.own.project_ids.length} 个项目
                            </Tooltip>
                          ) : (
                            <Typography.Text type="secondary">—</Typography.Text>
                          ),
                      },
                      {
                        title: '操作',
                        width: 190,
                        render: (_, r) => (
                          <Space size={4}>
                            {r.own ? (
                              <Button
                                size="small"
                                onClick={() => {
                                  setSelectedPreset(undefined)
                                  setBorrowing(false)
                                  fillFromSet(r.own as WarningRuleSetInfo)
                                }}
                              >
                                打开这套
                              </Button>
                            ) : (
                              <Tooltip title="只为这一个项目建一套，不影响别的项目">
                                <Button
                                  size="small"
                                  onClick={() => {
                                    setSetId(undefined)
                                    setName(`${r.project.name}监测方案`.slice(0, 100))
                                    setSourceNote('')
                                    setIsGlobal(false)
                                    setProjectIds([r.project.id])
                                    setRules({})
                                    setSharedCount(0)
                                    setBorrowing(false)
                                    setSelectedPreset(undefined)
                                  }}
                                >
                                  给它单独建一套
                                </Button>
                              </Tooltip>
                            )}
                          </Space>
                        ),
                      },
                    ]}
                    locale={{ emptyText: '还没有项目' }}
                  />
                ),
              },
            ]}
          />
        )}
      </Space>
    </Modal>
  )
}

/** [规则行] → { "indicator_level": 规则 }（表单内部用这个 key 定位） */
function byKeyOf(rows: { indicator: string; level: string }[]): Record<string, RuleInput> {
  const out: Record<string, RuleInput> = {}
  for (const r of rows) {
    out[`${r.indicator}_${r.level}`] = r as unknown as RuleInput
  }
  return out
}

/** 报警值和控制值是否填成了同一组数（那就只剩一级判定，双控形同虚设）。 */
function _alarmEqualsControl(rules: Record<string, RuleInput>): boolean {
  for (const indicator of ['settlement', 'displacement'] as Indicator[]) {
    const c = rules[`${indicator}_control`]
    const a = rules[`${indicator}_alarm`]
    if (!c || !a) continue
    if (
      (c.threshold_cumulative_mm ?? null) === (a.threshold_cumulative_mm ?? null) &&
      (c.threshold_rate_mm_per_day ?? null) === (a.threshold_rate_mm_per_day ?? null)
    ) {
      return true
    }
  }
  return false
}

/** 限值有没有真的被改动（只改名称/适用范围时不该触发确认）。 */
function _limitsChanged(before: { indicator: string; level: string }[], after: RuleInput[]): boolean {
  const key = (r: { indicator: string; level: string }) => `${r.indicator}_${r.level}`
  const oldMap = new Map(before.map((r) => [key(r), r as unknown as RuleInput]))
  for (const r of after) {
    const old = oldMap.get(key(r))
    if (!old) return true
    if ((old.threshold_cumulative_mm ?? null) !== (r.threshold_cumulative_mm ?? null)) return true
    if ((old.threshold_rate_mm_per_day ?? null) !== (r.threshold_rate_mm_per_day ?? null)) return true
  }
  return false
}
