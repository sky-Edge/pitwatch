import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  DatePicker,
  Input,
  InputNumber,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { Dayjs } from 'dayjs'
import {
  batchImportApi,
  projectApi,
  type BatchCommitResult,
  type BatchPoint,
  type BatchPreview,
  type Project,
} from '../api/client'
import { WARNING_LEVEL } from '../theme/tokens'
import { plainCalendarPanel } from '../components/CalendarCell'

const { Dragger } = Upload

/** 清单里的一行 = 一个测点；group = 用户指定的"属于哪个基坑" */
interface Row extends BatchPoint {
  key: string
  group: string
}

interface GroupCfg {
  project_code: string
  project_name: string
  obs_date: Dayjs | null
  /** 点号规则：前缀 + 起始序号（按清单里的出现顺序往下编） */
  prefix: string
  start: number
  /** 序号补零位数，0 = 不补（JJC1 / JJC01） */
  pad: number
}

/** 点号规则的默认值：JJC 是本项目现有测点的主流前缀（全库 60 个点都是它） */
const DEFAULT_PREFIX = 'JJC'
const DEFAULT_START = 1

/** 读值按米显示（库里统一 mm），便于和原始表逐行核对 */
const fmtM = (mm: number | null): string => (mm == null ? '—' : (mm / 1000).toFixed(3))

/** 字段中文名（把系统认出来的列写给用户核对——X/Y 是按数值大小判的） */
const COL_LABEL: Record<string, string> = { point_no: '点号', x: 'X', y: 'Y', z: 'Z(高程)' }
const colMapText = (m: Record<string, number>): string =>
  Object.keys(COL_LABEL)
    .filter((k) => m[k])
    .map((k) => `${COL_LABEL[k]}=第${m[k]}列`)
    .join(' · ')

export default function BatchProjectsPage({ onBack }: { onBack: () => void }) {
  const [step, setStep] = useState(0)
  const [parsing, setParsing] = useState(false)
  const [preview, setPreview] = useState<BatchPreview | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [cfgs, setCfgs] = useState<Record<string, GroupCfg>>({})
  const [projects, setProjects] = useState<Project[]>([])
  const [selKeys, setSelKeys] = useState<string[]>([])
  const [bulkTo, setBulkTo] = useState('')
  const [bulkDate, setBulkDate] = useState<Dayjs | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<BatchCommitResult | null>(null)

  useEffect(() => {
    void projectApi
      .list(true)
      .then(setProjects)
      .catch(() => message.warning('项目列表没读到，将按"全部新建"处理'))
  }, [])

  /** 命中已有项目时，查它现在有多少测点/数据 ——
   *  用来防"同一个基坑建出两套点"：用户之前在项目里建过测点（如 JJC1~12），
   *  这次清单里同一批点的编号不一样（如 15号1~11），同名匹配不上就会又建一套。 */
  const [existing, setExisting] = useState<Record<string, { points: number; records: number }>>({})

  /** 每组的点数（只统计真有点的组——输入过程中的中间态不会冒出来） */
  const groupStats = useMemo(() => {
    const m = new Map<string, number>()
    rows.forEach((r) => {
      if (r.group) m.set(r.group, (m.get(r.group) ?? 0) + 1)
    })
    return m
  }, [rows])
  const groupNames = useMemo(() => [...groupStats.keys()], [groupStats])

  /** 组的生效配置：用户改过的优先，否则按同名已有项目/组名推默认值 */
  const cfgOf = (name: string): GroupCfg => {
    const over = cfgs[name]
    const hit = projects.find((p) => p.code === name || p.name === name)
    return {
      project_code: over?.project_code ?? hit?.code ?? name,
      project_name: over?.project_name ?? hit?.name ?? name,
      obs_date: over?.obs_date ?? null,
      prefix: over?.prefix ?? DEFAULT_PREFIX,
      start: over?.start ?? DEFAULT_START,
      pad: over?.pad ?? 0,
    }
  }
  const patchCfg = (name: string, patch: Partial<GroupCfg>) =>
    setCfgs((prev) => ({ ...prev, [name]: { ...cfgOf(name), ...patch } }))

  /** 组内序号：按清单里的出现顺序从 1 开始（不是用表里原本的那串数字） */
  const seqOf = useMemo(() => {
    const counter: Record<string, number> = {}
    const m = new Map<string, number>()
    rows.forEach((r) => {
      if (!r.group) return
      counter[r.group] = (counter[r.group] ?? 0) + 1
      m.set(r.key, counter[r.group])
    })
    return m
  }, [rows])

  /** 该行最终会用的点号（表格里实时显示，落库用的也是它） */
  const pointNoOf = (r: Row): string => {
    if (!r.group) return ''
    const cfg = cfgOf(r.group)
    const n = cfg.start + (seqOf.get(r.key) ?? 1) - 1
    return `${cfg.prefix}${cfg.pad > 0 ? String(n).padStart(cfg.pad, '0') : n}`
  }

  /** 某组的点号区间文案：JJC1 ~ JJC12 */
  const rangeOf = (name: string): string => {
    const cfg = cfgOf(name)
    const n = groupStats.get(name) ?? 0
    if (n === 0) return ''
    const fmt = (x: number) => `${cfg.prefix}${cfg.pad > 0 ? String(x).padStart(cfg.pad, '0') : x}`
    return n === 1 ? fmt(cfg.start) : `${fmt(cfg.start)} ~ ${fmt(cfg.start + n - 1)}`
  }

  /** 把若干行归到某个基坑（组不存在就新建） */
  const assign = (keys: string[], rawName: string) => {
    const name = rawName.trim()
    if (!name || keys.length === 0) return
    setRows((prev) => prev.map((r) => (keys.includes(r.key) ? { ...r, group: name } : r)))
  }

  /** 取消某个分组：把组里的点退回"未分组"（不删点） */
  const clearGroup = (name: string) =>
    setRows((prev) => prev.map((r) => (r.group === name ? { ...r, group: '' } : r)))

  const handleFile = async (file: File) => {
    setParsing(true)
    try {
      const res = await batchImportApi.preview(file)
      const next: Row[] = res.points.map((p, i) => ({ ...p, key: `${i}`, group: '' }))
      // 有建议就先用着（用户仍可逐行改）——分组以人看着平面图定的为准，系统只是给个起点
      const suggested = new Map<string, string>()
      res.suggested_groups.forEach((g) => g.point_nos.forEach((no) => suggested.set(no, g.name)))
      next.forEach((r) => {
        r.group = suggested.get(r.point_no) ?? ''
      })
      setPreview(res)
      setRows(next)
      setCfgs({})
      setSelKeys([])
      setResult(null)
      setStep(1)
      if (res.suggested_groups.length === 0) {
        message.info('没能自动分组，请在表格里给每行选一个基坑（可以勾选多行一起归类）')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setParsing(false)
    }
  }

  const beforeUpload = (file: File) => {
    const lower = file.name.toLowerCase()
    if (!lower.endsWith('.xlsx') && !lower.endsWith('.xlsm') && !lower.endsWith('.xls')) {
      message.error(`仅支持 .xlsx / .xlsm / .xls：${file.name}`)
      return Upload.LIST_IGNORE
    }
    void handleFile(file)
    return false
  }

  const submit = async () => {
    if (groupNames.length === 0) {
      message.warning('还没有分好组 —— 请在表格「所属基坑」列给点号选一个基坑')
      return
    }
    const noDate = groupNames.filter((n) => !cfgOf(n).obs_date)
    if (noDate.length > 0) {
      message.warning(`还有 ${noDate.length} 组没选初始值日期：${noDate.join('、')}`)
      return
    }
    const noPrefix = groupNames.filter((n) => !cfgOf(n).prefix.trim())
    if (noPrefix.length > 0) {
      message.warning(`这些组没填点号前缀：${noPrefix.join('、')}`)
      return
    }
    setSubmitting(true)
    try {
      const res = await batchImportApi.commit(
        groupNames.map((n) => {
          const cfg = cfgOf(n)
          return {
            group_name: n,
            project_code: cfg.project_code.trim(),
            project_name: cfg.project_name.trim(),
            obs_date: cfg.obs_date!.format('YYYY-MM-DD'),
            // 点号按规则生成（前缀 + 起始序号，组内顺序往下编）——不用表里的名字
            points: rows
              .filter((r) => r.group === n)
              .map((r) => ({ point_no: pointNoOf(r), x: r.x, y: r.y, z: r.z })),
          }
        }),
      )
      setResult(res)
      setStep(2)
      const t = res.totals
      message.success(`已生成 ${t.projects_created + t.projects_reused} 个项目、${t.points_created} 个测点`)
    } catch (e) {
      message.error(`生成失败：${(e as Error).message}`)
    } finally {
      setSubmitting(false)
    }
  }

  const undecided = rows.filter((r) => !r.group).length

  /** 命中已有项目的那些编号 */
  const matchedCodes = useMemo(
    () =>
      [
        ...new Set(
          groupNames
            .map((n) => cfgOf(n).project_code.trim())
            .filter((c) => !!c && projects.some((p) => p.code === c)),
        ),
      ],
    // cfgOf 依赖 cfgs/projects；把它们列进依赖即可（cfgOf 本身每次渲染重建）
    [groupNames, cfgs, projects],
  )

  useEffect(() => {
    const todo = matchedCodes.filter((c) => !(c in existing))
    if (todo.length === 0) return
    let alive = true
    void Promise.all(
      todo.map(async (code) => {
        const p = projects.find((x) => x.code === code)
        if (!p) return null
        try {
          const d = await projectApi.dataCount(p.id)
          return [code, { points: d.points, records: d.records }] as const
        } catch {
          return null
        }
      }),
    ).then((list) => {
      if (!alive) return
      const next: Record<string, { points: number; records: number }> = {}
      list.forEach((r) => {
        if (r) next[r[0]] = r[1]
      })
      if (Object.keys(next).length > 0) setExisting((prev) => ({ ...prev, ...next }))
    })
    return () => {
      alive = false
    }
  }, [matchedCodes, projects, existing])

  const groupOptions = useMemo(
    () => [...new Set([...groupNames, ...projects.map((p) => p.code)])].map((v) => ({ value: v })),
    [groupNames, projects],
  )

  const columns: ColumnsType<Row> = [
    {
      title: '点号（按规则自动编）',
      width: 185,
      render: (_, r) =>
        r.group ? (
          <Space size={4}>
            <Typography.Text strong>{pointNoOf(r)}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {r.point_no}
            </Typography.Text>
          </Space>
        ) : (
          <Typography.Text type="secondary">{r.point_no}（未分组）</Typography.Text>
        ),
    },
    { title: 'X(m)', dataIndex: 'x', width: 140, render: (v: number | null) => fmtM(v) },
    { title: 'Y(m)', dataIndex: 'y', width: 140, render: (v: number | null) => fmtM(v) },
    { title: 'Z 高程(m)', dataIndex: 'z', width: 120, render: (v: number | null) => fmtM(v) },
    {
      title: '所属基坑（可改）',
      width: 200,
      render: (_, r) => (
        <AutoComplete
          size="small"
          style={{ width: 170 }}
          value={r.group}
          options={groupOptions}
          placeholder="选或输入基坑名"
          filterOption={(input, opt) => (opt?.value ?? '').includes(input)}
          onChange={(v) => assign([r.key], v)}
        />
      ),
    },
  ]

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Button onClick={onBack}>← 返回项目列表</Button>
        <Alert
          type="info"
          showIcon
          style={{ flex: 1 }}
          message="批量建项目：一次测了好几个基坑，一张表就能把项目全建好"
          description="把「点号 + X/Y/Z」清单传进来 → 告诉系统哪些点属于哪个基坑 → 每组选一个初始值日期 → 一次生成项目、测点和初始值。已有同名项目会直接用，不重复建。"
        />
      </Space>

      <Steps current={step} items={[{ title: '选择文件' }, { title: '分组建项目' }, { title: '完成' }]} />

      {step === 0 && (
        <Dragger
          accept=".xlsx,.xlsm,.xls"
          showUploadList={false}
          beforeUpload={beforeUpload}
          disabled={parsing}
          style={{ padding: 24 }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">{parsing ? '解析中…' : '点击或拖拽清单文件到此处'}</p>
          <p className="ant-upload-hint">
            表里只要有点号和坐标/高程就行（有没有表头都认）。一次采集了好几个基坑的点也没关系 ——
            下一步你来区分哪些点属于哪个基坑。
          </p>
          <p className="ant-upload-hint">
            ⚠ 这里要的是<b>点位清单</b>（点号 + X/Y/Z）。
            逐期观测台账（表里写着观测日期、填的是「本次测值」那种）不是清单，请回项目列表进对应项目后用「Excel 导入」。
          </p>
        </Dragger>
      )}

      {step === 1 && preview && (
        <>
          <Alert
            type="success"
            showIcon
            message={`读到 ${rows.length} 个点 ｜ 数据表：${preview.sheet_name}`}
            description={
              <Space direction="vertical" size={2} style={{ fontSize: 12 }}>
                <span>已识别列：{colMapText(preview.column_map)}</span>
                <span>
                  X/Y 是根据坐标数值大小自动判的，核对一下有没有判反（X 一般 341 万量级，Y 一般 51 万量级）。
                </span>
              </Space>
            }
          />

          {undecided > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`还有 ${undecided} 个点没归到基坑`}
              description="在下面表格的「所属基坑」列填上基坑名；也可以先勾选多行，再用上方的「归到该基坑」批量处理。"
            />
          )}

          <Card size="small">
            <Space direction="vertical" style={{ width: '100%' }} size={8}>
              <Space wrap>
                <Typography.Text strong>已选 {selKeys.length} 行</Typography.Text>
                <AutoComplete
                  size="small"
                  style={{ width: 170 }}
                  value={bulkTo}
                  options={groupOptions}
                  placeholder="归到哪个基坑"
                  filterOption={(input, opt) => (opt?.value ?? '').includes(input)}
                  onChange={setBulkTo}
                />
                <Button
                  size="small"
                  type="primary"
                  disabled={selKeys.length === 0 || !bulkTo.trim()}
                  onClick={() => {
                    assign(selKeys, bulkTo)
                    setSelKeys([])
                    setBulkTo('')
                  }}
                >
                  归到这个基坑
                </Button>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  也可以直接在表格里逐行选/输入
                </Typography.Text>
              </Space>
              <Table<Row>
                size="small"
                rowKey="key"
                rowSelection={{
                  selectedRowKeys: selKeys,
                  onChange: (keys) => setSelKeys(keys as string[]),
                }}
                columns={columns}
                dataSource={rows}
                pagination={false}
                scroll={{ y: 380 }}
                onRow={(r) => ({ style: r.group ? undefined : { background: WARNING_LEVEL.control.bg } })}
              />
            </Space>
          </Card>

          <Card
            size="small"
            title={`分成 ${groupNames.length} 个基坑 —— 每组要建/复用一个项目`}
            extra={
              <Space size={6}>
                <DatePicker
                  size="small"
                  value={bulkDate}
                  onChange={setBulkDate}
                  placeholder="统一设为这一天"
                  {...plainCalendarPanel}
                />
                <Button
                  size="small"
                  disabled={!bulkDate}
                  onClick={() => {
                    groupNames.forEach((n) => patchCfg(n, { obs_date: bulkDate }))
                    message.success('所有分组的日期已统一设置')
                  }}
                >
                  应用到全部
                </Button>
              </Space>
            }
          >
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              {groupNames.map((name) => {
                const cfg = cfgOf(name)
                const hit = projects.find(
                  (p) => p.code === cfg.project_code.trim() || p.name === cfg.project_name.trim(),
                )
                return (
                  <Card key={name} size="small" type="inner" title={`${name}（${groupStats.get(name)} 个点）`}>
                    <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Space wrap size={10}>
                      <Space size={4}>
                        <Typography.Text style={{ fontSize: 12 }}>项目编号</Typography.Text>
                        <Input
                          size="small"
                          style={{ width: 130 }}
                          value={cfg.project_code}
                          onChange={(e) => patchCfg(name, { project_code: e.target.value })}
                        />
                      </Space>
                      <Space size={4}>
                        <Typography.Text style={{ fontSize: 12 }}>项目名称</Typography.Text>
                        <Input
                          size="small"
                          style={{ width: 170 }}
                          value={cfg.project_name}
                          onChange={(e) => patchCfg(name, { project_name: e.target.value })}
                        />
                      </Space>
                      <Space size={4}>
                        <Typography.Text style={{ fontSize: 12 }}>初始值日期</Typography.Text>
                        <DatePicker
                          size="small"
                          value={cfg.obs_date}
                          onChange={(d) => patchCfg(name, { obs_date: d })}
                          placeholder="必选"
                          allowClear
                          {...plainCalendarPanel}
                        />
                      </Space>
                    </Space>

                    <Space wrap size={6}>
                      <Typography.Text style={{ fontSize: 12 }}>点号规则</Typography.Text>
                      <Input
                        size="small"
                        style={{ width: 76 }}
                        value={cfg.prefix}
                        onChange={(e) => patchCfg(name, { prefix: e.target.value })}
                        placeholder="JJC"
                      />
                      <InputNumber
                        size="small"
                        style={{ width: 62 }}
                        value={cfg.start}
                        min={0}
                        onChange={(v) => patchCfg(name, { start: Number(v ?? 0) })}
                      />
                      <Select
                        size="small"
                        style={{ width: 96 }}
                        value={cfg.pad}
                        onChange={(v) => patchCfg(name, { pad: v })}
                        options={[
                          { value: 0, label: '不补零' },
                          { value: 2, label: '补到 2 位' },
                          { value: 3, label: '补到 3 位' },
                        ]}
                      />
                      <Tag>{rangeOf(name)}</Tag>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        按清单顺序往下编，不用表里的名字
                      </Typography.Text>
                    </Space>

                    <Space wrap size={10}>
                      {hit ? (
                        <>
                          <Tag color="blue">已有项目「{hit.name}」→ 直接用它</Tag>
                          {(existing[cfg.project_code.trim()]?.points ?? 0) > 0 && (
                            <Typography.Text type="warning" style={{ fontSize: 12 }}>
                              ⚠ 这个项目里已经有 {existing[cfg.project_code.trim()].points} 个测点、
                              {existing[cfg.project_code.trim()].records} 条数据。若本组和它是同一批点，
                              把点号规则设成与已有编号一致（同号会跳过，不会重复建）；若确实是新点，
                              换个前缀，免得跟已有编号撞上。
                            </Typography.Text>
                          )}
                        </>
                      ) : (
                        <Tag color="green">将新建项目</Tag>
                      )}
                      <Button size="small" type="text" danger onClick={() => clearGroup(name)}>
                        取消这个分组
                      </Button>
                    </Space>
                  </Space>
                  </Card>
                )
              })}
              {groupNames.length === 0 && (
                <Typography.Text type="secondary">
                  还没有分组 —— 先在表格里给点号选基坑（同名项目会自动复用，不会重复建）。
                </Typography.Text>
              )}
            </Space>
          </Card>

          <Space wrap>
            <Button
              onClick={() => {
                setPreview(null)
                setRows([])
                setCfgs({})
                setSelKeys([])
                setStep(0)
              }}
            >
              重新选择
            </Button>
            <Button type="primary" loading={submitting} disabled={groupNames.length === 0} onClick={() => void submit()}>
              生成 {groupNames.length} 个项目（{groupNames.reduce((s, n) => s + (groupStats.get(n) ?? 0), 0)} 个测点）
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              已有同名测点会跳过，不会覆盖它的初始值
            </Typography.Text>
          </Space>
        </>
      )}

      {step === 2 && result && (
        <>
          <Alert
            type="success"
            showIcon
            message={`完成：新建项目 ${result.totals.projects_created} 个、复用 ${result.totals.projects_reused} 个；测点 ${result.totals.points_created} 个；初始值记录 ${result.totals.records_created} 条`}
            description={
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                {result.groups.map((g) => (
                  <div key={g.group_name}>
                    <b>{g.group_name}</b>
                    {g.error ? (
                      <Typography.Text type="danger"> —— {g.error}</Typography.Text>
                    ) : (
                      <span>
                        {' '}
                        → 项目「{g.project_name}」（{g.project_code}）
                        {g.created_project ? '【新建】' : '【复用已有】'}：测点 {g.points_created} 个、
                        初始值 {g.records_created} 条、日期 {g.obs_date}
                        {g.skipped.length > 0 && (
                          <Typography.Text type="warning">
                            ；跳过 {g.skipped.length} 个（{g.skipped.map((s) => `${s.point_no}：${s.reason}`).join('；')}）
                          </Typography.Text>
                        )}
                      </span>
                    )}
                  </div>
                ))}
              </Space>
            }
          />
          <Space>
            <Button
              onClick={() => {
                setPreview(null)
                setRows([])
                setCfgs({})
                setSelKeys([])
                setResult(null)
                setStep(0)
              }}
            >
              再建一批
            </Button>
            <Button type="primary" onClick={onBack}>
              返回项目列表
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              回到列表就能看到新项目了
            </Typography.Text>
          </Space>
        </>
      )}
    </Space>
  )
}
