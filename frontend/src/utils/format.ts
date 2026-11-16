/**
 * 显示层格式化。
 *
 * 为什么单独抽出来：mm 数值一路从 Calculation Engine（浮点）传到界面，
 * 实测出现过「累计 -7.200000286102295mm」直接印在首页报警横幅上 ——
 * 工程资料里这种数字一眼就是"算错了"，哪怕底层计算完全正确。
 *
 * 后端 `calc_service` 已在汇总接口里 round 到 2 位；这里再兜一道，
 * 因为记录页手填值、导入的旧数据、缓存结果都可能绕过后端那条路。
 */

/**
 * mm 数值显示：最多两位小数，去掉尾部 0。
 *
 * `7.200000286102295 → "7.2"`，`7.25 → "7.25"`，`null → "—"`。
 * 用 `Number(x.toFixed(2))` 而不是 `toFixed(2)`：后者会把 7.2 显示成 "7.20"，
 * 表格里一整列 "7.20 / 12.40 / -3.50" 看着比实际精度更高，反而失真。
 */
export function fmtMm(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === '') return '—'
  const n = typeof v === 'string' ? Number(v) : v
  if (!Number.isFinite(n)) return String(v)
  return String(Number(n.toFixed(2)))
}
