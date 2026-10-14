# -*- coding: utf-8 -*-
"""批量导入八号基坑全部期次到项目（通过真实 HTTP API 走九步流程，逐期上传→确认）。

用法：
    python tools/bulk_import.py <项目ID>                        # 用默认台账目录
    python tools/bulk_import.py <项目ID> --dir "D:\\某目录"
    python tools/bulk_import.py <项目ID> --base http://127.0.0.1:8000/api/v1

默认台账目录可用环境变量 MONITOR_LEDGER_DIR 覆盖，避免写死本机绝对路径。
"""
import argparse
import glob
import os
import re
import sys

import requests

DEFAULT_LEDGER_DIR = os.environ.get(
    "MONITOR_LEDGER_DIR",
    r"D:\文件\测量台账\7、测量资料\基坑观测\八号（已填）",
)
DEFAULT_BASE = os.environ.get("MONITOR_API_BASE", "http://127.0.0.1:8000/api/v1")


def sort_key(p):
    m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", p)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def main():
    ap = argparse.ArgumentParser(description="批量导入基坑观测 Excel（走九步导入流程）")
    ap.add_argument("project_id", nargs="?", type=int, default=2, help="目标项目 ID（默认 2）")
    ap.add_argument("--dir", dest="dir_", default=None, help="台账目录（默认 DEFAULT_LEDGER_DIR）")
    ap.add_argument("--base", dest="base", default=None, help="后端 API 基址（默认 DEFAULT_BASE）")
    args = ap.parse_args()

    project_id = args.project_id
    base = (args.base or DEFAULT_BASE).rstrip("/")
    dir_ = args.dir_ or DEFAULT_LEDGER_DIR

    if not os.path.isdir(dir_):
        print("[错误] 台账目录不存在：%s" % dir_)
        print("       请用 --dir 指定目录，或设置环境变量 MONITOR_LEDGER_DIR。")
        sys.exit(2)

    files = sorted(glob.glob(os.path.join(dir_, "*.xlsx")), key=sort_key)
    print(f"共 {len(files)} 个文件，目标项目 {project_id}，接口 {base}")
    total_imported = 0
    total_conflict = 0
    total_error = 0
    for f in files:
        name = os.path.basename(f)
        # Step 1-7：上传 + 预览
        with open(f, "rb") as fp:
            resp = requests.post(
                f"{base}/projects/{project_id}/import/upload",
                files={"file": (name, fp, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                timeout=60,
            )
        if resp.status_code != 200:
            print(f"× {name}: 上传失败 {resp.status_code} {resp.text[:120]}")
            total_error += 1
            continue
        data = resp.json()
        batch_id = data["batch_id"]
        n_valid = len(data["valid_rows"])
        n_conflict = len(data["conflict_rows"])
        n_error = len(data["error_rows"])
        # Step 8-9：确认导入（只导入有效行）
        conf = requests.post(
            f"{base}/import/batches/{batch_id}/confirm",
            json={"valid_rows": data["valid_rows"]},
            timeout=60,
        )
        if conf.status_code != 200:
            print(f"× {name}: 确认失败 {conf.status_code} {conf.text[:120]}")
            total_error += 1
            continue
        imported = conf.json()["imported_rows"]
        total_imported += imported
        total_conflict += n_conflict
        total_error += n_error
        flag = "√" if n_error == 0 else "△"
        print(f"{flag} {name}: 导入 {imported} 条 (冲突 {n_conflict} / 错误 {n_error})")
    print("=" * 60)
    print(f"汇总：导入 {total_imported} 条 | 冲突跳过 {total_conflict} 条 | 错误 {total_error} 条")


if __name__ == "__main__":
    main()
