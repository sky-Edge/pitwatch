import { useEffect, useRef, type RefObject } from 'react'
import { Button, Card, Input, Progress, Space, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, ArrowRightOutlined } from '@ant-design/icons'
import type { InputRef } from 'antd'
import type { Component, MeasurementPoint, ObservationRecord, SeriesType } from '../api/client'

export interface SequenceInputs {
  value: string
  x: string
  y: string
}

interface FieldDef {
  key: 'value' | 'x' | 'y'
  label: string
}

/**
 * 顺序录入面板：一屏一个测点，全键盘操作。
 *
 * 交互约定（现场读数场景）：
 * - 进入即自动聚焦第一个输入框，不需要先点一下。
 * - 回车 = 跳到下一个输入框；在本测点最后一个输入框按回车 = 跳到下一个测点。
 * - 上/下方向键 = 上一个/下一个测点（发现敲错了快速回退）。
 * - 留空 = 缺测（与表格模式语义一致）。
 *
 * 已录入的值会在进入某个测点时预填，方便核对后微调，而不是空白重敲。
 */
export default function SequenceEntry({
  points,
  index,
  inputs,
  filled,
  total,
  fieldsOf,
  inputRef,
  existingOf,
  onGoto,
  onSet,
  onSave,
  saving,
}: {
  points: MeasurementPoint[]
  index: number
  inputs: Record<number, SequenceInputs>
  filled: number
  total: number
  fieldsOf: (p: MeasurementPoint) => FieldDef[]
  inputRef: RefObject<InputRef>
  existingOf: (pointId: number, st: SeriesType, comp: Component) => ObservationRecord | undefined
  onGoto: (i: number) => void
  onSet: (pointId: number, key: 'value' | 'x' | 'y', v: string) => void
  onSave: () => void
  saving: boolean
}) {
  const point = points[index]
  const fields = point ? fieldsOf(point) : []
  const fieldRefs = useRef<Array<InputRef | null>>([])
  /** 防止回车连续触发两次跳转 */
  const lockRef = useRef(false)

  // 切换测点时：聚焦第一个输入框，并清空上一次的引用
  useEffect(() => {
    fieldRefs.current = []
    const t = window.setTimeout(() => focusField(0), 0)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, points.length])

  const focusField = (i: number) => {
    if (i >= 0 && i < fields.length) {
      fieldRefs.current[i]?.focus()
    } else if (i >= fields.length) {
      // 本测点已填完 → 跳下一个测点
      if (index < points.length - 1) onGoto(index + 1)
      else onSave()
    }
  }

  /** 回车：推进到下一个字段 / 下一个测点；到最后一个就保存。 */
  const handleEnter = (fieldIdx: number) => {
    if (lockRef.current) return
    lockRef.current = true
    window.setTimeout(() => {
      lockRef.current = false
    }, 120)
    focusField(fieldIdx + 1)
  }

  const handleKeyDown = (e: React.KeyboardEvent, fieldIdx: number) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleEnter(fieldIdx)
    } else if (e.key === 'ArrowDown') {
      // 方向键切换测点：不干扰输入框内的正常编辑
      e.preventDefault()
      if (index < points.length - 1) onGoto(index + 1)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (index > 0) onGoto(index - 1)
    }
  }

  if (!point) return null

  const unit = 'm'

  return (
    <Card
      size="small"
      title={
        <Space>
          <Typography.Text strong>顺序录入</Typography.Text>
          <Tag color="blue">
            第 {index + 1} / {points.length} 个测点
          </Tag>
        </Space>
      }
      extra={
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          回车=下一格 · ↑↓=切换测点 · 留空=缺测
        </Typography.Text>
      }
    >
      <Progress
        percent={total ? Math.round((filled / total) * 100) : 0}
        size="small"
        format={() => `${filled} / ${total} 项已填`}
        style={{ marginBottom: 12 }}
      />

      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 14 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {point.point_no}
        </Typography.Title>
        <Tag color={point.point_type === 'both' ? 'purple' : point.point_type === 'settlement' ? 'blue' : 'orange'}>
          {point.point_type === 'both' ? '沉降+位移' : point.point_type === 'settlement' ? '沉降' : '水平位移'}
        </Tag>
        {point.location_desc && (
          <Typography.Text type="secondary">{point.location_desc}</Typography.Text>
        )}
      </div>

      <Space direction="vertical" size={10} style={{ width: '100%', maxWidth: 420 }}>
        {fields.map((f, i) => {
          const st: SeriesType = f.key === 'value' ? 'settlement' : 'displacement'
          const comp: Component = f.key === 'value' ? 'value' : f.key === 'x' ? 'X' : 'Y'
          const existing = existingOf(point.id, st, comp)
          const val = (inputs[point.id] ?? { value: '', x: '', y: '' })[f.key]
          return (
            <div key={f.key} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Typography.Text style={{ width: 68, display: 'inline-block' }}>{f.label}</Typography.Text>
              <Input
                ref={(el) => {
                  fieldRefs.current[i] = el
                  if (i === 0) {
                    // 顶层也保留一个引用，供父组件切换模式后聚焦
                    ;(inputRef as React.MutableRefObject<InputRef | null>).current = el
                  }
                }}
                size="large"
                style={{ width: 180 }}
                suffix={<Typography.Text type="secondary">{unit}</Typography.Text>}
                placeholder="留空=缺测"
                value={val}
                onChange={(e) => onSet(point.id, f.key, e.target.value)}
                onKeyDown={(e) => handleKeyDown(e, i)}
              />
              {existing && existing.value_mm !== null && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  已录 {(existing.value_mm / 1000).toFixed(3)}
                </Typography.Text>
              )}
            </div>
          )
        })}
      </Space>

      <Space style={{ marginTop: 18 }}>
        <Button icon={<ArrowLeftOutlined />} disabled={index === 0} onClick={() => onGoto(index - 1)}>
          上一个
        </Button>
        <Button
          icon={<ArrowRightOutlined />}
          iconPosition="end"
          disabled={index >= points.length - 1}
          onClick={() => onGoto(index + 1)}
        >
          下一个
        </Button>
        <Button type="primary" loading={saving} onClick={onSave}>
          保存本期观测
        </Button>
      </Space>

      {index === points.length - 1 && (
        <div style={{ marginTop: 10 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            已是最后一个测点，按回车或点「保存本期观测」即可提交。
          </Typography.Text>
        </div>
      )}
    </Card>
  )
}
