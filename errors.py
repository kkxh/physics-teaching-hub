"""错因与行为记录：标签字典、写入与查询。

工程约定：

- 写入前**先验存在**：学生、考试/作业、标签任一不存在就整体拒绝（第 4 条：单事务）；
- 写入走「预览 → 确认 → 单事务写入」，`--yes` 才真正落库（第 9 条）；
- 标签 code 用英文、label 是显示名；内置 5 类随仓库分发，使用者可在 `config.toml`
  的 `[error_tags]` 里改显示名或加自己的 code。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import config_loader
import db as db_module
import importer

# 内置错因标签：code 是稳定标识，label 是显示名（可在配置里覆盖）
BUILTIN_ERROR_TAGS: tuple[tuple[str, str], ...] = (
    ("model_choice", "模型选择"),
    ("graph_reading", "图像读取"),
    ("calculation", "计算失误"),
    ("expression", "表达不规范"),
    ("concept_confusion", "概念混淆"),
)

# 内置行为类型：课堂参与 / 课后自主 / 实验表现 / 订正习惯 / 考前状态
BUILTIN_BEHAVIOR_KINDS: tuple[tuple[str, str], ...] = (
    ("class_participation", "课堂参与"),
    ("after_class", "课后自主"),
    ("experiment", "实验表现"),
    ("correction_habit", "订正习惯"),
    ("exam_prep", "考前状态"),
)

BEHAVIOR_KINDS = tuple(code for code, _label in BUILTIN_BEHAVIOR_KINDS)


@dataclass(frozen=True)
class ErrorRecordPlan:
    """一条错因记录的写入计划；dry-run 与真正执行共用。"""

    student_id: int
    student_uid: str
    student_name: str
    tag_code: str
    tag_label: str
    exam_id: int | None
    exam_label: str | None
    assignment_id: int | None
    assignment_label: str | None
    note: str | None
    recorded_at: str


@dataclass(frozen=True)
class BehaviorRecordPlan:
    student_id: int
    student_uid: str
    student_name: str
    kind: str
    detail: str | None
    recorded_at: str


@dataclass(frozen=True)
class ErrorRecordRow:
    record_id: int
    student_uid: str
    student_name: str
    class_name: str
    tag_code: str
    tag_label: str
    exam_label: str | None
    assignment_label: str | None
    note: str | None
    recorded_at: str


def sync_tag_dictionary(
    conn: sqlite3.Connection,
    extra_tags: Mapping[str, str] | None = None,
) -> int:
    """把内置标签 + 配置里的扩展写进 error_tags（幂等）。返回字典条数。"""
    tags = dict(BUILTIN_ERROR_TAGS)
    for code, label in (extra_tags or {}).items():
        tags[str(code)] = str(label)

    with conn:
        for code, label in tags.items():
            conn.execute(
                """
                INSERT INTO error_tags (code, label) VALUES (?, ?)
                ON CONFLICT (code) DO UPDATE SET label = excluded.label
                """,
                (code, label),
            )
    return len(tags)


def resolve_tag(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, code, label FROM error_tags WHERE code = ?", (code,)
    ).fetchone()
    if row is None:
        known = [
            str(item["code"]) for item in conn.execute("SELECT code FROM error_tags ORDER BY code")
        ]
        raise config_loader.ConfigError(
            f"没有这个错因标签：{code!r}；可用标签：{'、'.join(known) or '（字典是空的）'}。"
            "要加自定义标签，在 config.toml 的 [error_tags] 里写 代码 = \"显示名\"。"
        )
    return row


def resolve_exam_by_key(conn: sqlite3.Connection, exam_key: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, exam_key, name, exam_date FROM exams WHERE exam_key = ?", (exam_key,)
    ).fetchone()
    if row is None:
        known = [
            str(item["exam_key"]) for item in conn.execute("SELECT exam_key FROM exams ORDER BY id")
        ]
        raise config_loader.ConfigError(
            f"找不到考试：exam_key={exam_key!r}；库里的考试有：{'、'.join(known) or '（还没有）'}。"
        )
    return row


def resolve_assignment_by_key(conn: sqlite3.Connection, assign_key: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, assign_key, topic FROM homework_assignments WHERE assign_key = ?",
        (assign_key,),
    ).fetchone()
    if row is None:
        known = [
            str(item["assign_key"])
            for item in conn.execute("SELECT assign_key FROM homework_assignments ORDER BY id")
        ]
        raise config_loader.ConfigError(
            f"找不到作业：assign_key={assign_key!r}；库里的作业有：{'、'.join(known) or '（还没有）'}。"
        )
    return row


def build_error_plan(
    conn: sqlite3.Connection,
    *,
    student_uid: str | None = None,
    name: str | None = None,
    tag_code: str,
    exam_key: str | None = None,
    assign_key: str | None = None,
    note: str | None = None,
    recorded_at: str,
) -> ErrorRecordPlan:
    """校验「学生 / 考试或作业 / 标签」都存在，并给出写入计划（不写库）。"""
    if bool(exam_key) == bool(assign_key):
        raise config_loader.ConfigError(
            "错因要挂在一次考试或一份作业上：--exam-key 与 --assign-key 必须二选一。"
        )

    student = importer.resolve_student(conn, student_uid=student_uid, name=name)
    tag = resolve_tag(conn, tag_code)

    exam_id: int | None = None
    exam_label: str | None = None
    assignment_id: int | None = None
    assignment_label: str | None = None

    if exam_key:
        exam = resolve_exam_by_key(conn, exam_key)
        exam_id = int(exam["id"])
        exam_label = f"{exam['name']}（{exam['exam_date']}）"
    if assign_key:
        assignment = resolve_assignment_by_key(conn, assign_key)
        assignment_id = int(assignment["id"])
        assignment_label = f"{assignment['topic']}（{assignment['assign_key']}）"

    return ErrorRecordPlan(
        student_id=int(student["id"]),
        student_uid=str(student["student_uid"]),
        student_name=str(student["name"]),
        tag_code=str(tag["code"]),
        tag_label=str(tag["label"]),
        exam_id=exam_id,
        exam_label=exam_label,
        assignment_id=assignment_id,
        assignment_label=assignment_label,
        note=(note or "").strip() or None,
        recorded_at=recorded_at,
    )


def apply_error_plan(
    conn: sqlite3.Connection,
    plan: ErrorRecordPlan,
) -> dict[str, int]:
    """写入一条错因记录：单事务，出错整体回滚。"""
    with conn:
        # 复核一遍关联行还在（预览与执行之间可能被改动）
        if plan.exam_id is not None and not _exists(conn, "exams", plan.exam_id):
            raise config_loader.ConfigError("预览时选的考试已经不存在了，请重新运行。")
        if plan.assignment_id is not None and not _exists(
            conn, "homework_assignments", plan.assignment_id
        ):
            raise config_loader.ConfigError("预览时选的作业已经不存在了，请重新运行。")
        tag = conn.execute(
            "SELECT id FROM error_tags WHERE code = ?", (plan.tag_code,)
        ).fetchone()
        if tag is None:
            raise config_loader.ConfigError(f"预览时选的标签已经不存在了：{plan.tag_code}")

        conn.execute(
            """
            INSERT INTO error_records
                (student_id, exam_id, assignment_id, tag_id, note, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                plan.student_id,
                plan.exam_id,
                plan.assignment_id,
                int(tag["id"]),
                plan.note,
                plan.recorded_at,
            ),
        )
    return {"error_records_written": 1}


def _exists(conn: sqlite3.Connection, table: str, row_id: int) -> bool:
    row = conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return row is not None


def record_error(
    config: config_loader.AppConfig,
    *,
    student_uid: str | None = None,
    name: str | None = None,
    tag_code: str,
    exam_key: str | None = None,
    assign_key: str | None = None,
    note: str | None = None,
    recorded_at: str,
    confirmed: bool = False,
) -> dict[str, Any]:
    """错因写入入口：先给预览，`confirmed=True` 才落库。"""
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        sync_tag_dictionary(conn, config.error_tags)
        plan = build_error_plan(
            conn,
            student_uid=student_uid,
            name=name,
            tag_code=tag_code,
            exam_key=exam_key,
            assign_key=assign_key,
            note=note,
            recorded_at=recorded_at,
        )
        if not confirmed:
            return {"confirmed": False, "plan": plan}
        counts = apply_error_plan(conn, plan)
        return {"confirmed": True, "plan": plan, **counts}
    finally:
        conn.close()


def build_behavior_plan(
    conn: sqlite3.Connection,
    *,
    student_uid: str | None = None,
    name: str | None = None,
    kind: str,
    detail: str | None = None,
    recorded_at: str,
) -> BehaviorRecordPlan:
    if kind not in BEHAVIOR_KINDS:
        raise config_loader.ConfigError(
            f"没有这种行为类型：{kind!r}；可用类型：{'、'.join(BEHAVIOR_KINDS)}。"
        )
    student = importer.resolve_student(conn, student_uid=student_uid, name=name)
    return BehaviorRecordPlan(
        student_id=int(student["id"]),
        student_uid=str(student["student_uid"]),
        student_name=str(student["name"]),
        kind=kind,
        detail=(detail or "").strip() or None,
        recorded_at=recorded_at,
    )


def apply_behavior_plan(conn: sqlite3.Connection, plan: BehaviorRecordPlan) -> dict[str, int]:
    with conn:
        conn.execute(
            """
            INSERT INTO behavior_records (student_id, kind, detail, recorded_at)
            VALUES (?, ?, ?, ?)
            """,
            (plan.student_id, plan.kind, plan.detail, plan.recorded_at),
        )
    return {"behavior_records_written": 1}


def record_behavior(
    config: config_loader.AppConfig,
    *,
    student_uid: str | None = None,
    name: str | None = None,
    kind: str,
    detail: str | None = None,
    recorded_at: str,
    confirmed: bool = False,
) -> dict[str, Any]:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        plan = build_behavior_plan(
            conn,
            student_uid=student_uid,
            name=name,
            kind=kind,
            detail=detail,
            recorded_at=recorded_at,
        )
        if not confirmed:
            return {"confirmed": False, "plan": plan}
        counts = apply_behavior_plan(conn, plan)
        return {"confirmed": True, "plan": plan, **counts}
    finally:
        conn.close()


def list_errors(
    conn: sqlite3.Connection,
    *,
    student_uid: str,
    tag_code: str | None = None,
) -> list[ErrorRecordRow]:
    """列出某个学生的错因记录（按记录时间倒序，同时间按 id 倒序）。"""
    sql = """
        SELECT er.id, s.student_uid, s.name AS student_name, c.name AS class_name,
               t.code AS tag_code, t.label AS tag_label,
               e.name AS exam_name, e.exam_date,
               a.topic AS assignment_topic, a.assign_key,
               er.note, er.recorded_at
        FROM error_records er
        JOIN students s ON s.id = er.student_id
        JOIN classes c ON c.id = s.class_id
        JOIN error_tags t ON t.id = er.tag_id
        LEFT JOIN exams e ON e.id = er.exam_id
        LEFT JOIN homework_assignments a ON a.id = er.assignment_id
        WHERE s.student_uid = ?
    """
    params: list[Any] = [student_uid]
    if tag_code:
        sql += " AND t.code = ?"
        params.append(tag_code)
    sql += " ORDER BY er.recorded_at DESC, er.id DESC"

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        # 学生不存在和「这个学生没有记录」要区分开
        importer.resolve_student(conn, student_uid=student_uid)

    return [
        ErrorRecordRow(
            record_id=int(row["id"]),
            student_uid=str(row["student_uid"]),
            student_name=str(row["student_name"]),
            class_name=str(row["class_name"]),
            tag_code=str(row["tag_code"]),
            tag_label=str(row["tag_label"]),
            exam_label=(
                f"{row['exam_name']}（{row['exam_date']}）" if row["exam_name"] else None
            ),
            assignment_label=(
                f"{row['assignment_topic']}（{row['assign_key']}）"
                if row["assignment_topic"]
                else None
            ),
            note=row["note"],
            recorded_at=str(row["recorded_at"]),
        )
        for row in rows
    ]


def list_behavior(
    conn: sqlite3.Connection,
    *,
    student_uid: str,
) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT br.id, br.kind, br.detail, br.recorded_at
        FROM behavior_records br
        JOIN students s ON s.id = br.student_id
        WHERE s.student_uid = ?
        ORDER BY br.recorded_at DESC, br.id DESC
        """,
        (student_uid,),
    ).fetchall()
    if not rows:
        importer.resolve_student(conn, student_uid=student_uid)
    return list(rows)


def import_demo_error_records(
    conn: sqlite3.Connection,
    dataset: Mapping[str, Any],
    extra_tags: Mapping[str, str] | None = None,
) -> dict[str, int]:
    """把演示数据集里的错因与行为记录写进库（供 init-db --demo 调用）。"""
    sync_tag_dictionary(conn, extra_tags)
    errors = 0
    behaviors = 0

    for item in dataset.get("error_records") or []:
        plan = build_error_plan(
            conn,
            student_uid=str(item.get("student_id") or ""),
            tag_code=str(item.get("tag") or ""),
            exam_key=item.get("exam_key"),
            assign_key=item.get("assign_key"),
            note=item.get("note"),
            recorded_at=str(item.get("recorded_at") or ""),
        )
        errors += apply_error_plan(conn, plan)["error_records_written"]

    for item in dataset.get("behavior_records") or []:
        plan = build_behavior_plan(
            conn,
            student_uid=str(item.get("student_id") or ""),
            kind=str(item.get("kind") or ""),
            detail=item.get("detail"),
            recorded_at=str(item.get("recorded_at") or ""),
        )
        behaviors += apply_behavior_plan(conn, plan)["behavior_records_written"]

    return {"error_records": errors, "behavior_records": behaviors}
