"""导入虚构演示成绩（Phase 1 最小闭环第二步）。

用法：
    python3 import_scores.py                # 用配置里的数据库路径
    python3 import_scores.py --config my.toml

前置：先跑 `python3 init_db.py --demo` 建好库。导入是幂等的：考试按 exam_key、
成绩按（考试, 学生）更新，重复执行不会产生重复行；整批写入放在一个事务里，
中途出错整体回滚。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from typing import Any, Mapping

import config_loader
import db as db_module
import init_db
import seed_demo_data


def student_id_for(conn: sqlite3.Connection, student_uid: str) -> int:
    """按 student_uid 取库内学生主键；找不到就报错，不做任何猜测。"""
    row = conn.execute(
        "SELECT id FROM students WHERE student_uid = ?", (student_uid,)
    ).fetchone()
    if row is None:
        raise config_loader.ConfigError(
            f"数据库里没有这个学生：{student_uid}；请先运行 python3 init_db.py --demo。"
        )
    return int(row["id"])


def import_exam_scores(
    conn: sqlite3.Connection,
    dataset: Mapping[str, Any],
) -> dict[str, int]:
    """把演示成绩写进库里；返回考试与成绩条数。"""
    exam_count = 0
    score_count = 0

    with conn:
        for exam in dataset.get("exams") or []:
            exam_key = str(exam.get("key") or "").strip()
            if not exam_key:
                raise config_loader.ConfigError("演示数据里有考试缺少 key，拒绝导入。")
            full_score = exam.get("full_score")
            if full_score is None:
                raise config_loader.ConfigError(f"考试 {exam_key} 缺少满分，拒绝导入。")

            conn.execute(
                """
                INSERT INTO exams (exam_key, name, exam_date, full_score)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (exam_key) DO UPDATE SET
                    name = excluded.name,
                    exam_date = excluded.exam_date,
                    full_score = excluded.full_score
                """,
                (
                    exam_key,
                    str(exam.get("name") or ""),
                    str(exam.get("date") or ""),
                    float(full_score),
                ),
            )
            exam_id = conn.execute(
                "SELECT id FROM exams WHERE exam_key = ?", (exam_key,)
            ).fetchone()["id"]
            exam_count += 1

            for item in exam.get("scores") or []:
                score = item.get("score")
                # 0 分是合法成绩；只有「没有分数」才拒绝。
                if score is None:
                    raise config_loader.ConfigError(
                        f"{exam_key} 里有缺少分数的记录，拒绝导入。"
                    )
                student_id = student_id_for(conn, str(item.get("student_id") or ""))
                conn.execute(
                    """
                    INSERT INTO exam_scores (exam_id, student_id, score)
                    VALUES (?, ?, ?)
                    ON CONFLICT (exam_id, student_id) DO UPDATE SET score = excluded.score
                    """,
                    (exam_id, student_id, float(score)),
                )
                score_count += 1

    return {"exams": exam_count, "scores": score_count}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入虚构演示成绩（Phase 1）")
    parser.add_argument("--config", default=None, help="配置文件；省略时自动找")
    args = parser.parse_args(argv)

    try:
        config = config_loader.load_config(
            config_loader.resolve_cli_config_path(args.config)
        )
        if not config.paths.database.is_file():
            raise config_loader.ConfigError(
                f"还没有数据库：{config.paths.database}；"
                "请先运行 python3 init_db.py --demo。"
            )

        dataset = seed_demo_data.load_or_create_dataset(config)
        seed_demo_data.validate_dataset_for_import(dataset, config)

        conn = db_module.connect(config.paths.database)
        try:
            db_module.require_schema(conn)
            counts = import_exam_scores(conn, dataset)
        finally:
            conn.close()
    except config_loader.ConfigError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    print(
        f"已导入演示成绩：{counts['exams']} 场考试 / {counts['scores']} 条成绩"
        f"（数据库：{config.paths.database}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
