import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { InboxOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { plainCalendarPanel } from '../components/CalendarCell'
import {
  importApi,
  projectApi,
  type ImportPreviewRow,
  type ImportPreviewResponse,
  type MultiImportGroup,
  type MultiImportPending,
  type MultiImportUploadResponse,
  type Project,
} from '../api/client'
import { seriesLabel } from '../labels'

const { Dragger } = Upload

interface Props {
  onBack: () => void
}

/** 一组（同项目、同文件）在界面上的状态：预览可以被"改日期→重新核对"就地替换 */
interface GroupState {
  group: MultiImportGroup
  preview: ImportPreviewResponse
  /** 用户为某张表指定的观测日期（改过才有） */
  sheetDates: Record<string, string>
  rechecking: boolean
  importing: boolean
  importedRows: number | null
  error: string | null
}

const fmtM = (mm: number | null): string => (mm == null ? '缺测' : `${(mm / 1000).toFixed(4)} m`)

/** 一组里"要你拿主意"的行数：冲突 / 校验失败 / 缺日期 / 未知点 */
const attentionOf = (p: ImportPreviewResponse) =>
  (p.conflict_rows?.length ?? 0) + (p.error_rows?.length ?? 0) + (p.no_date_rows?.length ?? 0)

/** 将导入的行数（与项目内 Excel 导入同一口径：有效 + 覆盖系统生成，且必须有日期） */
const rowsToImport = (p: ImportPreviewResponse): ImportPreviewRow[] =>
  [...(p.valid_rows ?? []), ...(p.override_rows ?? [])].filter((r) => !!r.obs_date)

/** 行级明细（只读）：有冲突/错误时摊开给用户看，但**不在这里做逐点建点/映射** */
const rowColumns: ColumnsType<ImportPreviewRow> = [
  { title: '点号', dataIndex: 'point_no', width: 90 },
  {
    title: '类型',
    width: 110,
    render: (_, r) => `${seriesLabel(r.series_type)}${r.component && r.component !== 'value' ? ` ${r.component}` : ''}`,
  },
  { title: '数据表', dataIndex: 'sheet_name', width: 130, ellipsis: true },
  { title: '观测日期', dataIndex: 'obs_date', width: 110, render: (v) => v || '未指定' },
  { title: '本次测值', width: 110, align: 'right', render: (_, r) => fmtM(r.value_mm) },
  {
    title: '说明',
    render: (_, r) => (
      <Typography.Text type={r.error_kind === 'unknown_point' ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
        {r.error ?? (r.existing_value_mm != null ? `当天已有 ${fmtM(r.existing_value_mm)}` : '')}
      </Typography.Text>
    ),
  },
]

export default function DailyImportPage({ onBack }: Props) {
  const [step, setStep] = useState(0)
  const [parsing, setParsing] = useState(false)
  const [states, setStates] = useState<GroupState[]>([])
  const [pending, setPending] = useState<MultiImportPending[]>([])
  const [pointLists, setPointLists] = useState<MultiImportUploadResponse['point_lists']>([])
  const [errors, setErrors] = useState<string[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  /** 认不出的表 → 用户手选的项目（key = 文件名 + 表名） */
  const [assignPick, setAssignPick] = useState<Record<string, number>>({})
  const [assigning, setAssigning] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [summary, setSummary] = useState<{ ok: number; fail: number; rows: number; details: string[] }>({
    ok: 0,
    fail: 0,
    rows: 0,
    details: [],
  })

  useEffect(() => {
    void projectApi
      .list()
      .then(setProjects)
      .catch(() => setProjects([]))
  }, [])

  const patch = useCallback((batchId: number, p: Partial<GroupState>) => {
    setStates((prev) => prev.map((s) => (s.group.batch_id === batchId ? { ...s, ...p } : s)))
  }, [])

  const absorb = useCallback((res: MultiImportUploadResponse, replace: boolean) => {
    const fresh: GroupState[] = res.groups.map((g) => ({
      group: g,
      preview: g.preview,
      sheetDates: {},
      rechecking: false,
      importing: false,
      importedRows: null,
      error: null,
    }))
    setStates((prev) => (replace ? fresh : [...prev, ...fresh]))
    setPending((prev) => (replace ? res.pending : [...prev, ...res.pending]))
    setPointLists((prev) => (replace ? res.point_lists : [...prev, ...res.point_lists]))
    setErrors((prev) => (replace ? res.errors : [...prev, ...res.errors]))
  }, [])

  const parseFiles = useCallback(
    async (list: File[], replace: boolean) => {
      if (list.length === 0) return
      setParsing(true)
      try {
        absorb(await importApi.multiUpload(list), replace)
        setStep(1)
      } catch (e) {
        message.error((e as Error).message, 6)
      } finally {
        setParsing(false)
      }
    },
    [absorb],
  )

  /** antd 多选会逐个调用 beforeUpload：用队列 + 防抖攒成一批（一次请求传完） */
  const pendingFiles = useRef<File[]>([])
  const debounce = useRef<number | undefined>(undefined)
  /** 这一页当前有没有内容：防抖回调里要读**当次最新**状态，闭包里的 state 可能已过期 */
  const dirtyRef = useRef(false)
  useEffect(() => {
    dirtyRef.current = states.length > 0 || pending.length > 0
  }, [states, pending])
  const handleBeforeUpload = useCallback(
    (file: File) => {
      const lower = file.name.toLowerCase()
      if (!lower.endsWith('.xlsx') && !lower.endsWith('.xlsm') && !lower.endsWith('.xls')) {
        message.error(`仅支持 .xlsx / .xlsm / .xls：${file.name}`)
        return Upload.LIST_IGNORE
      }
      pendingFiles.current.push(file)
      window.clearTimeout(debounce.current)
      debounce.current = window.setTimeout(() => {
        const batch = [...pendingFiles.current]
        pendingFiles.current = []
        // 页面空着 → 顶掉上一次的残留；已经有内容 → 追加到后面
        void parseFiles(batch, !dirtyRef.current)
      }, 200)
      return false
    },
    [parseFiles],
  )

  /** 改某张表的观测日期 → 按新日期重新核对（与项目内导入同一接口，防抖交给用户手速） */
  const applySheetDate = async (s: GroupState, sheetName: string, value: string | null) => {
    const next = { ...s.sheetDates }
    if (value) next[sheetName] = value
    else delete next[sheetName]
    const bid = s.group.batch_id
    patch(bid, { sheetDates: next, rechecking: true })
    try {
      const dates = s.group.sheets.map((sh) => ({
        sheet_name: sh.sheet_name,
        obs_date: next[sh.sheet_name] ?? sh.obs_date,
      }))
      patch(bid, { preview: await importApi.recheck(bid, dates), rechecking: false })
    } catch (e) {
      patch(bid, { rechecking: false })
      message.error(`按新日期核对失败：${(e as Error).message}`, 6)
    }
  }

  const assign = async (p: MultiImportPending) => {
    const key = `${p.upload_name}::${p.sheet.sheet_name}`
    const pid = assignPick[key]
    if (!pid) {
      message.warning('先选一个项目')
      return
    }
    setAssigning(key)
    try {
      const g = await importApi.multiAssign(p.upload_name, [p.sheet.sheet_name], pid, p.filename)
      absorb({ groups: [g], pending: [], point_lists: [], errors: [] }, false)
      setPending((prev) => prev.filter((x) => `${x.upload_name}::${x.sheet.sheet_name}` !== key))
      message.success(`「${p.sheet.sheet_name}」已分到 ${g.project_name}，请在下方核对后一起导入`)
    } catch (e) {
      message.error((e as Error).message, 6)
    } finally {
      setAssigning(null)
    }
  }

  /** 全部导入：逐组调**已有的** confirm（与项目内 Excel 导入完全同一条路） */
  const confirmAll = async () => {
    const todo = states.filter((s) => s.importedRows === null)
    if (todo.length === 0) {
      message.warning('没有可导入的分组')
      return
    }
    const undated = todo.reduce((n, s) => n + (s.preview.no_date_rows?.length ?? 0), 0)
    const noRows = todo.filter((s) => rowsToImport(s.preview).length === 0)
    if (undated > 0 || noRows.length > 0) {
      const parts: string[] = []
      if (undated > 0) parts.push(`${undated} 条还没确定观测日期（展开那一组，在「观测日期」里选一天）`)
      if (noRows.length > 0) {
        parts.push(
          `${noRows.length} 个分组一条也导不进去（${noRows
            .map((s) => s.group.project_name)
            .slice(0, 3)
            .join('、')}…）`,
        )
      }
      message.warning(parts.join('；'), 8)
    }
    setConfirming(true)
    let ok = 0
    let fail = 0
    let rows = 0
    const details: string[] = []
    for (const s of todo) {
      const bid = s.group.batch_id
      const label = `${s.group.project_name} · ${s.group.filename}`
      const list = rowsToImport(s.preview)
      if (list.length === 0) {
        details.push(`${label}：没有可导入的数据行，已跳过`)
        patch(bid, { importedRows: 0 })
        continue
      }
      patch(bid, { importing: true, error: null })
      try {
        const r = await importApi.confirm(bid, list)
        ok += 1
        rows += r.imported_rows
        patch(bid, { importing: false, importedRows: r.imported_rows })
        details.push(
          `${label}：成功 ${r.imported_rows} 条` +
            (r.replaced_generated ? `（其中覆盖系统生成数据 ${r.replaced_generated} 条）` : '') +
            (r.skipped_dup_existing ? `，另有 ${r.skipped_dup_existing} 条当天已有记录、自动跳过` : ''),
        )
      } catch (e) {
        fail += 1
        patch(bid, { importing: false, error: (e as Error).message })
        details.push(`${label}：失败（${(e as Error).message}）`)
      }
    }
    setSummary({ ok, fail, rows, details })
    setConfirming(false)
    setStep(2)
    if (fail === 0) message.success(`导入完成：${ok} 个项目、共 ${rows} 条`)
    else message.warning(`导入结束：成功 ${ok} 个、失败 ${fail} 个`)
  }

  const totalRows = useMemo(() => states.reduce((n, s) => n + rowsToImport(s.preview).length, 0), [states])
  const todoCount = states.filter((s) => s.importedRows === null).length
  const attentionTotal = useMemo(() => states.reduce((n, s) => n + attentionOf(s.preview), 0), [states])

  const groupColumns: ColumnsType<GroupState> = [
    {
      title: '项目',
      width: 200,
      render: (_, s) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{s.group.project_name}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {s.group.project_code}
            {s.group.project_archived ? ' · 已归档' : ''}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '文件 / 数据表',
      render: (_, s) => (
        <Space direction="vertical" size={2}>
          <Typography.Text style={{ fontSize: 12 }}>{s.group.filename}</Typography.Text>
          <Space size={4} wrap>
            {s.group.sheets.map((sh) => (
              <Tag key={sh.sheet_name} style={{ marginInlineEnd: 0 }}>
                {sh.sheet_name}（{sh.row_count} 行）
              </Tag>
            ))}
          </Space>
        </Space>
      ),
    },
    {
      title: '观测日期',
      width: 150,
      render: (_, s) => {
        // 显示**生效中**的日期（改过的优先）—— 只摆表里原始日期，改完日期这一列不动，
        // 用户就会以为"改了没反应"。
        const ds = Array.from(
          new Set(s.group.sheets.map((sh) => s.sheetDates[sh.sheet_name] ?? sh.obs_date).filter(Boolean)),
        ) as string[]
        if (ds.length === 0) return <Typography.Text type="warning">未指定</Typography.Text>
        const changed = s.group.sheets.some((sh) => !!s.sheetDates[sh.sheet_name])
        return (
          <Space size={4}>
            <span>{ds.join('、')}</span>
            {changed && <Tag color="blue" style={{ marginInlineEnd: 0 }}>已改</Tag>}
          </Space>
        )
      },
    },
    {
      title: '可导入',
      width: 90,
      align: 'right',
      render: (_, s) => <b>{rowsToImport(s.preview).length}</b>,
    },
    {
      title: '要拿主意',
      width: 150,
      render: (_, s) => {
        const p = s.preview
        const items: Array<[string, number, string]> = [
          ['冲突', p.conflict_rows?.length ?? 0, 'orange'],
          ['错误', p.error_rows?.length ?? 0, 'red'],
          ['缺日期', p.no_date_rows?.length ?? 0, 'gold'],
        ]
        const shown = items.filter(([, n]) => n > 0)
        if (shown.length === 0) return <Tag color="green">没有</Tag>
        return (
          <Space size={4} wrap>
            {shown.map(([label, n, color]) => (
              <Tag key={label} color={color}>
                {label} {n}
              </Tag>
            ))}
          </Space>
        )
      },
    },
    {
      title: '凭什么认成它',
      width: 240,
      render: (_, s) => {
        const sh = s.group.sheets[0]
        const name = sh?.name_texts?.[0]
        return (
          <Space direction="vertical" size={0}>
            <Typography.Text style={{ fontSize: 12 }}>
              {sh?.how || '—'}
              {name ? `（表里写的是「${name}」）` : ''}
            </Typography.Text>
            <Space size={4}>
              {s.group.needs_eyeball && <Tag color="gold">请核一眼</Tag>}
              {sh?.ambiguous && <Tag color="red">分不清</Tag>}
              {s.importedRows !== null && <Tag color="blue">已导入 {s.importedRows} 条</Tag>}
            </Space>
          </Space>
        )
      },
    },
  ]

  const pendingColumns: ColumnsType<MultiImportPending> = [
    { title: '文件', dataIndex: 'filename', width: 190, ellipsis: true },
    {
      title: '数据表',
      width: 170,
      render: (_, p) => (
        <Space direction="vertical" size={0}>
          <span>{p.sheet.sheet_name}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {p.sheet.row_count} 行
            {p.sheet.name_texts?.length ? ` · 表里写的是「${p.sheet.name_texts[0]}」` : ' · 表里没写工程名称'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '为什么没认出来',
      width: 380,
      render: (_, p) => (
        <Typography.Text type="warning" style={{ fontSize: 12 }}>
          {p.sheet.reason || (p.sheet.ambiguous ? '这几个项目都像，分不清是哪份' : '对不上系统里的项目')}
          {p.sheet.candidates?.length > 0 && (
            <>
              {' '}
              最像的：
              {p.sheet.candidates
                .slice(0, 3)
                .map((c) => `${c.project_name}（${c.project_code}）`)
                .join('、')}
            </>
          )}
        </Typography.Text>
      ),
    },
    {
      title: '分到哪个项目',
      width: 250,
      render: (_, p) => {
        const key = `${p.upload_name}::${p.sheet.sheet_name}`
        return (
          <Space>
            <Select
              size="small"
              style={{ width: 160 }}
              showSearch
              optionFilterProp="label"
              placeholder="选一个项目"
              value={assignPick[key]}
              onChange={(v) => setAssignPick((prev) => ({ ...prev, [key]: v }))}
              options={projects.map((pr) => ({
                value: pr.id,
                label: `${pr.name}（${pr.code}）${pr.is_active === false ? ' · 已归档' : ''}`,
              }))}
            />
            <Button
              size="small"
              type="primary"
              ghost
              loading={assigning === key}
              onClick={() => void assign(p)}
            >
              指派
            </Button>
          </Space>
        )
      },
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
          message="每日导入：当天所有台账一次拖进来"
          description="系统按每张表里写的「监测工程名称」自动分到对应项目，一次核对、一个按钮全导。表里没写名称的会摆出来让你手选 —— 绝不替你猜。"
        />
      </Space>

      <Steps current={step} items={[{ title: '选择文件' }, { title: '核对并导入' }, { title: '完成' }]} />

      {step === 0 && (
        <Dragger
          accept=".xlsx,.xlsm,.xls"
          multiple
          showUploadList={false}
          beforeUpload={handleBeforeUpload}
          disabled={parsing}
          style={{ padding: 24 }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">{parsing ? '解析中…' : '点击或拖拽当天的台账到此处（可一次选多个）'}</p>
          <p className="ant-upload-hint">
            支持 .xlsx / .xlsm / .xls。一份文件里有好几个基坑的表也可以 —— 按表分开认。
          </p>
          <p className="ant-upload-hint">
            ⚠ 这个页面是「把观测数据录进已有项目」。<b>要新建基坑、批量建测点</b>请回项目列表用「批量建项目」。
          </p>
        </Dragger>
      )}

      {step === 1 && (
        <>
          {errors.length > 0 && (
            <Alert
              type="error"
              showIcon
              message={`有 ${errors.length} 个文件没能解析`}
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {errors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              }
            />
          )}

          {pointLists.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`有 ${pointLists.length} 张表是「点号 + 坐标/高程」点位清单，不是某一天的观测数据`}
              description={
                <span style={{ fontSize: 12 }}>
                  {pointLists.map((p) => `${p.filename} / ${p.sheet_name}`).join('、')}。
                  <b>要新建基坑、批量建测点</b>：回项目列表点「批量建项目」。
                  这些表不会被导入。
                </span>
              }
            />
          )}

          {pending.length > 0 && (
            <Card size="small" title={`这些表认不出是哪个基坑（${pending.length}）`}>
              <Table
                size="small"
                scroll={{ x: 990 }}
                rowKey={(p) => `${p.upload_name}::${p.sheet.sheet_name}`}
                columns={pendingColumns}
                dataSource={pending}
                pagination={false}
              />
            </Card>
          )}

          <Card
            size="small"
            title={`自动分好的（${states.length} 个项目分组 / ${totalRows} 条待导入）`}
            extra={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                点行左边的箭头可以展开改观测日期、看冲突与错误明细
              </Typography.Text>
            }
          >
            <Table
              size="small"
              rowKey={(s) => s.group.batch_id}
              columns={groupColumns}
              dataSource={states}
              pagination={false}
              expandable={{
                expandedRowRender: (s) => (
                  <Space direction="vertical" style={{ width: '100%' }} size={8}>
                    <Space wrap>
                      {s.group.sheets.map((sh) => (
                        <Space key={sh.sheet_name} size={4}>
                          <Typography.Text style={{ fontSize: 12 }}>{sh.sheet_name}</Typography.Text>
                          <DatePicker
                            size="small"
                            style={{ width: 132 }}
                            value={(s.sheetDates[sh.sheet_name] ?? sh.obs_date) ? dayjs(s.sheetDates[sh.sheet_name] ?? sh.obs_date!) : null}
                            onChange={(d) =>
                              void applySheetDate(s, sh.sheet_name, d ? d.format('YYYY-MM-DD') : null)
                            }
                            placeholder="选择观测日期"
                            allowClear
                            disabled={s.rechecking || s.importedRows !== null}
                            {...plainCalendarPanel}
                          />
                        </Space>
                      ))}
                      {s.rechecking && <Typography.Text type="secondary">重新核对中…</Typography.Text>}
                      {s.error && <Typography.Text type="danger">{s.error}</Typography.Text>}
                    </Space>
                    {(s.preview.override_rows?.length ?? 0) > 0 && (
                      <Alert
                        type="info"
                        showIcon
                        message={`${s.preview.override_rows.length} 条会覆盖「系统自动生成」的数据（以你导入的为准，原值留痕）`}
                      />
                    )}
                    {(s.preview.conflict_rows?.length ?? 0) > 0 && (
                      <>
                        <Alert
                          type="warning"
                          showIcon
                          message={`${s.preview.conflict_rows.length} 条当天已有真实记录 → 跳过不覆盖（原始数据只增不删）`}
                        />
                        <Table
                          size="small"
                          rowKey={(_r, i) => `c${i}`}
                          columns={rowColumns}
                          dataSource={s.preview.conflict_rows}
                          pagination={{ pageSize: 5, size: 'small' }}
                        />
                      </>
                    )}
                    {(s.preview.error_rows?.length ?? 0) > 0 && (
                      <>
                        <Alert
                          type="error"
                          showIcon
                          message={`${s.preview.error_rows.length} 条校验没通过（多半是测点在这个项目里不存在 = 分错项目了，或点号写法不一致）`}
                          description={
                            <span style={{ fontSize: 12 }}>
                              要逐个点号「自动建点 / 映射到已有点」，请到该项目内的「Excel 导入」页处理。
                            </span>
                          }
                        />
                        <Table
                          size="small"
                          rowKey={(_r, i) => `e${i}`}
                          columns={rowColumns}
                          dataSource={s.preview.error_rows}
                          pagination={{ pageSize: 5, size: 'small' }}
                        />
                      </>
                    )}
                    {(s.preview.no_date_rows?.length ?? 0) > 0 && (
                      <Alert
                        type="warning"
                        showIcon
                        message={`${s.preview.no_date_rows.length} 条还没确定观测日期 → 在上面选好日期就会自动重新核对`}
                      />
                    )}
                    {(s.preview.control_suggest ?? []).filter((c) => c.differs).length > 0 && (
                      <Alert
                        type="info"
                        showIcon
                        message="台账里的「控制值」和本项目预警规则对不上"
                        description={
                          <span style={{ fontSize: 12 }}>
                            这一页只导数据、不动预警规则。要同步控制值，请到该项目内的「Excel 导入」页勾上那一项。
                          </span>
                        }
                      />
                    )}
                  </Space>
                ),
              }}
            />
          </Card>

          <Space wrap>
            <Upload
              accept=".xlsx,.xlsm,.xls"
              multiple
              showUploadList={false}
              beforeUpload={handleBeforeUpload}
              disabled={parsing}
            >
              <Button icon={<PlusOutlined />} loading={parsing}>
                补传台账
              </Button>
            </Upload>
            <Button
              onClick={() => {
                setStates([])
                setPending([])
                setPointLists([])
                setErrors([])
                setAssignPick({})
                setStep(0)
              }}
            >
              重新选择
            </Button>
            <Button
              type="primary"
              loading={confirming}
              disabled={todoCount === 0 || totalRows === 0}
              onClick={() => void confirmAll()}
            >
              全部导入（{todoCount} 个项目 / {totalRows} 条）
            </Button>
            {attentionTotal > 0 && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                有 {attentionTotal} 条要你拿主意（展开对应分组看）；冲突行与校验失败行不会导入，原始数据不会被覆盖
              </Typography.Text>
            )}
          </Space>
        </>
      )}

      {step === 2 && (
        <>
          <Alert
            type={summary.fail ? 'warning' : 'success'}
            showIcon
            message={`导入完成：成功 ${summary.ok} 个项目分组、共 ${summary.rows} 条${summary.fail ? `；失败 ${summary.fail} 个` : ''}`}
            description={
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {summary.details.map((d, i) => (
                  <li key={i}>{d}</li>
                ))}
              </ul>
            }
          />
          <Space>
            <Button
              onClick={() => {
                setStates([])
                setPending([])
                setPointLists([])
                setErrors([])
                setAssignPick({})
                setSummary({ ok: 0, fail: 0, rows: 0, details: [] })
                setStep(0)
              }}
            >
              继续导入下一批
            </Button>
            <Button onClick={onBack}>返回项目列表</Button>
          </Space>
        </>
      )}
    </Space>
  )
}
