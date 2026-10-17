# -*- coding: utf-8 -*-
"""测试：导出文件检查 —— 把导出去的日报和系统里的数比一遍。

用户 2026-10-12：
    「我想让系统检查我导出的文件…检查主要是 变化速率 是否和系统中的同步、
      变化速率是否超过预警规则」

最要紧的一条：**刚导出的文件必须报"零问题"**。
判据一旦写偏（列读错、精度没对齐、空行当数据），结果页就是满屏假警报，
用户再也不会信这个功能 —— 所以下面每个"造出来的不一致"都必须能被精确抓到，
而"没动过的文件"必须一条问题都没有。
"""
import io
import zipfile
from datetime import date

from openpyxl import load_workbook

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ─────────────────────────── 脚手架 ───────────────────────────


def _mk_project_with_data(client, code: str, name: str, rates=("2026-07-30", "2026-07-31")) -> int:
    """建一个项目：1 个沉降测点 + 2 期读数（差值 1mm/天，速率好算）。"""
    pid = client.post("/api/v1/projects", json={"code": code, "name": name}).json()["id"]
    pt = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "JJC1", "point_type": "settlement", "initial_value_mm": 3000.0},
    ).json()
    for d, v in zip(rates, (3000.0, 2999.0)):
        client.post(
            f"/api/v1/points/{pt['id']}/records",
            json={"series_type": "settlement", "component": "value", "obs_date": d, "value_mm": v},
        )
    return pid


def _filename_of(resp) -> str | None:
    """从响应的 Content-Disposition 里取回文件名（导出时的真实文件名）。"""
    cd = resp.headers.get("content-disposition", "")
    if "filename*=UTF-8''" in cd:
        from urllib.parse import unquote

        return unquote(cd.split("filename*=UTF-8''", 1)[1].strip())
    return None


def _save_export(client, pid: int, obs: str, folder, template_id: int | None = None, name: str | None = None):
    """导出并落盘。默认用**服务端给的真实文件名**（现场就是这么存的）。

    真实文件名形如「乙基坑第3期2026.07.31.xlsx」，里面带着项目名 ——
    有些老母版表内没有「工程名称」格，就是靠文件名认出来的。
    故意改名的文件认不出来时，检查页会明确写"认不出"，而不是悄悄跳过。
    """
    params = {"obs_date": obs}
    if template_id:
        params["template_id"] = template_id
    r = client.get(f"/api/v1/projects/{pid}/reports/excel", params=params)
    assert r.status_code == 200, r.text
    fn = name or _filename_of(r) or f"{pid}_{obs}.xlsx"
    path = folder / fn
    path.write_bytes(r.content)
    return path


def _mk_rule_set(client, name: str, project_ids, cum=30.0, rate=10.0) -> int:
    body = {
        "name": name,
        "project_ids": project_ids,
        "rules": [
            {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": cum,
             "threshold_rate_mm_per_day": rate, "is_active": True},
            {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": cum + 10,
             "threshold_rate_mm_per_day": rate + 2, "is_active": True},
        ],
    }
    r = client.post("/api/v1/warning-rule-sets", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _run(client, folder) -> dict:
    r = client.post("/api/v1/audit/run", json={"folder": str(folder)})
    assert r.status_code == 200, r.text
    return r.json()


def _kinds(res: dict) -> set[str]:
    return {p["kind"] for it in res["items"] for p in it["problems"]}


_STD_HEADER_ROW = 5


def _find_rate_cell(ws, start_row: int = 1):
    """在数据表里定位「变化速率(mm/d)」那一列（表头行可能不是第 5 行）。"""
    for r in range(1, 10):
        cells = [str(ws.cell(row=r, column=c).value or "") for c in range(1, 20)]
        if not any("点号" in t for t in cells):
            continue
        for c in range(1, 20):
            if "速率" in str(ws.cell(row=r, column=c).value or "") and "控制" not in str(
                ws.cell(row=r - 1, column=c).value or ""
            ):
                return r, c
    return None, None


def _tamper_rate(path, delta: float = 9.99) -> str | None:
    """把数据区第一行的「变化速率」改掉 —— 模拟"报表和系统对不上"。"""
    wb = load_workbook(path)
    try:
        for name in wb.sheetnames:
            ws = wb[name]
            header, col = _find_rate_cell(ws)
            if not header or not col:
                continue
            r0 = header + 2
            old = ws.cell(row=r0, column=col).value
            if old is None:
                continue
            ws.cell(row=r0, column=col).value = float(old) + delta
            return f"{name}!{r0} {old} -> {float(old) + delta}"
    finally:
        wb.save(path)
        wb.close()
    return None


# ─────────────────────────── 一、刚导出的文件必须零问题 ───────────────────────────


def test_fresh_export_has_no_problems(client, tmp_path) -> None:
    """刚导出的标准版式日报，检查应当是"全部一致"。"""
    pid = _mk_project_with_data(client, "PRJ-A", "甲基坑")
    _mk_rule_set(client, "甲规则", [pid])
    _save_export(client, pid, "2026-07-31", tmp_path)

    res = _run(client, tmp_path)
    assert res["checked"] == 1, res["skipped"]
    assert res["rows_total"] >= 1
    assert res["problem_rows"] == 0, res["items"]
    assert res["skipped"] == []


def test_fresh_export_with_custom_master_has_no_problems(client, tmp_path) -> None:
    """母版套打那条路也一样 —— 两条导出路径的写法不同，检查必须都读对。"""
    from tests.test_template_library import _tpl_bytes, _upload

    pid = _mk_project_with_data(client, "PRJ-B", "乙基坑")
    _mk_rule_set(client, "乙规则", [pid])
    up = _upload(client, pid, "乙母版", scope=[pid], via_library=True)
    assert up.status_code in (200, 201), up.text
    tid = up.json()["id"]
    _save_export(client, pid, "2026-07-31", tmp_path, tid)

    res = _run(client, tmp_path)
    assert res["checked"] == 1, res["skipped"]
    assert res["problem_rows"] == 0, res["items"]


def test_repeated_export_is_not_double_counted(client, tmp_path) -> None:
    """同一期导了两遍（文件名不同）→ 两份都要比，但系统值只算一次、结果一致。"""
    pid = _mk_project_with_data(client, "PRJ-C", "丙基坑")
    _mk_rule_set(client, "丙规则", [pid])
    a = _save_export(client, pid, "2026-07-31", tmp_path)
    (tmp_path / "丙基坑_再导一份.xlsx").write_bytes(a.read_bytes())

    res = _run(client, tmp_path)
    assert res["checked"] == 2
    assert res["problem_rows"] == 0, res["items"]


# ─────────────────────────── 二、造出来的问题必须抓到 ───────────────────────────


def test_tampered_rate_is_caught(client, tmp_path) -> None:
    """报表被人改过（或系统改过之后报表没重导）→ 报「变化速率和系统对不上」。"""
    pid = _mk_project_with_data(client, "PRJ-D", "丁基坑")
    _mk_rule_set(client, "丁规则", [pid])
    f = _save_export(client, pid, "2026-07-31", tmp_path)
    assert _tamper_rate(f), "没找到可篡改的速率单元格"

    res = _run(client, tmp_path)
    assert res["problem_rows"] >= 1
    assert "rate" in _kinds(res)
    it = next(i for i in res["items"] if any(p["kind"] == "rate" for p in i["problems"]))
    assert it["report"]["rate"] != it["system"]["rate"], it


def test_system_corrected_after_export_is_caught(client, tmp_path) -> None:
    """先导出、后在系统里更正了读数 → 报表就"过期"了，要能查出来。

    这正是用户要的功能的核心场景：翻出一堆旧报表，看哪些已经和系统对不上。
    """
    pid = _mk_project_with_data(client, "PRJ-E", "戊基坑")
    _mk_rule_set(client, "戊规则", [pid])
    _save_export(client, pid, "2026-07-31", tmp_path)

    # 在系统里把 07-31 的读数改掉（走更正流程：旧值 superseded、原值永久保留）
    recs = client.get("/api/v1/points/1/records", params={"series_type": "settlement"}).json()
    target = next(r for r in recs if r["obs_date"] == "2026-07-31" and r["component"] == "value")
    r = client.post(f"/api/v1/records/{target['id']}/correct", json={"value_mm": 2990.0})
    assert r.status_code == 200, r.text

    res = _run(client, tmp_path)
    assert "rate" in _kinds(res) or "cum" in _kinds(res), res["items"]


def test_rate_over_limit_is_caught(client, tmp_path) -> None:
    """限值调小 → 报表上印的速率已经超了，要报「已超限值」。"""
    pid = _mk_project_with_data(client, "PRJ-F", "己基坑")
    _mk_rule_set(client, "己规则", [pid], rate=0.5)  # 实测速率 1.0 > 0.5
    _save_export(client, pid, "2026-07-31", tmp_path)

    res = _run(client, tmp_path)
    assert "rate_over" in _kinds(res), res["items"]
    it = next(i for i in res["items"] if any(p["kind"] == "rate_over" for p in i["problems"]))
    assert it["levels"]["report"] in ("control", "alarm")


def test_level_diff_is_caught_separately(client, tmp_path) -> None:
    """两边判级不一致要**单独列一条**（用户明确要的）。

    造法：报表里的速率被改大（超限），系统里的仍是 1.0（不超）→
    报表判 control/alarm、系统判 normal。
    """
    pid = _mk_project_with_data(client, "PRJ-G", "庚基坑")
    _mk_rule_set(client, "庚规则", [pid], rate=0.5)
    f = _save_export(client, pid, "2026-07-31", tmp_path)
    assert _tamper_rate(f)

    res = _run(client, tmp_path)
    kinds = _kinds(res)
    assert "rate_over" in kinds and "level_diff" in kinds, res["items"]
    it = next(i for i in res["items"] if any(p["kind"] == "level_diff" for p in i["problems"]))
    assert it["levels"]["report"] != it["levels"]["system"], it["levels"]


def test_columns_the_report_does_not_print_are_not_judged() -> None:
    """报表没印「变化速率」这一栏，就不许拿系统那侧的判级报"两边不一致"。

    老式模板（`build_from_template` 那条路）导出的表可能只有累计、没有速率列。
    第一版只判速率时用 `has_col["rate"]` 把整条比对一起挡住了；加上累计这一维之后
    如果只挡报表那侧、系统那侧照取高，结果就是**整表每行**都报"判级不一致"——
    报表那侧永远是 normal，因为那一栏它根本没有。
    这里直接喂 `_check_row`（不绕文件），把两侧**同一口径**这条钉住。
    """
    from backend.reports.export_reader import ReportRow
    from backend.services.export_audit_service import _check_row

    row = ReportRow(point_no="JJC1", component="value", excel_row=7, cum_mm=1.0, delta_mm=1.0)
    sysrow = {"prev_cum_mm": 0.0, "cum_mm": 1.0, "delta_mm": 1.0, "rate": 5.0}
    limits = {"control_cum": 30.0, "control_rate": 0.5, "alarm_cum": 40.0, "alarm_rate": 1.5}
    only_cum = {"point_col": 1, "data_start_row": 7, "prev_cum_mm": 5, "cum_mm": 6, "delta_mm": 7}

    probs, _, _, levels = _check_row(row, sysrow, limits, (30.0, 0.5), only_cum)
    kinds = {p["kind"] for p in probs}
    assert "level_diff" not in kinds and "rate_over" not in kinds, probs
    assert levels == {"report": "normal", "system": "normal"}, levels

    # 同一行，报表**有**速率列但那格是空的 → 系统已经超速率，这回必须报不一致
    with_rate = {**only_cum, "rate": 8}
    probs2, _, _, levels2 = _check_row(row, sysrow, limits, (30.0, 0.5), with_rate)
    assert "level_diff" in {p["kind"] for p in probs2}, probs2
    assert levels2 == {"report": "normal", "system": "alarm"}, levels2


def test_control_value_changed_in_rules_is_caught(client, tmp_path) -> None:
    """规则改了控制值 → 旧报表上印的还是老数，要报「控制值和规则对不上」。"""
    pid = _mk_project_with_data(client, "PRJ-H", "辛基坑")
    sid = _mk_rule_set(client, "辛规则", [pid], cum=30.0, rate=10.0)
    _save_export(client, pid, "2026-07-31", tmp_path)

    # 把规则里的速率控制值从 10 改成 2
    r = client.put(
        f"/api/v1/warning-rule-sets/{sid}",
        json={
            "rules": [
                {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30.0,
                 "threshold_rate_mm_per_day": 2.0, "is_active": True},
                {"indicator": "settlement", "level": "alarm", "threshold_cumulative_mm": 40.0,
                 "threshold_rate_mm_per_day": 12.0, "is_active": True},
            ]
        },
    )
    assert r.status_code == 200, r.text

    res = _run(client, tmp_path)
    assert "control_rate" in _kinds(res), res["items"]


def test_archived_project_files_are_skipped_but_reported(client, tmp_path) -> None:
    """**归档的项目不查**（用户 2026-10-12：「归档的不要检查了」）。

    归档＝这个基坑结束了、不再出报表，它的旧报表再报"对不上"没有意义，
    只会把真要处理的问题淹掉。但**不能静默丢** —— 结果里要写明是哪几个项目、各多少份。
    """
    active = _mk_project_with_data(client, "PRJ-L", "在用基坑")
    gone = _mk_project_with_data(client, "PRJ-M", "已归档基坑")
    _mk_rule_set(client, "L规则", [active])
    _mk_rule_set(client, "M规则", [gone])
    f_active = _save_export(client, active, "2026-07-31", tmp_path)
    f_gone = _save_export(client, gone, "2026-07-31", tmp_path)

    # 归档掉的这份**故意改动**速率：如果它还参与检查，就该报问题
    assert _tamper_rate(f_gone)
    assert client.delete(f"/api/v1/projects/{gone}").status_code == 200

    res = _run(client, tmp_path)
    assert res["scanned"] == 2
    assert res["checked"] == 1, "归档项目的文件不该参与比对"
    assert res["problem_rows"] == 0, res["items"]
    assert res["archived_files"] == 1
    assert res["archived"][0]["project_name"] == "已归档基坑"
    assert res["archived"][0]["files"] == [f_gone.name]
    # 在用项目那份照样比
    assert {p["project_code"] for p in res["projects"]} == {"PRJ-L"}


def test_restored_project_is_checked_again(client, tmp_path) -> None:
    """把项目「恢复在用」之后，它的报表就该重新参与检查（不用改任何设置）。"""
    pid = _mk_project_with_data(client, "PRJ-N", "会恢复的基坑")
    _mk_rule_set(client, "N规则", [pid])
    _save_export(client, pid, "2026-07-31", tmp_path)
    client.delete(f"/api/v1/projects/{pid}")

    assert _run(client, tmp_path)["checked"] == 0
    client.put(f"/api/v1/projects/{pid}", json={"is_active": True})
    assert _run(client, tmp_path)["checked"] == 1


def test_archived_only_folder_says_so(client, tmp_path) -> None:
    """文件夹里全是归档项目的报表 → checked=0 但**不能说成"没问题"**，要说明是跳过了。"""
    pid = _mk_project_with_data(client, "PRJ-O", "全归档基坑")
    _mk_rule_set(client, "O规则", [pid])
    _save_export(client, pid, "2026-07-31", tmp_path)
    client.delete(f"/api/v1/projects/{pid}")

    res = _run(client, tmp_path)
    assert res["checked"] == 0 and res["problem_rows"] == 0
    assert res["archived_files"] == 1  # 界面据此提示"这次没有可查的文件"


# ─────────────────────────── 三、认不出/读不了的文件不能静默丢 ───────────────────────────


def test_unrecognizable_file_is_reported_not_skipped_silently(client, tmp_path) -> None:
    """不是本系统的日报（认不出项目/日期）→ 必须单独列出来说明原因。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "竖向位移"
    ws["A5"] = "监测\n点号"
    ws["E5"] = "上次累计变化量（mm)"
    ws["F5"] = "本次累计变化量（mm)"
    ws["G5"] = "本次变化量（mm)"
    ws["H5"] = "变化速率（mm/d）"
    ws["A7"] = "JJC1"
    wb.save(tmp_path / "来路不明的表.xlsx")
    wb.close()

    pid = _mk_project_with_data(client, "PRJ-I", "壬基坑")
    _mk_rule_set(client, "壬规则", [pid])
    res = _run(client, tmp_path)
    assert res["checked"] == 0
    assert len(res["skipped"]) == 1
    assert "认不出" in res["skipped"][0]["why"]


def test_excel_lock_files_are_ignored(client, tmp_path) -> None:
    """`~$xxx.xlsx`（Excel 打开时的临时文件）不该被当成日报报"读不了"。"""
    pid = _mk_project_with_data(client, "PRJ-J", "癸基坑")
    _mk_rule_set(client, "癸规则", [pid])
    _save_export(client, pid, "2026-07-31", tmp_path)
    (tmp_path / "~$癸.xlsx").write_bytes(b"garbage")
    (tmp_path / ".hidden.xlsx").write_bytes(b"garbage")

    res = _run(client, tmp_path)
    assert res["scanned"] == 1, res["skipped"]
    assert res["checked"] == 1


# ──────────────── 三点五、名字对不上也要认（智能化，但绝不认错） ────────────────

_NAME_LABELS = ("监测工程名称：", "工程名称：", "工点名称：", "基坑名称：", "项目名称：")


def _set_name_cell(path, new_name: str) -> int:
    """把报表里所有「工程名称」都改成另一种写法 —— 模拟"表里的基坑名和系统项目名对不上"。

    ⚠ 必须**每一处都改**：母版里名称会出现在封面 / 签字 / 数据表好几处，
    只改一处的话，剩下那处仍然是完全一致 → 认出来的是老名字，测不到想测的东西。
    """
    wb = load_workbook(path)
    changed = 0
    try:
        for name in wb.sheetnames:
            ws = wb[name]
            for row in ws.iter_rows(min_row=1, max_row=8):
                for cell in row:
                    v = cell.value
                    if not isinstance(v, str):
                        continue
                    for lb in _NAME_LABELS:
                        i = v.find(lb)
                        if i >= 0:
                            cell.value = v[: i + len(lb)] + new_name
                            changed += 1
                            break
        if changed:
            wb.save(path)
    finally:
        wb.close()
    return changed


def test_name_written_differently_is_still_recognized(client, tmp_path) -> None:
    """用户 2026-10-13：「基坑名字和系统中项目名字不一定完全对得上」。

    报表写「嘉善兴善公路工程8号基坑围护结构监测」、系统里叫「八号基坑」——
    必须认出来照常比对（不是"认不出"），但要记一笔让用户确认一眼。
    """
    pid = _mk_project_with_data(client, "PRJ-M", "八号基坑")
    _mk_rule_set(client, "八规则", [pid])
    # 文件名也起个认不出来的：逼着它只能靠表里的名称认
    f = _save_export(client, pid, "2026-07-31", tmp_path, name="未命名报表.xlsx")
    assert _set_name_cell(f, "嘉善兴善公路工程8号基坑围护结构监测") > 0

    res = _run(client, tmp_path)
    assert res["checked"] == 1, res["skipped"]
    assert res["problem_rows"] == 0, res["items"][:2]
    assert res["fuzzy_files"] == 1, res["fuzzy"]
    one = res["fuzzy"][0]
    assert one["project_name"] == "八号基坑"
    assert one["score"] >= 0.72
    assert "8号基坑" in one["text"]


def test_exact_name_is_not_flagged_as_fuzzy(client, tmp_path) -> None:
    """名字完全对得上 → 不该出现在"按相近认的"那一栏里（否则提示就是噪音）。"""
    pid = _mk_project_with_data(client, "PRJ-N", "壬基坑")
    _mk_rule_set(client, "壬规则", [pid])
    _save_export(client, pid, "2026-07-31", tmp_path)

    res = _run(client, tmp_path)
    assert res["checked"] == 1
    assert res["fuzzy_files"] == 0, res["fuzzy"]


def test_unmatched_name_lists_best_candidates(client, tmp_path) -> None:
    """实在认不出 → 别只说一句"认不出"，把最像的摆出来，用户才知道该怎么改。"""
    pid = _mk_project_with_data(client, "PRJ-O", "八号基坑")
    _mk_rule_set(client, "八规则2", [pid])
    f = _save_export(client, pid, "2026-07-31", tmp_path, name="未命名报表2.xlsx")
    assert _set_name_cell(f, "甲基坑") > 0  # 系统里没有这个基坑

    res = _run(client, tmp_path)
    assert res["checked"] == 0
    assert len(res["skipped"]) == 1
    s = res["skipped"][0]
    assert "认不出" in s["why"]
    assert s["candidates"], "认不出时要把最像的候选给出来"
    assert s["candidates"][0]["project_name"] == "八号基坑"
    assert s["candidates"][0]["score"] < 0.72


def test_wrong_number_is_never_matched(client, tmp_path) -> None:
    """编号不同 → 不认。

    报表写着「九号基坑」、系统里只有「八号基坑」时，**不许糊到八号头上** ——
    名字只差一个字，硬认的话会刷出一屏假的"对不上"。
    """
    pid = _mk_project_with_data(client, "PRJ-Q", "八号基坑")
    _mk_rule_set(client, "八规则3", [pid])
    f = _save_export(client, pid, "2026-07-31", tmp_path, name="未命名报表3.xlsx")
    assert _set_name_cell(f, "嘉善兴善公路九号基坑") > 0  # 系统里没有九号

    res = _run(client, tmp_path)
    assert res["checked"] == 0, "九号的报表被糊到八号头上了"
    assert "认不出" in res["skipped"][0]["why"]
    # 认不出也要告诉用户"最像的是谁"，他才知道该去系统里加项目还是改表头
    assert res["skipped"][0]["candidates"][0]["project_name"] == "八号基坑"


# ─────────────────────────── 四、接口与"每周补扫"状态 ───────────────────────────


def test_missing_folder_is_422(client, tmp_path) -> None:
    r = client.post("/api/v1/audit/run", json={"folder": str(tmp_path / "并不存在")})
    assert r.status_code == 422
    assert "打不开" in r.json()["detail"]


def test_no_folder_configured_is_422(client) -> None:
    r = client.post("/api/v1/audit/run", json={})
    assert r.status_code == 422
    assert "文件夹" in r.json()["detail"]


def test_status_and_weekly_reminder_flag(client, tmp_path) -> None:
    """没配文件夹 → 不提醒；配了 → 提醒；查过一次 → 不提醒。"""
    pid = _mk_project_with_data(client, "PRJ-K", "子基坑")
    _mk_rule_set(client, "子规则", [pid])
    _save_export(client, pid, "2026-07-31", tmp_path)

    st = client.get("/api/v1/audit/status").json()
    assert st["folder"] is None and st["due"] is False

    client.put("/api/v1/settings", json={"audit_folder": str(tmp_path)})
    st = client.get("/api/v1/audit/status").json()
    assert st["folder"] == str(tmp_path)
    assert st["due"] is True and st["exists"] is True

    r = client.post("/api/v1/audit/run", json={"folder": str(tmp_path)})
    assert r.status_code == 200
    st = client.get("/api/v1/audit/status").json()
    assert st["due"] is False, "刚查过还在提醒 —— 每周补扫会一直弹"


def test_empty_folder_reports_zero(client, tmp_path) -> None:
    res = _run(client, tmp_path)
    assert res["scanned"] == 0 and res["checked"] == 0 and res["problem_rows"] == 0


def test_fs_dirs_lists_drives_and_subdirs(client, tmp_path) -> None:
    """目录浏览：不给 path 列盘符；给了列子目录；不存在 → 422。"""
    (tmp_path / "子目录A").mkdir()
    (tmp_path / "子目录B").mkdir()
    (tmp_path / "一个文件.txt").write_text("x", encoding="utf-8")

    r = client.get("/api/v1/fs/dirs").json()
    assert r["is_drive_list"] is True
    assert any(d["path"].endswith(":\\") for d in r["dirs"])

    r2 = client.get("/api/v1/fs/dirs", params={"path": str(tmp_path)}).json()
    names = [d["name"] for d in r2["dirs"]]
    assert names == ["子目录A", "子目录B"], names  # 文件不出现在里面
    assert r2["parent"]

    assert client.get("/api/v1/fs/dirs", params={"path": str(tmp_path / "没有这个")}).status_code == 422


def test_audit_feature_is_advertised(client) -> None:
    assert "export_file_audit" in client.get("/api/health").json()["features"]
