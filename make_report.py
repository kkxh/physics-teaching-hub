"""生成 Phase 1 演示报告（最小闭环第三步）。

用法：
    python3 make_report.py                  # 写到配置里的 paths.output_dir
    python3 make_report.py --config my.toml

报告是 Markdown，内容全部来自本地数据库与配置：学期名、教学周、教学阶段、
考试与班级统计。数据库里的时间按 UTC 存，报告按 project.timezone 显示。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import config_loader
import db as db_module
import init_db
import teaching_calendar

REPORT_NAME = "phase1_report.md"


def format_score(value: Any) -> str:
    """分数显示：整数不带小数点，小数保留一位，空值用破折号。"""
    if value is None:
        return "—"
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def display_path(path: Path, base_dir: Path) -> str:
    """报告里尽量用相对路径，避免把本机绝对路径带进可分享的文件。"""
    try:
        return str(path.relative_to(base_dir))
    except ValueError:
        return str(path)


def fetch_exam_stats(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT e.exam_key, e.name, e.exam_date, e.full_score,
               COUNT(s.id) AS student_count,
               AVG(s.score) AS average,
               MAX(s.score) AS highest,
               MIN(s.score) AS lowest
        FROM exams e
        LEFT JOIN exam_scores s ON s.exam_id = e.id
        GROUP BY e.id
        ORDER BY e.exam_date, e.id
        """
    ).fetchall()


def fetch_class_stats(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT c.name AS class_name,
               COUNT(st.id) AS student_count
        FROM classes c
        LEFT JOIN students st ON st.class_id = c.id
        GROUP BY c.id
        ORDER BY c.name
        """
    ).fetchall()


def fetch_class_averages(conn: sqlite3.Connection) -> dict[tuple[str, str], float]:
    rows = conn.execute(
        """
        SELECT c.name AS class_name, e.exam_key, AVG(s.score) AS average
        FROM exam_scores s
        JOIN students st ON st.id = s.student_id
        JOIN classes c ON c.id = st.class_id
        JOIN exams e ON e.id = s.exam_id
        GROUP BY c.id, e.id
        """
    ).fetchall()
    return {(row["class_name"], row["exam_key"]): row["average"] for row in rows}


def build_report(config: config_loader.AppConfig, conn: sqlite3.Connection) -> str:
    """按配置与库内数据生成 Markdown 报告。"""
    labels = config.labels
    calendar = teaching_calendar.TeachingCalendar.from_config(config)
    now = calendar.now()
    exams = fetch_exam_stats(conn)
    classes = fetch_class_stats(conn)
    class_averages = fetch_class_averages(conn)

    lines: list[str] = [
        f"# {config.project.name} — Phase 1 演示报告",
        "",
        f"> 数据来源：虚构演示数据（schema: {init_db_schema_version(conn)}）。"
        "本报告不含任何真实学生数据。",
        "",
        f"- 学期：{config.semester.name}"
        f"（{config.semester.starts_on.isoformat()} ~ {config.semester.ends_on.isoformat()}）",
        f"- 学段 / 学科：{config.project.stage_label} / {config.project.subject_label}",
        f"- 时区：{config.project.timezone}",
        f"- 生成时间：{now.strftime('%Y-%m-%d %H:%M')}（{config.project.timezone}）",
        f"- 数据库：{display_path(config.paths.database, config.base_dir)}",
        "",
        "## 考试概览",
        "",
        "| 考试 | 日期 | 教学周 | 教学阶段 | 满分 | 人数 | 平均分 | 最高 | 最低 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for exam in exams:
        day = date.fromisoformat(str(exam["exam_date"]))
        week = calendar.week_of(day)
        phase = calendar.current_phase(day)
        week_text = (
            f"第 {week} {labels.get('terms.teaching_week')}" if week else "不在学期内"
        )
        lines.append(
            "| {name} | {day} | {week} | {phase} | {full} | {count} | {avg} | {high} | {low} |".format(
                name=exam["name"],
                day=exam["exam_date"],
                week=week_text,
                phase=phase.name if phase else "—",
                full=format_score(exam["full_score"]),
                count=exam["student_count"],
                avg=format_score(exam["average"]),
                high=format_score(exam["highest"]),
                low=format_score(exam["lowest"]),
            )
        )

    lines.extend(
        [
            "",
            "## 班级平均分",
            "",
            "| 班级 | 人数 | " + " | ".join(str(exam["name"]) for exam in exams) + " |",
            "| --- | --- | " + " | ".join("---" for _ in exams) + " |",
        ]
    )
    for klass in classes:
        cells = [
            format_score(class_averages.get((klass["class_name"], str(exam["exam_key"]))))
            for exam in exams
        ]
        lines.append(
            f"| {klass['class_name']} | {klass['student_count']} | " + " | ".join(cells) + " |"
        )

    lines.extend(
        [
            "",
            "## 数据说明",
            "",
            "- 名单与成绩全部由 `seed_demo_data.py` 生成，姓名是「前缀 + 两位序号」的虚构样式。",
            f"- 数据库 schema 是 Phase 1 的**临时** schema（{init_db_schema_version(conn)}），"
            "Phase 2 会替换或扩展，表名与字段不承诺兼容。",
            f"- 入库时间按 UTC 存储，报告按 {config.project.timezone} 显示。",
            "",
        ]
    )
    return "\n".join(lines)


def init_db_schema_version(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return str(row["value"]) if row else "unknown"


def write_report(config: config_loader.AppConfig, conn: sqlite3.Connection) -> Path:
    path = config.paths.output_dir / REPORT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(config, conn), encoding="utf-8")
    return path


def generate_report(config: config_loader.AppConfig) -> Path:
    """按配置与库内数据生成报告，返回写出的路径。"""
    if not config.paths.database.is_file():
        raise config_loader.ConfigError(
            f"还没有数据库：{config.paths.database}；"
            "请先运行 python3 hub.py init-db --demo 与 python3 hub.py import-scores --demo。"
        )

    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        return write_report(config, conn)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """兼容垫片：等价于 `python3 hub.py make-report`。"""
    parser = argparse.ArgumentParser(description="生成演示报告（兼容 Phase 1 调用方式）")
    parser.add_argument("--config", default=None, help="配置文件；省略时自动找")
    parser.add_argument("--db", default=None, help="覆盖数据库路径")
    args = parser.parse_args(argv)

    import hub

    forwarded: list[str] = []
    if args.config:
        forwarded += ["--config", args.config]
    if args.db:
        forwarded += ["--db", args.db]
    forwarded += ["make-report"]
    return hub.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
