import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, DatePicker, Input, Modal, Select, Space, Tooltip, Typography, Upload, message } from 'antd'
import {
  EditOutlined,
  EyeOutlined,
  FileExcelOutlined,
  FileWordOutlined,
  PrinterOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import {
  baselineApi,
  downloadBlob,
  projectApi,
  reportApi,
  settingsApi,
  type ExportTemplateInfo,
  type Project,
  type ReportPreviewDoc,
  type TemplateCheckResult,
} from '../api/client'
import { observationCalendarPanel } from '../components/CalendarCell'
import ExcelPreviewModal from '../components/ExcelPreviewModal'
import MasterCheckAlert from '../components/MasterCheckAlert'
import ScopePicker from '../components/ScopePicker'
import WarningRulesModal from '../components/WarningRulesModal'

interface Props {
  projectId: number
  onBack: () => void
}

/** 记住每个项目上次用的导出模板与天气 —— 现场每次导出口径一致，不必重选。 */
const tplPrefKey = (projectId: number) => `monitor.report.tpl.${projectId}`
const weatherPrefKey = (projectId: number) => `monitor.report.weather.${projectId}`

function readPref(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writePref(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    /* 隐私模式下 localStorage 可能不可用，忽略即可 */
  }
}

/**
 * 项目内上传母版的老路径（保留）：传完自动绑定本项目。
 * "母版的集中管理"已挪到主界面的「母版库」，这里只留"我就想给当前项目传一份"这个最常用的动作。
 */

export default function ReportsPage({ projectId, onBack }: Props) {
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const [dates, setDates] = useState<string[]>([])
  const [templates, setTemplates] = useState<ExportTemplateInfo[]>([])
  const [templateId, setTemplateId] = useState<number | undefined>(undefined) // undefined = 内置标准
  const [weather, setWeather] = useState<string>('晴')
  /** 正在把日报转 PDF（服务端转换要几秒，得让用户看见在转） */
  const [printing, setPrinting] = useState(false)
  const [loadingExcel, setLoadingExcel] = useState(false)
  const [loadingWord, setLoadingWord] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadName, setUploadName] = useState('')
  const [uploading, setUploading] = useState(false)
  /** 重命名弹窗（上传时没想好名字、或事后想改） */
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [renaming, setRenaming] = useState(false)
  /** 模板独立化：母版自己声明"作用于哪些项目"（多对多 + 通用开关） */
  const [projects, setProjects] = useState<Project[]>([])
  const [uploadProjectIds, setUploadProjectIds] = useState<number[]>([projectId])
  const [uploadGlobal, setUploadGlobal] = useState(false)
  const [scopeOpen, setScopeOpen] = useState(false)
  const [scopeProjectIds, setScopeProjectIds] = useState<number[]>([])
  const [scopeGlobal, setScopeGlobal] = useState(false)
  const [savingScope, setSavingScope] = useState(false)
  /** 母版体检（换了母版就自动重查；有 error 时拦住导出按钮） */
  const [check, setCheck] = useState<TemplateCheckResult | null>(null)
  const [checking, setChecking] = useState(false)
  /** 导出预览：后端按当前模板生成一份、把网格传回来（不落盘、不下载） */
  const [previewDoc, setPreviewDoc] = useState<ReportPreviewDoc | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  /** 「去配预警规则」：没配控制值时后端导出本来会 422，直接在这里给入口 */
  const [rulesOpen, setRulesOpen] = useState(false)

  const loadAll = useCallback(async () => {
    try {
      const [d, ts, ps] = await Promise.all([
        baselineApi.dates(projectId),
        reportApi.listTemplates(projectId),
        // 母版的"适用项目"要能勾选，所以列表页的项目清单也要拿到
        // （旧后端没有这个能力标记，但接口一直在，失败也不该拦住导出）
        projectApi.list().catch(() => [] as Project[]),
      ])
      setProjects(ps)
      // 用**有实际读数**的日期，不用全部观测日期：
      // 有些日子录过记录、但值全是空的（缺测），导出那一期是一张空表 ——
      // 日历上不该把它标成"有数据"、更不该让它可选。
      // （旧后端没有这个字段，回退到 d.dates 并靠能力标记提示重启）
      setDates(d.dates_with_values ?? d.dates)
      // 默认选中最近一个观测日（= 单选单期）
      if (d.dates.length > 0) {
        const last = dayjs(d.dates[d.dates.length - 1])
        setRange([last, last])
      }
      const excelTpls = ts.filter((t) => t.template_type === 'excel')
      setTemplates(ts)

      // 模板优选顺序：① 上次用过的（仍存在）→ ② 本项目上传的母版 → ③ 系统标准版式。
      // 记忆优先，保证同一项目每期导出口径一致，不会因为新增模板而悄悄换掉。
      const remembered = Number(readPref(tplPrefKey(projectId)))
      const rememberedTpl = excelTpls.find((t) => t.id === remembered && t.file_ok !== false)
      const mine = excelTpls.find((t) => !t.is_builtin && t.file_ok !== false)
      const fallback = rememberedTpl ?? mine ?? excelTpls.find((t) => t.is_builtin) ?? excelTpls[0]
      if (fallback) {
        setTemplateId((prev) => (prev && excelTpls.some((t) => t.id === prev) ? prev : fallback.id))
      }

      const rememberedWeather = readPref(weatherPrefKey(projectId))
      if (rememberedWeather) {
        setWeather(rememberedWeather)
      } else {
        // 本项目还没选过天气 → 用主界面「项目默认值」里配的全站默认天气。
        // 取不到就算了（旧后端没有 /settings），保持页面里那个初始值。
        const cfg = await settingsApi.get().catch(() => null)
        if (cfg?.default_weather) setWeather(cfg.default_weather)
      }
    } catch (e) {
      message.error((e as Error).message)
    }
  }, [projectId])

  /** 用户手动换模板/天气时记住选择。 */
  const pickTemplate = (id: number | undefined) => {
    setTemplateId(id)
    writePref(tplPrefKey(projectId), id === undefined ? null : String(id))
  }
  const pickWeather = (w: string) => {
    setWeather(w)
    writePref(weatherPrefKey(projectId), w)
  }

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  /** 区间内实际存在的观测日期（升序）—— 同一天=1 期（单文件），跨日=N 期（ZIP） */
  const batchDates = useMemo(() => {
    if (!range || !range[0] || !range[1]) return []
    const s = range[0].format('YYYY-MM-DD')
    const e = range[1].format('YYYY-MM-DD')
    return dates.filter((d) => d >= s && d <= e)
  }, [range, dates])

  const singleDate = batchDates.length === 1 ? batchDates[0] : undefined

  /**
   * 日期面板：把"有数据的日期"标出来。
   *
   * 原先只有 `disabledDate`（有数据能点、没数据点不动），用户翻到某个月看不出
   * 这个月测过哪几天，只能一个个去试。现在有数据的那天带一条横条，
   * 面板底部也有图例说明 —— 与记录页的"已录入"用的是同一套视觉语言。
   */
  const calendarPanel = useMemo(() => observationCalendarPanel(dates), [dates])

  /**
   * 母版体检：换母版就自动查一次（后端只读母版与 layout，一个字节都不写回去）。
   *
   * 内置标准版式不查 —— 它没有原件，报表每行都直接取自预警规则，没有"够不着的旧值"这种东西。
   */
  const runCheck = useCallback(
    async (tid: number | undefined) => {
      const tpl = templates.find((t) => t.id === tid)
      if (!tpl || tpl.is_builtin || tpl.file_ok === false) {
        setCheck(null)
        return
      }
      setChecking(true)
      try {
        setCheck(await reportApi.checkTemplate(projectId, tpl.id))
      } catch (e) {
        // 体检失败不该拦住导出（导出自己还会再校验一遍），只是这次没提前看到问题
        message.warning(`母版体检没成功：${(e as Error).message}`)
        setCheck(null)
      } finally {
        setChecking(false)
      }
    },
    [projectId, templates],
  )

  useEffect(() => {
    void runCheck(templateId)
  }, [runCheck, templateId])

  /** 预览的是区间里的第一期（批量导出每期版式一样，只是数据不同） */
  const previewDate = batchDates.length > 0 ? batchDates[0] : undefined
  const blockedByCheck = (check?.errors ?? 0) > 0

  const preview = async () => {
    if (previewing) return
    if (!previewDate) {
      message.warning('请先选择要预览的期次')
      return
    }
    setPreviewOpen(true)
    setPreviewing(true)
    try {
      setPreviewDoc(await reportApi.preview(projectId, previewDate, templateId, weather))
    } catch (e) {
      message.error((e as Error).message, 6)
      setPreviewOpen(false)
    } finally {
      setPreviewing(false)
    }
  }

  /** 打印日报：服务端把 Excel 原样转 PDF，**直接调起浏览器打印框**。
   *
   *  用户 2026-10-29：「不能直接在系统中打印吗？还得跳转到 PDF 界面」
   *  —— 所以这里不 window.open，而是塞进一个隐藏 iframe 再调 `print()`：
   *  点一下 → 系统打印对话框直接弹出来，页面不跳走，打断感小很多。
   *
   *  多期时后端会合成一份 PDF，一次打印全部（不是开一堆窗口）。
   *  个别浏览器不允许打印 iframe 里的 PDF，这时降级成新窗口打开。
   */
  const printPdf = async () => {
    if (batchDates.length === 0) {
      message.warning('先选观测日期，再打印')
      return
    }
    setPrinting(true)
    // 服务端每期要起一个 LibreOffice（约 2 秒），批量时用户会干等 ——
    // 给一条带数量和预估时间的提示，比一个转圈的按钮有用得多。
    const estimate = Math.max(2, batchDates.length * 2)
    const tip = message.loading(
      `正在生成 ${batchDates.length} 期日报，约需 ${estimate} 秒…`,
      0,
    )
    try {
      const params = new URLSearchParams({ dates: batchDates.join(',') })
      if (templateId) params.set('template_id', String(templateId))
      if (weather) params.set('weather', weather)
      const res = await fetch(`/api/v1/projects/${projectId}/reports/pdf?${params.toString()}`)
      if (!res.ok) {
        let detail = `生成失败（HTTP ${res.status}）`
        try {
          const j = await res.json()
          if (j?.detail) detail = j.detail
        } catch {
          /* 不是 JSON，用默认文案 */
        }
        throw new Error(detail)
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)

      const iframe = document.createElement('iframe')
      iframe.setAttribute('aria-hidden', 'true')
      iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;'
      iframe.src = url
      iframe.onload = () => {
        // 等内置查看器把 PDF 渲染出来，立刻调打印会拿到空白页
        window.setTimeout(() => {
          try {
            iframe.contentWindow?.focus()
            iframe.contentWindow?.print()
          } catch {
            window.open(url, '_blank')
          }
        }, 400)
      }
      document.body.appendChild(iframe)
      // 打印框可能要开很久，留足时间再回收
      window.setTimeout(() => {
        iframe.remove()
        URL.revokeObjectURL(url)
      }, 180_000)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      tip()
      setPrinting(false)
    }
  }

  /** 导出 Excel：选 1 期 → 单文件；选 N 期（N≥2）→ 打包 ZIP（单/多合一入口） */
  const excel = async () => {
    // 防连点。为什么必须显式挡一道：批量导出要十几秒，这段时间里"点了没反应、又报超时"
    // 会让人本能地再点几次 —— 2026-10-09 那次同一批导出被点了 5 遍，后端白算 5 遍（每次都是全 200）。
    // antd 的 loading 会禁用按钮，但它挡不住"上一次已经报错、用户马上再点"。
    if (loadingExcel) return
    // 体检查出"这份文件发出去一定有问题"时不放行（用户 2026-10-16 选的就是"导出前拦"）。
    // 后端的 422 也拦得住，但那要等它算完整卷、而且提示是一整句技术话术。
    if (blockedByCheck) {
      message.error('母版体检有必须先处理的问题 —— 按上方的红色提示改好，再点一次体检', 6)
      return
    }
    if (batchDates.length === 0) {
      message.warning('所选起止日期内没有观测期次，请重新选择')
      return
    }
    const n = batchDates.length
    const isBatch = n > 1
    const msgKey = 'export-excel'
    const t0 = Date.now()
    // 批量导出没有进度可报，但"已用 N 秒"至少能证明它在跑。不显示的话用户会以为卡死了。
    const tick = isBatch
      ? window.setInterval(() => {
          message.loading({
            content: `正在生成 ${n} 期日报…已用 ${Math.round((Date.now() - t0) / 1000)} 秒（请不要重复点击）`,
            key: msgKey,
            duration: 0,
          })
        }, 1000)
      : undefined
    if (isBatch) {
      message.loading({ content: `正在生成 ${n} 期日报…`, key: msgKey, duration: 0 })
    }
    setLoadingExcel(true)
    try {
      if (batchDates.length === 1) {
        const { blob, summary } = await reportApi.downloadExcel(projectId, batchDates[0], templateId, weather)
        downloadBlob(blob, `监测日报_${batchDates[0]}.xlsx`)
        message.success('日报已导出')
        // 母版点位比项目测点少时，后端会自动补行/补块 —— 必须说清楚，否则用户以为导错了
        const added = Object.entries(summary?.added_points ?? {})
        const addedRaw = Object.entries(summary?.added_raw_points ?? {})
        if (added.length > 0 || addedRaw.length > 0) {
          const parts: string[] = []
          if (added.length > 0) {
            parts.push(
              `数据表补了 ${added.map(([sheet, pts]) => `${sheet} ${pts.length} 个（${pts.join('、')}）`).join('；')}`,
            )
          }
          if (addedRaw.length > 0) {
            parts.push(
              `原始数据区补了 ${addedRaw
                .map(([sheet, pts]) => `${sheet} ${pts.length} 个（${pts.join('、')}）`)
                .join('；')}`,
            )
          }
          message.info(`母版点位不够，已自动补上：${parts.join('；')}`)
        }
        const missing = Object.entries(summary?.missing_raw_points ?? {})
        if (missing.length > 0) {
          message.warning(
            `「原始数据区」的点位网格已排满，仍放不下：${missing
              .map(([sheet, pts]) => `${sheet} 的 ${pts.join('、')}`)
              .join('；')}。这些点的坐标没能导出，需要的话请上传一份预留更多点位的母版。`,
            8,
          )
        }
        // 概况/分析/成果提交是**按本期数据重写**的（母版里那些段落是别的工程的，不能留）
        const gen = Object.entries(summary?.text_generated ?? {})
        const clr = Object.entries(summary?.text_cleared ?? {})
        if (gen.length > 0 || clr.length > 0) {
          message.info(
            `${gen.map(([sheet, n]) => `${sheet} ${n} 处`).join('；')} 的文字已按本期数据自动重写` +
              (clr.length > 0
                ? `；母版里多出来的旧文字已清空（${clr
                    .map(([sheet, n]) => `${sheet} ${n} 格`)
                    .join('；')}）`
                : ''),
            6,
          )
        }
        const over = Object.entries(summary?.text_overflow ?? {})
        if (over.length > 0) {
          message.warning(
            `母版里的段落格子不够放本期内容：${over
              .map(([sheet, texts]) => `${sheet}「${texts[0]?.slice(0, 20)}…」`)
              .join('；')}。请上传一份预留更多段落的母版。`,
            8,
          )
        }
      } else {
        const blob = await reportApi.downloadExcelBatch(projectId, batchDates, templateId, weather)
        downloadBlob(blob, `监测日报批量_${batchDates[0]}~${batchDates[batchDates.length - 1]}_共${batchDates.length}期.zip`)
        message.success(
          `已批量导出 ${n} 期日报（ZIP，用了 ${Math.round((Date.now() - t0) / 1000)} 秒）`,
        )
      }
    } catch (e) {
      // 超时的话后端的活多半已经干完了，这一步只是没拿到文件 —— 提示里说清楚，别让人以为数据出问题
      message.error((e as Error).message, 6)
    } finally {
      if (tick) window.clearInterval(tick)
      // 关掉那条常驻的"正在生成…"；成功/失败提示各自独立，不受影响
      message.destroy(msgKey)
      setLoadingExcel(false)
    }
  }

  const word = async () => {
    if (!singleDate) {
      message.warning('Word 日报仅支持单期导出，请把起止日期选为同一天')
      return
    }
    setLoadingWord(true)
    try {
      const blob = await reportApi.downloadDaily(projectId, singleDate)
      downloadBlob(blob, `监测日报_${singleDate}.docx`)
      message.success('Word 日报已导出')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoadingWord(false)
    }
  }

  const doUpload = async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.xlsx') && !file.name.toLowerCase().endsWith('.xlsm')) {
      message.error('仅支持 .xlsx/.xlsm 模板文件')
      return false
    }
    if (!uploadGlobal && uploadProjectIds.length === 0) {
      message.warning('请先选这份母版作用于哪些项目（或勾选「所有项目通用」）')
      return false
    }
    setUploading(true)
    try {
      // 名称留空就交给后端用文件名兜底 —— 不再拦"请先填写模板名称"。
      // 传文件与起名字没有先后要求；传完想改还能点模板旁的「重命名」。
      const r = await reportApi.uploadTemplate(projectId, uploadName.trim(), file, {
        projectIds: uploadProjectIds,
        isGlobal: uploadGlobal,
      })
      message.success(
        `已保存母版「${r.name}」${r.detected_layout ? '，已在下方自动选中' : '；自动识别失败，请检查模板表头'}` +
          (uploadName.trim() ? '' : '（名字取自文件名，想改可点「重命名」）') +
          (uploadGlobal ? '；所有项目都能用' : `；适用于 ${uploadProjectIds.length} 个项目`),
      )
      setTemplateId(r.id) // 上传后直接切到这份母版，导出即按它来
      setUploadOpen(false)
      setUploadName('')
      void loadAll()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setUploading(false)
    }
    return false
  }

  const openScope = () => {
    if (!selectedTpl) return
    setScopeProjectIds(selectedTpl.project_ids ?? [])
    setScopeGlobal(Boolean(selectedTpl.is_global))
    setScopeOpen(true)
  }

  const doSaveScope = async () => {
    if (!selectedTpl) return
    if (!scopeGlobal && scopeProjectIds.length === 0) {
      message.warning('至少选一个项目，或者勾选「所有项目通用」—— 否则这份母版在导出页里就找不到了')
      return
    }
    // 把当前项目也取消勾选 = 这份母版立刻从本项目下拉里消失，像"丢了"一样 → 先确认
    const willVanish =
      !scopeGlobal && !scopeProjectIds.includes(projectId) && (selectedTpl.project_ids ?? []).includes(projectId)
    if (willVanish) {
      const ok = await new Promise<boolean>((resolve) => {
        Modal.confirm({
          title: '取消后本项目就用不了这份母版了',
          content: `「${selectedTpl.name}」将不再属于本项目，导出页下拉里会看不到它。确定要取消吗？`,
          okText: '确定取消',
          cancelText: '再想想',
          onOk: () => resolve(true),
          onCancel: () => resolve(false),
        })
      })
      if (!ok) return
    }
    setSavingScope(true)
    try {
      const t = await reportApi.setTemplateScope(selectedTpl.id, {
        projectIds: scopeProjectIds,
        isGlobal: scopeGlobal,
      })
      setTemplates((prev) => prev.map((x) => (x.id === t.id ? { ...x, ...t } : x)))
      message.success(
        scopeGlobal ? `「${t.name}」已设为所有项目通用` : `「${t.name}」适用于 ${scopeProjectIds.length} 个项目`,
      )
      setScopeOpen(false)
      // 若把本项目取消了，当前选中的这份母版已经不可用 → 回落到别的可用模板
      void loadAll()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSavingScope(false)
    }
  }

  const openRename = () => {
    if (!selectedTpl) return
    setRenameValue(selectedTpl.name)
    setRenameOpen(true)
  }

  const doRename = async () => {
    if (!selectedTpl) return
    const name = renameValue.trim()
    if (!name) {
      message.warning('名称不能为空 —— 写一个你能认出来的名字')
      return
    }
    if (name === selectedTpl.name) {
      setRenameOpen(false)
      return
    }
    if (templates.some((t) => t.id !== selectedTpl.id && !t.is_builtin && t.name === name)) {
      message.warning(`已经有一个叫「${name}」的母版了，换个名字 —— 同名的两个在下拉框里分不出来`)
      return
    }
    setRenaming(true)
    try {
      const t = await reportApi.renameTemplate(selectedTpl.id, name)
      setTemplates((prev) => prev.map((x) => (x.id === t.id ? { ...x, ...t } : x)))
      message.success(`已重命名为「${t.name}」`)
      setRenameOpen(false)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setRenaming(false)
    }
  }

  const selectedTpl = templates.find((t) => t.id === templateId)

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Button onClick={onBack}>← 返回项目列表</Button>
        <Alert
          type="info"
          showIcon
          style={{ flex: 1 }}
          message="导出观测日报"
          description="选好日期 → 点导出：选一天导出单份，选一段日期打包成 ZIP 一次下载。想用自己的报表格式？先上传一份填好的样表，之后每期自动套用；不上传就用系统标准格式。"
        />
      </Space>

      <Card title="导出观测日报">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap size="large">
            <span>
              报告模板：
              <Select
                style={{ width: 320, marginLeft: 8 }}
                value={templateId}
                onChange={(v) => pickTemplate(v)}
                options={[
                  ...templates
                    .filter((t) => t.template_type === 'excel' && !t.is_builtin)
                    .map((t) => ({
                      value: t.id,
                      label: `${t.is_global ? '【通用母版】' : '【我的模板】'}${t.name}${
                        t.file_ok === false ? '（原件缺失，请重传）' : ''
                      }`,
                      disabled: t.file_ok === false,
                    })),
                  ...templates
                    .filter((t) => t.template_type === 'excel' && t.is_builtin)
                    .map((t) => ({ value: t.id, label: `【系统标准】${t.name}` })),
                ]}
              />
            </span>
            {selectedTpl && !selectedTpl.is_builtin && (
              <Button size="small" icon={<EditOutlined />} onClick={openRename}>
                重命名
              </Button>
            )}
            {selectedTpl && !selectedTpl.is_builtin && (
              <Button size="small" icon={<SettingOutlined />} onClick={openScope}>
                设置适用项目
              </Button>
            )}
            {selectedTpl && !selectedTpl.is_builtin && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                适用于：
                {selectedTpl.is_global
                  ? '所有项目'
                  : selectedTpl.project_names?.length
                    ? selectedTpl.project_names.join('、')
                    : '未指定'}
              </Typography.Text>
            )}
            {selectedTpl && !selectedTpl.is_builtin && (
              <Typography.Text type={selectedTpl.file_ok === false ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
                {selectedTpl.file_ok === false
                  ? '✗ 模板原件文件缺失，请删除后重新上传（同名自动替换）'
                  : selectedTpl.layout_json
                    ? '✓ 布局已自动识别，可按此模板导出'
                    : '⚠ 布局未识别，导出将退回系统版式'}
              </Typography.Text>
            )}
            {selectedTpl?.is_builtin && (
              <Typography.Text type="warning" style={{ fontSize: 12 }}>
                系统标准版式只有两张数据表（没有封面/签字/概况）。要用你自己的报表格式，请选上面的「我的模板」。
              </Typography.Text>
            )}
            <Button
              icon={<UploadOutlined />}
              onClick={() => {
                // 每次打开都回到"只绑本项目"的安全默认（不继承上一次的选择）
                setUploadProjectIds([projectId])
                setUploadGlobal(false)
                setUploadOpen(true)
              }}
            >
              上传报送母版
            </Button>
          </Space>

          {/* 体检结论紧跟在模板那一行下面：换母版第一眼就该看到"这份套到本项目上会怎样" */}
          <MasterCheckAlert
            result={check}
            loading={checking}
            onRecheck={() => void runCheck(templateId)}
            onConfigRules={() => setRulesOpen(true)}
          />

          <Space wrap>
            <span>
              选择期次（观测日期，起止选同一天=单期，框选区间=多期）：
              <DatePicker.RangePicker
                style={{ marginLeft: 8 }}
                value={range}
                onChange={(v) => setRange(v as [Dayjs | null, Dayjs | null] | null)}
                disabledDate={(d) => !d || !dates.includes(d.format('YYYY-MM-DD'))}
                placeholder={['起始日期（或单期日期）', '截止日期']}
                {...calendarPanel}
              />
            </span>
            <span>
              天气：
              <Select
                style={{ width: 90, marginLeft: 8 }}
                value={weather}
                onChange={pickWeather}
                options={['晴', '多云', '阴', '小雨', '中雨', '大雨'].map((w) => ({ value: w, label: w }))}
              />
            </span>
            <Button icon={<ReloadOutlined />} onClick={() => void loadAll()}>
              刷新
            </Button>
          </Space>

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            当前模板：
            {selectedTpl ? `${selectedTpl.is_builtin ? '系统标准' : '我的模板'}「${selectedTpl.name}」` : '未选择'}
          </Typography.Text>

          {batchDates.length > 0 ? (
            <div>
              <Typography.Text type={batchDates.length === 1 ? 'secondary' : 'success'}>
                {batchDates.length === 1
                  ? `将导出 1 期：${batchDates[0]}（单文件）`
                  : `将导出 ${batchDates.length} 期：${batchDates[0]} ~ ${batchDates[batchDates.length - 1]}（打包 ZIP）`}
              </Typography.Text>
              {batchDates.length > 1 && (
                <>
                  <br />
                  {/* 期数一多就是"逐期生成再打包"，实测约 0.4~0.5 秒/期（12 个测点）。
                      先说清楚要等多久，用户才不会以为点坏了、反复点。 */}
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    批量是逐期生成再打包（实测约 0.5 秒/期），本次约 {Math.max(1, Math.round(batchDates.length * 0.5))} 秒，
                    期间请不要重复点击。
                  </Typography.Text>
                </>
              )}
            </div>
          ) : (
            range?.[0] &&
            range?.[1] && (
              // 区间内没有观测期次：必须说出来。日历上灰掉的日子点不动，
              // 用户很容易选出一个"看起来有范围、其实一期都没有"的区间。
              <Typography.Text type="warning">
                所选区间内没有观测数据（共 {dates.length} 期可选，日历上带横条的日子才是观测日）
              </Typography.Text>
            )
          )}

          <Space size="middle">
            <Tooltip title={blockedByCheck ? '母版体检有必须先处理的问题，改好后才能导出' : undefined}>
              <Button
                type="primary"
                icon={<FileExcelOutlined />}
                loading={loadingExcel}
                disabled={!range || batchDates.length === 0 || blockedByCheck}
                onClick={() => void excel()}
              >
                {batchDates.length > 1 ? `批量导出 Excel（${batchDates.length} 期 → ZIP）` : '导出 Excel 日报（单文件）'}
              </Button>
            </Tooltip>
            <Tooltip
              title={
                batchDates.length > 1
                  ? `把选中的 ${batchDates.length} 期合成一份 PDF，直接调起打印框（一次打完）`
                  : '生成 PDF 后直接弹出打印框，页面不跳转'
              }
            >
              <Button
                icon={<PrinterOutlined />}
                loading={printing}
                disabled={batchDates.length === 0 || blockedByCheck}
                onClick={() => void printPdf()}
              >
                {batchDates.length > 1 ? `打印日报（${batchDates.length} 期）` : '打印日报'}
              </Button>
            </Tooltip>
            <Button
              icon={<SafetyCertificateOutlined />}
              loading={checking}
              disabled={!selectedTpl || selectedTpl.is_builtin}
              onClick={() => void runCheck(templateId)}
            >
              体检这份母版
            </Button>
            <Button
              icon={<EyeOutlined />}
              loading={previewing}
              disabled={!previewDate}
              onClick={() => void preview()}
            >
              预览{previewDate && batchDates.length > 1 ? `（第一期 ${previewDate}）` : ''}
            </Button>
            <Button icon={<FileWordOutlined />} loading={loadingWord} disabled={!singleDate} onClick={() => void word()}>
              导出 Word 日报{!singleDate && batchDates.length > 0 ? '（仅单期）' : ''}
            </Button>
          </Space>
        </Space>
      </Card>

      <Modal title="上传报送母版（Excel 整卷样板）" open={uploadOpen} onCancel={() => setUploadOpen(false)} footer={null} width={560}>
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Alert
            type="info"
            showIcon
            message="上传一份填得最满意的整卷日报，系统学习它的结构"
            description="系统自动识别：封面/签字的日期与期次位置、两张数据表的数据区与列含义、天气格。此后每期导出=复制母版→自动更新期次/日期/天气/数据数值，其余（概况/结论等固定内容）原样保留。换模板只需再传一份新的。"
          />
          {/* 主操作放前面：先挑文件，名字可以之后再说 —— 早先是"必须先填名字才能选文件"，
              点了按钮就被退回"请先填写模板名称"，很别扭（用户 2026-10-06 反馈）。 */}
          <Upload beforeUpload={(f) => void doUpload(f)} accept=".xlsx,.xlsm" showUploadList={false} disabled={uploading}>
            <Button type="primary" block icon={<UploadOutlined />} loading={uploading}>
              {uploading ? '正在学习这份母版…' : '选择整卷文件上传'}
            </Button>
          </Upload>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              母版名称（可留空）
            </Typography.Text>
            <Input
              style={{ marginTop: 4 }}
              placeholder="留空就用文件名；上传后也能随时改"
              value={uploadName}
              onChange={(e) => setUploadName(e.target.value)}
            />
          </div>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              这份母版作用于哪些项目？（几个基坑版式一样就一次勾上，不用各传一遍）
            </Typography.Text>
            <div style={{ marginTop: 4 }}>
              <ScopePicker
                projects={projects}
                value={uploadProjectIds}
                isGlobal={uploadGlobal}
                onValueChange={setUploadProjectIds}
                onGlobalChange={setUploadGlobal}
              />
            </div>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            提示：用你日常报送最满意的那一期整卷（含封面/签字/数据表全部 Sheet）作母版最准。
            名字想改，在导出页选中这份母版后点「重命名」。
          </Typography.Text>
        </Space>
      </Modal>

      <Modal
        title={`设置适用项目 —— ${selectedTpl?.name ?? ''}`}
        open={scopeOpen}
        onCancel={() => setScopeOpen(false)}
        onOk={() => void doSaveScope()}
        okText="保存"
        confirmLoading={savingScope}
        width={520}
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: '100%' }} size="small">
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            一份母版可以被多个项目共用。改版式时<b>重新上传同名母版</b>即可，原先绑定的项目会自动继承，
            不用逐个项目重传。
          </Typography.Text>
          <ScopePicker
            projects={projects}
            value={scopeProjectIds}
            isGlobal={scopeGlobal}
            onValueChange={setScopeProjectIds}
            onGlobalChange={setScopeGlobal}
          />
        </Space>
      </Modal>

      <Modal
        title="重命名母版"
        open={renameOpen}
        onCancel={() => setRenameOpen(false)}
        onOk={() => void doRename()}
        okText="保存"
        confirmLoading={renaming}
        width={420}
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: '100%' }} size="small">
          <Input
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onPressEnter={() => void doRename()}
            placeholder="母版名称"
            autoFocus
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            只改显示名称，母版文件和已识别的布局都不受影响。【系统标准】模板不能改名。
          </Typography.Text>
        </Space>
      </Modal>

      <ExcelPreviewModal
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
        doc={previewDoc}
        loading={previewing}
      />

      {/* 体检里「没配控制值」这类问题，导出的数就来自这里 —— 就地开弹窗改，别让人去别页找 */}
      <WarningRulesModal
        projectId={projectId}
        open={rulesOpen}
        onClose={() => {
          setRulesOpen(false)
          void runCheck(templateId)
        }}
      />
    </Space>
  )
}
