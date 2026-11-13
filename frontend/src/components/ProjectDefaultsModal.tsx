import { useCallback, useEffect, useState } from 'react'

import { Alert, Button, Input, Modal, Select, Space, Tag, Typography, Upload, message } from 'antd'

import { UploadOutlined } from '@ant-design/icons'

import { reportApi, settingsApi, warningRuleApi, type ExportTemplateInfo, type WarningRuleSetInfo } from '../api/client'



const WEATHERS = ['晴', '多云', '阴', '小雨', '中雨', '大雨']



type WeatherLoc = { label: string; latitude: number; longitude: number }

type GeoHit = { label: string; admin?: string; latitude: number; longitude: number }

type Instrument = { name: string; model: string; serial: string; calibrated_on: string }

type InstrumentPreset = { category: string; name: string; model: string }

const EMPTY_INST: Instrument = { name: '', model: '', serial: '', calibrated_on: '' }



/**

 * 项目默认值 —— 新建项目自动带上规则套 / 母版 / 天气。

 *

 * 用户 2026-10-11：「每个项目都要调单独设置有点繁琐」

 * 新基坑进场最烦的就是"建完还得再配三样"。这里把这三样做成全站一份，

 * 建项目时自动套上（规则套绑定、项目加进母版的适用范围）。

 *

 * ⚠ 只影响**以后新建**的项目。已经存在的项目一个都不动 ——

 *   改历史配置是危险动作，要改请用项目列表上的「批量套规则 / 批量套母版」，

 *   那条路会明确告诉你影响了哪几个项目。

 */

export default function ProjectDefaultsModal({

  open,

  onClose,

}: {

  open: boolean

  onClose: () => void

}) {

  const [ruleSets, setRuleSets] = useState<WarningRuleSetInfo[]>([])

  const [templates, setTemplates] = useState<ExportTemplateInfo[]>([])

  const [ruleSetId, setRuleSetId] = useState<number | null>(null)

  const [templateId, setTemplateId] = useState<number | null>(null)

  const [loc, setLoc] = useState<WeatherLoc | null>(null)

  const [locQuery, setLocQuery] = useState('')

  const [locHits, setLocHits] = useState<GeoHit[]>([])

  const [locBusy, setLocBusy] = useState(false)

  /** 六个签字栏各自的签名 —— 一张图盖全 11 处，等于让一个人同时签编制和审核 */

  const [sigRoles, setSigRoles] = useState<
    { role: string; name: string; exists: boolean; image: string | null }[]
  >([])

  const [sigBusy, setSigBusy] = useState<string | null>(null)

  /** 仪器清单（全局一份，导出时选一台） */

  const [insts, setInsts] = useState<Instrument[]>([])

  const [instDraft, setInstDraft] = useState<Instrument>(EMPTY_INST)

  /** 常见仪器备选库（后端只读清单，选一台就预填名称+型号） */

  const [presets, setPresets] = useState<InstrumentPreset[]>([])

  const [weather, setWeather] = useState('晴')

  const [loading, setLoading] = useState(false)

  const [saving, setSaving] = useState(false)



  const searchLoc = async () => {

    const q = locQuery.trim()

    if (!q) return

    setLocBusy(true)

    try {

      const r = await fetch(`/api/v1/weather/geocode?name=${encodeURIComponent(q)}`)

      const j = await r.json()

      if (!r.ok) throw new Error(j?.detail || '没找到这个地名')

      setLocHits(j as GeoHit[])

    } catch (e) {

      message.error((e as Error).message)

    } finally {

      setLocBusy(false)

    }

  }



  const loadSignatures = async () => {
    try {
      const r = await fetch('/api/v1/settings/signatures')
      const j = await r.json()
      setSigRoles(Array.isArray(j?.roles) ? j.roles : [])
    } catch {
      setSigRoles([])
    }
  }

  const uploadRoleSignature = async (role: string, file: File) => {
    setSigBusy(role)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch(`/api/v1/settings/signatures/${role}`, { method: 'POST', body: fd })
      const j = await r.json()
      if (!r.ok) throw new Error(j?.detail || '上传失败')
      await loadSignatures()
      message.success('已保存')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSigBusy(null)
    }
  }

  const removeRoleSignature = async (role: string) => {
    setSigBusy(role)
    try {
      await fetch(`/api/v1/settings/signatures/${role}`, { method: 'DELETE' })
      await loadSignatures()
      message.success('已删除，这一栏会留空')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSigBusy(null)
    }
  }

  const load = useCallback(async () => {

    setLoading(true)

    try {

      const [cfg, sets, lib] = await Promise.all([

        settingsApi.get(),

        warningRuleApi.listSets(),

        reportApi.listLibrary(),

      ])

      setRuleSets(sets)

      // 只有 Excel 母版能做默认导出母版（Word 日报走的是另一条固定版式）

      setTemplates(lib.filter((t) => t.template_type === 'excel'))

      setRuleSetId(cfg.default_rule_set_id)

      setTemplateId(cfg.default_template_id)

      setWeather(cfg.default_weather || '晴')

      setLoc((cfg as { weather_location?: WeatherLoc | null }).weather_location ?? null)

      void loadSignatures()

      setInsts(((cfg as { instruments?: Instrument[] }).instruments ?? []) as Instrument[])

      void fetch('/api/v1/settings/instrument-presets')

        .then((r) => (r.ok ? r.json() : []))

        .then((j) => setPresets(Array.isArray(j) ? (j as InstrumentPreset[]) : []))

        .catch(() => setPresets([]))

    } catch (e) {

      message.error((e as Error).message)

    } finally {

      setLoading(false)

    }

  }, [])



  useEffect(() => {

    if (open) void load()

  }, [open, load])



  const save = async () => {

    setSaving(true)

    try {

      await settingsApi.update({

        default_rule_set_id: ruleSetId,

        default_template_id: templateId,

        default_weather: weather,

        weather_location: loc,

        instruments: insts.filter((i) => i.model.trim() || i.serial.trim()),

      })

      message.success('已保存 —— 以后新建的项目会自动套上这些设置')

      onClose()

    } catch (e) {

      message.error((e as Error).message)

    } finally {

      setSaving(false)

    }

  }



  const ruleName = ruleSets.find((s) => s.id === ruleSetId)?.name

  const tplName = templates.find((t) => t.id === templateId)?.name



  return (

    <Modal

      title="项目默认值（新建项目自动套上）"

      open={open}

      onCancel={onClose}

      onOk={() => void save()}

      confirmLoading={saving}

      okText="保存"

      cancelText="取消"

      width={640}

      loading={loading}

    >

      <Space direction="vertical" style={{ width: '100%' }} size="middle">

        <Alert

          type="info"

          showIcon

          message="这里只影响以后新建的项目"

          description="已经建好的项目一个都不动。要把已有的项目改成同一套，请回项目列表勾选它们，用上面的「套用规则 / 套用母版」。"

        />



        <div>

          <Typography.Text strong>默认预警规则</Typography.Text>

          <br />

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>

            新建项目自动绑定这一套 —— 建完就能判预警，不用再配一遍。选「不绑定」则靠通用规则兜底。

          </Typography.Text>

          <Select

            style={{ width: '100%', marginTop: 4 }}

            value={ruleSetId ?? 0}

            onChange={(v) => setRuleSetId(v === 0 ? null : v)}

            options={[

              { value: 0, label: '不绑定（用通用规则兜底）' },

              ...ruleSets.map((s) => ({

                value: s.id,

                label: `${s.name}${s.is_global ? '（通用）' : ` · 现作用于 ${s.project_ids.length} 个项目`}`,

              })),

            ]}

          />

        </div>



        <div>

          <Typography.Text strong>默认导出母版</Typography.Text>

          <br />

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>

            新建项目自动加入这份母版的适用范围 —— 建完就能直接导日报。一个项目可以有多份可用母版，

            这里只是"默认先挂上这一份"。

          </Typography.Text>

          <Select

            style={{ width: '100%', marginTop: 4 }}

            value={templateId ?? 0}

            onChange={(v) => setTemplateId(v === 0 ? null : v)}

            options={[

              { value: 0, label: '不挂母版（导出时用系统标准版式）' },

              ...templates.map((t) => ({

                value: t.id,

                label: `${t.name}${t.is_builtin ? '（系统内置）' : t.is_global ? '（通用）' : ''}${

                  t.file_ok === false ? ' —— 原件缺失' : ''

                }`,

              })),

            ]}

          />

        </div>



        <div>

          <Typography.Text strong>默认天气</Typography.Text>

          <br />

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>

            导出页打开时默认选中它（现场每期还能单独改）。

          </Typography.Text>

          <Select

            style={{ width: '100%', marginTop: 4 }}

            value={weather}

            onChange={setWeather}

            options={WEATHERS.map((w) => ({ value: w, label: w }))}

          />

        </div>



        <div>

          <Typography.Text strong>天气位置（导出时自动填天气）</Typography.Text>

          <br />

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>

            配好之后，导出日报的天气会自动按当天当地填写，不用每次手选。

            <b>中文地名可能搜不到，用拼音更稳</b>（Jiashan / Jiaxing / Shanghai）。

          </Typography.Text>

          <Space.Compact style={{ width: '100%', marginTop: 4 }}>

            <Input

              value={locQuery}

              onChange={(e) => setLocQuery(e.target.value)}

              placeholder="Jiashan"

              onPressEnter={() => void searchLoc()}

            />

            <Button onClick={() => void searchLoc()} loading={locBusy}>

              搜索

            </Button>

          </Space.Compact>

          {locHits.length > 0 && (

            <Select

              style={{ width: '100%', marginTop: 6 }}

              placeholder="选一个地点"

              value={undefined}

              onChange={(v) => {

                const hit = locHits.find((h) => h.label === v)

                if (hit) {

                  setLoc({ label: hit.label, latitude: hit.latitude, longitude: hit.longitude })

                  setLocHits([])

                  setLocQuery('')

                }

              }}

              options={locHits.map((h) => ({ value: h.label, label: `${h.label}${h.admin ? `（${h.admin}）` : ''}` }))}

            />

          )}

          {loc && (

            <Tag closable onClose={() => setLoc(null)} style={{ marginTop: 8 }} color="blue">

              天气位置：{loc.label}

            </Tag>

          )}

        </div>



        <div>
          <Typography.Text strong>电子签名（每个签字栏单独一张）</Typography.Text>
          <br />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            上传各人自己的手写签名（白纸黑字，PNG/JPG 都行）。导出的日报按栏盖对应的签名；
            <b>没上传的栏留空</b>，不会拿别人的顶替。
          </Typography.Text>
          <div style={{ marginTop: 8 }}>
            {sigRoles.map((r) => (
              <div
                key={r.role}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '5px 0',
                  borderBottom: '1px solid var(--wm-grid-line)',
                }}
              >
                <Typography.Text style={{ width: 96 }}>{r.name}</Typography.Text>
                {r.image ? (
                  <img
                    src={r.image}
                    alt={r.name}
                    style={{ height: 34, maxWidth: 130, objectFit: 'contain' }}
                  />
                ) : (
                  <Typography.Text type="secondary" style={{ fontSize: 12, width: 130 }}>
                    未设置
                  </Typography.Text>
                )}
                <span style={{ flex: 1 }} />
                <Upload
                  accept="image/*"
                  showUploadList={false}
                  beforeUpload={(file) => {
                    void uploadRoleSignature(r.role, file as File)
                    return false
                  }}
                >
                  <Button size="small" icon={<UploadOutlined />} loading={sigBusy === r.role}>
                    {r.exists ? '重传' : '上传'}
                  </Button>
                </Upload>
                {r.exists && (
                  <Button
                    size="small"
                    danger
                    loading={sigBusy === r.role}
                    onClick={() => void removeRoleSignature(r.role)}
                  >
                    删除
                  </Button>
                )}
              </div>
            ))}
            {sigRoles.length === 0 && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                正在读取签字栏…
              </Typography.Text>
            )}
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 6 }}>
            也可以直接把 PNG 放进服务器的 <code>database/signatures/</code>，文件名用栏名（如{' '}
            <code>编制.png</code>），刷新本页即可。
          </Typography.Text>
        </div>

        <div>

          <Typography.Text strong>测量仪器（导出时选一台）</Typography.Text>

          <br />

          <Typography.Text type="secondary" style={{ fontSize: 12 }}>

            配好之后，导出页会多一个「仪器」下拉；选了哪台，日报里的「仪器型号 / 出厂编号 / 检定日期」就填哪台。

            下面能搜型号（常见款已预置），库里没有的照旧手输。

          </Typography.Text>

          {insts.length > 0 && (

            <div style={{ marginTop: 6 }}>

              {insts.map((it, idx) => (

                <Tag

                  key={idx}

                  closable

                  onClose={() => setInsts(insts.filter((_, i) => i !== idx))}

                  style={{ marginBottom: 4 }}

                >

                  {it.name || '仪器'}

                  {it.model ? ` ${it.model}` : ''}

                  {it.serial ? ` #${it.serial}` : ''}

                </Tag>

              ))}

            </div>

          )}

          {/* 从备选库选：型号不用背，选中只预填名称和型号，

              出厂编号、检定日期还得自己填（那两样因人而异，库里不可能有） */}

          <Select

            showSearch

            style={{ width: '100%', marginTop: 6 }}

            placeholder={

              presets.length

                ? `从常见仪器里选（可搜型号，如 DNA03 / TS16），共 ${presets.length} 款`

                : '从常见仪器里选（型号可搜）'

            }

            value={undefined}

            optionFilterProp="label"

            onChange={(v: string) => {

              const hit = presets.find((x) => x.model === v)

              if (hit) setInstDraft({ ...instDraft, name: hit.name, model: hit.model })

            }}

            options={Array.from(new Set(presets.map((x) => x.category))).map((cat) => ({

              label: cat,

              options: presets

                .filter((x) => x.category === cat)

                .map((x) => ({ value: x.model, label: `${x.model}（${x.name}）` })),

            }))}

          />

          <Space.Compact style={{ width: '100%', marginTop: 6 }}>

            <Input

              placeholder="名称（水准仪/全站仪）"

              value={instDraft.name}

              onChange={(e) => setInstDraft({ ...instDraft, name: e.target.value })}

              style={{ width: '24%' }}

            />

            <Input

              placeholder="型号"

              value={instDraft.model}

              onChange={(e) => setInstDraft({ ...instDraft, model: e.target.value })}

              style={{ width: '28%' }}

            />

            <Input

              placeholder="出厂编号"

              value={instDraft.serial}

              onChange={(e) => setInstDraft({ ...instDraft, serial: e.target.value })}

              style={{ width: '24%' }}

            />

            <Input

              placeholder="检定 2025-10-20"

              value={instDraft.calibrated_on}

              onChange={(e) => setInstDraft({ ...instDraft, calibrated_on: e.target.value })}

              style={{ width: '24%' }}

            />

          </Space.Compact>

          <Button

            size="small"

            style={{ marginTop: 6 }}

            disabled={!instDraft.model.trim()}

            onClick={() => {

              setInsts([...insts, instDraft])

              setInstDraft(EMPTY_INST)

            }}

          >

            添加这台仪器

          </Button>

        </div>



        <Alert

          type="success"

          showIcon

          message="以后新建项目会自动："

          description={

            <ul style={{ margin: 0, paddingLeft: 18 }}>

              <li>绑定预警规则：{ruleName ? `「${ruleName}」` : '不绑定（靠通用规则）'}</li>

              <li>挂上母版：{tplName ? `「${tplName}」` : '不挂（用系统标准版式）'}</li>

              <li>默认天气：{weather}</li>

            </ul>

          }

        />



        <Button size="small" onClick={() => void load()}>

          重新读取当前设置

        </Button>

      </Space>

    </Modal>

  )

}

