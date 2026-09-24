"""周报与阶段巡检：从库里汇总，渲染成 Markdown。

- `weekly-report`：按教学周汇总（考试、作业、错因、行为、预警），默认上一周；
  `--week YYYY-Www` 显式指定，周必须落在学期内。
- `phase-patrol`：当前教学周进度、当前教学阶段、作业覆盖、预警未解决数与异常提醒。

报告文件只写相对路径、不带本机绝对路径，方便按需分享；时间按配置时区显示，
库里的时间戳按 UTC 存。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

import alerts as alerts_module
import config_loader
import homework as homework_module
import teaching_calendar

WEEK_LABEL_EXAMPLE = "2026-W42"
SUBMITTED_STATUSES = ("submitted", "late")


@dataclass(frozen=True)
class WeekWindow:
    label: str
    start: date
    end: date


@dataclass(frozen=True)
class WeeklyNumbers:
    week: WeekWindow
    teaching_week: int | None
    phase_name: str | None
    exams: tuple[Mapping[str, Any], ...]
    homework: tuple[Mapping[str, Any], ...]
    errors_by_tag: tuple[tuple[str, str, int], ...]
    behavior_count: int
    alerts_open: int
    alerts_created: int
    alerts_resolved: int


def parse_week_label(label: str) -> WeekWindow:
    """把 `YYYY-Www` 转成（周一, 周日）；格式不对就报错。"""
    text = (label or "").strip()
    try:
        year_text, week_text = text.split("-W")
        year, week = int(year_text), int(week_text)
        monday = date.fromisocalendar(year, week, 1)
    except (ValueError, AttributeError) as exc:
        raise config_loader.ConfigError(
            f"--week 的格式应为 YYYY-Www（如 {WEEK_LABEL_EXAMPLE}），收到：{label!r}"
        ) from exc
    return WeekWindow(
        label=f"{year}-W{week:02d}", start=monday, end=monday + timedelta(days=6)
    )


def previous_week_label(today: date) -> str:
    """上周的 ISO 周标签（周一为起点）。"""
    this_monday = today - timedelta(days=today.isoweekday() - 1)
    last_monday = this_monday - timedelta(days=7)
    iso_year, iso_week, _ = last_monday.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def resolve_week_window(
    config: config_loader.AppConfig,
    week_label: str | None,
) -> tuple[WeekWindow, teaching_calendar.TeachingCalendar]:
    """确定要汇总的那一周，并确认它落在学期内。"""
    calendar = teaching_calendar.TeachingCalendar.from_config(config)
    window = parse_week_label(week_label or previous_week_label(calendar.today()))
    if not calendar.is_in_semester(window.start):
        raise config_loader.ConfigError(
            f"{window.label}（{window.start.isoformat()} 起）不在学期内："
            f"本学期 {config.semester.starts_on.isoformat()} ~ "
            f"{config.semester.ends_on.isoformat()}。"
        )
    return window, calendar


def _class_condition(class_name: str | None) -> tuple[str, list[Any]]:
    """班级过滤片段：所有按班级汇总的查询都用同一套写法。"""
    if not class_name:
        return "", []
    return " AND c.name = ?", [class_name]


def collect_weekly_numbers(
    config: config_loader.AppConfig,
    conn: sqlite3.Connection,
    *,
    week_label: str | None = None,
    class_name: str | None = None,
) -> WeeklyNumbers:
    """把某一周的数据汇总出来（只读）。"""
    window, calendar = resolve_week_window(config, week_label)
    start, end = window.start.isoformat(), window.end.isoformat()
    class_sql, class_params = _class_condition(class_name)

    exam_rows = conn.execute(
        f"""
        SELECT e.name AS exam_name, e.exam_date, e.full_score,
               COUNT(es.id) AS student_count, AVG(es.score) AS average
        FROM exams e
        LEFT JOIN exam_scores es ON es.exam_id = e.id
        LEFT JOIN students s ON s.id = es.student_id
        LEFT JOIN classes c ON c.id = s.class_id
        WHERE e.exam_date BETWEEN ? AND ?{class_sql}
        GROUP BY e.id
        ORDER BY e.exam_date, e.id
        """,
        [start, end, *class_params],
    ).fetchall()

    homework_rows = conn.execute(
        f"""
        SELECT a.assign_key, a.topic, a.assigned_date, c.name AS class_name,
               COUNT(s.id) AS expected,
               SUM(CASE WHEN sub.status IN ('submitted', 'late') THEN 1 ELSE 0 END) AS submitted
        FROM homework_assignments a
        JOIN classes c ON c.id = a.class_id
        LEFT JOIN students s ON s.class_id = a.class_id
        LEFT JOIN homework_submissions sub
               ON sub.assignment_id = a.id AND sub.student_id = s.id
        WHERE a.assigned_date BETWEEN ? AND ?{class_sql}
        GROUP BY a.id
        ORDER BY a.assigned_date, a.id
        """,
        [start, end, *class_params],
    ).fetchall()

    error_rows = conn.execute(
        f"""
        SELECT t.code, t.label, COUNT(*) AS n
        FROM error_records er
        JOIN error_tags t ON t.id = er.tag_id
        JOIN students s ON s.id = er.student_id
        JOIN classes c ON c.id = s.class_id
        WHERE date(er.recorded_at) BETWEEN ? AND ?{class_sql}
        GROUP BY t.id
        ORDER BY n DESC, t.code
        """,
        [start, end, *class_params],
    ).fetchall()

    behavior_count = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM behavior_records br
        JOIN students s ON s.id = br.student_id
        JOIN classes c ON c.id = s.class_id
        WHERE date(br.recorded_at) BETWEEN ? AND ?{class_sql}
        """,
        [start, end, *class_params],
    ).fetchone()["n"]

    def count_alerts(condition: str, params: list[Any]) -> int:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n FROM alerts a
            JOIN students s ON s.id = a.student_id
            JOIN classes c ON c.id = s.class_id
            WHERE {condition}{class_sql}
            """,
            [*params, *class_params],
        ).fetchone()
        return int(row["n"])

    phase = calendar.current_phase(window.start)
    return WeeklyNumbers(
        week=window,
        teaching_week=calendar.week_of(window.start),
        phase_name=phase.name if phase else None,
        exams=tuple(dict(row) for row in exam_rows),
        homework=tuple(dict(row) for row in homework_rows),
        errors_by_tag=tuple(
            (row["code"], row["label"], int(row["n"])) for row in error_rows
        ),
        behavior_count=int(behavior_count),
        alerts_open=count_alerts("a.status = 'open'", []),
        alerts_created=count_alerts("date(a.created_at) BETWEEN ? AND ?", [start, end]),
        alerts_resolved=count_alerts(
            "a.status = 'resolved' AND date(a.resolved_at) BETWEEN ? AND ?", [start, end]
        ),
    )


def _percentage(part: int, whole: int) -> str:
    return "—" if whole <= 0 else f"{part / whole * 100:.1f}%"


def _counts_text(counts: Mapping[str, int] | None) -> str:
    """把 {kind: n} 排成人读的样子：`a 3、b 1`；空的时候给「无」。"""
    if not counts:
        return "无"
    return "、".join(f"{key} {value}" for key, value in sorted(counts.items()))


def render_weekly_report(
    config: config_loader.AppConfig,
    numbers: WeeklyNumbers,
    *,
    class_name: str | None = None,
) -> str:
    scope = class_name or "全部班级"
    progress = (
        f"第 {numbers.teaching_week} 教学周"
        if numbers.teaching_week
        else "不在学期内"
    )
    lines = [
        f"# 周报 · {config.semester.name} · {numbers.week.label}",
        "",
        f"- 范围：{scope}",
        f"- 日期：{numbers.week.start.isoformat()} ~ {numbers.week.end.isoformat()}",
        f"- 教学周：{progress}",
        f"- 教学阶段：{numbers.phase_name or '—'}",
        "",
        "## 本周考试",
        "",
    ]
    if numbers.exams:
        lines.extend(["| 考试 | 日期 | 满分 | 人数 | 平均分 |", "| --- | --- | --- | --- | --- |"])
        for exam in numbers.exams:
            average = exam["average"]
            shown = "—" if average is None else f"{float(average):.1f}"
            lines.append(
                f"| {exam['exam_name']} | {exam['exam_date']} | {float(exam['full_score']):g} | "
                f"{int(exam['student_count'])} | {shown} |"
            )
    else:
        lines.append("本周没有考试记录。")

    lines.extend(["", "## 本周作业", ""])
    if numbers.homework:
        lines.extend(
            [
                "| 作业 | 班级 | 布置日期 | 应交 | 已交 | 完成率 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in numbers.homework:
            expected = int(item["expected"] or 0)
            submitted = int(item["submitted"] or 0)
            lines.append(
                f"| {item['topic']} | {item['class_name']} | {item['assigned_date']} | "
                f"{expected} | {submitted} | {_percentage(submitted, expected)} |"
            )
    else:
        lines.append("本周没有布置作业。")

    lines.extend(["", "## 本周错因与行为", ""])
    if numbers.errors_by_tag:
        lines.extend(["| 错因 | 条数 |", "| --- | --- |"])
        for _code, label, count in numbers.errors_by_tag:
            lines.append(f"| {label} | {count} |")
    else:
        lines.append("本周没有错因记录。")
    lines.extend(["", f"行为记录：{numbers.behavior_count} 条"])

    lines.extend(
        [
            "",
            "## 预警",
            "",
            f"- 本周新建：{numbers.alerts_created} 条",
            f"- 本周解决：{numbers.alerts_resolved} 条",
            f"- 仍未解决：{numbers.alerts_open} 条",
            "",
            "## 说明",
            "",
            "- 本报告由本地数据库汇总生成，只包含本机数据。",
            "- 完成率口径：分子分母都只算「作业所属班级的当前学生」。",
            "- 入库时间按 UTC 存，本报告按配置时区显示。",
            "",
        ]
    )
    return "\n".join(lines)


def write_weekly_report(
    config: config_loader.AppConfig,
    conn: sqlite3.Connection,
    *,
    week_label: str | None = None,
    class_name: str | None = None,
) -> Path:
    numbers = collect_weekly_numbers(
        config, conn, week_label=week_label, class_name=class_name
    )
    path = config.paths.output_dir / f"weekly_{numbers.week.label}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_weekly_report(config, numbers, class_name=class_name), encoding="utf-8"
    )
    return path


def collect_phase_patrol(
    config: config_loader.AppConfig,
    conn: sqlite3.Connection,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """阶段巡检要看的数字：进度、覆盖、预警、画像。"""
    calendar = teaching_calendar.TeachingCalendar.from_config(config)
    day = today or calendar.today()
    phase = calendar.current_phase(day)
    profile_rows = conn.execute(
        """
        SELECT dimension, AVG(score) AS average, COUNT(*) AS n
        FROM ability_scores GROUP BY dimension ORDER BY dimension
        """
    ).fetchall()
    return {
        "today": day,
        "in_semester": calendar.is_in_semester(day),
        "teaching_week": calendar.week_of(day),
        "week_total": calendar.weeks_total(),
        "phase_name": phase.name if phase else None,
        "phase_range": (phase.starts_on, phase.ends_on) if phase else None,
        "classes": homework_module.homework_stats(conn),
        "alert_stats": alerts_module.alert_stats(conn),
        "profiles": {
            str(row["dimension"]): (float(row["average"]), int(row["n"]))
            for row in profile_rows
        },
    }


def render_phase_patrol(
    config: config_loader.AppConfig,
    patrol: Mapping[str, Any],
) -> str:
    today: date = patrol["today"]
    lines = [
        f"# 阶段巡检 · {config.semester.name}",
        "",
        f"- 日期：{today.isoformat()}（{config.project.timezone}）",
        f"- 学段 / 学科：{config.project.stage_label} / {config.project.subject_label}",
    ]
    if not patrol["in_semester"]:
        lines.append("- 今天不在学期内；下面的统计只作参考。")
    week = patrol["teaching_week"]
    lines.append(
        f"- 教学进度：第 {week} 教学周 / 共 {patrol['week_total']} 周"
        if week
        else f"- 教学进度：还没开课（学期共 {patrol['week_total']} 周）"
    )
    if patrol["phase_range"]:
        start, end = patrol["phase_range"]
        lines.append(
            f"- 当前阶段：{patrol['phase_name']}（{start.isoformat()} ~ {end.isoformat()}）"
        )
    else:
        lines.append("- 当前阶段：—")

    lines.extend(["", "## 作业覆盖", ""])
    classes = patrol["classes"]
    if classes:
        lines.extend(
            [
                "| 班级 | 作业数 | 应交 | 已交 | 缺交 | 完成率 | 订正率 |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in classes:
            lines.append(
                f"| {item.class_name} | {item.assignment_count} | {item.expected} | "
                f"{item.submitted} | {item.missing} | {item.completion_rate * 100:.1f}% | "
                f"{item.correction_rate * 100:.1f}% |"
            )
    else:
        lines.append("还没有作业数据。")

    stats = patrol["alert_stats"]
    lines.extend(
        [
            "",
            "## 预警",
            "",
            f"- 未解决：{stats['by_status'].get('open', 0)} 条"
            f"（按级别：{_counts_text(stats['open_by_severity'])}）",
            f"- 已解决：{stats['by_status'].get('resolved', 0)} 条",
            f"- 按规则：{_counts_text(stats['by_kind'])}",
            f"- 跟进记录：{stats['follow_ups']} 条",
        ]
    )

    profiles = patrol["profiles"]
    if profiles:
        lines.extend(["", "## 画像（平均分）", ""])
        for dimension, (average, count) in sorted(profiles.items()):
            lines.append(f"- {dimension}：{average:.1f}（{count} 条）")

    lines.extend(["", "## 异常提醒", ""])
    reminders: list[str] = []
    for item in classes:
        if item.expected > 0 and item.completion_rate < 0.8:
            reminders.append(
                f"{item.class_name} 作业完成率 {item.completion_rate * 100:.1f}%，低于 80%"
            )
    critical = stats["open_by_severity"].get("critical", 0)
    if critical:
        reminders.append(f"有 {critical} 条未解决的 critical 预警")
    if not stats["by_status"]:
        reminders.append("还没有扫描过预警：先跑 python3 hub.py scan-alerts")
    if not profiles:
        reminders.append("还没有算过画像：先跑 python3 hub.py compute-profile")
    if not reminders:
        reminders.append("没有需要特别提醒的事项")
    lines.extend(f"- {text}" for text in reminders)

    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 提醒只是按阈值拉出来的线索，不是结论；请结合课堂观察判断。",
            "- 本报告由本地数据库汇总生成，只包含本机数据。",
            "",
        ]
    )
    return "\n".join(lines)


def write_phase_patrol(
    config: config_loader.AppConfig,
    conn: sqlite3.Connection,
    *,
    today: date | None = None,
) -> Path:
    patrol = collect_phase_patrol(config, conn, today=today)
    day: date = patrol["today"]
    path = config.paths.output_dir / f"phase_patrol_{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_phase_patrol(config, patrol), encoding="utf-8")
    return path
