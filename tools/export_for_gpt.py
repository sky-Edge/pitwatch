# -*- coding: utf-8 -*-
"""把项目导成「给 GPT 看」的小体积包。

**为什么要这个脚本**：项目目录约 1 GB —— 其中 591 MB 是 `database/`（真实观测库 + 导入的原始台账）、
20 MB 是 `templates/`（别的基坑的母版）、230 MB 是 `frontend/node_modules`、180 MB 是打包产物。
直接传传不动；更要紧的是 **`database/` 与 `templates/` 里是用户真实数据，不该给外部模型**。
本脚本只挑源码，产出约 2 MB。

产出三类文件（都在同一个输出目录里）：
1. `项目源码.zip`  —— 纯源码压缩包（只含源码，不含依赖/产物/数据）
2. `00-目录与统计.md` —— 源码目录树 + 文件数/行数统计（让模型先建立整体认知）
3. `01-…md` 分卷源码汇编 —— 按「后端 / 前端 / 测试与工具」分卷，每卷不超过 `--max-kb`，
   便于一次塞进对话（超过上下文就只能看到开头，等于没给）

用法：
    python tools/export_for_gpt.py                    # 输出到 <项目根>/导出/GPT-看项目
    python tools/export_for_gpt.py --out 某目录        # 换输出目录
    python tools/export_for_gpt.py --max-kb 300       # 每卷上限（默认 320 KB）
环境变量 `MONITOR_EXPORT_DIR` 同样可覆盖输出目录。
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---- 要打包的源码目录（相对项目根）----
SRC_DIRS = ("backend", "tests", "tools", "build_tools", "frontend/src", "frontend/public")
# ---- 要单独打包的源码文件 ----
SRC_FILES = (
    "launcher.py",
    "requirements.txt",
    "启动系统.bat",
    "关闭服务.bat",
    ".gitignore",
    "UX-UI-优化分析报告.md",
    "frontend/index.html",
    "frontend/package.json",
    "frontend/tsconfig.json",
    "frontend/vite.config.ts",
)

# 明确排除：依赖、构建产物、缓存、以及**用户真实数据**
SKIP_DIRS = {
    "node_modules", "__pycache__", ".pytest_cache", "dist", "build_pyi", "release",
    "database", "templates", "logs", ".verify_tmp", ".workbuddy", ".vite",
}
SKIP_SUFFIX = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".xlsx", ".xlsm", ".xls", ".pdf", ".zip")
SKIP_NAME_PART = (".timestamp-",)

# 分卷时按主题归组：文件名前缀 → 归属卷
VOLUME_ORDER = (
    ("01-后端代码", ("backend/",)),
    ("02-前端代码", ("frontend/",)),
    ("03-测试与工具", ("tests/", "tools/", "build_tools/")),
    ("04-文档与根文件", ("docs/", "root")),
)

# 汇编里当成文本嵌入的扩展名（其余只在 zip 里）
TEXT_SUFFIX = {".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json", ".md",
               ".txt", ".bat", ".toml", ".yml", ".yaml", ".cfg", ".ini", ".svg", ".sh", ".gitignore"}

_LANG = {".py": "python", ".ts": "ts", ".tsx": "tsx", ".js": "js", ".css": "css",
         ".html": "html", ".json": "json", ".md": "markdown", ".bat": "bat",
         ".toml": "toml", ".yml": "yaml", ".yaml": "yaml", ".svg": "xml", ".sh": "bash"}


def _skipped(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    if path.suffix.lower() in SKIP_SUFFIX:
        return True
    return any(k in path.name for k in SKIP_NAME_PART)


def collect() -> list[Path]:
    """收集要导出的源码文件（相对项目根的路径，已排序）。"""
    out: list[Path] = []
    for d in SRC_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                rel = p.relative_to(ROOT)
                if not _skipped(rel):
                    out.append(rel)
    # docs：md/html 进包，PDF 不进（二进制，且体积大）
    docs = ROOT / "docs"
    if docs.exists():
        for p in docs.iterdir():
            if p.is_file() and p.suffix.lower() in (".md", ".html") and not _skipped(p.relative_to(ROOT)):
                out.append(p.relative_to(ROOT))
    for f in SRC_FILES:
        p = ROOT / f
        if p.exists() and p.is_file():
            out.append(Path(f))
    # 去重 + 按路径排序（同一主题的文件连在一起，读起来才连贯）
    return sorted(set(out), key=lambda r: (r.as_posix().lower()))


def build_zip(files: list[Path], dest: Path) -> int:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in files:
            zf.write(ROOT / rel, arcname=rel.as_posix())
    return dest.stat().st_size


def _volume_of(rel: Path) -> str:
    posix = rel.as_posix()
    for name, prefixes in VOLUME_ORDER:
        if any(posix.startswith(p) for p in prefixes):
            return name
    return "04-文档与根文件"


def _read_text(rel: Path) -> str | None:
    if rel.suffix.lower() not in TEXT_SUFFIX and rel.name != ".gitignore":
        return None
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return (ROOT / rel).read_text(encoding="gbk")
        except Exception:  # noqa: BLE001 —— 读不了就跳过，不因一个文件毁掉整包
            return None


def _header(volume: str, part: int, total: int, files: list[Path]) -> str:
    lines = [
        f"# {volume}" + (f"（第 {part}/{total} 卷）" if total > 1 else ""),
        "",
        "> 本卷是项目源码汇编，由 `tools/export_for_gpt.py` 自动生成，**不要改这里的代码**。",
        f"> 本卷包含 {len(files)} 个文件：",
        "",
    ]
    lines += [f"> - `{f.as_posix()}`" for f in files]
    lines.append("")
    return "\n".join(lines)


def build_digest(files: list[Path], out_dir: Path, max_bytes: int) -> list[Path]:
    """按主题分卷写出源码汇编，返回写出的文件列表。"""
    by_volume: dict[str, list[Path]] = {}
    for rel in files:
        by_volume.setdefault(_volume_of(rel), []).append(rel)

    written: list[Path] = []
    for volume in sorted(by_volume):
        chunks: list[tuple[list[Path], str]] = []
        cur: list[Path] = []
        buf: list[str] = []
        size = 0
        for rel in by_volume[volume]:
            text = _read_text(rel)
            if text is None:
                continue
            lang = _LANG.get(rel.suffix.lower(), "")
            block = f"\n## `{rel.as_posix()}`\n\n```{lang}\n{text.rstrip()}\n```\n"
            raw = len(block.encode("utf-8"))
            if cur and size + raw > max_bytes:
                chunks.append((cur, "".join(buf)))
                cur, buf, size = [], [], 0
            cur.append(rel)
            buf.append(block)
            size += raw
        if cur:
            chunks.append((cur, "".join(buf)))

        for i, (chunk_files, body) in enumerate(chunks, 1):
            head = _header(volume, i, len(chunks), chunk_files)
            name = f"{volume}.md" if len(chunks) == 1 else f"{volume}-{i}.md"
            dest = out_dir / name
            dest.write_text(head + body, encoding="utf-8")
            written.append(dest)
    return written


def build_manifest(files: list[Path], out_dir: Path) -> Path:
    """目录树 + 统计。让模型（和人都）先看清"有哪些东西"。"""
    lines: list[str] = ["# 源码目录与统计", "", "> 由 `tools/export_for_gpt.py` 自动生成。", ""]

    total_lines = 0
    total_bytes = 0
    per_dir: dict[str, list[tuple[Path, int, int]]] = {}
    for rel in files:
        raw = (ROOT / rel).read_bytes()
        text = _read_text(rel)
        n = len(text.splitlines()) if text else 0
        total_lines += n
        total_bytes += len(raw)
        top = rel.parts[0] if len(rel.parts) > 1 else "(根目录)"
        per_dir.setdefault(top, []).append((rel, n, len(raw)))

    lines += [
        f"- 文件数：**{len(files)}**",
        f"- 代码行数（文本文件）：**{total_lines}**",
        f"- 未压缩体积：**{total_bytes / 1e6:.2f} MB**",
        "",
        "## 各目录",
        "",
        "| 目录 | 文件数 | 文本行数 |",
        "|---|---:|---:|",
    ]
    for top in sorted(per_dir, key=lambda t: (-sum(x[2] for x in per_dir[t]), t)):
        items = per_dir[top]
        lines.append(f"| `{top}` | {len(items)} | {sum(x[1] for x in items)} |")

    lines += ["", "## 文件清单", "", "```"]
    for rel in sorted(files, key=lambda r: r.as_posix().lower()):
        lines.append(rel.as_posix())
    lines += ["```", ""]

    dest = out_dir / "00-目录与统计.md"
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="导出「给 GPT 看」的纯源码小包")
    ap.add_argument("--out", default=os.environ.get("MONITOR_EXPORT_DIR") or str(ROOT / "导出" / "GPT-看项目"),
                    help="输出目录（默认 <项目根>/导出/GPT-看项目，可用 MONITOR_EXPORT_DIR 覆盖）")
    ap.add_argument("--max-kb", type=int, default=320, help="每卷源码汇编上限（KB），默认 320")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = collect()
    if not files:
        print("没有收集到任何源码文件 —— 确认脚本放在项目根的 tools/ 下", file=sys.stderr)
        return 2

    zip_size = build_zip(files, out_dir / "项目源码.zip")
    manifest = build_manifest(files, out_dir)
    volumes = build_digest(files, out_dir, args.max_kb * 1024)

    print(f"输出目录：{out_dir}")
    print(f"  项目源码.zip                {zip_size / 1e6:.2f} MB（{len(files)} 个文件）")
    print(f"  {manifest.name:26s} {manifest.stat().st_size / 1e3:.0f} KB")
    for v in volumes:
        print(f"  {v.name:26s} {v.stat().st_size / 1e3:.0f} KB")
    print("\n提醒：database/ 与 templates/ 是用户真实数据，本脚本**不导出**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
