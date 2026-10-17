# -*- coding: utf-8 -*-
"""一次上传多份台账 → 按表内「工程名称」自动分到各项目。

用户 2026-10-16：「现在操作还是有一点点繁琐，可以把每天的工作量降低一点吗」，
在四个减负方向里选的是**「导入不用逐个项目点」**。

原来每天的路径是：项目列表 → 点进项目 → Excel 导入 → 拖文件 → 核对 → 全部导入 →
返回 → 换下一个项目。基坑多的时候，光是"进对项目的门"就占了大半工夫。

## 认项目的口径（三条，都是为了"宁可不认，不可认错"）

认错比认不出糟得多：A 基坑的数据进了 B 基坑，两边的点号常常还**恰好同名**
（都叫 Z1、Z2…），校验一路绿灯，等发现时累计变化量已经错了一整段。所以：

1. **表里写了「工程名称」→ 只按它认，文件名一概不参与。**
   为什么不让文件名一起打分：一份文件里混着几个基坑时，文件名指向的是其中一个，
   它会给那个项目送上 0.95 分，而表内名称满分也才 1.0 —— 两者差 0.05，
   正好卡在 `project_matcher.AMBIGUITY_MARGIN`(0.06) 里，**清清楚楚的一张表会被判成"分不清"**。
2. **表里没写名称**，才按"同一份文件里别的表认到了谁"来继承；一份文件里认出了
   两个以上项目（混表）时，这张没写名字的表**不猜**，摆给用户手选。
3. 只有整份文件都没写名称时，才退到文件名认，并且**明确标出来**
   （`from_filename`）—— 界面会说"按文件名认的，请核一眼"。

## 不碰的东西

- **绝不自动落库**：这里只建"预览态"的批次，落库仍走用户点过的那个
  `/import/batches/{id}/confirm`，一条规则都不改。
- **点位清单不路由**：那是「批量建项目」的活（`parser.is_period_ledger` 的反面），
  导进来会把几毫米的读数当高程写成初始值，全程不报错。这里只把它挑出来说明。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.importers.parser import KIND_POINT_LIST, ParsedSheet
from backend.services.project_matcher import match_project


@dataclass
class SheetRoute:
    """一张数据表认到了哪个项目（`project is None` = 没认出来，要用户手选）。"""

    sheet: ParsedSheet
    project: object | None = None
    how: str = ""
    score: float = 0.0
    #: 只是"相近"（不是完全一致）→ 界面上提醒核一眼
    close: bool = False
    #: 表里没写名称、只按文件名认的 → 更要提醒核一眼
    from_filename: bool = False
    ambiguous: bool = False
    reason: str = ""
    candidates: list[dict] = field(default_factory=list)

    @property
    def sheet_name(self) -> str:
        return self.sheet.sheet_name

    def to_dict(self) -> dict:
        p = self.project
        return {
            "sheet_name": self.sheet_name,
            "series_type": self.sheet.series_type,
            "kind": self.sheet.kind,
            "obs_date": self.sheet.obs_date.isoformat() if self.sheet.obs_date else None,
            "row_count": self.sheet.total_rows,
            "name_texts": list(self.sheet.name_texts),
            "project_id": getattr(p, "id", None),
            "project_code": getattr(p, "code", "") or "",
            "project_name": getattr(p, "name", "") or "",
            "project_archived": bool(p is not None and not getattr(p, "is_active", True)),
            "how": self.how,
            "score": round(self.score, 3),
            "close": self.close,
            "from_filename": self.from_filename,
            "ambiguous": self.ambiguous,
            "reason": self.reason,
            "candidates": self.candidates,
        }


def _route_by(sheet: ParsedSheet, projects, texts) -> SheetRoute:
    m = match_project(projects, texts)
    r = SheetRoute(sheet=sheet, ambiguous=m.ambiguous, reason=m.reason)
    if m.project is not None:
        r.project = m.project
        r.how = m.how
        r.score = m.score
        r.close = m.is_close
    r.candidates = [c.to_dict() for c in m.candidates]
    return r


def _label(project) -> str:
    code = getattr(project, "code", "") or ""
    name = getattr(project, "name", "") or ""
    return f"{name}（{code}）" if code else name


def _inherit(sheet: ParsedSheet, other: SheetRoute) -> SheetRoute:
    """表里没写名称，但同一份文件里别的表都指向同一个基坑 → 跟着它走。"""
    return SheetRoute(
        sheet=sheet,
        project=other.project,
        how="同一份文件里别的数据表写的都是这个基坑",
        score=other.score,
        close=other.close,
    )


def _mixed(sheet: ParsedSheet, labels: list[str], projects, hit_ids: set[int]) -> SheetRoute:
    """一份文件里混着好几个基坑，而这张表自己没写名称 → 不猜，摆出候选让用户手选。"""
    return SheetRoute(
        sheet=sheet,
        reason="这份文件里混着好几个基坑（" + "、".join(labels) + "），而这张表自己没写工程名称，不敢替你猜",
        candidates=[
            {
                "project_id": getattr(p, "id", None),
                "project_code": getattr(p, "code", "") or "",
                "project_name": getattr(p, "name", "") or "",
                "how": "这份文件里出现过的基坑",
                "weak": False,
            }
            for p in projects
            if id(p) in hit_ids
        ],
    )


def route_file(projects, filename: str, sheets: list[ParsedSheet]) -> list[SheetRoute]:
    """把一份文件里的数据表逐张认项目（含点位清单，那些会在 `split_routes` 里被挑出去）。

    `projects` 传全量（含已归档）：归档项目认出来了也要**说出来**，
    而不是变成"认不出"—— 后者用户只会以为系统笨，前者他能一眼看懂"这表是旧基坑的"。
    """
    # 一、先只按表内名称认（文件名不参与打分，理由见模块注释第 1 条）
    named: dict[int, SheetRoute] = {}
    for s in sheets:
        if s.name_texts:
            named[id(s)] = _route_by(s, projects, [(t, "表内名称") for t in s.name_texts])

    hits = [r for r in named.values() if r.project is not None]
    hit_ids = {id(r.project) for r in hits}
    hit_labels = sorted({_label(r.project) for r in hits})

    # 二、逐张定稿：写过名称的用自己那份结果，没写的按上面的三条口径兜底
    routes: list[SheetRoute] = []
    for s in sheets:
        own = named.get(id(s))
        if own is not None:
            routes.append(own)
        elif len(hit_ids) == 1:
            routes.append(_inherit(s, hits[0]))
        elif len(hit_ids) > 1:
            routes.append(_mixed(s, hit_labels, projects, hit_ids))
        else:
            # 整份文件都没写名称 → 只能看文件名，并且明说是按文件名认的
            fb = _route_by(s, projects, [(filename, "文件名")])
            if fb.project is not None:
                fb.from_filename = True
                fb.how = "表里没写工程名称，按文件名认的"
            elif not fb.reason:
                fb.reason = "表里没写工程名称，文件名里也看不出是哪个基坑"
            routes.append(fb)
    return routes


def split_routes(routes: list[SheetRoute]) -> tuple[list[SheetRoute], list[SheetRoute], list[SheetRoute]]:
    """分成（认到了项目的, 没认出来要手选的, 点位清单）。

    ⚠ 点位清单必须**先挑出去**再判前两类：它也可能被认到项目上
    （文件名里带着基坑名），若不排除就会既进"分好的组"又进"点位清单"，
    用户点一下导入，几毫米的读数被当成高程写进测点初始值 —— 而且全程不报错。
    """
    point_lists = [r for r in routes if r.sheet.kind == KIND_POINT_LIST]
    data = [r for r in routes if r.sheet.kind != KIND_POINT_LIST]
    routed = [r for r in data if r.project is not None]
    pending = [r for r in data if r.project is None]
    return routed, pending, point_lists


def group_by_project(routes: list[SheetRoute]) -> dict[int, list[SheetRoute]]:
    """按项目分组（同一份文件、同一个项目 = 一个批次）。"""
    out: dict[int, list[SheetRoute]] = {}
    for r in routes:
        pid = int(getattr(r.project, "id"))
        out.setdefault(pid, []).append(r)
    return out


def projects_for_routing(db: Session) -> list:
    from backend.services.project_service import list_projects

    return list(list_projects(db, include_inactive=True))
