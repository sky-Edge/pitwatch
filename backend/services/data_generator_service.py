# -*- coding: utf-8 -*-
"""观测数据自动生成器（用户需求 #9）。

用途：提供基坑的基准（V0）后，自动生成一段日期范围内、每天每个测点每分量的观测读数，
用于演示、测试、或快速预填试算。**生成数据一律 source=generated 落库**，
与真实录入（manual/excel）严格区分，可整批清理。

【日期冲突怎么办（2026-09-29 改）】
原先遇到"这天已经有数据"是**静默跳过**，用户根本不知道范围内哪些日期没生成。
现在改为**先预检、由用户决定**：

- `skip`（默认）：跳过已有数据的日期 —— 与原先行为一致，最安全；
- `replace_generated`：只替换掉那些**之前自动生成**的数据（可物理删除，与"清理演示数据"同类）；
- `replace_all`：连**真实数据**一起换掉。

⚠ `replace_all` 对真实数据**不做物理删除** —— 原始数据表只增不删是项目的硬约束。
真实记录只被置为 `status='void'`（作废），数值原样保留可追溯，随后插入新的生成记录
（`void` 不占唯一索引，所以不会撞 uq_obs_active）。

算法（确定性 + 可复算）：
- 对每个测点分量：从 V0（或范围开始日之前最近一期）出发逐日生成读数值。
- 每日变化 = **测量噪声**（现场重复性）+ **均值回复力**（围绕初值波动）+ 偶发扰动。
  **没有确定性趋势项** —— 用户明确要求"不要出现持续单调递增或递减、不要形成明显的趋势"；
  而且真实台账（八号 21 期）的累计量本来就是在 9~12mm 之间来回走，并非单调变形。
- 幅度锚定在**真实台账量出来的统计**上，不是锚定在规范限值上（见 `_NOISE_SPEC`）。
- 另有两条约束：**连涨连跌限制**（连续同向 3 期后大概率反向）、**防呆软限幅**（3 倍控制速率）。
- 生成的数据**会像真实台账一样偶尔触发预警**（2026-09-29 用户明确接受）；累计仍守在控制累计以内。
随机种子可固定（seed）保证同一参数可复现；也可每次随机。

约束：仅项目下已建测点；缺测概率可配 missing_rate（默认 0 = 每天都有）。
"""
from __future__ import annotations

import math
import random
from collections import Counter
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.observation_record import ObservationRecord
from backend.models.point import MeasurementPoint
from backend.repositories import observation_repo
from backend.repositories.observation_repo import INACTIVE_STATUS
from backend.schemas.observation_record import RecordCreate
from backend.services.point_service import list_points
from backend.services.warning_service import INDICATOR_CN, has_control_rule, report_control_values

# ── 冲突处理方式 ──────────────────────────────────────────────
CONFLICT_SKIP = "skip"
CONFLICT_REPLACE_GENERATED = "replace_generated"
CONFLICT_REPLACE_ALL = "replace_all"
CONFLICT_MODES = (CONFLICT_SKIP, CONFLICT_REPLACE_GENERATED, CONFLICT_REPLACE_ALL)

# 已"让位"的状态：既不占唯一索引，也不算"这天已有数据"。
# 否则用户替换过一次之后，每次再生成都会被告知"这些日期有数据"（其实是自己刚作废的）。
#
# 定义在 repository 层（`INACTIVE_STATUS`）——**全项目只此一份**。
# 这里 import 引用而不是再写一遍：两处各写一份的话，改了一处漏一处，
# 就会出现"预检说有数据、生成时又不认"这种自相矛盾（已经踩过一次）。
_REPLACED_STATUS = INACTIVE_STATUS


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# ── 变形形态（2026-09-29 三次改：去掉趋势，改成围绕初值波动） ──────────
#
# 三轮用户反馈把这块逼到现在的样子：
#   ① "生成的数据速率都一样，没参考价值"     → 从均匀随机游走换成收敛曲线
#   ② "真实一点，感觉全是正负 1、正负 2"     → 噪声幅度按真实台账标定（锚错了对象）
#   ③ **"不要出现持续单调递增或递减，避免形成明显的趋势；要在一定范围内合理波动，
#      包含上涨和下跌的交替变化，整体自然、无明显规律性"** → 去掉确定性趋势
#
# 【真实台账量出来的统计】八号基坑 21 期、168 个相邻期差（`parser.parse_workbook` 读的）：
#
#   沉降    平均|Δ| = 1.20mm   0:20%  ±1:42%  ±2:38%  ±3:1%    21 期累计跨度 9~12mm
#   位移 X  平均|Δ| = 2.09mm   0:10%  ±1:29%  ±2:27%  ±3:20%  ±4:9%  ±5:5%
#   位移 Y  平均|Δ| = 2.15mm   0:14%  ±1:27%  ±2:21%  ±3:19%  ±4:9%  ±5:6%
#
# **注意最后那列"累计跨度"**：21 期下来累计只在 9~12mm 之间来回 —— 真实台账里
# 累计量就是**围绕初值的往复游走**，并没有一条持续向上的变形趋势。
# 所以第三次改动的方向与实测是吻合的，不是让步：确定性趋势（尤其短期范围里）
# 会让整段变成单调爬升/下降，一眼假。
#
# ⚠ 单期变化量的幅度**不许再拿控制值去折算**（第②轮的根因）。曾经的写法是"控制速率 ÷ 4"，
# 而多数项目没配预警规则 → 兜底 (30,5) → 半幅 1.25mm → `round()` 后只剩 {−1,0,+1}。
# 真实幅度由**测量重复性**决定，与规范限值无关。
_NOISE_SPEC: dict[str, tuple[str, float]] = {
    # 沉降（电子水准仪）：离散近似**均匀** → 0:20% ±1:42% ±2:38% ±3:1%
    "settlement": ("uniform", 2.3),
    # 位移（全站仪 / 测斜）：散布更大、近似**正态** → 0:10% ±1:29% ±2:27% ±3:20% ±4:9% ±5:5%
    "displacement": ("normal", 2.6),
}
_NOISE_FALLBACK: tuple[str, float] = ("uniform", 2.3)

# 偶发扰动（降雨 / 邻近施工）：真实沉降里 ±3 只占 1%，所以概率低、幅度小。
_FLAKE_CHANCE = 0.04
_FLAKE_MIN_MM = 1.0
_FLAKE_MAX_MM = 3.0

# 均值回复：每期把累计往初值拉回这个比例。
#
# 为什么要它（而不是纯随机游走）：
# · 用户要"数值在**一定范围内**合理波动" —— 纯随机游走没有范围，走远了就不回来；
# · 与实测对得上：稳态标准差 = σ_ε/√(1−(1−θ)²)，θ=0.08 时约 3.6 倍 σ_ε，
#   沉降(σ_ε≈1.33) → ±11mm 上下，正是真实台账 21 期跨度 9~12mm 的量级。
_MEAN_REVERT = 0.08

# 回复力的单日上限（mm）。理由见 `_revert_mm()`：续测场景起点可能离初值很远，
# 不封顶会让序列以每天十几毫米的速度往初值俯冲。
_REVERT_MAX_MM = 2.0

# 连涨 / 连跌限制：连续同向走了这么多期之后，下一期**大概率**反向。
#
# 用户原话"不要出现持续单调递增或持续递减""包含上涨和下跌的交替变化"。
# 只改符号不改幅度，所以「|Δ| 的分布对齐真实台账」这条性质不受影响。
# 不是硬性禁止（概率 0.7），所以连走 4~5 期的情形仍然会出现 —— 那才自然。
_MAX_RUN = 3
_RUN_FLIP_CHANCE = 0.7


def _safety_ceiling_mm(limit_rate: float | None, noise_scale_mm: float) -> float:
    """整体单期变化的**防呆**软限幅（不再是"不触发预警"的步长上限）。

    真实台账里位移 ±3 占 20%、±4 占 9%、±5 占 5% —— 按 3mm/d 的控制速率，
    这些在系统里全是"预警"。用户明确要真实，所以这里只留 3 倍控制速率的宽松兜底
    （位移 → 9mm，实测极值 ±8，几乎不生效），防止配置写错生成出天文数字。

    没配速率时，这个防呆上限锚在**该系列自己的日波动量级**上（同样取 3 倍，
    沉降 2.3 → 6.9mm、位移 2.6 → 7.8mm，与配了速率的项目同一档），
    而不是替项目编一个速率限值 —— 这条纯粹防天文数字，不承担"合规"含义。
    """
    return max(3.0, (noise_scale_mm if limit_rate is None else limit_rate) * 3.0)


def _squash(x: float, cap: float) -> float:
    """把单期变化量平滑压进开区间 (−cap, cap)：`|x| ≤ 0.8·cap` 时原样通过。

    为什么要"平滑"而不是硬夹 `min(x, cap)`：被夹住的量会在边界上**堆成一排**。
    为什么不用 `cap·tanh(x/cap)`：tanh 在中段就开始压缩 —— cap=2 时原本 1.9mm 的一期
    会被压成 1.48mm，取整后掉到 ±1（当时 |Δ|≥2 只剩 13%，白改）。
    这里改成**分段**：线性区 + 指数软肩，`f'(0.8cap)=1` 连续可导，只软化真正超限的那一小截。
    """
    knee = cap * 0.8
    if abs(x) <= knee:
        return x
    rest = cap - knee                       # 还能长多少
    over = abs(x) - knee
    return math.copysign(knee + rest * (1.0 - math.exp(-over / rest)), x)


# ── 全站仪观测模式（2026-10-05 用户需求）──────────────────────────────
#
# 用户原话：「我用全站仪测点，把每次架设备的误差和设备误差加进去，可以加一个模式」。
#
# 【这个模式解决什么问题 —— 现行噪声的"性格"不对】
# `_NOISE_SPEC` 的 ±2.6mm 是**每个点、每一期各自独立**抽的噪声，生成出来的效果是
# "每个点各跳各的"。而全站仪的真实特征是：**每次架站，对中与定向的偏差由这一次
# 观测的所有点共同承担** —— 同一期的点会**朝同一个方向整体偏一点**，而不是互不相干
# 地乱跳。真实台账里能看到这个特征：同期各点的变化量之间是相关的（一起抬、一起落）。
#
# 【为什么不是"再叠一层误差" —— 会双重计数】
# ±2.6mm 是从真实台账的**相邻期差**实测标定的（位移平均 |Δ| = 2.09mm ÷ √(2/π) ≈ 2.6mm），
# 那个统计里**本来就已经包含了**当天那些架站误差。所以本模式不加大散布，而是把总量
# **重新分配**：一部分从"每点独立"挪到"同期公共"——
#
#     相邻期差的方差 = 2·σ_对中² + 2·σ_定向² + 2·σ_独立²      （期差 = 本期 − 上期）
#
# 于是开了模式之后总散布仍与真实台账对齐，不会出现"一开就散得离谱"。
#
# 【只管位移，不管沉降】
# 沉降是电子水准仪测的（每站独立、两点间高差与架站位置无关），不吃全站仪这套误差；
# 位移 X/Y 才是全站仪坐标观测的产物。硬给沉降加"架站误差"在工程上说不通。
_TOTAL_STATION_MODE = "total_station"

# 公共项在"相邻期差方差"里的**有效权重**。
#
# 纯解析看应该是 2.0（期差 = 本期 − 上期，两个独立的架站偏移，方差相加）。
# 但生成器里的**累计保护**（cum_cap，防止生成的数据突破控制值）会把架站误差
# 造成的随机游走削掉一部分，公共项的实际贡献因此打了折 —— 实测反推 ≈ 1.24，
# 取 1.3 留一点余量。不按实测值扣的话，位移平均 |Δ| 会从 2.12mm 掉到 1.87mm
# （散布比真实台账小了 12%，用户对这个量级很敏感，之前为它提过两轮需求）。
#
# ⚠ 这个值跟"累计保护的松紧"绑在一起：谁动了 cum_cap，就要重新实测标定这里。
_COMMON_SHARE = 1.3

# 默认参数：按**普通现场水平**给（不是实验室理想值）——
# 三脚架 + 光学对中器每次重新架站、后视单盘定向。用户比任何标称值都清楚自己现场的实际，
# 三个数界面上都能改，且有强制对中装置 / 固定测站的话应该往下调（0.3~0.5mm）。
_TS_DEFAULTS: dict[str, float] = {
    # 对中 + 整平误差的**极限值**（mm）。光学对中器现场一般按 ≤1mm 控制，
    # 但临时架站、地面软、没做强制对中时，1.5~2mm 是现场常态。
    "centering_mm": 2.0,
    # 后视定向误差的**中误差**（″）。正倒镜取中后 2~3″，单盘/短后视 5″ 很常见。
    "direction_sec": 5.0,
    # 仪器到基坑的**平均工作距离**（m）—— 把角度误差折算成毫米用的臂长。
    "distance_m": 30.0,
}


def _station_sigmas(centering_mm: float, direction_sec: float, distance_m: float) -> tuple[float, float]:
    """把"对中极限值 / 定向角度"折算成**单期、单轴**的标准差（mm）：(σ_对中, σ_定向)。

    · 对中：偏差落在半径 = 极限值的圆内 → 单轴 σ = R/2（圆内均匀分布）；
    · 定向：臂长 × 角度标准差。3″ 在 30m 处 ≈ 0.44mm —— 量级很小，这是对的：
      **全站仪真正伤精度的是对中，不是测角**。
    """
    sigma_c = max(0.0, centering_mm) / 2.0
    sigma_d = max(0.0, distance_m) * 1000.0 * math.radians(max(0.0, direction_sec) / 3600.0)
    return sigma_c, sigma_d


def _split_independent(sigma_total: float, sigma_c: float, sigma_d: float) -> float:
    """从实测总散布里扣掉"同期公共"那部分，得到**每点独立**噪声该有多大。

    `sigma_total` 是 `_NOISE_SPEC` 里从真实台账标定出来的那个值（单期噪声强度，
    在本文件的口径下 ≈ 相邻期差的散布）。加入架站公共项后：

        相邻期差的方差 = 2·σ_公共² + 2·σ_独立²   （期差 = 本期 − 上期，各自独立）

    所以要保住"实测的那个总散布"，独立项就得让出 `2σ_公共²`：

        σ_独立² = σ_total² − 2·(σ_对中² + σ_定向²)

    ⚠ 这里**不能**写成 `σ_total²/2 − σ_公共²`（第一版就是这么写的，错在把
    "单期噪声强度"当成了"期差总量"，结果独立项被砍掉一大半 ——
    实测位移平均 |Δ| 从 2.04mm 掉到 1.43mm，散布直接失真）。

    保底 0.3mm：参数调得很大时（比如对中 5mm），说明现场架站就是差，
    总散布本来就该比实测更大 —— 差数据就该长得差，不该反过来把独立项压成 0 去凑数。
    """
    var = sigma_total ** 2 - _COMMON_SHARE * (sigma_c ** 2 + sigma_d ** 2)
    return math.sqrt(max(var, 0.09))


def _xy_centroid(xy: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """测点水平坐标的质心（当作"基坑中心"，定向误差就绕它转）。"""
    pts = list(xy.values())
    return (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))


def build_station_offsets(
    rng: random.Random,
    xy: dict[str, tuple[float, float]],
    dates: list[date],
    *,
    centering_mm: float,
    direction_sec: float,
    distance_m: float,
) -> dict[tuple[str, str], dict[date, float]]:
    """预抽"每期架站给每个测点带来的公共偏移"（mm）。纯函数，便于测试。

    返回 {(点号, 分量): {日期: 偏移mm}} —— X 取 δx、Y 取 δy。
    做成"按点+分量查表"是为了不动现有的逐点逐分量循环结构，同时保证
    **同一期的所有点共享同一套架站参数**（这才是全站仪的特征）。

    每期各抽一次（每次架站都是新的架设）：
    · **对中偏差** (δx, δy)：全测站共用的平移，各期独立；
    · **定向偏差** θ：让所有点**绕测站微微转一下** —— 幅值 = 工作距离 × θ，
      方向取"基坑中心 → 测点"的切向（真实的臂长是"测站到点"的距离，
      这里用统一工作距离近似；几十米的基坑尺度上差不到两倍，量级是对的）。
    """
    sigma_c, sigma_d = _station_sigmas(centering_mm, direction_sec, distance_m)
    cx, cy = _xy_centroid(xy)
    arm_mm = max(0.0, distance_m) * 1000.0
    theta_sigma = math.radians(max(0.0, direction_sec) / 3600.0)

    # 每个点相对基坑中心的方向（定向误差的偏移方向）
    unit: dict[str, tuple[float, float]] = {}
    for pno, (px, py) in xy.items():
        dx, dy = px - cx, py - cy
        d = math.hypot(dx, dy)
        unit[pno] = (dx / d, dy / d) if d > 1e-9 else (0.0, 0.0)

    out: dict[tuple[str, str], dict[date, float]] = {}
    for d in dates:
        sx = rng.gauss(0.0, sigma_c) if sigma_c > 0 else 0.0
        sy = rng.gauss(0.0, sigma_c) if sigma_c > 0 else 0.0
        theta = rng.gauss(0.0, theta_sigma) if theta_sigma > 0 else 0.0
        for pno, (ux, uy) in unit.items():
            # 绕中心旋转 θ → 位移垂直于半径（切向），幅值 = 臂长 × θ
            ox = -uy * arm_mm * theta
            oy = ux * arm_mm * theta
            out.setdefault((pno, "X"), {})[d] = sx + ox
            out.setdefault((pno, "Y"), {})[d] = sy + oy
    return out


def _points_xy_mm(db: Session, points: list) -> dict[str, tuple[float, float]]:
    """各测点的水平坐标（mm）—— 取 X/Y 的 V0（显式初始值或首期有效读数）。

    缺坐标的测点直接跳过：它在生成时拿不到架站偏移，按普通独立噪声走，
    总比塞一个 (0,0) 进去（那会让它和基坑另一头的点算出荒唐的距离）强。
    """
    out: dict[str, tuple[float, float]] = {}
    for p in points:
        x = _v0_mm(db, p, "displacement", "X")
        y = _v0_mm(db, p, "displacement", "Y")
        if x is not None and y is not None:
            out[p.point_no] = (float(x), float(y))
    return out


def _noise_mm(rng: random.Random, series_type: str, scale: float | None = None) -> float:
    """单期测量噪声（现场重复性，半幅见 `_NOISE_SPEC`）。

    ⚠ 它**不随任何东西衰减**：基坑稳定之后现场读数照样会 ±1~±3mm 地跳，
    把稳定期压成一条直线才是最不像真实数据的地方。

    `scale` 由全站仪模式传入（把公共的架站误差分出去之后剩下的独立部分），
    不传就用 `_NOISE_SPEC` 里那个实测值。
    """
    kind, default_scale = _NOISE_SPEC.get(series_type, _NOISE_FALLBACK)
    s = default_scale if scale is None else scale
    if kind == "normal":
        return rng.gauss(0.0, s)
    return rng.uniform(-s, s)


def _flake_mm(rng: random.Random) -> float:
    """偶发扰动：小概率出现偏大的一期变化（降雨 / 邻近施工）。

    **方向随机**：现场扰动没有固定方向 —— 原先跟着趋势方向走，那是为了"别把趋势冲散"，
    现在没有趋势了，跟着走反而会形成一连串同向的大跳。
    """
    if rng.random() >= _FLAKE_CHANCE:
        return 0.0
    amp = rng.uniform(_FLAKE_MIN_MM, _FLAKE_MAX_MM)
    return amp if rng.random() < 0.5 else -amp


def _walk_plan(
    limit_cum: float | None,
    limit_rate: float | None,
    used_cum: float,
    noise_scale_mm: float,
    allow_warning: bool = True,
    grid_decimals: int = 2,
) -> dict:
    """为一个分量规划"围绕初值波动"的参数（纯函数，便于测试）。

    `used_cum`：这一段起点已经累计了多少（续测 / 替换场景下不为 0）。
    `limit_cum` / `limit_rate`：项目配了的控制值，**没配的那项传 None**。

    已经没有"曲线"了 —— 值由噪声驱动，围着初值来回走；这里只定三件事：
    · **回复强度** `_MEAN_REVERT`（全局常量，单独列出来是为了让测试能引用同一个数）；
    · **防呆限幅**（3 倍控制速率，实测极值几乎不触及）；
    · **累计硬上限**，随已有数据上浮（导入的真实数据本身可能已超限，
      死守 V0 会把新数据往回拽、整段序列断开）。

    没配累计 ⇒ `cum_cap = inf`，即这一维**不设限**（均值回复本身已经把累计稳住，
    量级见 `_revert_mm`），而不是拿一个凭空来的数去"保护"合规。
    """
    if not allow_warning:
        # 主界面开关关掉时走这里：**把波动范围收进预警线以内**，生成的数据不再产生预警点。
        #
        # ⚠ 必须按**精度网格**再收一次：读数如果只记到整毫米，`0.85 mm` 会被四舍五入成 `1 mm`，
        # 正好撞上「1 mm/d」这种限值（判级是 `>=`）—— 实测就是这样漏掉的。
        # 所以先把上限压到限值以下，再**向下取整到网格**：rate=3 → 2mm，rate=1 → 0mm。
        scale = 10 ** max(0, min(3, grid_decimals))
        def _fit(v: float) -> float:
            return math.floor(v * scale) / scale

        raw_step = (limit_rate * 0.9) if limit_rate else _safety_ceiling_mm(None, noise_scale_mm)
        ceiling = _fit(raw_step)
        if limit_cum is None:
            cum_cap = math.inf
        else:
            # 起点本来就超限（续测/替换真实数据）时不往回拽：拿现有累计做下界。
            cum_cap = max(_fit(limit_cum * 0.9), abs(used_cum))
        return {"revert": _MEAN_REVERT, "ceiling": ceiling, "cum_cap": cum_cap}

    return {
        "revert": _MEAN_REVERT,
        "ceiling": _safety_ceiling_mm(limit_rate, noise_scale_mm),
        "cum_cap": math.inf if limit_cum is None
        else max(limit_cum * 0.95, abs(used_cum) + limit_cum * 0.3),
    }


def _revert_mm(cumulative: float, plan: dict) -> float:
    """均值回复力：累计离初值越远，往回拉得越多（比例式，不是硬拽）。

    这就是"数值在一定范围内合理波动"的实现 —— 没有它，纯随机游走走远了就不回来，
    用户会看到一条缓慢但持续的单调爬升/下降（正是这次要消除的东西）。
    θ=0.08 时稳态标准差约 3.6σ_ε，与真实台账 21 期累计跨度 9~12mm 同量级。

    ⚠ **回复力必须封顶**（又是"任何混进日增量的力都要有量纲上限"那条教训）：
    续测场景里起点可能离初值很远（实测 300mm），θ=0.08 一乘就是 24mm/天，
    再被防呆限幅一削，序列会以每天十几毫米的速度"往初值俯冲" —— 而沉降的日间噪声
    才 ±2.3mm。封顶 2mm 之后，正常量级（累计 10~20mm）完全不受影响，
    只有"起点本来就跑偏很多"的极端场景才会被这一条兜住。
    """
    return max(-_REVERT_MAX_MM, min(_REVERT_MAX_MM, -cumulative * plan["revert"]))


def _run_should_flip(run: int, rng: random.Random) -> bool:
    """连续同向走了 `run` 期之后，这一期是否**强制反向**。

    用户要求"不要出现持续单调递增或持续递减""包含上涨和下跌的交替变化"。
    只在 `run ≥ _MAX_RUN` 之后、以 `_RUN_FLIP_CHANCE` 的概率反向 —— **不是硬性禁止**，
    所以连走 4~5 期的情况照样会出现（那才自然）。只改符号、不改幅度，
    因此「|Δ| 分布对齐真实台账」这条性质不受影响。
    """
    return run >= _MAX_RUN and rng.random() < _RUN_FLIP_CHANCE


def _bump_run(run: int, run_sign: int, delta: float) -> tuple[int, int]:
    """按本期的实际变化量推进"连涨连跌"计数。0 变化视为打断（它确实打断了单调性）。

    `delta` 是**实际落库的增量**（可能带小数：记到 0.1mm 的项目）。
    这里只判方向和是否为 0，所以不做取整 —— 取整会把 0.4mm 的变化当成"没动"。
    """
    if delta == 0:
        return 0, 0
    sign = 1 if delta > 0 else -1
    return (run + 1, sign) if sign == run_sign else (1, sign)


def _counts_as_occupied(rec: ObservationRecord) -> bool:
    """这条记录算不算"这天已经有数据"。

    `void`（已作废）与 `superseded`（已被更正）**不算** —— 它们不占唯一索引，
    对新生成的数据没有妨碍，也不该出现在冲突提示里。
    否则用户替换过一次之后，每次再生成都会被告知"这天有数据"（其实是自己刚作废的）。

    ⚠ **预检和生成两条路径必须共用这一个判据。** 之前这两处各写了一遍过滤，
    结果改了一处、另一处照旧 —— 界面上就会出现"提示说有数据、实际生成时又不认"的矛盾。
    """
    return rec.status not in _REPLACED_STATUS


def _v0_mm(db: Session, point, series_type: str, component: str) -> float | None:
    """取测点该分量的 V0（显式初始值优先，否则首期有效读数）。"""
    if point.initial_value_mm is not None:
        return point.initial_value_mm
    records = _occupying(db, point.id, series_type, component)
    ordered = sorted(records.values(), key=lambda r: r.obs_date)
    first = next((r for r in ordered if r.value_mm is not None), None)
    return first.value_mm if first else None


def _decimals(value: float) -> int:
    """这个读数实际带几位小数（先按 0.001mm 收一下，免得把二进制浮点尾巴也数进去）。"""
    s = f"{round(float(value), 3):.3f}".rstrip("0").rstrip(".")
    return len(s.split(".")[1]) if "." in s else 0


def _grid_decimals(db: Session, project_id: int) -> dict[tuple[str, str], Counter]:
    """这个项目**自己的记录精度分布**：每种「系列 × 分量」记到几位小数、各占多少条。

    用户 2026-10-16：
        「第一期变化值会出现像小数点，后面所有期都是整数在变化了，你这很容易被看出来啊」
        「水平位移这也是一样的 不要只改沉降观察」

    实测（生产库）：十一号 / 十二号的台账记到 0.1mm（沉降 2651.3、位移 X 3413111727.2），
    八号那几个记到 1mm（4907）。生成器原先一律 `round(cand)` 砍成**整数毫米** ——
    真实段带小数、生成段全整数，同一列数字摆在一起，一眼就看出哪一段是造的。

    ⚠ 这里**不猜一个"典型精度"**，而是把**整个分布**带回去按比例抽（见 `_pick_nd`）：

    · **取众数**：真实台账本身就是混的（项目 12 的位移 X：整数 13 条、0.1mm 6 条、
      0.01mm 7 条），众数会落回 0 —— "生成段全整数"的老毛病原样回来
      （第一次只按众数修，用户当场发现"水平位移也一样"）；
    · **取最大值**：某一条更正记录带个 .725 就能把整段生成值拖到 0.001mm 网格上，
      反过来跟真实段不像；
    · **按分布抽**：生成段的小数位构成 ≈ 真实段，两边摆在一起看不出接缝。

    只看**非 generated** 的数据（导入台账 / 手工 / correct 更正）：
    否则生成过的项目会被自己刚造出来的整数"教会"，越生成越假。
    项目里一条真实数据都没有时返回空 → 沿用原来的 1mm（全新项目没有参照物）。
    """
    stmt = (
        select(
            ObservationRecord.series_type,
            ObservationRecord.component,
            ObservationRecord.value_mm,
        )
        .join(MeasurementPoint, MeasurementPoint.id == ObservationRecord.point_id)
        .where(MeasurementPoint.project_id == project_id)
        .where(ObservationRecord.value_mm.is_not(None))
        .where(ObservationRecord.source != "generated")
        .where(ObservationRecord.status.not_in(list(_REPLACED_STATUS)))
    )
    votes: dict[tuple[str, str], Counter] = {}
    for st, comp, v in db.execute(stmt):
        votes.setdefault((st, comp), Counter())[_decimals(v)] += 1
    return votes


def _pick_nd(rng: random.Random, dist: Counter | None) -> int:
    """按真实数据的分布，抽一个"这一期记到几位小数"。

    逐期抽样（而不是每个分量定死一个位数）才能让生成段的**小数位构成**跟真实段一致 ——
    真实台账里整数和带小数的读数本来就是混着出现的。
    """
    if not dist:
        return 0
    total = sum(dist.values())
    if total <= 0:
        return 0
    pick = rng.random() * total
    acc = 0
    for nd in sorted(dist):
        acc += dist[nd]
        if pick < acc:
            return nd
    return max(dist)


def _limits(db: Session, project_id: int, series_type: str) -> tuple[float | None, float | None]:
    """该项目 `control` 级的 (控制累计 mm, 控制速率 mm/d)。**没填的那项返回 None。**

    口径与 `warnings.engine.check_level` 一致：填了哪项就按哪项约束，缺的项不参与。

    ⚠ 原先这里在规则缺失时 `return 30.0, 5.0`，出过两回事：
    · 09-29「生成的数据太假」——幅度曾按控制值折算，没配规则的项目走兜底 → 半幅 1.25mm
      → `round()` 后只剩 {−1,0,+1}。幅度后来改成由 `_NOISE_SPEC`（真实台账标定）决定；
    · 只填了累计 20 的项目，旧条件 `if a and b` 会把**已填的 20 一起丢弃**换成 30。
    所以：0 与 None 都算"未填"（`ge=0` 的限值本身没有意义），但绝不再替用户编一个数。

    取数走报表同一个出处 `report_control_values`（= `rule_limits` 的控制值那半边）——
    生成时用的、报表上印的、判定时比的，必须是同一条规则读出来的同两个数。
    """
    cum, rate = report_control_values(db, project_id, series_type)
    return cum or None, rate or None


def _require_limits(db: Session, project_id: int, series_types: set[str]) -> dict[str, tuple[float | None, float | None]]:
    """生成前校验：每个要生成的系列至少有一个控制值，否则**拒绝生成**（用户 2026-10-14 定）。

    为什么不兜底着继续生成：生成器的累计保护与防呆限幅都由这两个数缩放。
    按项目从没配过的限值生成，出来的是一批"看起来合规"的假台账 —— 而它会被拿去
    做曲线图、导日报、进汇报材料，事后没人知道它按的是哪个口径。

    "缺不缺"判据与导出同一条（`warning_service.has_control_rule`）：
    同一批数据，生成数据被拦、导日报却放行 = 换个入口换个口径。
    """
    limits = {st: _limits(db, project_id, st) for st in sorted(series_types)}
    missing = [INDICATOR_CN[st] for st in sorted(series_types) if not has_control_rule(db, project_id, st)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{'、'.join(missing)}没有配置「控制值」，无法生成数据 —— "
                "生成的数据要按控制值划定波动范围，没有控制值就只能凭空编一个限值，"
                "那样出来的台账看着合规、其实和这个项目的口径无关。"
                "请先到「预警规则」为该项目填写累计变化量或变化速率（至少一项），"
                "或从标准预设（GB 50497-2019 一/二/三级）套用一套规则。"
            ),
        )
    return limits


def _occupying(
    db: Session, point_id: int, series_type: str, component: str
) -> dict[date, ObservationRecord]:
    """该测点该分量上"真正占位"的记录：{日期: 记录}。

    已经作废（void）或被更正（superseded）的记录不算 —— 它们不占唯一索引，
    对新生成的数据没有妨碍，也不该出现在"这天已有数据"的提示里。
    """
    out: dict[date, ObservationRecord] = {}
    for r in observation_repo.list_by_point(db, point_id, series_type=series_type, active_only=True):
        if r.component != component or not _counts_as_occupied(r):
            continue
        out[r.obs_date] = r
    return out


def preview_generation(db: Session, project_id: int, start_date: date, end_date: date) -> dict:
    """预检：所选范围内哪些日期已有数据、是真实数据还是之前自动生成的。**只读，不落库**。

    给前端用来"生成前问一句"：这些日期要怎么处理。
    """
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="结束日期不能早于开始日期")

    per_day: dict[date, dict] = {}
    for p in list_points(db, project_id, include_inactive=False):
        for r in observation_repo.list_by_point(db, p.id, from_date=start_date, to_date=end_date):
            if not _counts_as_occupied(r):
                continue
            slot = per_day.setdefault(r.obs_date, {"records": 0, "sources": {}})
            slot["records"] += 1
            slot["sources"][r.source] = slot["sources"].get(r.source, 0) + 1

    days: list[dict] = []
    for d in _date_range(start_date, end_date):
        slot = per_day.get(d)
        if not slot:
            continue
        sources: dict = slot["sources"]
        has_real = any(s != "generated" for s in sources)
        has_gen = "generated" in sources
        kind = "mixed" if (has_real and has_gen) else ("real" if has_real else "generated")
        days.append(
            {"date": d.isoformat(), "records": slot["records"], "sources": sources, "kind": kind}
        )

    total = (end_date - start_date).days + 1
    return {
        "project_id": project_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_days": total,
        "empty_days": total - len(days),
        "occupied_days": days,
        # 只含自动生成数据的日期：可以整批删掉重来
        "generated_only_days": [x["date"] for x in days if x["kind"] == "generated"],
        # 含真实数据的日期：不能删，只能作废
        "real_data_days": [x["date"] for x in days if x["kind"] in ("real", "mixed")],
    }


def _resolve_conflict(db: Session, rec: ObservationRecord, on_conflict: str) -> str | None:
    """按用户选择处理一条冲突记录。

    返回 `'deleted'`（演示数据，已删除）/ `'voided'`（真实数据，已作废保留）/ `None`（保持不动）。
    """
    if on_conflict == CONFLICT_SKIP:
        return None

    if on_conflict == CONFLICT_REPLACE_GENERATED:
        if rec.source != "generated":
            return None  # 真实数据一律不碰
        db.delete(rec)
        db.flush()
        return "deleted"

    # replace_all
    if rec.source == "generated":
        db.delete(rec)
        db.flush()
        return "deleted"
    # 真实数据：不删，只作废（原始数据表只增不删）
    rec.status = "void"
    rec.remark = "重新生成演示数据时被替换（原值保留可追溯）"
    db.flush()
    return "voided"


def generate_series(
    db: Session,
    project_id: int,
    start_date: date,
    end_date: date,
    seed: int | None = None,
    missing_rate: float = 0.0,
    on_conflict: str = CONFLICT_SKIP,
    instrument_mode: str | None = None,
    station_centering_mm: float | None = None,
    station_direction_sec: float | None = None,
    station_distance_m: float | None = None,
    allow_warning: bool = True,
) -> dict:
    """生成 [start_date, end_date] 每天的观测数据并落库（source=generated）。

    - missing_rate：每日缺测概率（0~0.5），用于模拟偶尔没测。
    - on_conflict：遇到"这天已有数据"时怎么办，见模块顶部说明（默认跳过）。
    - instrument_mode=`total_station`：**全站仪观测模式** —— 除每点独立的读数噪声外，
      再叠加"每次架站"的公共误差（对中平移 + 定向微旋转，同期各点共享）。
      参数含义与误差预算见 `_TS_DEFAULTS` / `build_station_offsets`。
      只影响位移 X/Y；沉降（水准仪）不变。
    """
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="结束日期不能早于开始日期")
    if not (0 <= missing_rate <= 0.5):
        raise HTTPException(status_code=422, detail="missing_rate 需在 0~0.5 之间")
    if on_conflict not in CONFLICT_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"on_conflict 需为 {'/'.join(CONFLICT_MODES)} 之一",
        )
    if instrument_mode not in (None, "", _TOTAL_STATION_MODE):
        raise HTTPException(status_code=422, detail=f"instrument_mode 不认识：{instrument_mode}")

    rng = random.Random(seed)
    points = [p for p in list_points(db, project_id, include_inactive=False) if p.point_type]
    if not points:
        raise HTTPException(status_code=422, detail="该项目没有测点，请先建测点")

    # 控制值校验放在**建点循环之前**：只查项目实际要生成的那些系列
    # （只有沉降点的基坑，不该因为没配位移规则被拦住）。顺带避免下面逐点逐系列重复查规则。
    needed = {st for p in points for st in (
        ["settlement", "displacement"] if p.point_type == "both" else [p.point_type]
    )}
    limits = _require_limits(db, project_id, needed)

    # 项目自己的记录精度（0=整数毫米，1=0.1mm…）。生成的值要落到**同一张网格**上，
    # 否则第一期（真实）与后面各期（生成）的精度对不上 —— 见 `_grid_decimals` 的实测记录。
    grid = _grid_decimals(db, project_id)
    # 生成时按比例抽小数位（见 _pick_nd），所以限幅必须按**最粗**的那种取整：
    # 项目里只要出现过整数读数，`rate*0.9` 就可能被 round 上去撞限值（实测 0.9 → 1.0）。
    # 没有历史可参考时也按整数（最保守）。
    grid_nd = 0 if (not grid or any(0 in dist for dist in grid.values())) else 2

    # ── 全站仪观测模式：预抽"每期架站"参数 ──
    # 必须在测点循环**之前**算好：同一期的所有测点要共享同一套架站偏差 ——
    # 这是这个模式的全部意义（"每次架设备"的误差由这一期的所有点共同承担）。
    station_offsets: dict[tuple[str, str], dict[date, float]] = {}
    noise_scale: dict[str, float] = {}
    station_info: dict | None = None
    if instrument_mode == _TOTAL_STATION_MODE:
        centering = _TS_DEFAULTS["centering_mm"] if station_centering_mm is None else station_centering_mm
        direction = _TS_DEFAULTS["direction_sec"] if station_direction_sec is None else station_direction_sec
        distance = _TS_DEFAULTS["distance_m"] if station_distance_m is None else station_distance_m
        xy = _points_xy_mm(db, points)
        sigma_c, sigma_d = _station_sigmas(centering, direction, distance)
        base_indep = _split_independent(_NOISE_SPEC["displacement"][1], sigma_c, sigma_d)
        noise_scale["displacement"] = base_indep
        if xy:
            station_offsets = build_station_offsets(
                rng, xy, _date_range(start_date, end_date),
                centering_mm=centering, direction_sec=direction, distance_m=distance,
            )
        station_info = {
            "centering_mm": centering,
            "direction_sec": direction,
            "distance_m": distance,
            # 给界面说清"总散布没有被放大"：公共项从独立项里让出来的
            "sigma_common_mm": round(math.hypot(sigma_c, sigma_d), 3),
            "noise_scale_before_mm": _NOISE_SPEC["displacement"][1],
            "noise_scale_after_mm": round(base_indep, 3),
            "points_with_xy": len(xy),
        }

    total_days = (end_date - start_date).days + 1
    generated = 0
    replaced = 0        # 被替换（删除或作废）的记录数
    voided_real = 0     # 其中属于真实数据、被作废的
    kept_dates: set[date] = set()   # 因用户选择"跳过"而没动的日期
    summary: list[dict] = []
    # 「旧值 → 新值」对照样例。为什么需要它：替换前后读数只差 1~3mm（观测本来就这量级），
    # 而记录数/日期/测点数完全不变 —— 光看表格用户无法确认到底换没换。给几条实物对照最省事。
    changed_samples: list[dict] = []

    for p in points:
        series_types = ["settlement", "displacement"] if p.point_type == "both" else [p.point_type]
        for st in series_types:
            comps = ["value"] if st == "settlement" else ["X", "Y"]
            limit_cum, limit_rate = limits[st]
            # 没配速率时防呆限幅要用的量级：全站仪模式下独立项被让出来过，取实际值
            scale_mm = noise_scale[st] if st in noise_scale else _NOISE_SPEC.get(st, _NOISE_FALLBACK)[1]
            for comp in comps:
                v0 = _v0_mm(db, p, st, comp)
                if v0 is None:
                    # 无 V0 可生成：无任何历史记录 → 以首日生成值为基准不可靠，跳过
                    continue

                existing = _occupying(db, p.id, st, comp)

                # ── 滚动起点：**范围开始日之前**最近一期的值 ──
                # ⚠ 不能取"全历史最后一期"。只重做中间一段时（库里 09-01~09-29 都有数据，
                #   用户只重做 09-19~09-29），起点若取 09-29 的读数，第一天就会接在最后一天
                #   后面 —— 相当于让时间倒流，整段数据平移错位，且与 09-18 断开。
                before = [
                    r for r in existing.values()
                    if r.value_mm is not None and r.obs_date < start_date
                ]
                if before:
                    cur = max(before, key=lambda r: r.obs_date).value_mm
                else:
                    # 范围前没有任何历史 → 从 V0 开始走（首次生成就是这种情况）
                    cur = v0
                cumulative = cur - v0

                # 本分量的波动参数（每点每分量各一套 → 点与点之间也有差异）
                plan = _walk_plan(limit_cum, limit_rate, cumulative, scale_mm, allow_warning, grid_nd)
                # 连涨 / 连跌计数（用户要求：不要出现持续单调递增或递减）
                run, run_sign = 0, 0
                # **变形累计**：只统计"真实变形"那部分，用来驱动均值回复力。
                # ⚠ 不能让回复力去看"观测累计"（含架站误差）—— 回复力是我们给"变形"建的模型，
                #    仪器架设偏差不是变形。混在一起会让回复力把架站误差当变形去抵消，
                #    实测后果是开了全站仪模式后散布反而掉了 12%（2.04 → 1.83mm）。
                #    标准模式下没有架站偏移，两者恒等，行为与改动前完全一致。
                deform_cum = cumulative

                for d in _date_range(start_date, end_date):
                    rec = existing.get(d)
                    # 旧值/来源要在删除或作废**之前**取 —— 之后对象可能已失效
                    old_value = rec.value_mm if rec is not None else None
                    old_source = rec.source if rec is not None else None
                    action: str | None = None
                    if rec is not None:
                        action = _resolve_conflict(db, rec, on_conflict)
                        if action is None:
                            # 保留这条已有数据：它要成为后续生成的起点。
                            # 否则"跳过 7 天、从第 8 天继续"会让序列断开（数值跳回旧起点）。
                            if rec.value_mm is not None:
                                cur = rec.value_mm
                                cumulative = cur - v0
                                deform_cum = cumulative
                                # 起点换了，累计保护线也要按新起点重算
                                plan = _walk_plan(limit_cum, limit_rate, cumulative, scale_mm, allow_warning, grid_nd)
                                run, run_sign = 0, 0   # 中间夹了别的数据，连涨连跌从头算
                            kept_dates.add(d)
                            continue
                        replaced += 1
                        if action == "voided":
                            voided_real += 1

                    # 缺测模拟
                    if missing_rate > 0 and rng.random() < missing_rate:
                        new_value_mm = None
                        run, run_sign = 0, 0
                    else:
                        # 单日变化 = 测量噪声 + 均值回复力 + 偶发扰动（**没有确定性的趋势项**）
                        # 回复力看的是**变形累计**（不含架站误差），理由见 deform_cum 的注释
                        base = (
                            _noise_mm(rng, st, noise_scale.get(st))
                            + _revert_mm(deform_cum, plan)
                            + _flake_mm(rng)
                        )
                        # 连涨连跌限制：连续同向走了 _MAX_RUN 期后，这一期大概率反向
                        if _run_should_flip(run, rng):
                            base = -base
                        # 全站仪架站误差：**公共项单独叠加，不参与上面的取反** ——
                        # 它是仪器架设偏差，与"变形是否单调"无关，翻转它没有道理
                        # （翻转了就等于让架站误差去配合连涨连跌，反而失真）。
                        offset = station_offsets.get((p.point_no, comp), {}).get(d, 0.0)
                        # 防呆软限幅（3 倍控制速率，实测极值几乎不触及）
                        step = _squash(base + offset, plan["ceiling"])
                        cand = cur + step

                        # 硬保底：任何情况下都不越控制累计（均值回复可能来不及）
                        cum_after = cand - v0
                        if abs(cum_after) > plan["cum_cap"]:
                            cand = v0 + math.copysign(plan["cum_cap"], cum_after)

                        # 落库精度按**项目自己台账的分布**抽（见 `_grid_decimals` / `_pick_nd`）：
                        # 记到 1mm 的项目保持整数，记到 0.1mm 的项目就带小数。
                        # 噪声本来就是连续的，原先硬砍成整数毫米，反而把"生成的那一段"标了出来。
                        nd = _pick_nd(rng, grid.get((st, comp)))
                        new_value_mm = round(cand) if nd == 0 else round(cand, nd)
                        cumulative = new_value_mm - v0
                        # 变形累计只推进"变形那一部分"（用实际落库的增量减去本期的架站偏移）
                        deform_cum += (new_value_mm - cur) - offset
                        # 连涨连跌看的是**实际落库的增量**，0.1mm 精度下别再 int() 截断
                        # （截断会把 0.4mm 的微小变化当成"没动"，白白打断连涨计数）
                        run, run_sign = _bump_run(run, run_sign, new_value_mm - cur)

                    observation_repo.create(
                        db, p.id,
                        RecordCreate(series_type=st, component=comp, obs_date=d, value_mm=new_value_mm),
                        source="generated",
                    )
                    generated += 1
                    if new_value_mm is not None:
                        cur = new_value_mm

                    # 只有"被替换掉的"才值得对照 —— 新生成的空白日期没有旧值可比
                    if action is not None and len(changed_samples) < 12:
                        changed_samples.append({
                            "point_no": p.point_no,
                            "series_type": st,
                            "component": comp,
                            "obs_date": d.isoformat(),
                            "old_value_mm": old_value,
                            "old_source": old_source,
                            "new_value_mm": new_value_mm,
                            "action": action,
                        })

        summary.append({
            "point_no": p.point_no,
            "settlement": p.point_type in ("settlement", "both"),
            "displacement": p.point_type in ("displacement", "both"),
        })

    db.commit()
    return {
        "project_id": project_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": total_days,
        "generated": generated,
        "replaced": replaced,
        "voided_real": voided_real,
        "kept_dates": sorted(d.isoformat() for d in kept_dates),
        "points": len(points),
        "on_conflict": on_conflict,
        # 观测模式：standard（每点独立噪声）/ total_station（含每次架站的公共误差）
        "instrument_mode": instrument_mode or "standard",
        "station": station_info,
        "summary": summary,
        # 「旧值 → 新值」对照（最多 12 条）。给界面用来说清"到底换没换"——
        # 替换前后读数只差几 mm，只报一个条数用户没法确认。
        "changed_samples": changed_samples,
        "note": (
            "生成数据 source=generated，可在数据工具中整批清理。"
            if on_conflict != CONFLICT_REPLACE_ALL
            else "生成数据 source=generated；被替换的真实数据已作废（原值仍保留在库中，可追溯）。"
        ),
    }


def cleanup_generated(db: Session, project_id: int) -> dict:
    """删除某项目下全部 source=generated 的记录（仅供测试/演示清理）。"""
    from sqlalchemy import delete

    from backend.repositories import point_repo as _pr

    point_ids = [p.id for p in _pr.list_by_project(db, project_id, include_inactive=True)]
    if not point_ids:
        return {"deleted": 0}
    result = db.execute(
        delete(ObservationRecord).where(
            ObservationRecord.point_id.in_(point_ids),
            ObservationRecord.source == "generated",
        )
    )
    db.commit()
    return {"deleted": result.rowcount}
