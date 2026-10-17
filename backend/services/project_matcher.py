# -*- coding: utf-8 -*-
"""把报表里写的「工程名称」认成系统里的「项目」—— 宽松，但绝不硬猜。

用户 2026-10-13：
    「检查时 基坑名字和系统中项目名字不一定完全对得上 加一个智能识别 不要太死板」

现实里两边对不上是常态，实测会遇到的写法：

| 报表里写的 | 系统里的项目名 | 为什么要能认出来 |
|---|---|---|
| 示例公路 八号基坑围护工程 | 八号基坑 | 表头常带地名前缀 + 工程类别后缀 |
| 8号基坑 | 八号基坑 | 中文数字 / 阿拉伯数字 |
| 八号基坑（右幅） | 八号基坑 | 括号里是幅别、期次之类的限定 |
| （表里没写名称）PRJ-08_日报_2026.08.01.xlsx | 编号 PRJ-08 | 老母版的表内没有「工程名称」格 |

## 三条底线（都是为了"宁可不认，不可认错"）

认错比认不出糟得多：把 A 基坑的日报算到 B 头上，结果页会刷出一屏"对不上"，
用户白忙一场还开始不信这个功能。所以：

1. **基坑编号对不上 → 直接不认**：「八号基坑」和「五号基坑」很像，但根本不是一回事；
2. **实体词对不上 → 直接不认**：「八号基坑」和「八号桥墩」同理；
3. **前两名分数咬得太近 → 不认**，并把候选摆出来让用户自己定 —— 猜一半对一半不如不猜。

另外，**认出来但只是"相近"的**（不是完全一致），会单独记一笔给用户看：
"这份是按相近认的，你确认一下" —— 这样它认对了用户放心，认歪了一眼能发现。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

#: 打不到这个分数就不认（再低就只是"有几个字一样"）
MIN_SCORE = 0.72

#: 第一名领先第二名不到这个数 → 认为分不清，不认
AMBIGUITY_MARGIN = 0.06

#: 低于这个分数就算"只是相近"（结果里会提醒用户确认一下）
CLOSE_SCORE = 0.90


# ────────────────────────────── 归一化 ──────────────────────────────

_CN_DIGITS = {
    "零": "0", "〇": "0", "一": "1", "壹": "1", "二": "2", "两": "2", "贰": "2",
    "三": "3", "叁": "3", "四": "4", "肆": "4", "五": "5", "伍": "5",
    "六": "6", "陆": "6", "七": "7", "柒": "7", "八": "8", "捌": "8", "九": "9", "玖": "9",
}

#: 中文数字后跟这些字，才算"编号"（八号、十二期）；不跟就不动它，
#: 免得把"统一""二期"这类词里的汉字无故改成数字。
_NUM_SUFFIX = "号期区块段墩井层孔桩楼跨轴幅标座台仓联道"

#: "十"单独处理（十 / 十二 / 二十 / 二十三）
_TEN_PAT = re.compile(r"([一二三四五六七八九])?十([一二三四五六七八九])?")
_SINGLE_NUM_PAT = re.compile(r"([零〇一壹二两贰三叁四肆五伍六柒七捌八玖九])(?=[" + _NUM_SUFFIX + r"]|$)")

#: 日期与"第 N 期"：认项目时是噪音（文件名里天天有），而且里面的数字会干扰编号比较
_DATE_PAT = re.compile(
    r"\d{4}\s*[-年./]\s*\d{1,2}\s*[-月./]\s*\d{1,2}\s*日?"
    r"|\d{8}"
    r"|第\s*[0-9一二三四五六七八九十]+\s*期"
)

#: 括号（全角已被 NFKC 转成半角）
_PAREN_PAT = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]")

#: 只留下 数字 / 小写字母 / 汉字，其余（空格、下划线、短横、点、括号符号…）全去掉
_KEEP_PAT = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")

#: 文档 / 流程类词：说的是"这是什么文件"，跟"是哪个基坑"无关，比较前先摘掉。
#: ⚠ 只摘这类词，**不摘「基坑 / 墩 / 桥 / 隧道」** —— 那是实体，
#:   摘了就会把「八号基坑」和「八号桥墩」混成同一个东西。
_GENERIC_WORDS = (
    "监测日报", "监测报表", "监测数据", "监控量测", "监测",
    "观测", "量测", "测量", "日报", "周报", "月报", "报表", "报告",
    "汇总表", "统计表", "汇总", "统计", "数据表", "数据", "记录表", "记录",
    "成果表", "成果", "资料", "表格", "专项方案", "施工方案", "方案", "图纸",
    "项目", "工程", "施工", "新建", "续建", "改扩建", "主体", "附属",
    "围护结构", "围护", "支护", "结构",
)

#: 实体词 → 说法归一（"桥墩/墩柱"都算"墩"）
_ENTITY_CANON = {
    "基坑": "基坑", "桥墩": "墩", "墩柱": "墩", "墩": "墩",
    "盖梁": "盖梁", "承台": "承台",
    "桩基": "桩", "围护桩": "桩", "桩": "桩",
    "地下连续墙": "地连墙", "地连墙": "地连墙",
    "隧道": "隧道", "车站": "车站", "路基": "路基", "边坡": "边坡",
    "管廊": "管廊", "涵洞": "涵洞", "匝道": "匝道", "地道": "地道",
    "桥梁": "桥", "桥": "桥", "竖井": "竖井", "工作井": "竖井", "盾构": "盾构",
}
_ENTITY_PAIRS = sorted(_ENTITY_CANON.items(), key=lambda kv: -len(kv[0]))

_NUM_PAT = re.compile(r"\d+")


def _cn_to_arabic(m: re.Match) -> str:
    """「十」→10、「十二」→12、「二十」→20、「二十三」→23。"""
    a, b = m.group(1), m.group(2)
    tens = 1 if a is None else int(_CN_DIGITS[a])
    ones = 0 if b is None else int(_CN_DIGITS[b])
    return str(tens * 10 + ones)


def normalize(text: str, *, is_filename: bool = False) -> str:
    """把一个名字搓成可比较的样子。

    `is_filename=True` 时会**丢掉括号里的内容** —— 文件名里那对括号常常是
    「（母版名）」「（再导一份）」，母版名还可能是**别的基坑**的名字
    （母版串名那件事），留着它会把报表认到别人头上。
    """
    t = unicodedata.normalize("NFKC", str(text or ""))  # 全角→半角、全角括号→半角
    t = t.lower()
    t = _DATE_PAT.sub("", t)
    t = _TEN_PAT.sub(_cn_to_arabic, t)
    t = _SINGLE_NUM_PAT.sub(lambda m: _CN_DIGITS[m.group(1)], t)
    if is_filename:
        t = _PAREN_PAT.sub("", t)
    return _KEEP_PAT.sub("", t)


def _core(s: str) -> str:
    """摘掉通用词之后剩下的"这个基坑叫什么"。"""
    out = s
    for w in _GENERIC_WORDS:  # 已按"长的在前"排好，先摘"监测日报"再摘"监测"
        if w in out:
            out = out.replace(w, "")
    return out


def _numbers(s: str) -> set[str]:
    """编号里的数字（"08" 与 "8" 视作同一个）。"""
    return {str(int(n)) for n in _NUM_PAT.findall(s)}


def entities_of(text: str) -> frozenset[str]:
    """这句话里说的是什么实体（基坑 / 墩 / 桥 …）。"""
    s = normalize(text)
    found: set[str] = set()
    for word, canon in _ENTITY_PAIRS:
        if word in s:
            found.add(canon)
            s = s.replace(word, "·")  # 已经算过的别被更短的词重复统计
    return frozenset(found)


def numbers_of(text: str) -> set[str]:
    return _numbers(normalize(text))


def _dice(a: str, b: str) -> float:
    """二元组重合度（对中文这种没有空格的语言比编辑距离稳）。"""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    if not ga or not gb:
        return 0.0
    return 2 * len(ga & gb) / (len(ga) + len(gb))


def _skeleton(s: str) -> str:
    """把实体词整个摘掉，只留"哪儿 + 几号"：八号桥墩 → 8号。

    用来接住简称：报表写「8号墩」、系统里叫「八号桥墩」—— 实体词是对得上的，
    差的只是"桥"这个中间字，光看包含关系认不出来。
    ⚠ 只在**实体词已经对得上**之后才用（实体不同的那种，前面就否决掉了），
      否则会把「八号基坑」和「八号桥墩」当成同一个东西。
    """
    out = s
    for word, _canon in _ENTITY_PAIRS:
        if word in out:
            out = out.replace(word, "")
    return out


def score(report_text: str, project_name: str, *, report_is_filename: bool = False) -> float:
    """报表里那句名字 vs 系统项目名：0 ~ 1。0 表示"肯定不是"（被否决了）。

    分数是怎么来的（从高到低）：
      1.00 一模一样
      0.96 摘掉"工程/监测/围护"这类词之后一模一样      → 八号基坑围护工程 ＝ 八号基坑
      0.93 实体和编号都对得上、只是写法长短不同        → 8号墩 ＝ 八号桥墩
      0.81~0.95 一方包含另一方（越接近越像）          → 示例公路八号基坑 ⊃ 八号基坑
      0.50~0.72 二元组重合度（只是有几个字一样）      → 不够，不认
    """
    q = normalize(report_text, is_filename=report_is_filename)
    t = normalize(project_name)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0

    cq, ct = _core(q), _core(t)
    if not cq or not ct:
        return 0.0
    if cq == ct:
        return 0.96

    # ── 否决一：编号不同 → 肯定不是同一个基坑 ──
    nq, nt = _numbers(cq), _numbers(ct)
    if nq and nt and not (nq & nt):
        return 0.0

    # ── 否决二：实体不同 → "八号基坑" 不是 "八号桥墩" ──
    eq, et = entities_of(cq), entities_of(ct)
    if eq and et and not (eq & et):
        return 0.0

    # 同一实体、同一编号，只是叫法长短不同（8号墩 / 八号桥墩）
    sq, st = _skeleton(cq), _skeleton(ct)
    if sq and sq == st:
        return 0.93

    if cq in ct or ct in cq:
        short, long_ = (cq, ct) if len(cq) <= len(ct) else (ct, cq)
        s = 0.78 + 0.17 * (len(short) / len(long_))
        if eq and et:
            s += 0.03  # 实体也对上了，更可信
        return min(s, 0.95)
    return _dice(cq, ct)


# ────────────────────────────── 认项目 ──────────────────────────────


@dataclass
class Candidate:
    """一个候选项目 + 它是怎么被看上的。

    `kind`: "name"（比名字）或 "code"（比编号）—— 措辞不一样：
    编号对上了是**认出来的**，名字只是"像"。
    `weak`: 只是"沾点边"的参考项（编号/实体都没对上），**不会用来认定项目**，
    只在"认不出"时摆给用户看，让他知道该改表头还是该去系统里加项目。
    """

    project: object
    score: float
    text: str
    how: str
    kind: str = "name"
    weak: bool = False

    @property
    def name(self) -> str:
        return getattr(self.project, "name", "") or getattr(self.project, "code", "")

    @property
    def label(self) -> str:
        """带编号的名字 —— 两个项目同名时（工地上真会这样）才分得清是哪一个。"""
        code = getattr(self.project, "code", "") or ""
        return f"{self.name}（{code}）" if code else self.name

    def to_dict(self) -> dict:
        return {
            "project_id": getattr(self.project, "id", None),
            "project_code": getattr(self.project, "code", ""),
            "project_name": getattr(self.project, "name", ""),
            "score": round(self.score, 3),
            "text": self.text,
            "how": self.how,
            "weak": self.weak,
        }


@dataclass
class Match:
    """认项目的结果。`project is None` 时看 `reason` / `candidates`。"""

    project: object | None = None
    how: str = ""
    score: float = 0.0
    text: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    ambiguous: bool = False
    reason: str = ""

    @property
    def is_close(self) -> bool:
        """认出来了，但只是"相近"（不是完全一致）→ 结果里提醒用户确认一下。"""
        return self.project is not None and self.score < CLOSE_SCORE


def _code_of(project) -> str:
    """编号：去掉分隔符、小写、数字去前导零（PRJ-08 → prj8）。"""
    raw = str(getattr(project, "code", "") or "")
    return normalize(raw)


def _code_in(code: str, text: str) -> bool:
    """编号出现在这段文字里，且**后面不再跟数字**。

    后跟数字的判据是为了"PRJ1 不要匹配到 PRJ10_日报" —— 实测踩过这个坑的话，
    项目编号只差一位时会大面积认错。
    """
    return re.search(re.escape(code) + r"(?![0-9])", text) is not None


def _loose_score(report_text: str, project_name: str, *, report_is_filename: bool = False) -> float:
    """**只用于"认不出"时排参考项**的宽松分 —— 故意跳过两道否决。

    为什么单独一个函数：认定项目时必须严格（编号不同就不认），
    但"认不出"之后给用户看的"最像的"应该宽松些，否则九号基坑的报表连
    "八号基坑"都提不出来，用户只能干瞪眼。这个分数**永远不参与认定**。
    """
    q = normalize(report_text, is_filename=report_is_filename)
    t = normalize(project_name)
    cq, ct = _core(q), _core(t)
    if not cq or not ct:
        return 0.0
    if cq == ct:
        return 0.96
    if cq in ct or ct in cq:
        short, long_ = (cq, ct) if len(cq) <= len(ct) else (ct, cq)
        return 0.78 + 0.17 * (len(short) / len(long_))
    return _dice(cq, ct)


def _loose_candidates(projects, cands, limit: int = 3) -> list[Candidate]:
    out: list[Candidate] = []
    for proj in projects:
        name = str(getattr(proj, "name", "") or "")
        if not name:
            continue
        best = None
        for text, src in cands:
            sc = _loose_score(text, name, report_is_filename=(src == "文件名"))
            if best is None or sc > best[0]:
                best = (sc, text)
        if best and best[0] > 0:
            out.append(Candidate(proj, best[0], best[1], "仅作参考", weak=True))
    out.sort(key=lambda c: -c.score)
    return out[:limit]


def match_project(projects, texts) -> Match:
    """认项目。

    `texts`: [(文本, 出处)]，出处是「表内名称」或「文件名」；按可靠程度从高到低传进来。
    表内名称比文件名可靠（文件名可能是母版名、也可能是别的基坑的）。
    """
    cands: list[tuple[str, str]] = [(str(t), src) for t, src in texts if str(t or "").strip()]
    if not cands:
        return Match(reason="表里没写工程名称")

    best: dict[int, Candidate] = {}

    def keep(c: Candidate) -> None:
        key = id(c.project)
        cur = best.get(key)
        if cur is None or c.score > cur.score:
            best[key] = c

    # ── 一、编号：确定性最强，直接比归一化后的编号 ──
    # ⚠ 不做"第一个命中就返回"：一份表里可能同时出现两个项目的名字/编号
    #   （母版是别人那期另存的，旧名残留），谁在前面就认谁 = 结果被项目顺序左右。
    #   统一打分、最后判歧义，分不清就交给用户。
    code_hits: list[tuple[int, object, str, str]] = []  # (编号长度, 项目, 文本, 出处)
    for proj in projects:
        code = _code_of(proj)
        if len(code) < 2:  # "1" 这种会匹配到一大堆无关文字
            continue
        for text, src in cands:
            nt = normalize(text, is_filename=(src == "文件名"))
            if not nt:
                continue
            if nt == code or _code_in(code, nt):
                code_hits.append((len(code), proj, text, src))
    if code_hits:
        # 多个项目都命中时**只认最长的那个编号** —— PRJ10 比 PRJ1 更像
        top_len = max(h[0] for h in code_hits)
        top = [h for h in code_hits if h[0] == top_len]
        for _n, proj, text, src in top:
            exact = normalize(text, is_filename=(src == "文件名")) == _code_of(proj)
            # 编号是硬判据（最长编号取胜 + 后面不跟数字）。完全相等给满分，
            # 只是"出现在文件名里"给 0.95 —— 万一编号被复用，用户还能在
            # "按相近认的"那栏里看见它。
            keep(
                Candidate(
                    proj,
                    1.0 if exact else 0.95,
                    text,
                    f"{src}里的编号",
                    kind="code",
                )
            )

    # ── 二、名称：归一化后比相似度 ──
    for proj in projects:
        name = str(getattr(proj, "name", "") or "")
        if not name:
            continue
        for text, src in cands:
            sc = score(text, name, report_is_filename=(src == "文件名"))
            if sc <= 0:
                continue
            keep(
                Candidate(
                    proj,
                    sc,
                    text,
                    "表内名称" if src == "表内名称" else "文件名",
                )
            )

    if not best:
        # 全被否决了（编号或实体对不上）。这时**光说"对不上"不够** ——
        # 用户想知道的是"系统里最接近的是哪个"，才好决定去改表头还是加项目。
        # 所以给几个"沾点边"的参考项，并明确标成 weak：它们**绝不参与认定**。
        return Match(
            candidates=_loose_candidates(projects, cands),
            reason="系统里没有能对得上的项目（下面这几个只是沾点边）",
        )

    ranked = sorted(best.values(), key=lambda c: -c.score)
    top = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None

    if top.score < MIN_SCORE:
        return Match(
            candidates=ranked[:3],
            reason=f"都不太像 —— 最像的「{top.name}」也只有 {top.score:.0%} 像，不敢替你定",
        )
    if runner_up is not None and runner_up.score >= MIN_SCORE and (top.score - runner_up.score) < AMBIGUITY_MARGIN:
        tied = [c for c in ranked if c.score >= MIN_SCORE][:3]
        return Match(
            candidates=tied,
            ambiguous=True,
            reason="这几个项目都像，分不清是哪份：" + "、".join(c.label for c in tied),
        )

    how = _how_sentence(top)
    return Match(project=top.project, how=how, score=top.score, text=top.text, candidates=ranked[:3])


def _how_sentence(c: Candidate) -> str:
    """一句话说清"凭什么认成它" —— 结果页要拿这句话给用户看。"""
    if c.kind == "code":
        return f"{c.how}完全一致" if c.score >= 1.0 else f"按{c.how}认出来的（{c.score:.0%}）"
    if c.score >= 1.0:
        return f"{c.how}完全一致"
    if c.score >= CLOSE_SCORE:
        return f"{c.how}基本一致（{c.score:.0%}）"
    return f"{c.how}相近（{c.score:.0%}）"
