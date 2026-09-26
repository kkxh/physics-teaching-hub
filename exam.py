"""考试链路：小题级导入、考试分析、讲评讲义。

方法说明（也写进生成的报告里）：

- 得分率 = 该题平均分 ÷ 该题满分；
- 难度分档按难度系数 P（= 得分率）：`易` P ≥ 0.7、`中` 0.4 ≤ P < 0.7、`难` P < 0.4；
- 区分度用**高低分组法**：按总分排序取前 27%（高分组）与后 27%（低分组），
  D = 高分组该题得分率 − 低分组该题得分率；D ≥ 0.4 算好，0.2 ~ 0.4 可接受，< 0.2 需修改；
  人数太少时每组至少取 1 人。

版权边界：讲义里**不放任何试卷原题**，题目位置用自制示例题标记占位，题面由使用者自行补入。
"""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import config_loader
import db as db_module
import importer
import questions as questions_module

DEFAULT_ITEM_COLUMNS: Mapping[str, str] = {
    "student_uid": "student_uid",
    "name": "name",
    "item_no": "item_no",
    "score": "score",
    "full_score": "full_score",
}
DEFAULT_ITEM_FULL_SCORE = 10.0
SAMPLE_QUESTION_MARKER = "【自制示例题】"
# 文件名里不安全的字符（组卷讲义按题目 key / 学生学号命名，可能带中文与符号）
UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\s]+')

EASY_THRESHOLD = 0.7
HARD_THRESHOLD = 0.4
DISCRIMINATION_GOOD = 0.4
DISCRIMINATION_ACCEPTABLE = 0.2
# 得分率/区分度都是浮点减法算出来的，边界比较留一点余量，
# 免得算出来正好是 0.7 或 0.2 时因为 1e-16 的误差掉到下一档
BOUNDARY_EPSILON = 1e-9


@dataclass(frozen=True)
class ItemRow:
    row_number: int
    student_uid: str | None
    name: str | None
    item_no: str
    score: float
    full_score: float | None


@dataclass(frozen=True)
class ResolvedItemScore:
    row_number: int
    student_id: int
    student_uid: str
    student_name: str
    item_no: str
    score: float
    full_score: float


@dataclass(frozen=True)
class ItemImportPlan:
    exam_key: str
    exam_name: str
    scores: tuple[ResolvedItemScore, ...] = ()
    items: tuple[tuple[str, float], ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def row_count(self) -> int:
        return len(self.scores)


@dataclass(frozen=True)
class ItemStats:
    item_no: str
    full_score: float
    student_count: int
    average: float | None
    score_rate: float | None
    difficulty_band: str
    high_rate: float | None
    low_rate: float | None
    discrimination: float | None
    discrimination_band: str


@dataclass(frozen=True)
class ExamAnalysis:
    exam_key: str
    exam_name: str
    exam_date: str
    full_score: float
    student_count: int
    average: float | None
    highest: float | None
    lowest: float | None
    pass_rate: float | None
    items: tuple[ItemStats, ...]
    error_tag_counts: tuple[tuple[str, str, int], ...]
    problems: tuple[str, ...] = ()


def parse_full_score(raw: Any, row_number: int) -> float | None:
    text = "" if raw is None else str(raw).strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError as exc:
        raise config_loader.ConfigError(
            f"第 {row_number} 行的小题满分不是数字：{raw!r}"
        ) from exc
    if value <= 0:
        raise config_loader.ConfigError(
            f"第 {row_number} 行的小题满分必须大于 0：{value:g}"
        )
    return value


def parse_score(raw: Any, row_number: int) -> float:
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise config_loader.ConfigError(f"第 {row_number} 行没有分数，拒绝导入。")
    try:
        value = float(text)
    except ValueError as exc:
        raise config_loader.ConfigError(
            f"第 {row_number} 行的小题得分不是数字：{raw!r}"
        ) from exc
    if value < 0:
        raise config_loader.ConfigError(f"第 {row_number} 行的小题得分是负数：{value:g}")
    return value


def read_item_rows(csv_path: Path, columns: Mapping[str, str]) -> list[ItemRow]:
    """读小题得分 CSV：必须有 item_no 与 score，学生标识至少一列。"""
    if not csv_path.is_file():
        raise config_loader.ConfigError(f"找不到小题得分表：{csv_path}")

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        present = importer.resolve_columns(
            headers, columns, csv_path.name, required=("item_no", "score")
        )
        rows: list[ItemRow] = []
        for row_number, raw in enumerate(reader, start=2):
            item_no = importer.field_value(raw, present, "item_no")
            if not item_no:
                raise config_loader.ConfigError(f"第 {row_number} 行缺少小题号。")
            rows.append(
                ItemRow(
                    row_number=row_number,
                    student_uid=importer.field_value(raw, present, "student_uid"),
                    name=importer.field_value(raw, present, "name"),
                    item_no=item_no,
                    score=parse_score(importer.field_value(raw, present, "score"), row_number),
                    full_score=parse_full_score(
                        importer.field_value(raw, present, "full_score"), row_number
                    ),
                )
            )

    if not rows:
        raise config_loader.ConfigError(f"{csv_path.name} 里没有任何小题得分。")
    return rows


def resolve_exam(conn: sqlite3.Connection, exam_key: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, exam_key, name, exam_date, full_score FROM exams WHERE exam_key = ?",
        (exam_key,),
    ).fetchone()
    if row is None:
        known = [
            str(item["exam_key"])
            for item in conn.execute("SELECT exam_key FROM exams ORDER BY id")
        ]
        raise config_loader.ConfigError(
            f"找不到考试：exam_key={exam_key!r}；库里的考试有：{'、'.join(known) or '（还没有）'}。"
            "先用 import-scores 建考试，再导小题得分。"
        )
    return row


def build_item_plan(
    conn: sqlite3.Connection,
    *,
    rows: Sequence[ItemRow],
    exam_key: str,
    default_item_score: float,
) -> ItemImportPlan:
    """把小题得分匹配到库内考试与学生，并定下每题的满分。"""
    exam = resolve_exam(conn, exam_key)
    exam_id = int(exam["id"])

    existing_items = {
        str(row["item_no"]): row
        for row in conn.execute(
            "SELECT id, item_no, full_score FROM exam_items WHERE exam_id = ?", (exam_id,)
        )
    }

    item_full_scores: dict[str, float] = {
        item_no: float(row["full_score"]) for item_no, row in existing_items.items()
    }
    warnings: list[str] = []
    resolved: list[ResolvedItemScore] = []
    seen: set[tuple[int, str]] = set()

    for row in rows:
        full_score = row.full_score
        if full_score is None:
            full_score = item_full_scores.get(row.item_no, default_item_score)
        elif row.item_no in item_full_scores and abs(
            item_full_scores[row.item_no] - full_score
        ) > 1e-9:
            warnings.append(
                f"第 {row.row_number} 行给 {row.item_no} 的满分是 {full_score:g}，"
                f"库里记的是 {item_full_scores[row.item_no]:g}；按行里的值更新。"
            )
        item_full_scores[row.item_no] = full_score

        if row.score > full_score + 1e-9:
            raise config_loader.ConfigError(
                f"第 {row.row_number} 行 {row.item_no} 的得分 {row.score:g} "
                f"超过该题满分 {full_score:g}；请先核对数据。"
            )

        student = importer.resolve_student(
            conn, student_uid=row.student_uid, name=row.name
        )
        student_id = int(student["id"])
        key = (student_id, row.item_no)
        if key in seen:
            warnings.append(
                f"第 {row.row_number} 行 {student['name']} 的 {row.item_no} 重复，"
                "后一条覆盖前一条。"
            )
        seen.add(key)
        resolved.append(
            ResolvedItemScore(
                row_number=row.row_number,
                student_id=student_id,
                student_uid=str(student["student_uid"]),
                student_name=str(student["name"]),
                item_no=row.item_no,
                score=row.score,
                full_score=full_score,
            )
        )

    return ItemImportPlan(
        exam_key=str(exam["exam_key"]),
        exam_name=str(exam["name"]),
        scores=tuple(resolved),
        items=tuple(sorted(item_full_scores.items())),
        warnings=tuple(warnings),
    )


def apply_item_plan(conn: sqlite3.Connection, plan: ItemImportPlan) -> dict[str, int]:
    """写入小题与小題得分：单事务，出错整体回滚。"""
    exam = resolve_exam(conn, plan.exam_key)
    exam_id = int(exam["id"])
    items_written = 0
    scores_written = 0

    with conn:
        item_ids: dict[str, int] = {}
        for item_no, full_score in plan.items:
            conn.execute(
                """
                INSERT INTO exam_items (exam_id, item_no, full_score)
                VALUES (?, ?, ?)
                ON CONFLICT (exam_id, item_no) DO UPDATE SET full_score = excluded.full_score
                """,
                (exam_id, item_no, float(full_score)),
            )
            item_ids[item_no] = int(
                conn.execute(
                    "SELECT id FROM exam_items WHERE exam_id = ? AND item_no = ?",
                    (exam_id, item_no),
                ).fetchone()["id"]
            )
            items_written += 1

        for item in plan.scores:
            item_id = item_ids.get(item.item_no)
            if item_id is None:
                raise config_loader.ConfigError(f"小题 {item.item_no} 没有登记满分，拒绝写入。")
            conn.execute(
                """
                INSERT INTO item_scores (item_id, student_id, score)
                VALUES (?, ?, ?)
                ON CONFLICT (item_id, student_id) DO UPDATE SET score = excluded.score
                """,
                (item_id, item.student_id, float(item.score)),
            )
            scores_written += 1

    return {"items_written": items_written, "item_scores_written": scores_written}


def import_item_scores_from_csv(
    config: config_loader.AppConfig,
    *,
    csv_path: str | Path,
    exam_key: str,
    default_item_score: float = DEFAULT_ITEM_FULL_SCORE,
    dry_run: bool = False,
    columns_spec: str | None = None,
) -> dict[str, Any]:
    if not config.paths.database.is_file():
        raise config_loader.ConfigError(
            f"还没有数据库：{config.paths.database}；请先运行 python3 hub.py init-db --demo。"
        )

    columns = importer.parse_columns(columns_spec, DEFAULT_ITEM_COLUMNS)
    rows = read_item_rows(Path(csv_path), columns)

    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        plan = build_item_plan(
            conn, rows=rows, exam_key=exam_key, default_item_score=default_item_score
        )
        if dry_run:
            return {"dry_run": True, "plan": plan}
        counts = apply_item_plan(conn, plan)
        return {"dry_run": False, "plan": plan, **counts}
    finally:
        conn.close()


def import_demo_item_scores(
    conn: sqlite3.Connection,
    dataset: Mapping[str, Any],
    *,
    default_item_score: float = DEFAULT_ITEM_FULL_SCORE,
) -> dict[str, int]:
    """把演示数据集里的小题与小題得分写进库（供 import-scores --demo 调用）。"""
    items_written = 0
    scores_written = 0
    for exam in dataset.get("exams") or []:
        items = exam.get("items") or []
        if not items:
            continue
        exam_key = str(exam.get("key") or "")
        rows: list[ItemRow] = []
        item_full_scores: dict[str, float] = {}
        for item in items:
            item_no = str(item.get("item_no") or "")
            full_score = float(item.get("full_score") or default_item_score)
            item_full_scores[item_no] = full_score
            for record in item.get("scores") or []:
                rows.append(
                    ItemRow(
                        row_number=0,
                        student_uid=str(record.get("student_id") or ""),
                        name=None,
                        item_no=item_no,
                        score=float(record.get("score") or 0),
                        full_score=full_score,
                    )
                )
        plan = build_item_plan(
            conn, rows=rows, exam_key=exam_key, default_item_score=default_item_score
        )
        counts = apply_item_plan(conn, plan)
        items_written += counts["items_written"]
        scores_written += counts["item_scores_written"]
    return {"items": items_written, "item_scores": scores_written}


def difficulty_band(score_rate: float | None) -> str:
    if score_rate is None:
        return "—"
    if score_rate >= EASY_THRESHOLD - BOUNDARY_EPSILON:
        return "易"
    if score_rate >= HARD_THRESHOLD - BOUNDARY_EPSILON:
        return "中"
    return "难"


def discrimination_band(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= DISCRIMINATION_GOOD - BOUNDARY_EPSILON:
        return "好"
    if value >= DISCRIMINATION_ACCEPTABLE - BOUNDARY_EPSILON:
        return "可接受"
    return "需修改"


def group_size(student_count: int) -> int:
    """高低分组各取多少人：27% 取整，至少 1 人。"""
    return max(1, round(student_count * 0.27))


def analyze_exam(
    conn: sqlite3.Connection,
    *,
    exam_key: str,
) -> ExamAnalysis:
    """算一场考试的总体统计、逐题得分率/难度/区分度，以及错因分布。"""
    exam = resolve_exam(conn, exam_key)
    exam_id = int(exam["id"])
    full_score = float(exam["full_score"])

    total_rows = conn.execute(
        """
        SELECT s.id AS student_id, s.name AS student_name, es.score AS score
        FROM exam_scores es JOIN students s ON s.id = es.student_id
        WHERE es.exam_id = ?
        ORDER BY es.score DESC, s.id
        """,
        (exam_id,),
    ).fetchall()
    scores = [float(row["score"]) for row in total_rows]
    student_count = len(scores)
    average = sum(scores) / student_count if scores else None
    highest = max(scores) if scores else None
    lowest = min(scores) if scores else None
    pass_rate = (
        len([score for score in scores if score >= full_score * 0.6]) / student_count
        if student_count
        else None
    )

    size = group_size(student_count) if student_count else 0
    high_ids = {int(row["student_id"]) for row in total_rows[:size]} if size else set()
    low_ids = {int(row["student_id"]) for row in total_rows[-size:]} if size else set()

    item_rows = conn.execute(
        """
        SELECT id, item_no, full_score FROM exam_items
        WHERE exam_id = ? ORDER BY id
        """,
        (exam_id,),
    ).fetchall()
    problems: list[str] = []
    items: list[ItemStats] = []
    for item in item_rows:
        item_id = int(item["id"])
        item_full = float(item["full_score"])
        score_rows = conn.execute(
            """
            SELECT student_id, score FROM item_scores WHERE item_id = ? ORDER BY student_id
            """,
            (item_id,),
        ).fetchall()
        item_scores = [float(row["score"]) for row in score_rows]
        item_count = len(item_scores)
        item_average = sum(item_scores) / item_count if item_count else None
        rate = item_average / item_full if item_average is not None and item_full > 0 else None

        def group_rate(ids: set[int]) -> float | None:
            picked = [
                float(row["score"]) / item_full
                for row in score_rows
                if int(row["student_id"]) in ids
            ]
            return sum(picked) / len(picked) if picked else None

        high_rate = group_rate(high_ids)
        low_rate = group_rate(low_ids)
        discrimination = (
            high_rate - low_rate
            if high_rate is not None and low_rate is not None
            else None
        )
        if item_count < student_count:
            problems.append(
                f"{item['item_no']} 只有 {item_count} 条小题得分（总分有 {student_count} 人）"
            )

        items.append(
            ItemStats(
                item_no=str(item["item_no"]),
                full_score=item_full,
                student_count=item_count,
                average=item_average,
                score_rate=rate,
                difficulty_band=difficulty_band(rate),
                high_rate=high_rate,
                low_rate=low_rate,
                discrimination=discrimination,
                discrimination_band=discrimination_band(discrimination),
            )
        )

    error_rows = conn.execute(
        """
        SELECT t.code, t.label, COUNT(*) AS n
        FROM error_records er JOIN error_tags t ON t.id = er.tag_id
        WHERE er.exam_id = ?
        GROUP BY t.id ORDER BY n DESC, t.code
        """,
        (exam_id,),
    ).fetchall()

    return ExamAnalysis(
        exam_key=str(exam["exam_key"]),
        exam_name=str(exam["name"]),
        exam_date=str(exam["exam_date"]),
        full_score=full_score,
        student_count=student_count,
        average=average,
        highest=highest,
        lowest=lowest,
        pass_rate=pass_rate,
        items=tuple(items),
        error_tag_counts=tuple(
            (row["code"], row["label"], int(row["n"])) for row in error_rows
        ),
        problems=tuple(problems),
    )


def _score_text(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _rate_text(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render_exam_analysis(
    config: config_loader.AppConfig,
    analysis: ExamAnalysis,
) -> str:
    lines = [
        f"# 考试分析 · {analysis.exam_name}",
        "",
        f"- 考试标识：{analysis.exam_key}",
        f"- 考试日期：{analysis.exam_date}",
        f"- 满分：{analysis.full_score:g}",
        f"- 人数：{analysis.student_count}",
        f"- 平均分：{_score_text(analysis.average)}"
        f"（最高 {_score_text(analysis.highest)} / 最低 {_score_text(analysis.lowest)}）",
        f"- 及格率（≥ 满分 60%）：{_rate_text(analysis.pass_rate)}",
        "",
        "## 逐题分析",
        "",
    ]
    if analysis.items:
        lines.extend(
            [
                "| 题号 | 满分 | 人数 | 平均分 | 得分率 | 难度 | 高分组 | 低分组 | 区分度 | 区分度评价 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in analysis.items:
            lines.append(
                f"| {item.item_no} | {item.full_score:g} | {item.student_count} | "
                f"{_score_text(item.average)} | {_rate_text(item.score_rate)} | "
                f"{item.difficulty_band} | {_rate_text(item.high_rate)} | "
                f"{_rate_text(item.low_rate)} | {_score_text(item.discrimination, 2)} | "
                f"{item.discrimination_band} |"
            )
    else:
        lines.append("这场考试还没有小题得分：用 import-item-scores 导入后再看。")

    lines.extend(["", "## 错因分布", ""])
    if analysis.error_tag_counts:
        lines.extend(["| 错因 | 条数 |", "| --- | --- |"])
        for _code, label, count in analysis.error_tag_counts:
            lines.append(f"| {label} | {count} |")
    else:
        lines.append("这场考试还没有登记错因记录。")

    lines.extend(["", "## 讲评顺序建议", ""])
    lines.extend(f"- {text}" for text in teaching_priorities(analysis))

    if analysis.problems:
        lines.extend(["", "## 数据提示", ""])
        lines.extend(f"- {text}" for text in analysis.problems)

    lines.extend(
        [
            "",
            "## 方法说明",
            "",
            f"- 难度分档按难度系数 P（= 得分率）：易 P ≥ {EASY_THRESHOLD}、"
            f"中 {HARD_THRESHOLD} ≤ P < {EASY_THRESHOLD}、难 P < {HARD_THRESHOLD}。",
            f"- 区分度用高低分组法：按总分取前 27% 与后 27%（至少各 1 人），"
            f"D = 高分组得分率 − 低分组得分率；D ≥ {DISCRIMINATION_GOOD} 算好，"
            f"{DISCRIMINATION_ACCEPTABLE} ~ {DISCRIMINATION_GOOD} 可接受，"
            f"< {DISCRIMINATION_ACCEPTABLE} 需修改。",
            "- 本报告由本地数据库汇总生成，只包含本机数据；文件里只写相对路径。",
            "",
        ]
    )
    return "\n".join(lines)


def teaching_priorities(analysis: ExamAnalysis) -> list[str]:
    """按「难且能拉开差距」优先，给出讲评顺序建议。"""
    if not analysis.items:
        return ["还没有小题数据，暂时无法排序。"]

    def priority_key(item: ItemStats) -> tuple[float, float]:
        rate = item.score_rate if item.score_rate is not None else 1.0
        disc = item.discrimination if item.discrimination is not None else 0.0
        return (rate, -disc)

    ordered = sorted(analysis.items, key=priority_key)
    suggestions: list[str] = []
    for index, item in enumerate(ordered[:3], start=1):
        rate = _rate_text(item.score_rate)
        disc = _score_text(item.discrimination, 2)
        suggestions.append(
            f"第 {index} 讲：{item.item_no}（得分率 {rate}，区分度 {disc}，"
            f"难度 {item.difficulty_band}）"
        )
    hardest = ordered[0]
    suggestions.append(
        f"基础巩固：其余题目先过一遍得分率最低的 {hardest.item_no}，"
        "再按错因分布挑 2~3 类高频错因集中讲。"
    )
    return suggestions


def render_handout(
    config: config_loader.AppConfig,
    analysis: ExamAnalysis,
) -> str:
    """讲评讲义：只写数据与讲评建议，题目位置用自制示例题标记占位。"""
    lines = [
        f"# 讲评讲义 · {analysis.exam_name}",
        "",
        f"- 考试标识：{analysis.exam_key}（{analysis.exam_date}）",
        f"- 人数：{analysis.student_count}｜平均分：{_score_text(analysis.average)}"
        f"｜及格率：{_rate_text(analysis.pass_rate)}",
        "",
        "> 本讲义**不含试卷原题**：题目请自行补入使用者有权使用的材料。",
        f"> 每道题的题面位置留了 {SAMPLE_QUESTION_MARKER} 标记，方便你逐题粘贴或板书。",
        "",
        "## 讲评顺序",
        "",
    ]
    lines.extend(f"{index}. {text}" for index, text in enumerate(teaching_priorities(analysis), start=1))

    lines.extend(["", "## 逐题讲评卡片", ""])
    if not analysis.items:
        lines.append("还没有小题得分数据。")
    for item in analysis.items:
        lines.extend(
            [
                f"### {item.item_no}",
                "",
                f"{SAMPLE_QUESTION_MARKER}（题面位置：{item.item_no}）",
                "",
                f"- 数据：满分 {item.full_score:g}，平均分 {_score_text(item.average)}，"
                f"得分率 {_rate_text(item.score_rate)}，难度 {item.difficulty_band}，"
                f"区分度 {_score_text(item.discrimination, 2)}（{item.discrimination_band}）",
                f"- 讲评建议：{item_advice(item)}",
                "",
            ]
        )

    lines.extend(["", "## 错因聚焦", ""])
    if analysis.error_tag_counts:
        for _code, label, count in analysis.error_tag_counts:
            lines.append(f"- {label}：{count} 条")
    else:
        lines.append("- 这场考试还没有登记错因记录，可在讲评后补录。")

    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 讲义由本地数据库汇总生成；不含任何第三方题目内容，也不含本机绝对路径。",
            "- 数据只反映这一次考试，不构成对学生的评价结论。",
            "",
        ]
    )
    return "\n".join(lines)


def item_advice(item: ItemStats) -> str:
    if item.score_rate is None:
        return "先补齐这一题的小题得分。"
    if item.score_rate < HARD_THRESHOLD and (item.discrimination or 0) >= DISCRIMINATION_GOOD:
        return "难但能拉开差距：值得整题精讲，讲完当堂练一道同类题。"
    if item.score_rate < HARD_THRESHOLD:
        return "偏难且区分度不高：先确认是不是题目本身有问题，再决定要不要细讲。"
    if item.score_rate >= EASY_THRESHOLD and (item.discrimination or 0) < DISCRIMINATION_ACCEPTABLE:
        return "普遍做对：快速带过，把时间留给中低得分率的题。"
    return "中档题：抽查解题过程，重点看步骤与表达是否规范。"


def write_exam_analysis(
    config: config_loader.AppConfig,
    conn: sqlite3.Connection,
    *,
    exam_key: str,
) -> Path:
    analysis = analyze_exam(conn, exam_key=exam_key)
    path = config.paths.output_dir / f"exam_analysis_{exam_key}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_exam_analysis(config, analysis), encoding="utf-8")
    return path


def write_handout(
    config: config_loader.AppConfig,
    conn: sqlite3.Connection,
    *,
    exam_key: str,
) -> Path:
    analysis = analyze_exam(conn, exam_key=exam_key)
    path = config.paths.output_dir / f"handout_{exam_key}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_handout(config, analysis), encoding="utf-8")
    return path


def render_question_handout(
    config: config_loader.AppConfig,
    items: Sequence[questions_module.Question],
    *,
    source: str,
    student_label: str | None = None,
    with_answer: bool = False,
) -> str:
    """组卷讲义：题目来自使用者题库或自制示例题，结构取自文案表的 [handout] 段。

    默认**不含答案与解析**（课堂投影优先）；`with_answer=True` 才在文末追加一节。
    讲义只写题目内容与来源，不嵌入任何第三方教材原文。
    """
    labels = config.labels
    lines = [f"# {labels.get('handout.title_questions')}", ""]
    lines.append(f"- {labels.get('handout.label_source')}：{source}")
    if student_label:
        lines.append(f"- {labels.get('handout.label_student')}：{student_label}")
    lines.append(f"- {labels.get('handout.label_count')}：{len(items)} 道")
    lines.extend(["", f"> {labels.get('handout.boundary')}"])
    if not with_answer:
        lines.append(f"> {labels.get('handout.answers_hidden')}")

    lines.extend(["", f"## {labels.get('handout.section_questions')}", ""])
    for index, question in enumerate(items, start=1):
        meta = [questions_module.qtype_label(question.qtype)]
        difficulty = (
            labels.get("handout.difficulty_unmarked")
            if question.difficulty is None
            else str(question.difficulty)
        )
        meta.append(f"{labels.get('handout.label_difficulty')} {difficulty}")
        if question.tags:
            meta.append(
                f"{labels.get('handout.label_tags')}：{'、'.join(question.tags)}"
            )
        if question.source_label:
            meta.append(
                f"{labels.get('handout.label_source')}：{question.source_label}"
            )
        lines.extend([f"### {index}. {question.question_key}（{'｜'.join(meta)}）", ""])
        lines.append(question.stem)
        if question.options:
            lines.append("")
            lines.extend(f"- {option}" for option in question.options)
        lines.append("")

    if with_answer:
        lines.extend([f"## {labels.get('handout.section_answers')}", ""])
        for index, question in enumerate(items, start=1):
            lines.append(f"### {index}. {question.question_key}")
            lines.append("")
            lines.append(f"- {labels.get('handout.label_answer')}：{question.answer}")
            if question.analysis:
                lines.append(
                    f"- {labels.get('handout.label_analysis')}：{question.analysis}"
                )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def safe_filename_part(text: str, *, limit: int = 40) -> str:
    """把题目 key / 学生学号变成能当文件名的一段；空的话给个兜底名字。"""
    cleaned = UNSAFE_FILENAME_CHARS.sub("-", str(text).strip()).strip("-")
    if len(cleaned) > limit:
        cleaned = cleaned[:limit]
    return cleaned or "unnamed"


def write_question_handout(
    config: config_loader.AppConfig,
    items: Sequence[questions_module.Question],
    *,
    source: str,
    anchor: str,
    student_label: str | None = None,
    with_answer: bool = False,
) -> Path:
    """写出组卷讲义；文件名与讲评讲义（`handout_<exam_key>.md`）分开，避免互相覆盖。"""
    if not items:
        raise config_loader.ConfigError("没有可组卷的题目：请先确认题库不为空，或放宽筛选条件。")
    text = render_question_handout(
        config,
        items,
        source=source,
        student_label=student_label,
        with_answer=with_answer,
    )
    path = (
        config.paths.output_dir
        / f"handout_questions_{safe_filename_part(anchor)}_{len(items)}道.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
