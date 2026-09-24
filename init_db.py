"""建库 + 灌入虚构演示名单（Phase 2 起用正式 schema）。

用法：
    python3 init_db.py --demo                    # 用配置里的数据库路径建库
    python3 init_db.py --demo --config my.toml
    python3 init_db.py --demo --rebuild --yes    # 删掉旧库重建（Phase 1 临时库必须这样处理）

演示导入只接受虚构名单：一旦出现不符合虚构模式的姓名就直接拒绝；
真实成绩走 `hub.py import-scores`（CSV / Excel）。
建库是幂等的：重复执行不会产生重复行，也不会清掉已有数据。

schema 由 schema/*.sql 按固定顺序建好（core → scores → homework → errors → profile → alerts），
`meta.schema_version` 记为 `phase2`；此后的结构变更走 `schema/migrations/`。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping

import config_loader
import db as db_module
import homework as homework_module
import seed_demo_data

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
MIGRATIONS_DIR = SCHEMA_DIR / "migrations"

# 建库顺序固定：后面的文件引用前面文件里的表（外键）。
SCHEMA_FILES: tuple[str, ...] = (
    "core.sql",
    "scores.sql",
    "homework.sql",
    "errors.sql",
    "profile.sql",
    "alerts.sql",
)

# 每个文件必须建出的表：按完整清单校验，避免残缺 schema 被当成建好了。
EXPECTED_TABLES_BY_FILE: Mapping[str, tuple[str, ...]] = {
    "core.sql": ("meta", "schema_migrations", "classes", "students"),
    "scores.sql": ("exams", "exam_scores", "exam_items", "item_scores"),
    "homework.sql": ("homework_assignments", "homework_submissions", "corrections"),
    "errors.sql": ("error_tags", "error_records", "behavior_records"),
    "profile.sql": ("ability_scores",),
    "alerts.sql": ("alerts", "follow_ups"),
}

SCHEMA_VERSION = db_module.SCHEMA_VERSION
PHASE1_SCHEMA_VERSION = db_module.PHASE1_SCHEMA_VERSION


def connect(db_path: Path) -> sqlite3.Connection:
    """兼容入口：Phase 1 的脚本与测试从这里拿连接，实际走 db.connect。"""
    return db_module.connect(db_path)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return db_module.table_exists(conn, name)


def schema_version(conn: sqlite3.Connection) -> str | None:
    return db_module.schema_version(conn)


def apply_schema(conn: sqlite3.Connection) -> None:
    """按固定顺序执行建表脚本，逐文件校验表是否建全，最后写入 schema 版本。"""
    for name in SCHEMA_FILES:
        path = SCHEMA_DIR / name
        conn.executescript(path.read_text(encoding="utf-8"))
        missing = [
            table for table in EXPECTED_TABLES_BY_FILE[name] if not table_exists(conn, table)
        ]
        if missing:
            raise config_loader.ConfigError(
                f"{name} 没建全，缺少这些表：{'、'.join(missing)}；请检查 schema 文件是否被改动过。"
            )

    with conn:
        conn.execute(
            """
            INSERT INTO meta (key, value) VALUES ('schema_version', ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (SCHEMA_VERSION,),
        )


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    """执行 schema/migrations/ 里还没应用过的迁移，返回本次应用的迁移名。

    注意：脚本执行（executescript 会隐式提交）与写 schema_migrations 记录不在同一事务里，
    所以每个迁移文件都必须**幂等**——万一记录没写上，下次重放也不能出问题。
    """
    applied = {
        str(row["name"]) for row in conn.execute("SELECT name FROM schema_migrations")
    }
    done: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        with conn:
            conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (path.name,))
        done.append(path.name)
    return done


def remove_database(database: Path) -> list[Path]:
    """删掉数据库文件及其 WAL/SHM 附属文件；返回实际删掉的路径。"""
    removed: list[Path] = []
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{database}{suffix}")
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def import_roster(conn: sqlite3.Connection, dataset: Mapping[str, Any]) -> dict[str, int]:
    """把演示名单写进库里；班级按名字、学生按 student_uid 幂等更新。"""
    class_count = 0
    student_count = 0

    with conn:
        for klass in dataset.get("classes") or []:
            class_name = str(klass.get("name") or "").strip()
            if not class_name:
                raise config_loader.ConfigError("演示数据里有空班名，拒绝导入。")
            conn.execute(
                "INSERT INTO classes (name) VALUES (?) ON CONFLICT (name) DO NOTHING",
                (class_name,),
            )
            class_id = conn.execute(
                "SELECT id FROM classes WHERE name = ?", (class_name,)
            ).fetchone()["id"]
            class_count += 1

            for student in klass.get("students") or []:
                conn.execute(
                    """
                    INSERT INTO students (student_uid, name, class_id, seat_no)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (student_uid) DO UPDATE SET
                        name = excluded.name,
                        class_id = excluded.class_id,
                        seat_no = excluded.seat_no
                    """,
                    (
                        str(student.get("id") or ""),
                        str(student.get("name") or ""),
                        class_id,
                        student.get("seat_no"),
                    ),
                )
                student_count += 1

        conn.execute(
            """
            INSERT INTO meta (key, value) VALUES ('demo_seed', ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (str(dataset.get("seed", "")),),
        )
        conn.execute(
            """
            INSERT INTO meta (key, value) VALUES ('dataset_version', ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (str(dataset.get("version", "")),),
        )

    return {"classes": class_count, "students": student_count}


def init_database(
    config: config_loader.AppConfig,
    *,
    rebuild: bool = False,
    confirmed: bool = False,
) -> dict[str, Any]:
    """建库 + 导入演示名单；返回一份摘要，供命令行打印与测试断言。"""
    database = config.paths.database
    database.parent.mkdir(parents=True, exist_ok=True)
    removed: list[Path] = []

    if rebuild:
        if database.exists():
            if not confirmed:
                raise config_loader.ConfigError(
                    f"--rebuild 会删除现有数据库：{database}；确认无误后请再加 --yes。"
                )
            removed = remove_database(database)
    elif database.exists():
        conn = connect(database)
        try:
            existing = schema_version(conn)
        finally:
            conn.close()
        if existing != SCHEMA_VERSION:
            hint = (
                "Phase 1 的临时库不会自动迁移"
                if existing == PHASE1_SCHEMA_VERSION
                else "schema 版本对不上"
            )
            raise config_loader.ConfigError(
                f"{hint}：{database} 当前是 {existing or '未知版本'}，本版本要求 {SCHEMA_VERSION}；"
                "请加 --rebuild --yes 重建（会删掉这个库）。"
            )

    dataset = seed_demo_data.load_or_create_dataset(config)
    seed_demo_data.validate_dataset_for_import(dataset, config)

    conn = connect(database)
    try:
        apply_schema(conn)
        applied_migrations = apply_migrations(conn)
        counts = import_roster(conn, dataset)
        homework_counts = homework_module.import_demo_homework(conn, dataset)
    finally:
        conn.close()

    return {
        "database": database,
        "schema_version": SCHEMA_VERSION,
        "classes": counts["classes"],
        "students": counts["students"],
        "homework": homework_counts,
        "dataset": config.demo.output,
        "migrations": applied_migrations,
        "removed": removed,
    }


def main(argv: list[str] | None = None) -> int:
    """兼容垫片：等价于 `python3 hub.py init-db --demo`。"""
    parser = argparse.ArgumentParser(
        description="建库并灌入虚构演示名单（兼容 Phase 1 调用方式）"
    )
    parser.add_argument("--config", default=None, help="配置文件；省略时自动找")
    parser.add_argument("--db", default=None, help="覆盖数据库路径")
    parser.add_argument("--demo", action="store_true", help="导入虚构演示名单")
    parser.add_argument("--rebuild", action="store_true", help="删掉旧库重建")
    parser.add_argument("--yes", action="store_true", help="确认 --rebuild 的删除动作")
    args = parser.parse_args(argv)

    import hub

    forwarded: list[str] = []
    if args.config:
        forwarded += ["--config", args.config]
    if args.db:
        forwarded += ["--db", args.db]
    forwarded += ["init-db"]
    if args.demo:
        forwarded += ["--demo"]
    if args.rebuild:
        forwarded += ["--rebuild"]
    if args.yes:
        forwarded += ["--yes"]
    return hub.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
