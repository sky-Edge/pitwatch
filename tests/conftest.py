# -*- coding: utf-8 -*-
"""pytest 配置：把所有会写盘的目录都指向临时目录，保证测试不碰真实数据。

⚠ 这里隔离的不只是数据库。见下方 `_guard_against_prod_paths` 的事故说明 ——
备份目录与模板目录一旦漏隔离，测试就会删掉用户的真实文件。
"""
import os
import tempfile
from pathlib import Path

# 必须在导入 backend 之前设置环境变量（config 读取一次）
_TMP_DIR = Path(tempfile.mkdtemp(prefix="monitor_test_"))
os.environ["MONITOR_DB_URL"] = f"sqlite:///{_TMP_DIR / 'test.db'}"
# 这两个同样必须隔离：多个测试的清理 fixture 会清空 backup_dir、rmtree templates_dir/<项目>。
# 少隔离一个，测试就会删掉用户的真实备份 / 上传模板。
os.environ["MONITOR_BACKUP_DIR"] = str(_TMP_DIR / "backups")
os.environ["MONITOR_TEMPLATES_DIR"] = str(_TMP_DIR / "templates")
os.environ["MONITOR_UPLOAD_DIR"] = str(_TMP_DIR / "uploads")
# 节假日缓存：测试会往里写"假年份"的缓存。若指向生产目录，这些假数据会留在用户的
# 日期面板里（功能数据被污染比丢个临时文件严重得多）。
os.environ["MONITOR_HOLIDAY_CACHE_DIR"] = str(_TMP_DIR / "holidays")
# 运行日志：测试会故意打 4xx 去验证"错误请求落盘"。指向生产目录就会把测试的
# 假错误混进用户真实日志（而那份日志正是用来排查现场问题的，绝不能被污染）。
os.environ["MONITOR_LOG_DIR"] = str(_TMP_DIR / "logs")
# 测试一律不联网：既不希望测试依赖外网，也不希望每次跑测试都去打第三方接口。
os.environ["MONITOR_OFFLINE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.core.config import BASE_DIR, settings  # noqa: E402
from backend.core.db import init_db  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models import Base  # noqa: E402

# 生产路径（测试绝不允许写到这些位置）
_PROD_DB_FILE = (BASE_DIR / "database" / "monitor.db").resolve()
_PROD_BACKUP_DIR = (BASE_DIR / "database" / "backups").resolve()
_PROD_TEMPLATES_DIR = (BASE_DIR / "templates").resolve()
_PROD_UPLOAD_DIR = (BASE_DIR / "database" / "uploads").resolve()
_PROD_HOLIDAY_CACHE_DIR = (BASE_DIR / "database" / "cache" / "holidays").resolve()


def _sqlite_file(url: str) -> Path | None:
    return Path(url.removeprefix("sqlite:///")).resolve() if url.startswith("sqlite:///") else None


@pytest.fixture(autouse=True)
def _guard_against_prod_paths():
    """铁律：测试不得指向生产数据目录。每个测试前都检查一次。

    为什么需要这条守卫（真实事故，2026-09-26 查明并修复）：

    - `test_backup.py` / `test_hard_delete.py` / `test_hard_delete_chain.py` /
      `test_master_report.py` 的清理 fixture 会执行
      `for f in settings.backup_dir.glob("*.db"): f.unlink()`；
    - `test_hard_delete_template.py` 的清理还会
      `shutil.rmtree(settings.templates_dir / <项目>)`。

    而这两个目录原先**硬编码指向生产目录**，`conftest` 只隔离了数据库。
    结果是：每跑一次完整测试，就删掉用户的真实备份和上传的导出模板。
    实测损失 —— `backup_records` 30 条记录中 29 个备份文件永久丢失；
    8 个用户上传的自定义导出模板文件全部丢失（数据库里仍有记录，界面上会显示但用不了）。

    现在 `conftest` 顶部已通过 `MONITOR_BACKUP_DIR` / `MONITOR_TEMPLATES_DIR`
    把它们指向临时目录，这条断言确保以后不会有人把它改回去。
    """
    assert settings.backup_dir.resolve() != _PROD_BACKUP_DIR, (
        f"备份目录未隔离：{settings.backup_dir} 是生产目录，测试会删掉真实备份"
    )
    assert settings.templates_dir.resolve() != _PROD_TEMPLATES_DIR, (
        f"模板目录未隔离：{settings.templates_dir} 是生产目录，测试会删掉用户上传的模板"
    )
    assert settings.upload_dir.resolve() != _PROD_UPLOAD_DIR, (
        f"上传目录未隔离：{settings.upload_dir} 是生产目录，测试会把假台账混进真实上传目录"
    )
    assert settings.holiday_cache_dir.resolve() != _PROD_HOLIDAY_CACHE_DIR, (
        f"节假日缓存目录未隔离：{settings.holiday_cache_dir} 是生产目录，"
        "测试写的假数据会留在用户的日期面板里"
    )
    db = _sqlite_file(str(settings.database_url))
    assert db != _PROD_DB_FILE, f"数据库未隔离：{db} 是生产库，测试会污染真实数据"
    yield


@pytest.fixture(autouse=True)
def _fresh_db():
    """每个测试前重建所有表，保证隔离。"""
    from backend.core.db import engine

    Base.metadata.drop_all(bind=engine)
    init_db()
    yield


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


#: 默认「控制值」：30/10、20/3 = 报送母版上印着的那两个数。
#: 测试项目配上这一套之后，报表上 I/J 的期望值和"没配规则时照抄母版"时期一样，
#: 既有断言不用改 —— 变的只是**出处**（从母版来的 → 从系统规则来的）。
DEFAULT_CONTROL_RULES = [
    {"indicator": "settlement", "level": "control", "threshold_cumulative_mm": 30.0,
     "threshold_rate_mm_per_day": 10.0, "is_active": True},
    {"indicator": "displacement", "level": "control", "threshold_cumulative_mm": 20.0,
     "threshold_rate_mm_per_day": 3.0, "is_active": True},
]


def put_control_rules(client, pid: int, rules: list[dict] | None = None) -> None:
    """给项目配上「控制值」规则。

    为什么每个要导报表的测试都得配：项目一类观测两个控制值都没配时，
    日报**生成不出来**（422），数据生成同样被拦（用户 2026-10-14 定：
    报表上不许出现系统里查不到的数）。要专门测"被拦住"那条路，就别调用本函数。
    """
    r = client.put(f"/api/v1/projects/{pid}/warning-rules", json={"rules": rules or DEFAULT_CONTROL_RULES})
    assert r.status_code == 200, r.text
