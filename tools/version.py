#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""版本号与回退。

用户 2026-10-17：
    「你先别动手 先把版本号做好 搞错了 我好退版本」

用户 2026-11-13：
    「这版本号更新的太频繁了……只做大版本 小改不做版本更替 只做说明」

所以是两条道：
· **小改**（修个位置、调个措辞、加个小开关）→ `note`，只往 CHANGELOG 记一句说明，
  **版本号一个字不动**，照样提交、照样部署。
· **大版本**（告一段落、要发给人用）→ `save`，版本号跳到下一个整版
  （1.2.0 → 2.0.0 这种），并且把攒下来的说明**汇总**进这一版的条目里。

用法（在项目根目录执行）：

    python tools/version.py list                     # 有哪些版本、各自改了什么
    python tools/version.py show                     # 现在跑的是哪一版、有没有没存的改动
    python tools/version.py note "小改一句说明"       # 只记录，不动版本号
    python tools/version.py save "这一版都干了啥"      # 发大版本（版本号 +1 到下一个整版）
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

#: CHANGELOG 里"还没发大版本"的那一节的标题。攒着，等 save 时并进版本条目。
NOTE_HEADING = "## 未发版改动（只做说明，不动版本号）"


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


def bump_minor(ver: str) -> str:
    """跳到下一个**整版**：1.1.41 → 1.2.0，1.9.3 → 2.0.0。

    刻意不提供"只 +1 补丁号"：小改不涨号，涨号就是发版（用户 2026-11-13）。
    """
    major, minor, _patch = (int(x) for x in ver.split("."))
    if minor >= 9:
        return f"{major + 1}.0.0"
    return f"{major}.{minor + 1}.0"


def _after_first_rule(text: str) -> int:
    """第一条 `---` 之后的插入点（CHANGELOG 的版本条目都在它下面）。"""
    marker = "\n---\n"
    idx = text.find(marker)
    return idx + len(marker) if idx >= 0 else len(text)


def add_note_text(text: str, msg: str, today: str) -> str:
    """往「未发版改动」里加一条（纯文本操作，方便单测）。"""
    line = f"- {today} {msg}"
    pos = text.find(NOTE_HEADING)
    if pos >= 0:
        # 已有这一节 → 接在标题后面的第一条空行之后
        end = text.find("\n## ", pos + len(NOTE_HEADING))
        block = text[pos:end if end >= 0 else len(text)]
        if not block.endswith("\n"):
            block += "\n"
        new_block = block.rstrip("\n") + "\n" + line + "\n\n"
        return text[:pos] + new_block + (text[end:] if end >= 0 else "")
    at = _after_first_rule(text)
    return text[:at] + f"\n{NOTE_HEADING}\n\n{line}\n" + text[at:]


def takes_notes(text: str) -> list[str]:
    """取出「未发版改动」里的每条说明（不含日期前缀以外的处理）。"""
    pos = text.find(NOTE_HEADING)
    if pos < 0:
        return []
    end = text.find("\n## ", pos + len(NOTE_HEADING))
    block = text[pos:end if end >= 0 else len(text)]
    return [ln.strip()[2:].strip() for ln in block.splitlines() if ln.strip().startswith("- ")]


def fold_notes_text(text: str, ver: str, today: str, headline: str) -> tuple[str, list[str]]:
    """发大版本时把攒的说明**并进**这一版，并清空「未发版改动」。"""
    notes = takes_notes(text)
    pos = text.find(NOTE_HEADING)
    if pos >= 0:
        end = text.find("\n## ", pos + len(NOTE_HEADING))
        text = text[:pos] + (text[end + 1:] if end >= 0 else "")
        text = text.replace("\n\n\n---\n\n\n", "\n\n---\n\n")

    bullets = "".join(f"  - {n}\n" for n in notes)
    entry = f"\n## v{ver} — {today}\n\n- {headline}\n"
    if bullets:
        entry += "\n改动明细：\n" + bullets
    at = _after_first_rule(text)
    return text[:at] + entry + text[at:], notes


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
        print(f"工作区　　　：{n} 个文件有改动（还没记录）")
        print("              —— 小改：python tools/version.py note \"改了啥\"（不动版本号）")
        print("              —— 发大版本：python tools/version.py save \"这一版干了啥\"")
    else:
        print("工作区　　　：干净（和最近一次版本一致）")
    return 0


def cmd_save(args) -> int:
    """发**大版本**：号跳到下一个整版，并把攒的说明汇总进这一条。"""
    msg = (args.message or "").strip()
    if not msg:
        raise SystemExit('要说清"这一版干了啥"：python tools/version.py save "..."')
    dirty = git("status", "--porcelain", check=False)

    cur = read_version()
    new = bump_minor(cur)
    today = datetime.date.today().isoformat()

    text = CHANGELOG.read_text(encoding="utf-8")
    text, notes = fold_notes_text(text, new, today, msg)
    CHANGELOG.write_text(text, encoding="utf-8")
    write_version(new)

    git("add", "-A")
    git("-c", "user.name=阿锁", "-c", "user.email=asuo@local", "commit", "-q", "-m", f"v{new}: {msg}")
    git("tag", "-f", "-a", f"v{new}", "-m", msg)
    print(f"已发大版本 v{new}（{cur} → {new}）")
    if notes:
        print(f"  顺带把攒下的 {len(notes)} 条小改说明并进了这一版")
    if not dirty:
        print("  （工作区本来就干净，只动了版本号和 CHANGELOG）")
    print(f"  要退回上一版：python tools/version.py rollback v{cur} --yes")
    return 0


def cmd_note(args) -> int:
    """小改只记说明，**不动版本号**（用户 2026-11-13 定的规矩）。"""
    msg = (args.message or "").strip()
    if not msg:
        raise SystemExit('记一句说明：python tools/version.py note "改了啥"')

    today = datetime.date.today().isoformat()
    text = CHANGELOG.read_text(encoding="utf-8")
    CHANGELOG.write_text(add_note_text(text, msg, today), encoding="utf-8")

    if git("status", "--porcelain", check=False):
        git("add", "-A")
        git("-c", "user.name=阿锁", "-c", "user.email=asuo@local", "commit", "-q", "-m", f"说明: {msg}")
        print(f"已记下：{msg}（版本号仍是 v{read_version()}）")
    else:
        print(f"已记下：{msg}")
    print("  要发大版本时再跑 save，这些说明会自动汇总进那一版。")
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

    ps = sub.add_parser("save", help="发大版本（版本号跳到下一个整版）")
    ps.add_argument("message", nargs="?", help="这一版干了啥")
    ps.set_defaults(func=cmd_save)

    pn = sub.add_parser("note", help="小改只记说明，不动版本号")
    pn.add_argument("message", nargs="?", help="改了啥")
    pn.set_defaults(func=cmd_note)

    pr = sub.add_parser("rollback", help="退回某个版本（只动源码）")
    pr.add_argument("tag", help="版本标签，如 v1.1.0")
    pr.add_argument("--yes", action="store_true", help="确认执行")
    pr.set_defaults(func=cmd_rollback)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
