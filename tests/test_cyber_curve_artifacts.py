# -*- coding: utf-8 -*-
"""回归测试：曲线可视化演示件 docs/cyber-curve.html 的静态自洽性。

为什么需要这层测试：
    cyber-curve.html 是一个自包含的 WebGL2 页面，浏览器跑不对时往往只表现为
    「黑屏」——没有编译报错、没有异常，肉眼完全看不出哪里断了。
    这里把「脚本能解析 / shader 不重复声明 / 参数与面板一一对应 / 曲线与配色
    名字前后一致」这些容易手滑改坏的点固化下来，避免下次改动引入静默失败。

GPU 侧的实跑验证不在这里（见 docs/cyber-curve 的 CDP 验证方法），
本文件只做零依赖的静态检查，保证 CI 里能秒过。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "docs" / "cyber-curve.html"


@pytest.fixture(scope="module")
def src() -> str:
    assert HTML.is_file(), f"演示件不存在：{HTML}"
    return HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js(src: str) -> str:
    """取出 <script> 里的 JS 主体。"""
    m = re.search(r"<script>(.*)</script>", src, re.S)
    assert m, "没找到 <script> 块"
    return m.group(1)


# ---------------------------------------------------------------- 基本完整

def test_file_is_self_contained(src: str) -> None:
    """单文件交付：不得引用外部 js/css/图片，否则挪个目录就白屏。"""
    body = src[src.index("<body"):] if "<body" in src else src
    externals = re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', body)
    bad = [u for u in externals if not u.startswith(("data:", "#", "javascript:"))]
    assert not bad, f"引用了外部资源，破坏单文件自包含：{bad}"


def test_js_parses(js: str, tmp_path: Path) -> None:
    """脚本语法必须成立（括号/引号配平）。用 node 真解析，比数括号可靠。"""
    import subprocess

    node = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    exes = sorted(node.glob("*/node.exe")) if node.is_dir() else []
    if not exes:
        pytest.skip("找不到 node.exe，跳过语法解析")
    exe = str(sorted(exes, key=lambda p: p.parent.name)[-1])

    # 临时文件放 pytest 的临时目录，**不要写进项目目录**：
    # ① 写进 docs/ 会污染仓库；② 进程被杀时会留下垃圾文件（实测留下过 _tmp_syntax_check.js）；
    # ③ 写进项目目录的临时文件在清理时会走"安全删除"通道，在批量删除受限的环境下会失败。
    tmp = tmp_path / "_tmp_syntax_check.js"
    tmp.write_text(js, encoding="utf-8")
    r = subprocess.run([exe, "--check", str(tmp)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"JS 语法错误：{r.stderr[:400]}"


def test_webgl2_required_not_webgl1(src: str) -> None:
    """必须申请 webgl2 上下文；拿到 webgl1 会导致 GLSL 3.00 全部编译失败。"""
    assert "getContext('webgl2'" in src or 'getContext("webgl2"' in src


def test_fallback_message_present(src: str) -> None:
    """拿不到 WebGL2 时必须有可读提示，而不是留一片黑。"""
    assert "webgl2" in src.lower()
    assert re.search(r"(不支持|失败|无法).{0,20}(WebGL|webgl)", src), "缺少 WebGL2 不可用时的提示文案"


# ---------------------------------------------------------------- Shader

def test_all_shader_sources_present(src: str) -> None:
    """八个 shader 源码常量一个都不能少，否则管线某个 PASS 会静默失效。"""
    for name in ("VS_QUAD", "VS_LINE", "FS_LINE", "FS_BRIGHT",
                 "FS_BLUR", "FS_COMPOSITE", "VS_POINT", "FS_POINT"):
        assert re.search(rf"const\s+{name}\s*=", src), f"缺少 shader 常量 {name}"


def test_glsl_version_directive(src: str) -> None:
    """每个 shader 片段都要有 #version 300 es，否则按 ES 1.00 解析直接编译失败。"""
    n_dir = len(re.findall(r"#version\s+300\s+es", src))
    n_shader = len(re.findall(r"const\s+(?:VS|FS)_\w+\s*=", src))
    assert n_dir >= n_shader, f"#version 指令数({n_dir}) 少于 shader 数({n_shader})"


def test_no_duplicate_uniform_declaration(src: str) -> None:
    """同一个 shader 内重复声明 uniform 会编译失败（改代码时最容易手滑）。"""
    blocks = re.findall(r"const\s+((?:VS|FS)_\w+)\s*=\s*`(.*?)`;", src, re.S)
    assert blocks, "没解析出 shader 源码块"
    for name, body in blocks:
        decls = re.findall(r"uniform\s+\w+\s+(\w+)\s*;", body)
        dup = {d for d in decls if decls.count(d) > 1}
        assert not dup, f"{name} 重复声明 uniform：{dup}"


def test_fragment_shaders_declare_precision(src: str) -> None:
    """fragment shader 必须声明 float 精度，否则部分驱动上直接编译报错。"""
    for name, body in re.findall(r"const\s+(FS_\w+)\s*=\s*`(.*?)`;", src, re.S):
        assert re.search(r"precision\s+(highp|mediump|lowp)\s+float", body), \
            f"{name} 缺少 precision 声明"


# ---------------------------------------------------------------- 曲线 / 配色

def test_curve_list_matches_signature(src: str) -> None:
    """曲线库必须覆盖 0..N-1 的每个 uCurve 取值。

    最危险的静默 bug：CURVES 里加了新曲线但 curveAt 没加分支 ——
    选到它时 shader 会落到最后的 else 分支，画出上一条曲线，
    页面不报任何错，只有肉眼能发现。
    """
    m = re.search(r"const\s+CURVES\s*=\s*\[([^\]]+)\]", src)
    assert m, "没找到 CURVES 数组"
    names = [s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()]
    assert len(names) >= 5, f"曲线种类过少：{names}"

    # 只看 curveAt 函数体内的分支，避免把主循环等其它地方的比较算进来
    fn = re.search(r"vec2\s+curveAt\([^)]*\)\s*\{(.*?)\n\}", src, re.S)
    assert fn, "没找到 curveAt 函数"
    used = sorted({int(n) for n in re.findall(r"uCurve\s*==\s*(\d)", fn.group(1))})
    assert used, "curveAt 里没有任何 uCurve 分支"

    # 最后一条曲线按约定由函数末尾的 "兜底 return" 承载，不需要显式 if 分支
    # （写成 `else if` 再跟一个 return 也一样，所以两种写法都放行）
    missing = [i for i in range(len(names) - 1) if i not in used]
    assert not missing, \
        f"CURVES 有 {len(names)} 条，但 curveAt 缺少这些序号的分支：{missing}（会画出上一条曲线）"

    tail = fn.group(1).rsplit("uCurve", 1)[-1]
    assert "return" in tail, \
        f"curveAt 末尾没有兜底 return —— 第 {len(names)} 条曲线（{names[-1]}）将没有实现"


def test_each_curve_has_button_label(src: str) -> None:
    """每条曲线都要有对应的界面入口，否则用户看不到。"""
    m = re.search(r"const\s+CURVES\s*=\s*\[([^\]]+)\]", src)
    names = [s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()]
    for n in names:
        assert re.search(rf"\b{n}:", src), f"曲线 {n} 没有中文标签映射"


def test_scheme_keys_have_hue(src: str) -> None:
    """每套配色方案都要给出 hue / hueSpread / chroma 三个键。"""
    m = re.search(r"const\s+SCHEMES\s*=\s*\{(.*?)\n\};", src, re.S)
    assert m, "没找到 SCHEMES"
    entries = re.findall(r"(\w+)\s*:\s*\{([^}]*)\}", m.group(1))
    assert len(entries) >= 4, f"配色方案过少：{len(entries)}"
    for key, body in entries:
        for field in ("hue", "hueSpread", "chroma"):
            assert field in body, f"配色 {key} 缺少 {field}"


# ---------------------------------------------------------------- 参数面板

def test_params_have_slider_entry(src: str) -> None:
    """参数面板是数据驱动的：P 里每个可调项都要在 SLIDERS 里定义滑杆范围。

    漏一个 → 该参数只能靠 URL 调，面板上摸不到，用户以为没这个功能。
    """
    m = re.search(r"const\s+P\s*=\s*\{(.*?)\n\};", src, re.S)
    assert m, "没找到参数对象 P"
    keys = re.findall(r"(\w+)\s*:", m.group(1))
    # curve 由曲线按钮控制、paused 由暂停键控制，不走滑杆
    tunable = [k for k in keys if k not in ("curve", "paused")]

    sl = re.search(r"const\s+SLIDERS\s*=\s*\[(.*?)\n\];", src, re.S)
    assert sl, "没找到 SLIDERS 定义"
    slider_keys = re.findall(r"\bk\s*:\s*'(\w+)'", sl.group(1))
    assert slider_keys, "SLIDERS 里没解析出任何 k 字段"

    missing = [k for k in tunable if k not in slider_keys]
    assert not missing, f"这些参数没有面板滑杆（用户调不到）：{missing}"

    unknown = [k for k in slider_keys if k not in keys]
    assert not unknown, f"SLIDERS 里的这些 k 在参数对象 P 里不存在（滑杆会是死的）：{unknown}"


def test_slider_build_iterates_definitions(src: str) -> None:
    """滑杆必须由 SLIDERS 循环生成，不能是硬抄一遍的静态 HTML。

    硬抄的话改一个默认值就会出现「面板显示和实际渲染不符」。
    """
    assert re.search(r"SLIDERS\.forEach\(", src), "滑杆不是从 SLIDERS 生成的"


# ---------------------------------------------------------------- 交互 / 分享

def test_mouse_interaction_wired(src: str) -> None:
    """鼠标交互（拖拽/滚轮）必须真的挂上事件，否则「扭曲拉伸」是空话。"""
    for ev in ("pointermove", "pointerdown", "wheel"):
        assert ev in src, f"没有监听 {ev}"


def test_url_params_cover_main_knobs(src: str) -> None:
    """URL 分享参数要能覆盖曲线、配色与主要观感参数。"""
    m = re.search(r"function\s+applyQuery\(\)\s*\{(.*?)\n\s*\}\)\(\);", src, re.S)
    assert m, "没找到 applyQuery"
    body = m.group(1)
    for knob in ("curve", "scheme", "hue", "bloom", "lines", "hide"):
        assert f"'{knob}'" in body or f'"{knob}"' in body, f"URL 参数不支持 {knob}"


def test_url_override_resyncs_panel(src: str) -> None:
    """URL 覆盖后要重新同步界面，否则 HUD 曲线名/滑杆位置与实际渲染不符。

    这是实测踩过的坑：用 ?curve=rose 打开时，画的是玫瑰线，
    HUD 却仍显示 SINE LATTICE（因为 syncUI 跑在 URL 覆盖之前）。
    """
    # 取 applyQuery 这个 IIFE 的调用点之后的一段，检查紧接着有没有 syncUI
    calls = [m.end() for m in re.finditer(r"\}\)\(\);\s*//\s*URL 覆盖之后同步一次界面", src)]
    assert calls, "没找到 applyQuery IIFE 的调用点"
    tail = src[calls[0]: calls[0] + 400]
    assert "syncUI" in tail, \
        "applyQuery() 之后没有调用 syncUI() —— URL 打开时面板会显示错误状态"


def test_adaptive_quality_present(src: str) -> None:
    """高帧率要求靠自适应画质兜底，掉帧时降 DPR。"""
    assert "autoQuality" in src
    assert re.search(r"autoQuality\s*-", src) and re.search(r"autoQuality\s*\+", src), \
        "自适应画质只升不降或只降不升"
