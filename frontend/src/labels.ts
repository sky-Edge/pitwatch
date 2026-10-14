// -*- coding: utf-8 -*-
/**
 * 测点/观测项的中文标签 —— 全端唯一来源。
 *
 * 为什么要单独立一个文件：同一份"沉降 / 水平位移 / 沉降+位移"的映射原先抄了
 * 3 份 `TYPE_LABEL`（ImportPage、BatchPointsModal、PointsPage），另有 6 处内联三元
 * 各写各的。写错的后果是**界面上同一件事两个名字** —— 实测 `ProjectsPage` 的
 * 「需要关注」列表用「位移」，别处一律用「水平位移」，现场人员对不上是哪一列。
 *
 * 用词以台账口径为准：界面显示「水平位移」，不简写。
 */
import type { PointType, SeriesType } from './api/client'

export const POINT_TYPE_LABEL: Record<PointType, string> = {
  settlement: '沉降',
  displacement: '水平位移',
  both: '沉降+位移',
}

/** antd Tag 的预设色名（不是十六进制，不受 test_frontend_theme 的字面量约束） */
export const POINT_TYPE_TAG_COLOR: Record<PointType, string> = {
  settlement: 'blue',
  displacement: 'orange',
  both: 'purple',
}

/** 观测系列名：沉降 / 水平位移（与 POINT_TYPE_LABEL 同一口径） */
export const seriesLabel = (seriesType: SeriesType): string => POINT_TYPE_LABEL[seriesType]
