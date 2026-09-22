"""建库 + 灌入虚构演示名单（Phase 1 最小闭环第一步）。

用法：
    python3 init_db.py --demo                 # 用配置里的数据库路径建库
    python3 init_db.py --demo --config my.toml

Phase 1 只支持虚构演示数据：名单里一旦出现不符合虚构模式的姓名，直接拒绝导入。
建库是幂等的：重复执行不会产生重复行，也不会清掉已有数据。

数据库 schema 是**临时的**（schema/phase1_schema.sql，meta.schema_version='phase1-temp'），
Phase 2 会替换或扩展，表名与字段不承诺兼容。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Mapping

import config_loader
import seed_demo_data

SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "phase1_schema.sql"
SCHEMA_VERSION = "phase1-temp"
EXPECTED_TABLES = ("meta", "classes", "students", "exams", "exam_scores")


def connect(db_path: Path) -> sqlite3.Connection:
    """打开数据库连接：行按名字取，并显式开启外键（SQLite 默认是关的）。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def apply_schema(conn: sqlite3.Connection) -> None:
    """建表并写入 schema 版本；按「完整表清单」校验，避免残缺 schema 被当成建好了。"""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    missing = [name for name in EXPECTED_TABLES if not table_exists(conn, name)]
    if missing:
        raise config_loader.ConfigError(
            f"建库不完整，缺少这些表：{'、'.join(missing)}；"
            f"请检查 {SCHEMA_PATH.name} 是否被改动过。"
        )

    with conn:
        conn.execute(
            """
            INSERT INTO meta (key, value) VALUES ('schema_version', ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (SCHEMA_VERSION,),
        )


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


def init_database(config: config_loader.AppConfig) -> dict[str, Any]:
    """建库 + 导入演示名单；返回一份摘要，供命令行打印与测试断言。"""
    database = config.paths.database
    database.parent.mkdir(parents=True, exist_ok=True)

    dataset = seed_demo_data.load_or_create_dataset(config)
    seed_demo_data.validate_dataset_for_import(dataset, config)

    conn = connect(database)
    try:
        apply_schema(conn)
        counts = import_roster(conn, dataset)
    finally:
        conn.close()

    return {
        "database": database,
        "schema_version": SCHEMA_VERSION,
        "classes": counts["classes"],
        "students": counts["students"],
        "dataset": config.demo.output,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="建库并灌入虚构演示名单（Phase 1）")
    parser.add_argument("--config", default=None, help="配置文件；省略时自动找")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="导入虚构演示名单（Phase 1 只支持这一种）",
    )
    args = parser.parse_args(argv)

    if not args.demo:
        print(
            "[提示] Phase 1 只支持 --demo：请用 python3 init_db.py --demo 建演示库；"
            "真实成绩导入在 Phase 2。",
            file=sys.stderr,
        )
        return 2

    try:
        config = config_loader.load_config(
            config_loader.resolve_cli_config_path(args.config), env=None
        )
        summary = init_database(config)
    except config_loader.ConfigError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    print(f"已建库：{summary['database']}（schema: {summary['schema_version']}）")
    print(
        f"已导入演示名单：{summary['classes']} 个虚构班级 / "
        f"{summary['students']} 名学生（数据集：{summary['dataset']}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
