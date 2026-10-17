# -*- coding: utf-8 -*-
"""部署配置守卫：Dockerfile / .dockerignore / 部署指南。

上线后才发现的配置错误（端口写死、时区丢了、把真实数据打进镜像）代价很高，
所以把这几条钉成测试。

每一条都对应一次**真实会出事**的场景，不是为了凑数。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
IGNORE = ROOT / ".dockerignore"
GUIDE = ROOT / "docs" / "部署-Zeabur.md"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    assert DOCKERFILE.exists(), "Dockerfile 不见了（Zeabur / 任何容器平台都要它）"
    return DOCKERFILE.read_text(encoding="utf-8")


def test_listens_on_platform_injected_port(dockerfile: str) -> None:
    """必须读平台的 `PORT`，且绑 0.0.0.0。

    Zeabur / Render / HF 都是**注入 PORT 环境变量**并只从外部访问；
    写死端口或绑 127.0.0.1 的结果是：容器起来了，但外面连不上（平台报 unhealthy）。
    """
    assert "0.0.0.0" in dockerfile, "没绑 0.0.0.0，容器外访问不到"
    assert "${PORT:-8000}" in dockerfile, "没读平台注入的 PORT，端口会被平台判为不通"
    assert "EXPOSE 8000" in dockerfile


def test_timezone_is_pinned(dockerfile: str) -> None:
    """时区必须钉住 Asia/Shanghai。

    系统全部时间戳都是**本地时间**（naive datetime，数据库不存时区）。
    容器默认 UTC，"今天"会偏 8 小时 —— 观测日期、日报期次、周报统计全错，
    而且这种错**不会报错**，只会让数据慢慢歪掉。
    """
    assert "TZ=Asia/Shanghai" in dockerfile
    assert "tzdata" in dockerfile, "slim 镜像没装 tzdata，TZ 设了也不生效"


def test_data_dirs_point_into_one_volume(dockerfile: str) -> None:
    """所有运行数据目录都指向 /data，这样挂一个卷就全保住。"""
    for var in (
        "MONITOR_DB_URL",
        "MONITOR_TEMPLATES_DIR",
        "MONITOR_BACKUP_DIR",
        "MONITOR_UPLOAD_DIR",
        "MONITOR_LOG_DIR",
    ):
        assert var in dockerfile, f"缺环境变量 {var}，容器里会写到镜像内部、重启即丢"
    assert dockerfile.count("/data/") >= 5
    assert "sqlite:////data/" in dockerfile, "SQLite 路径要四个斜杠（绝对路径 /data/...）"


def test_demo_mode_on_by_default(dockerfile: str) -> None:
    """镜像默认开演示模式：免费平台磁盘是临时的，重启后得自己长回示例数据。"""
    assert "MONITOR_DEMO_SEED=1" in dockerfile


def test_frontend_is_built_in_image(dockerfile: str) -> None:
    """前端必须在构建阶段编译好并放进镜像（否则只有 API，没有页面）。"""
    assert "npm ci" in dockerfile
    assert "npm run build" in dockerfile
    assert "frontend/dist" in dockerfile


def test_real_data_never_enters_build_context() -> None:
    """真实的监测数据、母版、日志绝不能进镜像 / 传到构建上下文。"""
    text = IGNORE.read_text(encoding="utf-8")
    for d in ("database/", "templates/", "logs/", "导出/"):
        assert d in text, f".dockerignore 没排除 {d}"
    assert "node_modules/" in text


def test_deploy_guide_exists_and_names_the_volume_path() -> None:
    """部署指南必须在，且写清卷挂到 /data（挂错路径 = 数据还是丢）。"""
    assert GUIDE.exists(), "部署指南不见了"
    text = GUIDE.read_text(encoding="utf-8")
    assert "/data" in text
    assert "MONITOR_DEMO_SEED" in text


# ─────────────────────── Render 免费档（2026-10 起的主推方案）───────────────────────


def test_render_blueprint_matches_the_image() -> None:
    """`render.yaml` 必须指向 Dockerfile 本身，而不是自己再描述一遍怎么装。

    重复描述构建步骤是这类蓝图的头号坑：Dockerfile 改了、yaml 没改，
    线上跑的还是老逻辑，而且看日志也看不出来。
    """
    yml = ROOT / "render.yaml"
    assert yml.exists(), "render.yaml 不见了（Render 一键部署要靠它）"
    text = yml.read_text(encoding="utf-8")

    assert "runtime: docker" in text, "不是 Docker 运行时，会去猜语言"
    assert "dockerfilePath: ./Dockerfile" in text, "没指向仓库根目录的 Dockerfile"
    assert "plan: free" in text, "计划不是 free —— 用户要的就是免费"
    assert "healthCheckPath: /api/health" in text, "没配健康检查，冷启动会被平台判失败"


def test_render_blueprint_sets_the_two_required_env() -> None:
    """演示模式与时区必须显式写进蓝图（这是部署后最容易忘的两项）。"""
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "MONITOR_DEMO_SEED" in text, "没开演示模式 → 重启后站点变空壳"
    assert "Asia/Shanghai" in text, "没钉时区 → 日期整体偏 8 小时且不报错"


def test_render_guide_documents_free_tier_limits() -> None:
    """指南必须写明免费档的三个限制，否则用户上线后才发现，会以为系统坏了。"""
    guide = ROOT / "docs" / "部署-Render.md"
    assert guide.exists(), "Render 部署指南不见了"
    text = guide.read_text(encoding="utf-8")
    for must in ("512", "休眠", "持久磁盘"):
        assert must in text, f"指南没写清「{must}」这一条限制"
