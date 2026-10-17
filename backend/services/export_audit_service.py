# -*- coding: utf-8 -*-
"""导出文件检查：把用户导出去的日报，和系统里的数逐项比一遍。

用户 2026-10-12：
    「我想让系统检查我导出的文件 …检查主要是 变化速率 是否和系统中的同步、
      变化速率是否超过预警规则」

四件事（按用户勾选）：
1. **变化速率** —— 报表 H 列 vs 系统算出来的；
2. **超限** —— 按**当前**规则把报表上印的数和系统里的数**各判一遍**（累计、速率双控，
   与 `warnings.engine.evaluate` 同口径取高），两边判级不一致也单独列；
3. **本次变化量 / 本次累计变化量** —— 同一行顺带比；
4. **报表「控制值」两列** vs 项目当前规则。

⚠ 第 2 条里的**累计**是 2026-10-14 补的：用户当时的原话只点了"变化速率"，
所以第一版只判速率。结果是"把累计控制值从 30 改到 12"之后，旧报表里早已超新限值的行
一条都不报 —— 改的规则正是检查该盯的东西，检查却对它无感。

三条要紧的判据（写错了会得出完全相反的结论）：

- **比的是"按报表精度"的数**：导出时所有数值都 `round(v, 3)` 写进去，
  所以系统这一侧也必须 `round(系统值, 3)` 再比。直接拿未舍入的系统值比，会**整表误报**。
- **超限口径复用 `warnings.engine.check_level`**（`|值| ≥ 限值`，报警优先）——
  一处用 `>` 一处用 `>=` 就会得出相反结论，所以判定只有那一处。
- **系统没配规则时不比控制值**：导出端 `report_control_values` 返回 None 表示
  "保留母版原值"，那是**有意为之**，不该报成"不一致"。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path

from sqlalchemy.orm import Session

from backend.reports.export_reader import ReadReport, ReportRow, read_report
from backend.services import export_template_service
from backend.services.export_template_service import is_data_sheet_name
from backend.services.project_matcher import match_project
from backend.services.warning_service import report_control_values, rule_limits
from backend.warnings.engine import check_level

#: 比对时给系统值兜的精度上限。导出端已在 `paper_values` 按纸上口径收过
#: （毫米 1 位、速率 3 位），这里再 round 到 3 位只是防浮点尾巴，不改变结论。
REPORT_DECIMALS = 3

#: 单个文件最多收多少条明细，整批最多多少条（结果页不至于把浏览器压死）
MAX_ITEMS = 800

_COMPONENT_LABEL = {"value": "沉降", "X": "位移X", "Y": "位移Y"}

#: 问题类型 → 中文（结果页按这个分组）
PROBLEM_LABELS = {
    "rate": "变化速率和系统对不上",
    "delta": "本次变化量和系统对不上",
    "cum": "本次累计变化量和系统对不上",
    "prev_cum": "上次累计变化量和系统对不上",
    "control_cum": "「控制值·累计变化量」和系统规则对不上",
    "control_rate": "「控制值·变化速率」和系统规则对不上",
    "rate_over": "变化速率已超限值（报表没体现）",
    "cum_over": "累计变化量已超限值（报表没体现）",
    "level_diff": "两边判级不一致",
    "no_system_data": "系统里找不到这一期的数据",
    "unknown_point": "报表上的点号在本项目里不存在",
}


def _r3(v: float | None) -> float | None:
    """按报表精度取整 —— 导出端就是 `round(v, 3)` 写进去的。"""
    return None if v is None else round(float(v), REPORT_DECIMALS)


def _eq(a: float | None, b: float | None) -> bool:
    """两个值"是否一致"。None 与 None 一致；一边有一边没有 → 不一致。"""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return a == b


@dataclass
class AuditRow:
    """一行比对结果（只在**有问题**时才进结果）。"""

    file: str
    path: str
    project_id: int
    project_code: str
    project_name: str
    obs_date: str
    sheet: str
    excel_row: int
    point_no: str
    component: str
    problems: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    system: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)
    levels: dict = field(default_factory=dict)


def _match_project(projects: list, rep: ReadReport):
    """认项目 —— 走 `project_matcher`（宽松匹配 + 三道防认错的否决）。

    用户 2026-10-13：
        「检查时 基坑名字和系统中项目名字不一定完全对得上 加一个智能识别 不要太死板」

    报表里那句名字和系统项目名对不上是常态（带地名前缀、中文数字、括号限定、
    老母版干脆没写名称只有文件名里的编号）。匹配规则全部收在 `project_matcher` 里，
    这里只负责把候选文本按**可靠程度**排好递进去：**表内名称 > 文件名**。
    理由：文件名里那对括号常常是母版名，而母版可能是**别的基坑**的（母版串名）。
    """
    texts = [(c, "表内名称") for c in rep.name_candidates if c]
    if rep.filename:
        texts.append((rep.filename, "文件名"))
    return match_project(projects, texts)


def _system_rows(db: Session, project_id: int, obs_date: str) -> dict[tuple[str, str], dict]:
    """系统里这一期的取数 —— **与导出时同一套**（`series_rows_for_date`）。"""
    from backend.reports.template_excel import series_rows_for_date

    want = _date.fromisoformat(obs_date)
    out: dict[tuple[str, str], dict] = {}
    for r in series_rows_for_date(db, project_id, want):
        out[(r["point_no"], r["component"])] = r
    return out


def _limits_of(db: Session, project_id: int, kind: str) -> dict:
    """当前规则里**报表上那两列**的阈值（结果页要把它印出来，用户才知道"按哪套数判的"）。

    ⚠ 这里**有意不取「本次变化量 ΔV」那一档**：报表上没有 ΔV 的控制值列，
    系统侧一旦按 ΔV 判级，就会报出"报表正常、系统报警"的假不一致
    —— 判级必须只按报表上真有的那几维取高（同 `_check_row` 里的 `has_col` 口径）。
    """
    lim = rule_limits(db, project_id, kind)
    return {
        "control_cum": lim.control_cumulative_mm,
        "control_rate": lim.control_rate_mm_per_day,
        "alarm_cum": lim.alarm_cumulative_mm,
        "alarm_rate": lim.alarm_rate_mm_per_day,
    }


def _level_name(n: int) -> str:
    return ("normal", "control", "alarm")[n]


def _check_row(row: ReportRow, sysrow: dict, limits: dict, ctrl: tuple, columns: dict) -> tuple[list[dict], dict, dict, dict]:
    """比一行。返回 (问题列表, 报表值, 系统值, 判级)。

    ⚠ **报表里没有那一列就不比**：旧式模板（`build_from_template` 那条老路）导出的表
    可能只有「本次累计」而没有「本次变化 / 变化速率」列。拿"报表没有"去对"系统有"，
    会整表报"对不上"—— 那不是报表错了，是这份表本来就没这一栏（实测踩过）。
    """
    problems: list[dict] = []
    rep_vals = {
        "prev_cum": row.prev_cum_mm,
        "cum": row.cum_mm,
        "delta": row.delta_mm,
        "rate": row.rate_mm_per_day,
    }
    sys_vals = {
        "prev_cum": _r3(sysrow.get("prev_cum_mm")),
        "cum": _r3(sysrow.get("cum_mm")),
        "delta": _r3(sysrow.get("delta_mm")),
        "rate": _r3(sysrow.get("rate")),
    }
    has_col = {
        "prev_cum": bool(columns.get("prev_cum_mm")),
        "cum": bool(columns.get("cum_mm")),
        "delta": bool(columns.get("delta_mm")),
        "rate": bool(columns.get("rate")),
    }
    for key in ("rate", "delta", "cum", "prev_cum"):
        if not has_col[key]:
            continue  # 这份报表没有这一列 → 比不了，也不算问题
        a = _r3(rep_vals[key])
        b = sys_vals[key]
        if not _eq(a, b):
            problems.append({"kind": key, "report": a, "system": b})

    # 「控制值」两列：只有系统配了规则、且报表里有这一列时才比
    # （没配规则 → 导出端有意保留母版原值，不该报"不一致"）
    ctrl_cum, ctrl_rate = ctrl
    for key, want in (("control_cum", ctrl_cum), ("control_rate", ctrl_rate)):
        if want is None or not columns.get(key):
            continue
        a = _r3(row.control_cum_mm if key == "control_cum" else row.control_rate_mm_per_day)
        b = _r3(want)
        if not _eq(a, b):
            problems.append({"kind": key, "report": a, "system": b})

    # 超限判定：**报表上的数**和**系统里的数**各按当前规则判一遍。
    #
    # 双控 = 累计、速率各判一次、取高（与 `warnings.engine.evaluate` 同一口径）。
    # 原先只判速率：把**累计**控制值调小之后，旧报表上早已超新的累计限值的行一个都不报 ——
    # 用户改的正是这条规则，检查却对它无感，"文档和系统一一对应"就断在这里。
    # 报表里没有对应列时那一侧不判（拿"报表没有这一栏"去报"超限"是假警报）。
    lv_rep_cum = check_level(
        _r3(row.cum_mm) if has_col["cum"] else None, limits["control_cum"], limits["alarm_cum"]
    )
    lv_sys_cum = check_level(_r3(sysrow.get("cum_mm")), limits["control_cum"], limits["alarm_cum"])
    lv_rep_rate = check_level(
        _r3(row.rate_mm_per_day) if has_col["rate"] else None, limits["control_rate"], limits["alarm_rate"]
    )
    lv_sys_rate = check_level(_r3(sysrow.get("rate")), limits["control_rate"], limits["alarm_rate"])

    lv_rep = max(lv_rep_cum, lv_rep_rate)
    # 系统侧只按**报表上有的那几维**取高。报表没这一栏时无从比对 ——
    # 拿"报表空白"去对"系统已超限"会整表报"两边判级不一致"，那是假警报（见 _check_row 开头）。
    lv_sys = max(lv_sys_cum if has_col["cum"] else 0, lv_sys_rate if has_col["rate"] else 0)
    levels = {"report": _level_name(lv_rep), "system": _level_name(lv_sys)}
    if has_col["rate"] and lv_rep_rate:
        problems.append({"kind": "rate_over", "report": _r3(row.rate_mm_per_day), "level": _level_name(lv_rep_rate)})
    if has_col["cum"] and lv_rep_cum:
        problems.append({"kind": "cum_over", "report": _r3(row.cum_mm), "level": _level_name(lv_rep_cum)})
    if lv_rep != lv_sys:
        problems.append(
            {
                "kind": "level_diff",
                "report": _level_name(lv_rep),
                "system": _level_name(lv_sys),
            }
        )
    return problems, rep_vals, sys_vals, levels


@dataclass
class AuditResult:
    folder: str
    scanned: int = 0
    checked: int = 0
    skipped: list[dict] = field(default_factory=list)
    items: list[AuditRow] = field(default_factory=list)
    rows_total: int = 0
    truncated: bool = False
    project_stats: list[dict] = field(default_factory=list)
    #: 属于**已归档**项目、按用户要求跳过的文件（按项目聚合）—— 不查，但要报出来
    archived: list[dict] = field(default_factory=list)
    #: 认出来但**只是相近**（不是完全一致）的：{文件, 认成谁, 凭什么, 多少分}
    fuzzy: list[dict] = field(default_factory=list)
    counters: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "folder": self.folder,
            "scanned": self.scanned,
            "checked": self.checked,
            "skipped": self.skipped,
            "rows_total": self.rows_total,
            "problem_rows": len(self.items),
            "truncated": self.truncated,
            "counters": self.counters,
            "problem_labels": PROBLEM_LABELS,
            "projects": self.project_stats,
            "archived": self.archived,
            "archived_files": sum(a["count"] for a in self.archived),
            # 名称不是完全一致、按相近认出来的（用户要看一眼确认没认歪）
            "fuzzy": self.fuzzy[:200],
            "fuzzy_files": len(self.fuzzy),
            "items": [
                {
                    "file": it.file,
                    "path": it.path,
                    "project_id": it.project_id,
                    "project_code": it.project_code,
                    "project_name": it.project_name,
                    "obs_date": it.obs_date,
                    "sheet": it.sheet,
                    "excel_row": it.excel_row,
                    "point_no": it.point_no,
                    "component": it.component,
                    "component_label": _COMPONENT_LABEL.get(it.component, it.component),
                    "problems": it.problems,
                    "report": it.report,
                    "system": it.system,
                    "limits": it.limits,
                    "levels": it.levels,
                }
                for it in self.items
            ],
        }


def list_report_files(folder: Path) -> list[Path]:
    """文件夹里像"导出日报"的 Excel。

    跳过 `~$` 开头的（Excel 打开时的临时文件）与隐藏文件 —— 不然会报一堆"打不开"。
    """
    if not folder.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        name = p.name
        if name.startswith("~$") or name.startswith("."):
            continue
        if p.suffix.lower() not in (".xlsx", ".xlsm"):
            continue
        out.append(p)
    return out


def audit_folder(db: Session, folder: Path, limit_items: int = MAX_ITEMS) -> AuditResult:
    """扫一个文件夹，逐份日报和系统比一遍。

    ⚠ **已归档的项目不查**（用户 2026-10-12：「归档的不要检查了」）——
    归档意味着"这个基坑结束了、不再出报表"，它的旧报表再报"速率对不上"也没有意义，
    只会把真正要处理的问题淹掉。
    但**不静默丢**：这些文件按项目聚合后放进 `archived`，结果页会写明
    「有 N 份属于已归档项目，按你的要求跳过了」—— 哪天想查，把项目恢复在用再跑一次即可。
    """
    from backend.services.project_service import list_projects

    res = AuditResult(folder=str(folder))
    if not folder.is_dir():
        res.skipped.append({"file": str(folder), "why": "这个文件夹不存在或打不开"})
        return res

    files = list_report_files(folder)
    res.scanned = len(files)
    # 用**全量项目**去认：只有认出来才能判断"它是不是归档项目的"，
    # 直接拿在用项目去认的话，归档项目的报表会被报成"认不出是哪个项目"（误导）。
    projects = list(list_projects(db, include_inactive=True))

    # 认项目/日期（不落库、不比对，先把能读的挑出来）
    parsed: list[tuple[Path, ReadReport, object, str]] = []
    archived: dict[int, dict] = {}
    for path in files:
        try:
            rep = read_report(path)
        except Exception as e:  # noqa: BLE001
            res.skipped.append({"file": path.name, "why": f"读不了：{e}"})
            continue
        if rep.problems and not rep.sheets:
            res.skipped.append({"file": path.name, "why": "；".join(rep.problems)})
            continue
        m = _match_project(projects, rep)
        if m.project is None:
            # 认不出时**把最像的候选摆出来** —— 用户看到"最像的是八号基坑(62%)"
            # 立刻就知道该改表头、还是该在系统里加个新项目，比干一句"认不出"有用得多
            res.skipped.append(
                {
                    "file": path.name,
                    "why": "认不出是哪个项目 —— "
                    + (
                        m.reason
                        if m.reason
                        else "表里没写工程名称，文件名里也看不出是哪个项目"
                    ),
                    "candidates": [c.to_dict() for c in m.candidates],
                    "ambiguous": m.ambiguous,
                }
            )
            continue
        proj = m.project
        if m.is_close:
            # 认出来了，但名字只是"相近"（不是完全一致）→ 记一笔，结果页提醒用户确认
            res.fuzzy.append(
                {
                    "file": path.name,
                    "project_id": proj.id,
                    "project_code": proj.code,
                    "project_name": proj.name,
                    "text": m.text,
                    "how": m.how,
                    "score": round(m.score, 3),
                }
            )
        if not proj.is_active:
            row = archived.setdefault(
                proj.id,
                {
                    "project_id": proj.id,
                    "project_code": proj.code,
                    "project_name": proj.name,
                    "count": 0,
                    "files": [],
                },
            )
            row["count"] += 1
            if len(row["files"]) < 20:  # 只留前 20 个文件名，够说明"是哪些"就行
                row["files"].append(path.name)
            continue
        if rep.obs_date is None:
            res.skipped.append({"file": path.name, "why": "认不出观测日期"})
            continue
        parsed.append((path, rep, proj, rep.obs_date))

    res.archived = sorted(archived.values(), key=lambda x: x["project_code"])

    # 按 (项目, 日期) 只算一次系统值 —— 同一期导了两遍也不会重复算
    sys_cache: dict[tuple[int, str], dict] = {}
    ctrl_cache: dict[tuple[int, str], tuple] = {}
    lim_cache: dict[tuple[int, str], dict] = {}
    stats: dict[int, dict] = {}

    for path, rep, proj, obs in parsed:
        res.checked += 1
        key = (proj.id, obs)
        if key not in sys_cache:
            sys_cache[key] = _system_rows(db, proj.id, obs)
        st = stats.setdefault(
            proj.id,
            {"project_id": proj.id, "project_code": proj.code, "project_name": proj.name,
             "files": 0, "rows": 0, "problem_rows": 0},
        )
        st["files"] += 1

        for sheet in rep.sheets:
            if key + (sheet.kind,) not in ctrl_cache:
                ctrl_cache[key + (sheet.kind,)] = report_control_values(db, proj.id, sheet.kind)
            if key + (sheet.kind,) not in lim_cache:
                lim_cache[key + (sheet.kind,)] = _limits_of(db, proj.id, sheet.kind)
            ctrl = ctrl_cache[key + (sheet.kind,)]
            limits = lim_cache[key + (sheet.kind,)]

            for row in sheet.rows:
                res.rows_total += 1
                st["rows"] += 1
                sysrow = sys_cache[key].get((row.point_no, row.component))

                # 报表上这行**一个数都没有** → 跳过。
                # 别把它报成"报表漏了数据"：母版套打对"该点当日无记录"的行就是清空的，
                # 旧式模板末尾还常多出一两行初始值行 —— 两者与"报表过期"在外观上无法区分，
                # 报了就是假警报。用户要查的是"速率对不上 / 超限"，那两条已经覆盖了真正的问题。
                if all(
                    v is None
                    for v in (row.prev_cum_mm, row.cum_mm, row.delta_mm, row.rate_mm_per_day)
                ):
                    continue

                if sysrow is None:
                    # 报表上有数、系统里查不到这个点/这一期 —— 这本身就是要报的问题
                    it = AuditRow(
                        file=path.name, path=str(path),
                        project_id=proj.id, project_code=proj.code, project_name=proj.name,
                        obs_date=obs, sheet=sheet.sheet_name, excel_row=row.excel_row,
                        point_no=row.point_no, component=row.component,
                        problems=[{"kind": "unknown_point"}],
                        report={"rate": _r3(row.rate_mm_per_day), "delta": _r3(row.delta_mm), "cum": _r3(row.cum_mm)},
                        system={}, limits=limits, levels={},
                    )
                    st["problem_rows"] += 1
                    if len(res.items) < limit_items:
                        res.items.append(it)
                    else:
                        res.truncated = True
                    continue

                problems, rep_vals, sys_vals, levels = _check_row(row, sysrow, limits, ctrl, sheet.columns)
                if not problems:
                    continue
                st["problem_rows"] += 1
                if len(res.items) >= limit_items:
                    res.truncated = True
                    continue
                res.items.append(
                    AuditRow(
                        file=path.name, path=str(path),
                        project_id=proj.id, project_code=proj.code, project_name=proj.name,
                        obs_date=obs, sheet=sheet.sheet_name, excel_row=row.excel_row,
                        point_no=row.point_no, component=row.component,
                        problems=problems, report=rep_vals, system=sys_vals,
                        limits=limits, levels=levels,
                    )
                )

    counter: Counter = Counter()
    for it in res.items:
        for p in it.problems:
            counter[p["kind"]] += 1
    res.counters = dict(counter)
    res.project_stats = sorted(stats.values(), key=lambda x: (-x["problem_rows"], x["project_code"]))
    return res


def folder_status(db: Session) -> dict:
    """「文件检查」页要的现状：配了哪个文件夹、上次什么时候查的、该不该提醒补扫。"""
    from backend.services.app_settings_service import get_settings

    cfg = get_settings(db)
    folder = cfg.get("audit_folder")
    last = cfg.get("audit_last_run")
    need = True
    if isinstance(last, str) and last:
        try:
            need = (_date.today() - _date.fromisoformat(last[:10])).days >= 7
        except ValueError:
            need = True
    return {
        "folder": folder,
        "last_run": last,
        "due": bool(folder) and need,
        "exists": bool(folder) and Path(str(folder)).is_dir(),
    }
