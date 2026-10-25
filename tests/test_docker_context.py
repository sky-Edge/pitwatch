# -*- coding: utf-8 -*-
"""镜像构建上下文守卫：Dockerfile 要 COPY 的文件，必须真的在上下文里。

【为什么会有这个测试】
2026-10-25 部署到 AWS 时构建直接失败：

    failed to compute cache key: failed to calculate checksum of ref
    ... "/CHANGELOG.md": not found

原因是 `.dockerignore` 里写了 `*.md`（只放行 `README.md`），而 Dockerfile 里有
`COPY launcher.py VERSION CHANGELOG.md ./` —— 本地没跑过 `docker build`，谁也没发现。

这类错误**在本地开发时完全不可见**（Python 跑得好好的、前端也构建成功），
只有真去 `docker build` 才炸，而且报错信息离原因很远。所以用静态检查把它钉住：
解析 Dockerfile 的 COPY，逐个文件按 `.dockerignore` 的规则（最后匹配优先）判断。
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _rules() -> list[str]:
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _excluded(path: str) -> bool:
    """按 .dockerignore 判断某路径是否被排除（后写的规则覆盖前面的）。"""
    hit = False
    for rule in _rules():
        negate = rule.startswith("!")
        pattern = rule[1:] if negate else rule
        pattern = pattern.lstrip("/")
        # 目录规则（如 `database/`）连目录下所有内容一起排除
        if pattern.endswith("/"):
            if path == pattern.rstrip("/") or path.startswith(pattern):
                hit = not negate
        elif fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern):
            hit = not negate
    return hit


def _copy_sources() -> list[str]:
    """Dockerfile 里所有 COPY 的本地源路径（跳过 `--from=` 的跨阶段拷贝）。"""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    sources: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY "):
            continue
        if "--from=" in line:
            continue
        parts = re.split(r"\s+", line)[1:]
        parts = [p for p in parts if not p.startswith("--")]
        sources.extend(parts[:-1])  # 最后一个是目标路径
    return sources


def test_every_copied_file_exists() -> None:
    """Dockerfile 要拷的每个文件都得在仓库里（写错文件名时立刻报）。"""
    missing = [s for s in _copy_sources() if not (ROOT / s).exists()]
    assert not missing, f"Dockerfile 要 COPY 但仓库里没有：{missing}"


def test_every_copied_path_is_in_build_context() -> None:
    """被 `.dockerignore` 排除的文件，Docker 里根本看不见 —— 必须在这里拦住。

    ★ 就是这条规则漏了 `CHANGELOG.md`，导致 AWS 上构建失败。
    """
    blocked = [s for s in _copy_sources() if _excluded(s)]
    assert not blocked, (
        f"这些路径被 .dockerignore 排除了，docker build 会报 not found：{blocked}\n"
        f"修法：在 .dockerignore 里加 `!文件名` 放行。"
    )


def test_dockerignore_still_excludes_runtime_data() -> None:
    """反过来也要守：真实数据目录绝不能进镜像。

    镜像会发到别人手上，带上生产监测数据是事故。
    """
    for must_exclude in ("database/monitor.db", "templates/x.xlsx", "logs/app.log", "uploads/a.xlsx"):
        assert _excluded(must_exclude), f"{must_exclude} 没有被排除 —— 真实数据会进镜像"
