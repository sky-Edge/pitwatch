# -*- coding: utf-8 -*-
"""数据库引擎与会话。

SQLite 采用 WAL 模式 + 外键约束；
业务层只通过 Session 访问数据库，禁止在引擎层写业务逻辑。
"""
import sys
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import settings


def _make_engine(url: str):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表 + 极简迁移（V1 用 create_all；后续升级为 Alembic 迁移）。"""
    from backend.models import Base  # noqa: F401  确保所有模型已注册

    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)
    migrate_template_library()
    migrate_warning_rule_sets()
    migrate_rule_delta_limit()


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def harden_console() -> None:
    """把标准输出的编码失败策略改成 `replace` —— **一次兜住所有 print/日志**。

    只改 `errors`、**不动 `encoding`**：日志文件仍是原来的 GBK，用户照旧用记事本打开；
    区别只是遇到 GBK 编不出的字符（↔ ⚠ ✓ 这类）写成 `?`，而不是抛异常把进程带走。

    ⚠ 不这么做的后果见 `_safe_print` 里记的那次事故（后端启动失败）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 —— 某些被重定向的对象没有 reconfigure，忽略即可
            pass


def _safe_print(text: str) -> None:
    """在启动路径上打印一行 —— **绝不因为"打不出来"而中断启动**。

    ⚠ 2026-10-09 真实事故：`启动系统.bat` 的控制台是 **GBK**，
    而迁移里 `print("…模板↔项目…")` 的 `↔`(U+2194) 与 `⚠`(U+26A0) **GBK 编不出来**，
    `print` 直接抛 `UnicodeEncodeError` → `init_db()` 失败 → **整个后端起不来**，
    黑窗口一片乱码。而 pytest 捕获 stdout（不是 GBK）所以测试全绿 —— 这类坑只有真机才炸。

    两道保险：① 编不出的字符降级成 `?` 再打；② 万一连降级都失败，就当没这行日志。
    """
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(text.encode(enc, errors="replace").decode(enc, errors="replace"))
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def migrate_warning_rule_sets() -> dict:
    """预警规则独立化迁移（幂等，可重复执行）。

    背景：规则原先"挂在项目下"（`warning_rules.project_id`），实测生产库 8 个项目
    各抄了同一套数（32 行），另有 4 个项目一条规则都没有 → 根本不判预警。
    用户 2026-10-09：「把预警规则提出来 直接作用于系统 用预警规则来选择项目」。

    四步：
    1. 给 `warning_rules` 加 `set_id`（`create_all` 不会给已有表加列）；
    2. 按项目把现有规则分组成套：`warning_rule_sets` + 回填 `set_id` + 建项目绑定；
    3. **数值完全相同的项目自动合并成一套**（source_note 也相同才算）——
       实测正是这种情况（6 个项目同一套数），合并后改一次全体跟随；
    4. 老 `project_id` 置 NULL（废弃字段，保留只为代码回退时行为不变）。

    ⚠ 只加表加列 + 回填归属，**不改任何限值数字、不碰观测数据**。
    迁移前后每个项目的**生效规则必须逐字一致**（有守卫测试）。
    """
    if not settings.database_url.startswith("sqlite"):
        return {"skipped": "非 SQLite 数据库，跳过"}

    added_column = False
    created_sets: list[str] = []
    bound = 0
    merged_groups = 0

    with engine.begin() as conn:
        tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "warning_rules" not in tables:
            return {"skipped": "尚无 warning_rules 表"}

        if not _column_exists(conn, "warning_rules", "set_id"):
            # 纯 INTEGER（不写 REFERENCES）：SQLite 的 ADD COLUMN 加外键约束限制多，
            # 这里以"迁移绝不失败"优先；新建的库走 create_all，约束齐全。
            conn.exec_driver_sql("ALTER TABLE warning_rules ADD COLUMN set_id INTEGER")
            added_column = True
        if "warning_rule_sets" not in tables:
            return {"skipped": "warning_rule_sets 建表未完成"}

        # 还没归套的项目级规则
        rows = conn.exec_driver_sql(
            "SELECT id, project_id, indicator, level, threshold_cumulative_mm, "
            "threshold_rate_mm_per_day, is_active, source_note "
            "FROM warning_rules WHERE set_id IS NULL AND project_id IS NOT NULL "
            "ORDER BY project_id, indicator, level"
        ).fetchall()
        if not rows:
            return {"added_column": added_column, "created_sets": [], "bound_projects": 0}

        names = {
            int(i): str(n)
            for i, n in conn.exec_driver_sql("SELECT id, name FROM projects").fetchall()
        }
        existing_names = {
            str(r[0]) for r in conn.exec_driver_sql("SELECT name FROM warning_rule_sets").fetchall()
        }

        # 按 项目 → 该项目的规则指纹
        by_project: dict[int, list[tuple]] = {}
        for r in rows:
            by_project.setdefault(int(r[1]), []).append(r)
        fingerprints: dict[tuple, list[int]] = {}
        for pid, items in by_project.items():
            fp = (
                tuple(sorted(
                    (r[2], r[3], r[4], r[5], bool(r[6])) for r in items
                )),
                (items[0][7] or ""),
            )
            fingerprints.setdefault(fp, []).append(pid)

        merged_groups = sum(1 for pids in fingerprints.values() if len(pids) > 1)
        # 组大的先建：这样"来源说明"这个名字会落在**多数项目共用的那套**上，
        # 而不是被最先遍历到的那套小规则抢走（实测正是 6 个项目共用一套的情况）。
        ordered = sorted(fingerprints.items(), key=lambda kv: (-len(kv[1]), kv[1][0]))
        for fp, pids in ordered:
            pids = sorted(pids)
            note = fp[1] or None
            # 名字：优先用来源说明；重名（或已被别的套占用）时用"<项目名> 的预警规则"区分
            name = note or f"{names.get(pids[0], '项目' + str(pids[0]))} 的预警规则"
            if name in existing_names:
                name = f"{names.get(pids[0], '项目' + str(pids[0]))} 的预警规则"
                suffix = 2
                base = name
                while name in existing_names:
                    name = f"{base}（{suffix}）"
                    suffix += 1
            existing_names.add(name)

            cur = conn.exec_driver_sql(
                "INSERT INTO warning_rule_sets (name, source_note, is_global) VALUES (?, ?, 0)",
                (name, note),
            )
            set_id = int(cur.lastrowid)
            created_sets.append(name)
            for i, pid in enumerate(pids):
                if i == 0:
                    # 第一家的规则行**直接成为这套规则**（数值=该组指纹，组内各家完全相同）
                    conn.exec_driver_sql(
                        "UPDATE warning_rules SET set_id = ?, project_id = NULL "
                        "WHERE project_id = ? AND set_id IS NULL",
                        (set_id, pid),
                    )
                else:
                    # 其余各家数值与第一家完全相同 → 删掉自己的行，共用第一家的那套。
                    # ⚠ 不能只改 set_id：那样一套规则里会堆进 N×4 行重复（界面会显示一堆重复项）。
                    conn.exec_driver_sql(
                        "DELETE FROM warning_rules WHERE project_id = ? AND set_id IS NULL",
                        (pid,),
                    )
                conn.exec_driver_sql(
                    "INSERT OR IGNORE INTO warning_rule_set_projects (set_id, project_id) "
                    "VALUES (?, ?)",
                    (set_id, pid),
                )
                bound += 1

    if created_sets:
        _safe_print(
            f"[migrate] 预警规则独立化：建了 {len(created_sets)} 套规则、绑定 {bound} 个项目"
            + (f"（其中 {merged_groups} 组数值相同、已合并成一套）" if merged_groups else "")
        )
        for n in created_sets:
            _safe_print(f"[migrate]   - {n}")
    return {
        "added_column": added_column,
        "created_sets": created_sets,
        "bound_projects": bound,
        "merged_groups": merged_groups,
    }


def migrate_rule_delta_limit() -> dict:
    """给 `warning_rules` 加「本次变化量 ΔV 限值」列（幂等）。

    背景：原先每个指标只有**累计**和**速率**两维参与判定，ΔV（这一期跳了多少）
    只算不判。用户 2026-10-16 指出他想要的是"控制系统每期变化的上限" —— 隔 3 天
    测一次时速率会被摊薄，ΔV 才是那道卡住的闸。

    ⚠ 只加列、**不回填任何数字**：老规则这一维是 NULL = 不参与判定，
    行为与加列前逐字一致（绝不替项目编一个限值，见 `warning_service.rule_limits`）。
    """
    if not settings.database_url.startswith("sqlite"):
        return {"skipped": "非 SQLite 数据库，跳过"}

    added = False
    with engine.begin() as conn:
        tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "warning_rules" not in tables:
            return {"skipped": "尚无 warning_rules 表"}
        if not _column_exists(conn, "warning_rules", "threshold_delta_mm"):
            conn.exec_driver_sql("ALTER TABLE warning_rules ADD COLUMN threshold_delta_mm FLOAT")
            added = True

    if added:
        _safe_print("[migrate] 预警规则加了「本次变化量」限值列（老规则该维留空=不判）")
    return {"added_column": added}


def migrate_template_library() -> dict:
    """模板独立化迁移（幂等，可重复执行）。

    背景：模板原先是"挂在项目下"的（`export_templates.project_id`），
    用户 2026-10-08 提出「每个项目单独维护一个模板太繁琐」→ 改成模板自己声明适用项目。

    三步，都可重复执行：
    1. 建关联表 `template_projects`（`create_all` 已做，这里兜底）；
    2. 给 `export_templates` 加 `is_global` —— **`create_all` 不会给已有表加列**，
       本项目又没有 Alembic，所以必须自己 PRAGMA 判断 + ALTER TABLE；
    3. 回填：把老的 `project_id` 写成一条关联（`INSERT OR IGNORE`，重复执行不会重复插）。

    ⚠ 只加表加列、**不搬任何模板文件、不碰任何观测数据** —— 想回退，忽略新表新列即可。
    """
    if not settings.database_url.startswith("sqlite"):
        return {"skipped": "非 SQLite 数据库，跳过"}

    added_column = False
    backfilled = 0
    with engine.begin() as conn:
        tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "export_templates" not in tables:
            return {"skipped": "尚无 export_templates 表"}

        if not _column_exists(conn, "export_templates", "is_global"):
            conn.exec_driver_sql(
                "ALTER TABLE export_templates ADD COLUMN is_global BOOLEAN NOT NULL DEFAULT 0"
            )
            added_column = True

        if "template_projects" in tables:
            before = conn.exec_driver_sql("SELECT COUNT(*) FROM template_projects").scalar() or 0
            conn.exec_driver_sql(
                """
                INSERT OR IGNORE INTO template_projects (template_id, project_id)
                SELECT id, project_id FROM export_templates
                WHERE project_id IS NOT NULL AND is_builtin = 0
                """
            )
            after = conn.exec_driver_sql("SELECT COUNT(*) FROM template_projects").scalar() or 0
            backfilled = int(after) - int(before)

        # 迁移体检：模板名从"项目内唯一"变成"全局唯一"，先把历史遗留的重名报出来
        dups = conn.exec_driver_sql(
            """
            SELECT name, COUNT(*) c FROM export_templates
            WHERE is_builtin = 0 GROUP BY name HAVING c > 1
            """
        ).fetchall()

    if added_column or backfilled:
        _safe_print(
            f"[migrate] 模板库迁移完成：{'新增 is_global 列；' if added_column else ''}"
            f"回填 {backfilled} 条 模板-项目 关联"
        )
    if dups:
        names = "、".join(str(r[0]) for r in dups)
        _safe_print(f"[migrate] [注意] 存在重名模板（{names}）—— 导出页下拉里会出现同名项，建议改名区分")
    return {"added_column": added_column, "backfilled": backfilled, "duplicate_names": [str(r[0]) for r in dups]}
