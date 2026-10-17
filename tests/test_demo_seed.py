# -*- coding: utf-8 -*-
"""演示模式（`MONITOR_DEMO_SEED`）：空库时自动长出一个示例项目。

为什么需要：Zeabur / Hugging Face 这类免费平台的容器磁盘是临时的，
重启就把 database/ 丢了。演示站不能因此变成空壳 —— 启动时发现库是空的，
就自己建一个示例项目，别人随时点开都能看到完整流程。

两条判据必须同时成立，缺一个都会出问题：
  ① **开关没开就不许建** —— 本地正常使用时绝不能凭空多出一个项目；
  ② **库里有东西就不许再建** —— 挂了持久卷的部署重启后不能每次多长一个。
"""
from __future__ import annotations

import pytest

from backend.main import _seed_demo_if_empty


def test_demo_seed_off_by_default(client, monkeypatch) -> None:
    """没设环境变量 → 什么都不做（本地用户绝不能被塞一个示例项目）。"""
    monkeypatch.delenv("MONITOR_DEMO_SEED", raising=False)
    _seed_demo_if_empty()
    assert client.get("/api/v1/projects").json() == []


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_demo_seed_accepts_common_truthy_values(client, monkeypatch, value: str) -> None:
    """`1/true/yes/on` 都算开 —— 部署时手写环境变量很容易写成这些。"""
    monkeypatch.setenv("MONITOR_DEMO_SEED", value)
    _seed_demo_if_empty()
    projects = client.get("/api/v1/projects").json()
    assert len(projects) == 1, f"MONITOR_DEMO_SEED={value} 没触发建示例项目"
    assert projects[0]["code"].startswith("DEMO-")


def test_demo_seed_only_when_library_is_empty(client, monkeypatch) -> None:
    """已经有项目 → 不再建第二个（否则每次重启都多一个，列表越堆越长）。"""
    monkeypatch.setenv("MONITOR_DEMO_SEED", "1")

    _seed_demo_if_empty()
    first = client.get("/api/v1/projects").json()
    assert len(first) == 1

    _seed_demo_if_empty()
    _seed_demo_if_empty()
    assert len(client.get("/api/v1/projects").json()) == 1, "重复调用又建了项目"


def test_demo_project_has_real_data(client, monkeypatch) -> None:
    """示例项目必须**真有数据** —— 只有空项目壳子的话，曲线和日报还是空的。"""
    monkeypatch.setenv("MONITOR_DEMO_SEED", "1")
    _seed_demo_if_empty()

    pid = client.get("/api/v1/projects").json()[0]["id"]
    counts = client.get(f"/api/v1/projects/{pid}/data-count").json()
    assert counts["points"] == 8
    assert counts["records"] > 0

    # 有数据才导得出日报（演示站最值钱的就是能完整走一遍导出）
    dates = client.get(f"/api/v1/projects/{pid}/record-dates").json()
    assert dates.get("dates_with_values"), "示例项目没有可用观测日期"


def test_demo_seed_never_crashes_startup(client, monkeypatch) -> None:
    """建示例数据失败也不能把服务带崩 —— 用户至少还能打开界面自己建项目。

    这里用一个必然失败的场景：把项目编号规则搞崩不现实，
    改为直接验证函数对异常是吞掉的（它内部有 try/except，这里验证不抛）。
    """
    monkeypatch.setenv("MONITOR_DEMO_SEED", "1")
    monkeypatch.setattr(
        "backend.services.sample_project_service.create_sample_project",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("故意炸")),
    )
    _seed_demo_if_empty()  # 不抛就算通过
