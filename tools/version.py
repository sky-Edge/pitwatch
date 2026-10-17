#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""版本号与回退。

用户 2026-10-17：
    「你先别动手 先把版本号做好 搞错了 我好退版本」

用法（在项目根目录执行）：

    python tools/version.py list                     # 有哪些版本、各自改了什么
    python tools/version.py show                     # 现在跑的是哪一版、有没有没存的改动
    python tools/version.py save "这次改了啥"         # 把当前状态存成一个新版本（版本号自动 +1）
    python tools/version.py rollback v1.1.0 --yes    # 退回某个版本

四条安全措施（都是"退错了也还能回来"）：

1. `rollback` 会先把当前改动 stash 起来（**含新建的文件**），并打一个
   `pre-rollback-<时间>` 的标签；
2. 必须带 `--yes` —— 防止手滑；
3. **只退源码**。`database/`、`templates/`、`logs/`、`导出/` 根本不在版本库里，
   一个测点、一条记录、一份母版都不会动；
4. 退完会打印"怎么回到刚才"的那条命令。
"""
from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PY = ROOT / "backend" / "core" / "version.py"
VERSION_FILE = ROOT / "VERSION"
CHANGELOG = ROOT / "CHANGELOG.md"


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{(r.stderr or r.stdout).strip()}")
    return (r.stdout or "").strip()


def read_version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', VERSION_PY.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f"读不到版本号：{VERSION_PY}")
    return m.group(1)


def write_version(ver: str) -> None:
    """两处一起写 —— 只改一头是这类东西最容易犯的错。"""
    text = VERSION_PY.read_text(encoding="utf-8")
    text = re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{ver}"', text, count=1)
    VERSION_PY.write_text(text, encoding="utf-8")
    VERSION_FILE.write_text(f"{ver}\n", encoding="utf-8")


def bump_patch(ver: str) -> str:
    major, minor, patch = (int(x) for x in ver.split("."))
    return f"{major}.{minor}.{patch + 1}"


def cmd_list(_args) -> int:
    # 按**版本号**排（-v:refname），不是按"打标签的时间" ——
    # 补打历史标签时（v1.0.0 是今天补的）按时间排会乱成一锅粥。
    tags = git("for-each-ref", "--sort=-v:refname", "--format=%(refname:short)", "refs/tags").splitlines()
    if not tags:
        print("还没有任何版本标签。")
        return 0
    print(f"{'版本':<12}{'提交日期':<13}说明")
    print("-" * 78)
    for tag in tags:
        # 日期取**提交日期**而不是 tag 的创建日期，否则补打的老版本会显示成今天
        day = git("log", "-1", "--format=%cd", "--date=short", tag, check=False)
        subj = git("log", "-1", "--format=%s", tag, check=False)
        print(f"{tag:<12}{day:<13}{subj[:46]}")
    print("\n退回某一版：python tools/version.py rollback <版本> --yes")
    return 0


def cmd_show(_args) -> int:
    ver = read_version()
    desc = git("describe", "--tags", "--always", "--dirty", check=False)
    dirty = git("status", "--porcelain", check=False)
    print(f"代码里的版本：{ver}")
    print(f"git 描述　　：{desc}")
    if dirty:
        n = len(dirty.splitlines())
        print(f"工作区　　　：{n} 个文件有改动（还没存成版本）")
        print("              —— 想固定下来：python tools/version.py save \"这次改了啥\"")
    else:
        print("工作区　　　：干净（和最近一次版本一致）")
    return 0


def cmd_save(args) -> int:
    msg = (args.message or "").strip()
    if not msg:
        raise SystemExit('要说清"这次改了啥"：python tools/version.py save "..."')
    dirty = git("status", "--porcelain", check=False)
    if not dirty:
        print("工作区没有改动，不用存版本。")
        return 0

    cur = read_version()
    new = bump_patch(cur)
    today = datetime.date.today().isoformat()
    write_version(new)

    # CHANGELOG 插到第一条分隔线之后（新版本在上）
    text = CHANGELOG.read_text(encoding="utf-8")
    marker = "\n---\n"
    entry = f"\n## v{new} — {today}\n\n- {msg}\n"
    idx = text.find(marker)
    text = text[: idx + len(marker)] + entry + text[idx + len(marker):] if idx >= 0 else text + entry
    CHANGELOG.write_text(text, encoding="utf-8")

    git("add", "-A")
    git("-c", "user.name=阿锁", "-c", "user.email=asuo@local", "commit", "-q", "-m", f"v{new}: {msg}")
    git("tag", "-f", "-a", f"v{new}", "-m", msg)
    print(f"已存成版本 v{new}（{cur} → {new}）")
    print(f"  提交 + 标签都打好了；要退回上一版：python tools/version.py rollback v{cur} --yes")
    return 0


def cmd_rollback(args) -> int:
    tag = args.tag
    tags = git("tag", "--list", check=False).splitlines()
    if tag not in tags:
        raise SystemExit(f"没有这个版本：{tag}\n可用版本：{', '.join(tags) or '（无）'}")

    cur = read_version()
    dirty = git("status", "--porcelain", check=False)
    print(f"准备把源码退回 {tag}（当前是 v{cur}）")
    if dirty:
        print(f"  当前有 {len(dirty.splitlines())} 个文件没存版本 —— 会先自动 stash 保存")
    if not args.yes:
        print("\n数据目录（database / templates / logs / 导出）不会被碰。")
        print("确认要退，请加 --yes 再执行一次：")
        print(f"  python tools/version.py rollback {tag} --yes")
        return 2

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safety = f"pre-rollback-{stamp}"
    if dirty:
        git("stash", "push", "-u", "-m", f"回退到 {tag} 之前自动保存 {stamp}")
    git("tag", "-f", safety)  # 给"刚才的状态"留个标签，退错了还能回来
    git("reset", "--hard", tag)

    print(f"已退回 {tag}。")
    print(f"  刚才的状态留了标签：{safety}")
    print(f"  想回到刚才：python tools/version.py rollback {safety} --yes")
    print("  然后关掉黑窗口、重新运行「启动系统.bat」")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="版本号与回退")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="有哪些版本").set_defaults(func=cmd_list)
    sub.add_parser("show", help="现在跑的是哪一版").set_defaults(func=cmd_show)

    ps = sub.add_parser("save", help="把当前状态存成新版本")
    ps.add_argument("message", nargs="?", help="这次改了啥")
    ps.set_defaults(func=cmd_save)

    pr = sub.add_parser("rollback", help="退回某个版本（只动源码）")
    pr.add_argument("tag", help="版本标签，如 v1.1.0")
    pr.add_argument("--yes", action="store_true", help="确认执行")
    pr.set_defaults(func=cmd_rollback)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
