"""学生画像：纯计算 + 数据层封装。

维度（权重在 `config.toml` 的 `[profile]` 段，默认 0.5 / 0.3 / 0.2）：

- `score_level` 成绩水平 = 100 × 各场考试平均得分率；
- `homework_habit` 作业习惯 = 100 × (0.7 × 完成率 + 0.3 × 按时率)；
- `error_control` 错因控制 = 100 − 20 × (错因记录数 ÷ max(1, 考试数 + 作业数))，
  上限 100、下限 0：平均每场考试/每份作业出现 1 条错因扣 20 分；
- `overall` 综合分 = 三个维度的加权平均（权重和为 0 时按等权）。

纯函数（`compute_dimensions` / `compute_profile`）不碰数据库，输入是普通数据类；
读库与写库在 `load_inputs` / `save_profiles` / `compute_profiles` 里。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import config_loader
import db as db_module

ERROR_PENALTY_PER_RECORD = 20.0
SUBMITTED_STATUSES = ("submitted", "late")


@dataclass(frozen=True)
class ExamScoreInput:
    score: float
    full_score: float


@dataclass(frozen=True)
class HomeworkInput:
    status: str


@dataclass(frozen=True)
class StudentProfileInput:
    """算一个学生的画像所需要的全部数据（纯数据，便于单测）。"""

    student_uid: str
    exam_scores: tuple[ExamScoreInput, ...] = ()
    homework: tuple[HomeworkInput, ...] = ()
    error_count: int = 0

    @property
    def work_count(self) -> int:
        """「工作量」：有成绩的考试数 + 该生所在班的作业数，用于错因密度。"""
        return len(self.exam_scores) + len(self.homework)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def score_level(scores: Sequence[ExamScoreInput]) -> float:
    rates = [
        _clamp(item.score / item.full_score, 0.0, 1.0)
        for item in scores
        if item.full_score > 0
    ]
    if not rates:
        return 0.0
    return _clamp(100.0 * sum(rates) / len(rates))


def homework_habit(items: Sequence[HomeworkInput]) -> float:
    if not items:
        return 0.0
    submitted = [item for item in items if item.status in SUBMITTED_STATUSES]
    completion = len(submitted) / len(items)
    on_time = (
        len([item for item in submitted if item.status == "submitted"]) / len(submitted)
        if submitted
        else 0.0
    )
    return _clamp(100.0 * (0.7 * completion + 0.3 * on_time))


def error_control(error_count: int, work_count: int) -> float:
    units = max(1, work_count)
    return _clamp(100.0 - ERROR_PENALTY_PER_RECORD * (error_count / units))


def compute_dimensions(data: StudentProfileInput) -> dict[str, float]:
    """纯函数：输入数据 → 三个维度分（0~100）。"""
    return {
        "score_level": score_level(data.exam_scores),
        "homework_habit": homework_habit(data.homework),
        "error_control": error_control(data.error_count, data.work_count),
    }


def compute_profile(
    data: StudentProfileInput,
    weights: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """纯函数：输入数据 → 三个维度分 + 综合分。"""
    dimensions = compute_dimensions(data)
    effective = dict(weights or config_loader.DEFAULT_PROFILE_WEIGHTS)
    total_weight = sum(max(0.0, effective.get(name, 0.0)) for name in dimensions)
    if total_weight <= 0:
        overall = sum(dimensions.values()) / len(dimensions)
    else:
        overall = sum(
            dimensions[name] * max(0.0, effective.get(name, 0.0)) for name in dimensions
        ) / total_weight
    return {**dimensions, "overall": _clamp(overall)}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_inputs(
    conn: sqlite3.Connection,
    student_uid: str | None = None,
) -> list[StudentProfileInput]:
    """读库：把一个或全部学生的成绩、作业与错因数取出来。"""
    sql = "SELECT id, student_uid, class_id FROM students"
    params: list[Any] = []
    if student_uid:
        sql += " WHERE student_uid = ?"
        params.append(student_uid)
    sql += " ORDER BY id"
    students = conn.execute(sql, params).fetchall()

    inputs: list[StudentProfileInput] = []
    for student in students:
        scores = conn.execute(
            """
            SELECT s.score AS score, e.full_score AS full_score
            FROM exam_scores s JOIN exams e ON e.id = s.exam_id
            WHERE s.student_id = ?
            """,
            (int(student["id"]),),
        ).fetchall()
        homework = conn.execute(
            """
            SELECT sub.status AS status
            FROM homework_assignments a
            LEFT JOIN homework_submissions sub
                   ON sub.assignment_id = a.id AND sub.student_id = ?
            WHERE a.class_id = ?
            """,
            (int(student["id"]), int(student["class_id"])),
        ).fetchall()
        error_count = conn.execute(
            "SELECT COUNT(*) AS n FROM error_records WHERE student_id = ?",
            (int(student["id"]),),
        ).fetchone()["n"]

        inputs.append(
            StudentProfileInput(
                student_uid=str(student["student_uid"]),
                exam_scores=tuple(
                    ExamScoreInput(score=float(row["score"]), full_score=float(row["full_score"]))
                    for row in scores
                ),
                homework=tuple(
                    HomeworkInput(status=str(row["status"]))
                    for row in homework
                    if row["status"] is not None
                ),
                error_count=int(error_count),
            )
        )
    return inputs


def students_needing_recompute(conn: sqlite3.Connection) -> set[str]:
    """哪些学生的源数据在画像之后变过（或从没算过）。"""
    rows = conn.execute(
        """
        SELECT s.id, s.student_uid,
               MAX(
                   COALESCE((SELECT MAX(created_at) FROM exam_scores WHERE student_id = s.id), ''),
                   COALESCE((SELECT MAX(created_at) FROM homework_submissions WHERE student_id = s.id), ''),
                   COALESCE((SELECT MAX(created_at) FROM error_records WHERE student_id = s.id), '')
               ) AS latest_source,
               COALESCE((SELECT MAX(computed_at) FROM ability_scores WHERE student_id = s.id), '') AS latest_profile
        FROM students s
        """
    ).fetchall()
    return {
        str(row["student_uid"])
        for row in rows
        if str(row["latest_source"]) > str(row["latest_profile"])
    }


def save_profiles(
    conn: sqlite3.Connection,
    profiles: Mapping[str, Mapping[str, float]],
    computed_at: str,
) -> int:
    """写入画像：单事务、按（学生, 维度）幂等覆盖。返回写入的维度行数。"""
    written = 0
    with conn:
        for student_uid, dimensions in profiles.items():
            row = conn.execute(
                "SELECT id FROM students WHERE student_uid = ?", (student_uid,)
            ).fetchone()
            if row is None:
                raise config_loader.ConfigError(f"找不到学生：{student_uid!r}")
            student_id = int(row["id"])
            for dimension, value in dimensions.items():
                conn.execute(
                    """
                    INSERT INTO ability_scores (student_id, dimension, score, computed_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (student_id, dimension) DO UPDATE SET
                        score = excluded.score,
                        computed_at = excluded.computed_at
                    """,
                    (student_id, dimension, float(value), computed_at),
                )
                written += 1
    return written


def compute_profiles(
    config: config_loader.AppConfig,
    *,
    student_uid: str | None = None,
    rebuild: bool = False,
) -> dict[str, Any]:
    """计算并落库画像；默认只算源数据变过的学生，`rebuild=True` 全部重算。"""
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        inputs = load_inputs(conn, student_uid)
        if not inputs:
            raise config_loader.ConfigError(f"找不到学生：{student_uid!r}")

        if rebuild or student_uid:
            targets = inputs
        else:
            pending = students_needing_recompute(conn)
            targets = [item for item in inputs if item.student_uid in pending]

        computed_at = utc_now()
        profiles = {
            item.student_uid: compute_profile(item, config.profile_weights)
            for item in targets
        }
        written = save_profiles(conn, profiles, computed_at) if profiles else 0
        return {
            "profiles": profiles,
            "written": written,
            "computed": len(profiles),
            "skipped": len(inputs) - len(profiles),
            "computed_at": computed_at,
        }
    finally:
        conn.close()


def format_profile_table(profiles: Mapping[str, Mapping[str, float]]) -> str:
    lines = [
        "| 学生 | 成绩水平 | 作业习惯 | 错因控制 | 综合分 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for student_uid, dimensions in sorted(profiles.items()):
        lines.append(
            f"| {student_uid} | {dimensions['score_level']:.1f} | "
            f"{dimensions['homework_habit']:.1f} | {dimensions['error_control']:.1f} | "
            f"{dimensions['overall']:.1f} |"
        )
    return "\n".join(lines)
