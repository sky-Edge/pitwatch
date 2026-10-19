import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Space, Spin, Switch, Tag, Typography } from 'antd'
import { pointApi, type PointLayout } from '../api/client'
import { BRAND, NEUTRAL } from '../theme/tokens'

/**
 * 测点平面布置图。
 *
 * 用户 2026-10-19：「我想看到我每个基坑中初始值在 CAD 中的位置关系」
 *
 * 一句话：把每个测点的**初始位置**（水平位移的首期 X/Y 读数，就是大地坐标）按真实比例画出来，
 * 一眼看清谁在哪、间距多少、整体多大。
 *
 * 几条刻意的设计：
 * - **严格等比**，绝不为了"填满画布"把某个方向拉伸 —— 拉过之后看间距就没意义了。
 * - 屏幕上的点是固定大小的圆（不随比例缩放），但**位置**是真坐标，所以量间距可信。
 * - 画不上的点（纯沉降点、缺 Y、坐标录反）**在下面点名**，不静默少几个。
 * - 右下角有比例尺。工程图没有比例尺，读者没法判断"这堆点到底多大"。
 */

const CANVAS_W = 760
/** 四周留白：上方给点号、下方给坐标文字 */
const PAD = 68
/** 绘图区最大高度：先按宽度定比例，实在太高（狭长基坑）才缩。
 *  420 太紧 —— 等比之后整张图只占画布中间一小块，点挤成一团、文字互相压。 */
const MAX_INNER_H = 860
/** 比例尺候选长度（米） */
const NICE_LENGTHS = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]

export default function PointLayoutCard({ projectId }: { projectId: number }) {
  const [data, setData] = useState<PointLayout | null>(null)
  const [loading, setLoading] = useState(false)
  const [showCoords, setShowCoords] = useState(true)
  /** 图默认展开；但 12 个点就有 880px 高，一直摊着会把下面的测点表挤走 */
  const [showChart, setShowChart] = useState(true)
  const [showDims, setShowDims] = useState(true)
  const [showBox, setShowBox] = useState(true)

  useEffect(() => {
    let alive = true
    setLoading(true)
    pointApi
      .layout(projectId)
      .then((d) => {
        if (alive) setData(d)
      })
      .catch(() => {
        if (alive) setData(null)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [projectId])

  const geo = useMemo(() => {
    const bbox = data?.bbox
    if (!bbox || (data?.points.length ?? 0) === 0) return null
    // ★ 测量坐标系 → 图纸方向（与 CAD 一致）：**X = 北（向上）、Y = 东（向右）**。
    // 所以屏幕上**横向取 Y、纵向取 X**。反过来的话整张图会转 90°，
    // 拿去对 CAD 总平面就会觉得"X/Y 是反的"（用户 2026-10-19 实际反馈）。
    const spanEW = Math.max(bbox.height_m, 0.001) // 东西向跨度 = Y 跨度
    const spanNS = Math.max(bbox.width_m, 0.001) // 南北向跨度 = X 跨度

    // 等比：取两个方向里更"紧"的那个比例，保证宽高都不越界
    const scale = Math.min((CANVAS_W - PAD * 2) / spanEW, MAX_INNER_H / spanNS)
    const innerH = spanNS * scale
    const H = innerH + PAD * 2

    /** 测量 Y（东）→ 屏幕横坐标 */
    const sx = (y: number) => PAD + (y - bbox.min_y_m) * scale
    /** 测量 X（北）→ 屏幕纵坐标（屏幕 y 向下，所以要翻） */
    const sy = (x: number) => H - PAD - (x - bbox.min_x_m) * scale

    const barM = NICE_LENGTHS.find((v) => v * scale >= 72) ?? NICE_LENGTHS[NICE_LENGTHS.length - 1]

    return { H, sx, sy, scale, barM, spanEW, spanNS }
  }, [data])

  if (loading && !data) {
    return (
      <Card size="small" title="平面布置">
        <Spin />
      </Card>
    )
  }

  if (!data) return null

  const title = (
    <Space size={8}>
      <span>平面布置</span>
      <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
        （按初始位置的真实坐标，等比例）
      </Typography.Text>
    </Space>
  )

  const collapseBtn = data.placed_count > 0 && (
    <Button type="link" size="small" onClick={() => setShowChart((v) => !v)}>
      {showChart ? '收起图' : '展开图'}
    </Button>
  )

  if (data.placed_count === 0) {
    return (
      <Card size="small" title={title}>
        <Alert
          type="info"
          showIcon
          message="这个项目的测点没有平面坐标，画不出布置图"
          description={
            <>
              <div style={{ marginBottom: 6 }}>
                平面位置取的是<b>水平位移的 X/Y 首期读数</b>。下面这些点取不到：
              </div>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {data.missing.slice(0, 6).map((m) => (
                  <li key={m.point_no}>
                    {m.point_no} —— {m.why}
                  </li>
                ))}
                {data.missing.length > 6 && <li>……还有 {data.missing.length - 6} 个</li>}
              </ul>
            </>
          }
        />
      </Card>
    )
  }

  return (
    <Card size="small" title={title} extra={collapseBtn}>
      {!showChart && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          图已收起（{data.placed_count} 个点，范围 {data.bbox?.width_m} m × {data.bbox?.height_m} m）
        </Typography.Text>
      )}
      {showChart && (
        <>
      <Space wrap size={16} style={{ marginBottom: 10 }}>
        <Tag color="blue">
          画了 {data.placed_count} 个点
        </Tag>
        {data.bbox && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            南北 {data.bbox.width_m} m × 东西 {data.bbox.height_m} m
          </Typography.Text>
        )}
        <Space size={4}>
          <Switch size="small" checked={showCoords} onChange={setShowCoords} />
          <Typography.Text style={{ fontSize: 12 }}>坐标数值</Typography.Text>
        </Space>
        <Space size={4}>
          <Switch size="small" checked={showDims} onChange={setShowDims} />
          <Typography.Text style={{ fontSize: 12 }}>相邻间距</Typography.Text>
        </Space>
        <Space size={4}>
          <Switch size="small" checked={showBox} onChange={setShowBox} />
          <Typography.Text style={{ fontSize: 12 }}>范围框</Typography.Text>
        </Space>
      </Space>

      {geo && (
        <svg
          viewBox={`0 0 ${CANVAS_W} ${Math.round(geo.H)}`}
          width="100%"
          role="img"
          aria-label="测点平面布置图"
          style={{ display: 'block', maxHeight: 880 }}
        >
          {/* 范围框：按点的包络线画，不是真实围护结构 */}
          {showBox && data.bbox && (
            <rect
              x={geo.sx(data.bbox.min_y_m)}
              y={geo.sy(data.bbox.max_x_m)}
              width={Math.max(1, data.bbox.height_m * geo.scale)}
              height={Math.max(1, data.bbox.width_m * geo.scale)}
              fill="none"
              stroke={NEUTRAL.muted}
              strokeWidth={1}
              strokeDasharray="6 4"
            />
          )}

          {/* 相邻点连线 + 间距 */}
          {showDims &&
            data.segments.map((s) => {
              const a = data.points.find((p) => p.point_no === s.from)
              const b = data.points.find((p) => p.point_no === s.to)
              if (!a || !b) return null
              const x1 = geo.sx(a.y_m)
              const y1 = geo.sy(a.x_m)
              const x2 = geo.sx(b.y_m)
              const y2 = geo.sy(b.x_m)
              const mx = (x1 + x2) / 2
              const my = (y1 + y2) / 2
              return (
                <g key={`${s.from}-${s.to}`}>
                  <line
                    x1={x1}
                    y1={y1}
                    x2={x2}
                    y2={y2}
                    stroke={NEUTRAL.muted}
                    strokeWidth={1}
                    strokeDasharray="4 3"
                  />
                  <text
                    x={mx}
                    y={my - 5}
                    textAnchor="middle"
                    fontSize={10}
                    fill={NEUTRAL.muted}
                    style={{ paintOrder: 'stroke' }}
                    stroke={NEUTRAL.white}
                    strokeWidth={3}
                  >
                    {s.length_m} m
                  </text>
                </g>
              )
            })}

          {/* 点 + 点号 + 坐标 */}
          {data.points.map((p) => {
            const cx = geo.sx(p.y_m)
            const cy = geo.sy(p.x_m)
            return (
              <g key={p.point_no}>
                <circle cx={cx} cy={cy} r={5.5} fill={BRAND.primary} stroke={NEUTRAL.white} strokeWidth={2} />
                <text
                  x={cx}
                  y={cy - 12}
                  textAnchor="middle"
                  fontSize={12.5}
                  fill={BRAND.primary}
                  fontWeight={500}
                  stroke={NEUTRAL.white}
                  strokeWidth={3.5}
                  style={{ paintOrder: 'stroke' }}
                >
                  {p.point_no}
                </text>
                {showCoords && (
                  <>
                    <text
                      x={cx}
                      y={cy + 18}
                      textAnchor="middle"
                      fontSize={9}
                      fill={NEUTRAL.muted}
                      stroke={NEUTRAL.white}
                      strokeWidth={3}
                      style={{ paintOrder: 'stroke' }}
                    >
                      X {p.x_m.toFixed(3)}
                    </text>
                    <text
                      x={cx}
                      y={cy + 29}
                      textAnchor="middle"
                      fontSize={9}
                      fill={NEUTRAL.muted}
                      stroke={NEUTRAL.white}
                      strokeWidth={3}
                      style={{ paintOrder: 'stroke' }}
                    >
                      Y {p.y_m.toFixed(3)}
                    </text>
                  </>
                )}
              </g>
            )
          })}

          {/* 指北针：X 增大方向就是北（屏幕上向上）。工程图没这个标记，
              读者没法确认"哪边是北"，方向一旦画反也没人看得出来 */}
          <g transform={`translate(${CANVAS_W - 46}, 26)`}>
            <line x1={0} y1={24} x2={0} y2={8} stroke={NEUTRAL.muted} strokeWidth={1.5} />
            <path d="M0 0 L-5 10 L0 7 L5 10 Z" fill={NEUTRAL.muted} />
            <text x={0} y={-3} textAnchor="middle" fontSize={11} fontWeight={500} fill={NEUTRAL.muted}>
              N
            </text>
          </g>

          {/* 比例尺：没有它，读者没法判断这堆点到底多大 */}
          {(() => {
            const x0 = CANVAS_W - PAD
            const y0 = Math.round(geo.H) - 18
            const len = geo.barM * geo.scale
            return (
              <g>
                <line x1={x0 - len} y1={y0} x2={x0} y2={y0} stroke={NEUTRAL.muted} strokeWidth={1.5} />
                <line x1={x0 - len} y1={y0 - 4} x2={x0 - len} y2={y0 + 4} stroke={NEUTRAL.muted} strokeWidth={1.5} />
                <line x1={x0} y1={y0 - 4} x2={x0} y2={y0 + 4} stroke={NEUTRAL.muted} strokeWidth={1.5} />
                <text x={x0 - len / 2} y={y0 - 7} textAnchor="middle" fontSize={10} fill={NEUTRAL.muted}>
                  {geo.barM} m
                </text>
              </g>
            )
          })()}
        </svg>
      )}

      <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
        位置取每个测点<b>最早一次</b>位移观测的 X/Y（大地坐标，单位 m）。方向按测量习惯画：
        <b>横向是 Y（东西）、纵向是 X（北）</b>，和 CAD 里打开看到的一致。
        点的圆圈大小是屏幕尺寸，位置是真实坐标；范围框只是所有点的包络，不代表围护结构。
      </Typography.Text>

      {data.missing.length > 0 && (
        <Alert
          style={{ marginTop: 10 }}
          type="warning"
          showIcon
          message={`有 ${data.missing.length} 个测点画不上`}
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {data.missing.slice(0, 6).map((m) => (
                <li key={m.point_no}>
                  {m.point_no} —— {m.why}
                </li>
              ))}
              {data.missing.length > 6 && <li>……还有 {data.missing.length - 6} 个</li>}
            </ul>
          }
        />
      )}
        </>
      )}
    </Card>
  )
}
