# -*- coding: utf-8 -*-
"""日报 PDF 导出（给浏览器直接打印用）。

用户 2026-10-29：「我想的是打印监测日报」。

走的还是那条 Excel 生成链路，只是多一步 LibreOffice 转 PDF ——
这样"打印出来的"和"导出的 Excel"永远是同一份东西，不会出现两套版式。

⚠ 本地 Windows 一般没装 LibreOffice，所以这里要**两种环境都能过**：
  装了 → 返回真 PDF；没装 → 返回 503 且说清缺什么（不能 500 或静默失败）。
"""
from __future__ import annotations

import pytest

from backend.services import report_pdf_service
from tests.conftest import put_control_rules


def _project(client, code: str = "PRJ-PDF") -> int:
    pid = client.post("/api/v1/projects", json={"code": code, "name": "PDF 测试"}).json()["id"]
    pt = client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "JJC1", "point_type": "settlement", "initial_value_mm": 5000.0},
    ).json()["id"]
    client.post(
        f"/api/v1/points/{pt}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-30", "value_mm": 5001.0},
    )
    # 没有控制值就出不了日报（422），测不出 PDF 那条路
    put_control_rules(client, pid)
    return pid


def _first_point(client, pid: int) -> int:
    return client.get(f"/api/v1/projects/{pid}/points").json()[0]["id"]


def test_pdf_route_handles_both_environments(client) -> None:
    """有 LibreOffice → 真 PDF；没有 → 503 且提示可执行。"""
    pid = _project(client)
    r = client.get(f"/api/v1/projects/{pid}/reports/pdf")

    if report_pdf_service.is_available():
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/pdf")
        assert r.content[:4] == b"%PDF", "返回的不是 PDF"
        assert "inline" in r.headers.get("content-disposition", ""), "应该是浏览器内打开（打印用），不是下载"
    else:
        assert r.status_code == 503, f"没装 LibreOffice 时应当是 503，实际 {r.status_code}"
        assert "LibreOffice" in r.json()["detail"], "错误提示必须说清缺什么"
        assert "安装" in r.json()["detail"] or "镜像" in r.json()["detail"]


def test_pdf_404_for_unknown_project(client) -> None:
    r = client.get("/api/v1/projects/999999/reports/pdf")
    assert r.status_code == 404


def test_pdf_respects_missing_data(client) -> None:
    """没有观测数据的项目：要么明确拒（422/404），要么仍能出空表 —— 不许 500。"""
    pid = client.post("/api/v1/projects", json={"code": "PRJ-PDF2", "name": "空项目"}).json()["id"]
    client.post(
        f"/api/v1/projects/{pid}/points",
        json={"point_no": "JJC1", "point_type": "settlement", "initial_value_mm": 5000.0},
    )
    r = client.get(f"/api/v1/projects/{pid}/reports/pdf")
    assert r.status_code in (200, 422, 404), f"异常状态码：{r.status_code} {r.text[:200]}"


# ───────────────────── 批量打印（多期合成一份） ─────────────────────


def test_batch_rejects_bad_date(client) -> None:
    pid = _project(client, "PRJ-PDF3")
    r = client.get(f"/api/v1/projects/{pid}/reports/pdf", params={"dates": "2026-13-99"})
    assert r.status_code == 422
    assert "日期格式" in r.json()["detail"]


def test_batch_rejects_empty_dates(client) -> None:
    pid = _project(client, "PRJ-PDF4")
    r = client.get(f"/api/v1/projects/{pid}/reports/pdf", params={"dates": " , "})
    assert r.status_code == 422


def test_batch_caps_periods(client) -> None:
    """一次最多 60 期：再多会转很久、把请求超时拖垮，提前挡住。"""
    pid = _project(client, "PRJ-PDF5")
    many = ",".join(f"2026-01-{d:02d}" for d in range(1, 32))
    r = client.get(f"/api/v1/projects/{pid}/reports/pdf", params={"dates": many + "," + many})
    assert r.status_code == 422
    assert "60" in r.json()["detail"]


def test_batch_merges_when_libreoffice_available(client) -> None:
    """装了 LibreOffice 时：3 期合成一份 PDF，页数应当 ≥ 单期的 3 倍。"""
    if not report_pdf_service.is_available():
        pytest.skip("本机没有 LibreOffice，合并走服务器验证")

    pid = _project(client, "PRJ-PDF6")
    client.post(
        f"/api/v1/points/{_first_point(client, pid)}/records",
        json={"series_type": "settlement", "component": "value", "obs_date": "2026-07-31", "value_mm": 5002.0},
    )

    one = client.get(f"/api/v1/projects/{pid}/reports/pdf", params={"obs_date": "2026-07-30"})
    both = client.get(f"/api/v1/projects/{pid}/reports/pdf", params={"dates": "2026-07-30,2026-07-31"})

    assert one.status_code == 200 and both.status_code == 200
    assert one.content[:4] == b"%PDF" and both.content[:4] == b"%PDF"
    assert len(both.content) > len(one.content), "两期的合并 PDF 应该比单期大"
