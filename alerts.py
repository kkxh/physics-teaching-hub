"""预警与跟进闭环：规则扫描、列表、解决与统计。

规则（阈值写在 `config.toml` 的 `[alerts]` 段）：

- `homework_missing`：该生当前班级的作业按布置日期排序，**尾部连续缺交**达到阈值；
  超过阈值 2 倍算 `critical`；
- `low_average`：考试成绩平均分低于阈值；再低 10 分算 `critical`；
- `score_drop`：最近一次考试比上一次低至少阈值分；达到 2 倍算 `critical`。

约定：

- 扫描**幂等**：同一学生在同一规则上已有一条未解决预警时只刷新内容，不重复新建；
  已解决的条目在条件（severity / 说明）没变化时也不再重新开，避免「解决 → 又出现」的循环；
  条件升级时会重新开一条新的未解决预警；
- 条件消失**不会自动关闭**预警——关闭必须走 `resolve_alert` 并留下跟进记录，
  不允许「静默解决」（`follow_ups` 里必须有人、时间和说明）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import config_loader
import db as db_module

MISSING_KIND = "homework_missing"
LOW_AVERAGE_KIND = "low_average"
SCORE_DROP_KIND = "score_drop"
OPEN = "open"
RESOLVED = "resolved"


@dataclass(frozen=True)
class AlertCandidate:
    student_uid: str
    student_name: str
    class_name: str
    kind: str
    severity: str
    description: str


def _severity(level: float, threshold: float) -> str:
    """达到 2 倍阈值算 critical，否则 warning。"""
    return "critical" if threshold > 0 and level >= threshold * 2 else "warning"


def _low_average_severity(average: float, threshold: float) -> str:
    """平均分低于阈值的 80% 算 critical。"""
    return "critical" if threshold > 0 and average < threshold * 0.8 else "warning"


def detect_alerts(
    conn: sqlite3.Connection,
    rules: config_loader.AlertRules,
) -> list[AlertCandidate]:
    """按规则算出当前应当存在的预警（只读，不写库）。"""
    candidates: list[AlertCandidate] = []

    students = conn.execute(
        """
        SELECT s.id, s.student_uid, s.name AS student_name, c.name AS class_name
        FROM students s JOIN classes c ON c.id = s.class_id
        ORDER BY s.id
        """
    ).fetchall()

    # 1) 连续缺交：按学生取「本班作业」的状态序列（没有提交行也算缺交）
    homework_rows = conn.execute(
        """
        SELECT s.id AS student_id, a.id AS assignment_id, a.assigned_date, a.topic,
               sub.status AS status
        FROM students s
        LEFT JOIN homework_assignments a ON a.class_id = s.class_id
        LEFT JOIN homework_submissions sub
               ON sub.assignment_id = a.id AND sub.student_id = s.id
        ORDER BY s.id, a.assigned_date, a.id
        """
    ).fetchall()
    homework_by_student: dict[int, list[sqlite3.Row]] = {}
    for row in homework_rows:
        if row["assignment_id"] is None:
            continue
        homework_by_student.setdefault(int(row["student_id"]), []).append(row)

    # 2) 平均分
    average_rows = conn.execute(
        """
        SELECT s.id AS student_id, AVG(es.score) AS average, COUNT(es.id) AS exam_count
        FROM students s
        LEFT JOIN exam_scores es ON es.student_id = s.id
        GROUP BY s.id
        """
    ).fetchall()
    averages = {int(row["student_id"]): row for row in average_rows}

    # 3) 最近两次考试
    score_rows = conn.execute(
        """
        SELECT es.student_id, es.score, e.exam_date, e.name AS exam_name
        FROM exam_scores es JOIN exams e ON e.id = es.exam_id
        ORDER BY es.student_id, e.exam_date DESC, e.id DESC
        """
    ).fetchall()
    recent_scores: dict[int, list[sqlite3.Row]] = {}
    for row in score_rows:
        recent_scores.setdefault(int(row["student_id"]), []).append(row)

    for student in students:
        student_id = int(student["id"])
        identity = {
            "student_uid": str(student["student_uid"]),
            "student_name": str(student["student_name"]),
            "class_name": str(student["class_name"]),
        }

        items = homework_by_student.get(student_id) or []
        trailing_missing = 0
        for row in reversed(items):
            if (row["status"] or "missing") == "missing":
                trailing_missing += 1
            else:
                break
        if trailing_missing >= rules.missing_homework_threshold:
            latest = items[-1]
            candidates.append(
                AlertCandidate(
                    **identity,
                    kind=MISSING_KIND,
                    severity=_severity(
                        trailing_missing, float(rules.missing_homework_threshold)
                    ),
                    description=(
                        f"连续 {trailing_missing} 次作业缺交"
                        f"（阈值 {rules.missing_homework_threshold}）"
                        f"，最近一次：{latest['topic']}"
                    ),
                )
            )

        average_row = averages.get(student_id)
        if average_row and int(average_row["exam_count"] or 0) > 0:
            average = float(average_row["average"])
            if average < rules.low_average_threshold:
                candidates.append(
                    AlertCandidate(
                        **identity,
                    kind=LOW_AVERAGE_KIND,
                        severity=_low_average_severity(average, rules.low_average_threshold),
                        description=(
                            f"考试成绩平均分 {average:.1f}，低于阈值 "
                            f"{rules.low_average_threshold:g}"
                        ),
                    )
                )

        recent = recent_scores.get(student_id) or []
        if len(recent) >= 2:
            latest_score = float(recent[0]["score"])
            previous_score = float(recent[1]["score"])
            drop = previous_score - latest_score
            if drop >= rules.score_drop_threshold and rules.score_drop_threshold > 0:
                candidates.append(
                    AlertCandidate(
                        **identity,
                        kind=SCORE_DROP_KIND,
                        severity=_severity(drop, rules.score_drop_threshold),
                        description=(
                            f"最近一次考试（{recent[0]['exam_name']}）比上一次"
                            f"（{recent[1]['exam_name']}）低 {drop:.1f} 分，"
                            f"阈值 {rules.score_drop_threshold:g}"
                        ),
                    )
                )

    return candidates


def scan_alerts(
    config: config_loader.AppConfig,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """扫描并写入预警；同一个（学生, 规则）只保留一条未解决预警。"""
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        candidates = detect_alerts(conn, config.alert_rules)
        open_rows = conn.execute(
            "SELECT id, student_id, kind, severity, description FROM alerts WHERE status = ?",
            (OPEN,),
        ).fetchall()
        open_by_key = {
            (int(row["student_id"]), str(row["kind"])): row for row in open_rows
        }
        # 已解决的预警：同一条（学生, 规则）且内容没变化时不再重新开一条，
        # 否则老师会陷入「解决 → 下次扫描又出现」的循环；只有条件升级才重新开。
        resolved_by_key: dict[tuple[int, str], sqlite3.Row] = {}
        for row in conn.execute(
            """
            SELECT id, student_id, kind, severity, description
            FROM alerts WHERE status = ? ORDER BY id
            """,
            (RESOLVED,),
        ):
            resolved_by_key[(int(row["student_id"]), str(row["kind"]))] = row
        student_ids = {
            str(row["student_uid"]): int(row["id"])
            for row in conn.execute("SELECT id, student_uid FROM students")
        }

        created = 0
        updated = 0
        skipped_resolved = 0
        if not dry_run:
            with conn:
                for candidate in candidates:
                    student_id = student_ids[candidate.student_uid]
                    existing = open_by_key.get((student_id, candidate.kind))
                    if existing is None:
                        resolved = resolved_by_key.get((student_id, candidate.kind))
                        if (
                            resolved is not None
                            and str(resolved["severity"]) == candidate.severity
                            and str(resolved["description"]) == candidate.description
                        ):
                            skipped_resolved += 1
                            continue
                        conn.execute(
                            """
                            INSERT INTO alerts
                                (student_id, kind, severity, status, description)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                student_id,
                                candidate.kind,
                                candidate.severity,
                                OPEN,
                                candidate.description,
                            ),
                        )
                        created += 1
                    elif (
                        str(existing["severity"]) != candidate.severity
                        or str(existing["description"]) != candidate.description
                    ):
                        conn.execute(
                            """
                            UPDATE alerts SET severity = ?, description = ?
                            WHERE id = ?
                            """,
                            (candidate.severity, candidate.description, int(existing["id"])),
                        )
                        updated += 1

        current_keys = {
            (student_ids[candidate.student_uid], candidate.kind) for candidate in candidates
        }
        stale = [
            row
            for key, row in open_by_key.items()
            if key not in current_keys
        ]
        return {
            "dry_run": dry_run,
            "candidates": candidates,
            "created": created,
            "updated": updated,
            "skipped_resolved": skipped_resolved,
            "stale": len(stale),
            "open_total": len(open_by_key) + (0 if dry_run else created),
        }
    finally:
        conn.close()


def list_alerts(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
) -> list[sqlite3.Row]:
    if status and status not in (OPEN, RESOLVED):
        raise config_loader.ConfigError(
            f"状态只能是 {OPEN} 或 {RESOLVED}，收到：{status!r}"
        )
    sql = """
        SELECT a.id, s.student_uid, s.name AS student_name, c.name AS class_name,
               a.kind, a.severity, a.status, a.description,
               a.created_at, a.resolved_at
        FROM alerts a
        JOIN students s ON s.id = a.student_id
        JOIN classes c ON c.id = s.class_id
    """
    params: list[Any] = []
    if status:
        sql += " WHERE a.status = ?"
        params.append(status)
    sql += " ORDER BY a.status, a.severity, a.created_at DESC, a.id DESC"
    return list(conn.execute(sql, params).fetchall())


def resolve_alert(
    config: config_loader.AppConfig,
    *,
    alert_id: int,
    note: str,
    outcome: str | None = None,
    recorded_by: str | None = None,
    resolved_at: str | None = None,
) -> dict[str, Any]:
    """解决一条预警：状态置为 resolved，并在同一事务里写一条跟进记录。"""
    text = (note or "").strip()
    if not text:
        raise config_loader.ConfigError("解决预警必须写跟进说明（--note）；不允许静默解决。")

    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        row = conn.execute(
            "SELECT id, status FROM alerts WHERE id = ?", (alert_id,)
        ).fetchone()
        if row is None:
            raise config_loader.ConfigError(f"找不到预警：id={alert_id}")
        if str(row["status"]) == RESOLVED:
            raise config_loader.ConfigError(f"预警 id={alert_id} 已经解决过了。")

        stamp = resolved_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with conn:
            conn.execute(
                "UPDATE alerts SET status = ?, resolved_at = ? WHERE id = ?",
                (RESOLVED, stamp, alert_id),
            )
            conn.execute(
                """
                INSERT INTO follow_ups (alert_id, note, outcome, recorded_by)
                VALUES (?, ?, ?, ?)
                """,
                (alert_id, text, (outcome or "").strip() or None, (recorded_by or "").strip() or None),
            )
        return {"alert_id": alert_id, "resolved_at": stamp, "note": text}
    finally:
        conn.close()


def alert_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT status, kind, severity, COUNT(*) AS n FROM alerts GROUP BY status, kind, severity"
    ).fetchall()
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for row in rows:
        count = int(row["n"])
        by_status[str(row["status"])] = by_status.get(str(row["status"]), 0) + count
        by_kind[str(row["kind"])] = by_kind.get(str(row["kind"]), 0) + count
        if str(row["status"]) == OPEN:
            by_severity[str(row["severity"])] = (
                by_severity.get(str(row["severity"]), 0) + count
            )
    follow_ups = conn.execute("SELECT COUNT(*) AS n FROM follow_ups").fetchone()["n"]
    return {
        "by_status": by_status,
        "by_kind": by_kind,
        "open_by_severity": by_severity,
        "follow_ups": int(follow_ups),
    }


def list_follow_ups(
    conn: sqlite3.Connection,
    *,
    alert_id: int | None = None,
    student_uid: str | None = None,
) -> list[sqlite3.Row]:
    """列出跟进记录（可按预警 id 或学生过滤），按记录时间倒序。"""
    sql = """
        SELECT f.id, f.alert_id, a.kind, a.status AS alert_status,
               s.student_uid, s.name AS student_name,
               f.note, f.outcome, f.recorded_by, f.created_at
        FROM follow_ups f
        JOIN alerts a ON a.id = f.alert_id
        JOIN students s ON s.id = a.student_id
    """
    conditions: list[str] = []
    params: list[Any] = []
    if alert_id is not None:
        conditions.append("f.alert_id = ?")
        params.append(alert_id)
    if student_uid:
        conditions.append("s.student_uid = ?")
        params.append(student_uid)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY f.created_at DESC, f.id DESC"
    return list(conn.execute(sql, params).fetchall())


def format_follow_up_table(rows: Sequence[sqlite3.Row]) -> str:
    lines = [
        "| 预警 id | 学生 | 规则 | 跟进说明 | 结果 | 跟进人 | 记录时间(UTC) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['alert_id']} | {row['student_name']}（{row['student_uid']}） | "
            f"{row['kind']} | {row['note']} | {row['outcome'] or '—'} | "
            f"{row['recorded_by'] or '—'} | {row['created_at']} |"
        )
    return "\n".join(lines)


def format_alert_table(rows: Sequence[sqlite3.Row]) -> str:
    lines = [
        "| id | 状态 | 级别 | 学生 | 班级 | 规则 | 说明 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['status']} | {row['severity']} | {row['student_name']}"
            f"（{row['student_uid']}） | {row['class_name']} | {row['kind']} | {row['description']} |"
        )
    return "\n".join(lines)
