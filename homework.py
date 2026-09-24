"""作业与订正：导入与统计。

统计口径（同时写在 `hub.py homework-stats --help` 与 `data/README.md` 里）：

- **完成率 = 已交 / 应交**，分子与分母都只算「作业所属班级的**当前**学生」。
  转班学生的历史提交既不计入原班、也不计入新班——否则完成率可能超过 100%；
- 已交 = 状态 `submitted` 或 `late`；迟交单列；缺交 = 应交 − 已交；
- 订正率 = 有订正记录的已交记录 / 已交记录；
- Phase 2 不做历史名册：应交按「当前名册 × 作业份数」回算，中途插班的学生会算进更早的作业。

工程约定：身份按 ID（`importer.resolve_student`）、整批单事务、dry-run 与执行同条件。
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import config_loader
import db as db_module
import importer
import seed_demo_data

DEFAULT_COLUMNS: Mapping[str, str] = {
    "student_uid": "student_uid",
    "name": "name",
    "status": "status",
    "submitted_on": "submitted_on",
    "corrected_on": "corrected_on",
    "note": "note",
}
STATUSES = seed_demo_data.HOMEWORK_STATUSES
SUBMITTED_STATUSES = ("submitted", "late")


@dataclass(frozen=True)
class HomeworkRow:
    row_number: int
    student_uid: str | None
    name: str | None
    status: str
    submitted_on: str | None
    corrected_on: str | None
    note: str | None


@dataclass(frozen=True)
class ResolvedHomeworkRow:
    row_number: int
    student_id: int
    student_uid: str
    student_name: str
    class_name: str
    status: str
    submitted_on: str | None
    corrected_on: str | None
    note: str | None

    @property
    def has_correction(self) -> bool:
        return bool(self.corrected_on or self.note)


@dataclass(frozen=True)
class HomeworkPlan:
    assign_key: str
    class_name: str
    topic: str
    assigned_date: str
    due_date: str | None
    assignment_exists: bool
    rows: tuple[ResolvedHomeworkRow, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class ClassHomeworkStats:
    class_name: str
    assignment_count: int
    expected: int
    submitted: int
    late: int
    corrected: int

    @property
    def missing(self) -> int:
        return max(self.expected - self.submitted, 0)

    @property
    def completion_rate(self) -> float:
        return 0.0 if self.expected == 0 else self.submitted / self.expected

    @property
    def correction_rate(self) -> float:
        """订正率 = 有订正记录的已交记录 / 已交记录。"""
        return 0.0 if self.submitted == 0 else self.corrected / self.submitted


def resolve_class(conn: sqlite3.Connection, class_name: str) -> sqlite3.Row:
    row = conn.execute("SELECT id, name FROM classes WHERE name = ?", (class_name,)).fetchone()
    if row is None:
        known = [item["name"] for item in conn.execute("SELECT name FROM classes ORDER BY name")]
        raise config_loader.ConfigError(
            f"找不到班级：{class_name!r}；库里的班级有：{'、'.join(known) or '（还没有）'}。"
        )
    return row


def parse_status(raw: Any, row_number: int) -> str:
    text = "" if raw is None else str(raw).strip().lower()
    if not text:
        raise config_loader.ConfigError(f"第 {row_number} 行没有状态，拒绝导入。")
    if text not in STATUSES:
        raise config_loader.ConfigError(
            f"第 {row_number} 行的状态不认识：{raw!r}；只能是 {'、'.join(STATUSES)}。"
        )
    return text


def read_homework_rows(
    csv_path: Path,
    columns: Mapping[str, str],
) -> list[HomeworkRow]:
    """读作业 CSV：必须有 status 列，以及 student_uid / name 里至少一列。"""
    if not csv_path.is_file():
        raise config_loader.ConfigError(f"找不到作业表：{csv_path}")

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        present = importer.resolve_columns(
            headers, columns, csv_path.name, required=("status",)
        )
        rows: list[HomeworkRow] = []
        for row_number, raw in enumerate(reader, start=2):
            rows.append(
                HomeworkRow(
                    row_number=row_number,
                    student_uid=importer.field_value(raw, present, "student_uid"),
                    name=importer.field_value(raw, present, "name"),
                    status=parse_status(importer.field_value(raw, present, "status"), row_number),
                    submitted_on=importer.field_value(raw, present, "submitted_on"),
                    corrected_on=importer.field_value(raw, present, "corrected_on"),
                    note=importer.field_value(raw, present, "note"),
                )
            )

    if not rows:
        raise config_loader.ConfigError(f"{csv_path.name} 里没有任何作业记录。")
    return rows


def build_homework_plan(
    conn: sqlite3.Connection,
    *,
    rows: Sequence[HomeworkRow],
    assign_key: str,
    class_name: str,
    topic: str,
    assigned_date: str,
    due_date: str | None,
) -> HomeworkPlan:
    """把作业表匹配到库内学生；dry-run 与真正执行都走这里。"""
    klass = resolve_class(conn, class_name)
    class_id = int(klass["id"])

    existing = conn.execute(
        """
        SELECT id, class_id, topic, assigned_date, due_date
        FROM homework_assignments WHERE assign_key = ?
        """,
        (assign_key,),
    ).fetchone()
    warnings: list[str] = []
    if existing is not None and int(existing["class_id"]) != class_id:
        raise config_loader.ConfigError(
            f"作业标识 {assign_key!r} 已属于别的班级（class_id={existing['class_id']}）；"
            "换个 --assign-key，或先修正库里的数据。"
        )

    resolved: list[ResolvedHomeworkRow] = []
    seen: set[int] = set()
    for row in rows:
        if row.corrected_on and row.status not in SUBMITTED_STATUSES:
            raise config_loader.ConfigError(
                f"第 {row.row_number} 行是 {row.status}，不该有订正记录。"
            )
        student = importer.resolve_student(conn, student_uid=row.student_uid, name=row.name)
        student_id = int(student["id"])
        if int(student["class_id"]) != class_id:
            warnings.append(
                f"第 {row.row_number} 行 {student['name']} 现在在 {student['class_name']}，"
                f"不属于作业班级 {class_name}；这条提交不会计入 {class_name} 的完成率。"
            )
        if student_id in seen:
            warnings.append(
                f"第 {row.row_number} 行与前面某行指向同一名学生（{student['name']}），后一条覆盖前一条。"
            )
        seen.add(student_id)
        resolved.append(
            ResolvedHomeworkRow(
                row_number=row.row_number,
                student_id=student_id,
                student_uid=str(student["student_uid"]),
                student_name=str(student["name"]),
                class_name=str(student["class_name"]),
                status=row.status,
                submitted_on=row.submitted_on,
                corrected_on=row.corrected_on,
                note=row.note,
            )
        )

    return HomeworkPlan(
        assign_key=assign_key,
        class_name=class_name,
        topic=topic,
        assigned_date=assigned_date,
        due_date=due_date,
        assignment_exists=existing is not None,
        rows=tuple(resolved),
        warnings=tuple(warnings),
    )


def apply_homework_plan(conn: sqlite3.Connection, plan: HomeworkPlan) -> dict[str, int]:
    """按计划写入作业、提交与订正：单事务，出错整体回滚。"""
    created = 0
    written = 0
    corrections = 0

    with conn:
        klass = resolve_class(conn, plan.class_name)
        existing = conn.execute(
            "SELECT id FROM homework_assignments WHERE assign_key = ?", (plan.assign_key,)
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO homework_assignments
                    (assign_key, class_id, topic, assigned_date, due_date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (plan.assign_key, int(klass["id"]), plan.topic, plan.assigned_date, plan.due_date),
            )
            created = 1
            assignment_id = int(
                conn.execute(
                    "SELECT id FROM homework_assignments WHERE assign_key = ?",
                    (plan.assign_key,),
                ).fetchone()["id"]
            )
        else:
            assignment_id = int(existing["id"])
            conn.execute(
                """
                UPDATE homework_assignments
                SET topic = ?, assigned_date = ?, due_date = ?
                WHERE id = ?
                """,
                (plan.topic, plan.assigned_date, plan.due_date, assignment_id),
            )

        for item in plan.rows:
            conn.execute(
                """
                INSERT INTO homework_submissions
                    (assignment_id, student_id, status, submitted_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (assignment_id, student_id) DO UPDATE SET
                    status = excluded.status,
                    submitted_at = excluded.submitted_at
                """,
                (assignment_id, item.student_id, item.status, item.submitted_on),
            )
            written += 1

            if not item.has_correction:
                continue
            submission_id = int(
                conn.execute(
                    """
                    SELECT id FROM homework_submissions
                    WHERE assignment_id = ? AND student_id = ?
                    """,
                    (assignment_id, item.student_id),
                ).fetchone()["id"]
            )
            conn.execute(
                """
                INSERT INTO corrections (submission_id, corrected_at, note)
                VALUES (?, ?, ?)
                ON CONFLICT (submission_id) DO UPDATE SET
                    corrected_at = excluded.corrected_at,
                    note = excluded.note
                """,
                (submission_id, item.corrected_on, item.note),
            )
            corrections += 1

    return {"assignments_created": created, "submissions_written": written, "corrections": corrections}


def import_homework_from_csv(
    config: config_loader.AppConfig,
    *,
    csv_path: str | Path,
    assign_key: str,
    class_name: str,
    topic: str,
    assigned_date: str,
    due_date: str | None = None,
    dry_run: bool = False,
    columns_spec: str | None = None,
) -> dict[str, Any]:
    """从 CSV 导入一次作业的提交情况。"""
    if not config.paths.database.is_file():
        raise config_loader.ConfigError(
            f"还没有数据库：{config.paths.database}；请先运行 python3 hub.py init-db --demo。"
        )

    columns = importer.parse_columns(columns_spec, DEFAULT_COLUMNS)
    rows = read_homework_rows(Path(csv_path), columns)

    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        plan = build_homework_plan(
            conn,
            rows=rows,
            assign_key=assign_key,
            class_name=class_name,
            topic=topic,
            assigned_date=assigned_date,
            due_date=due_date,
        )
        if dry_run:
            return {"dry_run": True, "plan": plan}
        counts = apply_homework_plan(conn, plan)
        return {"dry_run": False, "plan": plan, **counts}
    finally:
        conn.close()


def import_demo_homework(
    conn: sqlite3.Connection,
    dataset: Mapping[str, Any],
) -> dict[str, int]:
    """把演示数据集里的作业写进库（作业 + 提交 + 订正），供 init-db --demo 调用。"""
    assignments = 0
    submissions = 0
    corrections = 0

    for item in dataset.get("homework") or []:
        class_name = str(item.get("class") or "")
        plan = build_homework_plan(
            conn,
            rows=[
                HomeworkRow(
                    row_number=index,
                    student_uid=str(record.get("student_id") or "") or None,
                    name=None,
                    status=str(record.get("status") or ""),
                    submitted_on=record.get("submitted_on"),
                    corrected_on=(record.get("correction") or {}).get("corrected_on"),
                    note=(record.get("correction") or {}).get("note"),
                )
                for index, record in enumerate(item.get("records") or [], start=2)
            ],
            assign_key=str(item.get("assign_key") or ""),
            class_name=class_name,
            topic=str(item.get("topic") or ""),
            assigned_date=str(item.get("assigned_date") or ""),
            due_date=item.get("due_date"),
        )
        counts = apply_homework_plan(conn, plan)
        assignments += counts["assignments_created"]
        submissions += counts["submissions_written"]
        corrections += counts["corrections"]

    return {
        "assignments": assignments,
        "submissions": submissions,
        "corrections": corrections,
    }


def homework_stats(
    conn: sqlite3.Connection,
    class_name: str | None = None,
) -> list[ClassHomeworkStats]:
    """按班级统计作业完成情况；分子分母都限定「作业所属班级的当前学生」。"""
    sql = """
        SELECT c.name AS class_name,
               COUNT(DISTINCT a.id) AS assignment_count,
               COUNT(s.id) AS expected,
               SUM(CASE WHEN sub.status IN ('submitted', 'late') THEN 1 ELSE 0 END) AS submitted,
               SUM(CASE WHEN sub.status = 'late' THEN 1 ELSE 0 END) AS late,
               SUM(CASE WHEN cor.id IS NOT NULL AND sub.status IN ('submitted', 'late')
                        THEN 1 ELSE 0 END) AS corrected
        FROM classes c
        LEFT JOIN homework_assignments a ON a.class_id = c.id
        LEFT JOIN students s ON s.class_id = a.class_id
        LEFT JOIN homework_submissions sub
               ON sub.assignment_id = a.id AND sub.student_id = s.id
        LEFT JOIN corrections cor ON cor.submission_id = sub.id
    """
    params: list[Any] = []
    if class_name:
        sql += " WHERE c.name = ?"
        params.append(class_name)
    sql += " GROUP BY c.id ORDER BY c.name"

    rows = conn.execute(sql, params).fetchall()
    if class_name and not rows:
        resolve_class(conn, class_name)

    return [
        ClassHomeworkStats(
            class_name=str(row["class_name"]),
            assignment_count=int(row["assignment_count"] or 0),
            expected=int(row["expected"] or 0),
            submitted=int(row["submitted"] or 0),
            late=int(row["late"] or 0),
            corrected=int(row["corrected"] or 0),
        )
        for row in rows
    ]


def format_stats_table(stats: Sequence[ClassHomeworkStats]) -> str:
    """把统计结果排成 Markdown 表格（便于复制到报告或聊天里）。"""
    lines = [
        "| 班级 | 作业数 | 应交 | 已交 | 迟交 | 缺交 | 完成率 | 订正率 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in stats:
        lines.append(
            f"| {item.class_name} | {item.assignment_count} | {item.expected} | "
            f"{item.submitted} | {item.late} | {item.missing} | "
            f"{item.completion_rate * 100:.1f}% | {item.correction_rate * 100:.1f}% |"
        )
    return "\n".join(lines)
