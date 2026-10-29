import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Input, Modal, Select, Space, Tag, Typography, message } from 'antd'
import { reportApi, settingsApi, warningRuleApi, type ExportTemplateInfo, type WarningRuleSetInfo } from '../api/client'

const WEATHERS = ['晴', '多云', '阴', '小雨', '中雨', '大雨']

type WeatherLoc = { label: string; latitude: number; longitude: number }
type GeoHit = { label: string; admin?: string; latitude: number; longitude: number }

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
