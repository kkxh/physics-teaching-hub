"""通用导入框架：学生匹配、考试匹配、CSV 解析与批量写入。

工程约定（见 docs/ENGINEERING_NOTES.md）：

- 第 1 条：身份只认 ID；姓名匹配到 0 条或不止 1 条都报错，**绝不「取第一条」**；
- 第 4 条：整批写入放在一个事务里，出错整体回滚；dry-run 与真正执行走同一套解析与校验；
- 第 5 条：用户输入进 `LIKE` 时转义 `%` 与 `_`；
- 第 6 条：分数判空用 `is None`，0 分是合法成绩。

考试按「考试名 + 日期」匹配；不存在时按这两个字段创建，`exam_key` 由它们拼成，
这样同一天重复导入不会产生两场考试，换一天则是另一场。
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import config_loader
import db as db_module

# CSV 表头约定：内部字段 → 表头名（可用 --columns 覆盖）
DEFAULT_COLUMNS: Mapping[str, str] = {
    "student_uid": "student_uid",
    "name": "name",
    "score": "score",
}
@dataclass(frozen=True)
class ScoreRow:
    """CSV 里的一行原始成绩。"""

    row_number: int
    student_uid: str | None
    name: str | None
    score: float


@dataclass(frozen=True)
class ResolvedScore:
    """已经匹配到库内学生的一行成绩。"""

    row_number: int
    student_id: int
    student_uid: str
    student_name: str
    class_name: str
    score: float


@dataclass(frozen=True)
class ImportPlan:
    """导入计划：dry-run 与真正执行共用同一份计划。"""

    exam_name: str
    exam_date: str
    exam_key: str
    full_score: float
    exam_exists: bool
    scores: tuple[ResolvedScore, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def row_count(self) -> int:
        return len(self.scores)


def escape_like(text: str) -> str:
    """把用户输入里的 LIKE 通配符转义掉，避免 `%` / `_` 意外扩大匹配范围。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def make_exam_key(exam_name: str, exam_date: str) -> str:
    return f"{exam_date}|{exam_name}"


def find_students_by_name(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """按姓名找学生：先精确（忽略空格），再模糊。返回全部匹配行，不截断。"""
    cleaned = name.strip()
    base_sql = """
        SELECT s.id, s.student_uid, s.name, s.class_id, c.name AS class_name
        FROM students s
        JOIN classes c ON c.id = s.class_id
    """
    exact = conn.execute(
        base_sql + " WHERE REPLACE(s.name, ' ', '') = REPLACE(?, ' ', '') ORDER BY s.id",
        (cleaned,),
    ).fetchall()
    if exact:
        return list(exact)

    pattern = f"%{escape_like(cleaned)}%"
    fuzzy = conn.execute(
        base_sql
        + " WHERE REPLACE(s.name, ' ', '') LIKE ? ESCAPE '\\' ORDER BY s.id",
        (pattern,),
    ).fetchall()
    return list(fuzzy)


def describe_matches(rows: Sequence[sqlite3.Row]) -> str:
    return "、".join(
        f"{row['name']}（{row['class_name']}，student_uid={row['student_uid']}）"
        for row in rows
    )


def resolve_student(
    conn: sqlite3.Connection,
    *,
    student_uid: str | None = None,
    name: str | None = None,
) -> sqlite3.Row:
    """定位唯一学生：优先 student_uid；否则按姓名，歧义就报错。"""
    if student_uid:
        row = conn.execute(
            """
            SELECT s.id, s.student_uid, s.name, s.class_id, c.name AS class_name
            FROM students s
            JOIN classes c ON c.id = s.class_id
            WHERE s.student_uid = ?
            """,
            (student_uid,),
        ).fetchone()
        if row is None:
            raise config_loader.ConfigError(
                f"找不到学生：student_uid={student_uid!r}；请核对成绩表里的学号列。"
            )
        return row

    if not name or not name.strip():
        raise config_loader.ConfigError("成绩行既没有 student_uid 也没有姓名，无法匹配学生。")

    matches = find_students_by_name(conn, name)
    if not matches:
        raise config_loader.ConfigError(f"找不到学生：{name!r}；请核对姓名或用 student_uid。")
    if len(matches) > 1:
        raise config_loader.ConfigError(
            f"姓名 {name!r} 匹配到 {len(matches)} 名学生：{describe_matches(matches)}；"
            "请在成绩表里用 student_uid 指定到人。"
        )
    return matches[0]


def resolve_exam(
    conn: sqlite3.Connection,
    *,
    exam_name: str,
    exam_date: str,
    full_score: float,
    create: bool,
) -> tuple[sqlite3.Row | None, str, bool]:
    """按「考试名 + 日期」定位考试；不存在时（create=True）创建。"""
    exam_key = make_exam_key(exam_name, exam_date)
    row = conn.execute(
        "SELECT id, exam_key, name, exam_date, full_score FROM exams WHERE name = ? AND exam_date = ?",
        (exam_name, exam_date),
    ).fetchone()
    if row is not None:
        return row, str(row["exam_key"]), True
    if not create:
        return None, exam_key, False

    with conn:
        conn.execute(
            """
            INSERT INTO exams (exam_key, name, exam_date, full_score)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (exam_key) DO UPDATE SET
                name = excluded.name,
                exam_date = excluded.exam_date,
                full_score = excluded.full_score
            """,
            (exam_key, exam_name, exam_date, float(full_score)),
        )
    row = conn.execute(
        "SELECT id, exam_key, name, exam_date, full_score FROM exams WHERE exam_key = ?",
        (exam_key,),
    ).fetchone()
    return row, exam_key, False


def parse_columns(
    spec: str | None,
    defaults: Mapping[str, str] = DEFAULT_COLUMNS,
) -> dict[str, str]:
    """把 `--columns "表头=字段,表头2=字段2"` 解析成 内部字段 → 表头。"""
    columns = dict(defaults)
    if not spec:
        return columns

    for item in spec.split(","):
        piece = item.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise config_loader.ConfigError(
                f"--columns 的每一项要写成「表头=字段」，例如 学号=student_uid；收到：{piece!r}"
            )
        header, _, field_name = piece.partition("=")
        header, field_name = header.strip(), field_name.strip()
        if field_name not in defaults:
            raise config_loader.ConfigError(
                f"--columns 里的字段名只能是 {'、'.join(defaults)}；收到：{field_name!r}"
            )
        columns[field_name] = header
    return columns


def parse_score(raw: Any, row_number: int) -> float:
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise config_loader.ConfigError(f"第 {row_number} 行没有分数，拒绝导入。")
    try:
        score = float(text)
    except ValueError as exc:
        raise config_loader.ConfigError(
            f"第 {row_number} 行的分数不是数字：{raw!r}"
        ) from exc
    if score < 0:
        raise config_loader.ConfigError(f"第 {row_number} 行的分数是负数：{score}；拒绝导入。")
    return score


def resolve_columns(
    headers: Sequence[str],
    columns: Mapping[str, str],
    source: str,
    *,
    required: Sequence[str],
    require_student: bool = True,
) -> dict[str, str]:
    """确认表头里有必需列与（可选的）学生标识列；返回实际存在的列。"""
    present = {field: header for field, header in columns.items() if header in headers}
    shown = "、".join(headers) or "（空）"

    missing = [columns[field] for field in required if field not in present]
    if missing:
        raise config_loader.ConfigError(
            f"{source} 缺少这些列：{'、'.join(missing)}；"
            f"实际表头是：{shown}。可用 --columns 映射表头。"
        )
    if require_student and "student_uid" not in present and "name" not in present:
        raise config_loader.ConfigError(
            f"{source} 里找不到学生标识列：至少要有一列 "
            f"{columns['student_uid']!r}（学号）或 {columns['name']!r}（姓名）；"
            f"实际表头是：{shown}。可用 --columns 映射表头。"
        )
    return present


def field_value(
    raw: Mapping[str, Any],
    present: Mapping[str, str],
    field: str,
) -> str | None:
    """从一行原始数据里取某个内部字段的值；列不存在或值为空都返回 None。"""
    header = present.get(field)
    if header is None:
        return None
    value = raw.get(header)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_rows(
    headers: Sequence[str],
    records: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    source: str,
    columns: Mapping[str, str],
) -> list[ScoreRow]:
    """把「表头 + 逐行取值」解析成 ScoreRow；CSV 与 Excel 共用这一套。"""
    present = resolve_columns(headers, columns, source, required=("score",))

    rows: list[ScoreRow] = []
    for row_number, raw in records:
        score = parse_score(raw.get(present["score"]), row_number)
        rows.append(
            ScoreRow(
                row_number=row_number,
                student_uid=field_value(raw, present, "student_uid"),
                name=field_value(raw, present, "name"),
                score=score,
            )
        )

    if not rows:
        raise config_loader.ConfigError(f"{source} 里没有任何成绩行。")
    return rows


def read_score_rows(csv_path: Path, columns: Mapping[str, str]) -> list[ScoreRow]:
    """读 CSV：必须有分数列，以及 student_uid / name 里至少一列。"""
    if not csv_path.is_file():
        raise config_loader.ConfigError(f"找不到成绩表：{csv_path}")

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        records = [(number, dict(raw)) for number, raw in enumerate(reader, start=2)]

    return parse_rows(headers, records, source=csv_path.name, columns=columns)


def load_openpyxl():
    """按需导入 openpyxl；没装就给出可操作的安装提示。"""
    try:
        import openpyxl
    except ImportError as exc:
        raise config_loader.ConfigError(
            "读取 Excel 需要 openpyxl：请先运行 python3 -m pip install -r requirements.txt"
        ) from exc
    return openpyxl


def read_score_rows_from_excel(
    xlsx_path: Path,
    columns: Mapping[str, str],
    sheet: str | None = None,
) -> list[ScoreRow]:
    """读 Excel（.xlsx）：默认第一个工作表，可用 sheet 指定。"""
    if not xlsx_path.is_file():
        raise config_loader.ConfigError(f"找不到成绩表：{xlsx_path}")

    openpyxl = load_openpyxl()
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        if sheet:
            if sheet not in workbook.sheetnames:
                raise config_loader.ConfigError(
                    f"{xlsx_path.name} 里没有工作表 {sheet!r}；"
                    f"现有工作表：{'、'.join(workbook.sheetnames)}"
                )
            worksheet = workbook[sheet]
        else:
            worksheet = workbook.worksheets[0]

        rows = worksheet.iter_rows(values_only=True)
        first = next(rows, None)
        if first is None:
            raise config_loader.ConfigError(f"{xlsx_path.name} 的工作表是空的。")
        headers = ["" if cell is None else str(cell).strip() for cell in first]

        records: list[tuple[int, Mapping[str, Any]]] = []
        for row_number, values in enumerate(rows, start=2):
            if all(value is None or str(value).strip() == "" for value in values):
                continue
            records.append((row_number, dict(zip(headers, values))))

        source = f"{xlsx_path.name}[{worksheet.title}]"
        return parse_rows(headers, records, source=source, columns=columns)
    finally:
        workbook.close()


def build_import_plan(
    conn: sqlite3.Connection,
    *,
    rows: Sequence[ScoreRow],
    exam_name: str,
    exam_date: str,
    full_score: float,
) -> ImportPlan:
    """把成绩表行匹配到库内学生；dry-run 与真正执行都走这里。"""
    existing_exam, exam_key, exam_exists = resolve_exam(
        conn, exam_name=exam_name, exam_date=exam_date, full_score=full_score, create=False
    )

    resolved: list[ResolvedScore] = []
    seen: set[int] = set()
    warnings: list[str] = []
    if existing_exam is not None:
        stored_full_score = float(existing_exam["full_score"])
        if abs(stored_full_score - float(full_score)) > 1e-9:
            warnings.append(
                f"库里这场考试的满分是 {stored_full_score:g}，本次指定 {float(full_score):g}；"
                "导入后按本次指定的满分更新。"
            )
    for row in rows:
        student = resolve_student(conn, student_uid=row.student_uid, name=row.name)
        student_id = int(student["id"])
        if student_id in seen:
            warnings.append(
                f"第 {row.row_number} 行与前面某行指向同一名学生（{student['name']}），后导入的分数会覆盖先前那条。"
            )
        seen.add(student_id)
        if row.student_uid and row.name and row.name.strip() != str(student["name"]).strip():
            warnings.append(
                f"第 {row.row_number} 行给的姓名 {row.name!r} 与库里的 {student['name']!r} 不一致，"
                "已按 student_uid 匹配。"
            )
        resolved.append(
            ResolvedScore(
                row_number=row.row_number,
                student_id=student_id,
                student_uid=str(student["student_uid"]),
                student_name=str(student["name"]),
                class_name=str(student["class_name"]),
                score=row.score,
            )
        )

    return ImportPlan(
        exam_name=exam_name,
        exam_date=exam_date,
        exam_key=exam_key,
        full_score=float(full_score),
        exam_exists=exam_exists,
        scores=tuple(resolved),
        warnings=tuple(warnings),
    )


def apply_import_plan(conn: sqlite3.Connection, plan: ImportPlan) -> dict[str, int]:
    """按计划写入成绩：单事务，出错整体回滚。"""
    created_exam = 0
    full_score_updated = 0
    written = 0
    with conn:
        row, _, _ = resolve_exam(
            conn,
            exam_name=plan.exam_name,
            exam_date=plan.exam_date,
            full_score=plan.full_score,
            create=True,
        )
        assert row is not None  # create=True 时一定拿得到考试行
        created_exam = 0 if plan.exam_exists else 1
        exam_id = int(row["id"])

        stored_full_score = float(row["full_score"])
        if abs(stored_full_score - float(plan.full_score)) > 1e-9:
            conn.execute(
                "UPDATE exams SET full_score = ? WHERE id = ?",
                (float(plan.full_score), exam_id),
            )
            full_score_updated = 1

        for item in plan.scores:
            conn.execute(
                """
                INSERT INTO exam_scores (exam_id, student_id, score)
                VALUES (?, ?, ?)
                ON CONFLICT (exam_id, student_id) DO UPDATE SET score = excluded.score
                """,
                (exam_id, item.student_id, float(item.score)),
            )
            written += 1

    return {
        "exams_created": created_exam,
        "full_score_updated": full_score_updated,
        "scores_written": written,
    }


def import_scores_from_csv(
    config: config_loader.AppConfig,
    *,
    csv_path: str | Path,
    exam_name: str,
    exam_date: str,
    full_score: float = 100.0,
    dry_run: bool = False,
    columns_spec: str | None = None,
) -> dict[str, Any]:
    """从 CSV 导入成绩。"""
    columns = parse_columns(columns_spec)
    rows = read_score_rows(Path(csv_path), columns)
    return import_score_rows(
        config,
        rows=rows,
        exam_name=exam_name,
        exam_date=exam_date,
        full_score=full_score,
        dry_run=dry_run,
    )


def import_scores_from_excel(
    config: config_loader.AppConfig,
    *,
    xlsx_path: str | Path,
    exam_name: str,
    exam_date: str,
    full_score: float = 100.0,
    dry_run: bool = False,
    columns_spec: str | None = None,
    sheet: str | None = None,
) -> dict[str, Any]:
    """从 Excel（.xlsx）导入成绩。"""
    columns = parse_columns(columns_spec)
    rows = read_score_rows_from_excel(Path(xlsx_path), columns, sheet=sheet)
    return import_score_rows(
        config,
        rows=rows,
        exam_name=exam_name,
        exam_date=exam_date,
        full_score=full_score,
        dry_run=dry_run,
    )


def import_score_rows(
    config: config_loader.AppConfig,
    *,
    rows: Sequence[ScoreRow],
    exam_name: str,
    exam_date: str,
    full_score: float = 100.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把已经解析好的成绩行导入库；dry_run=True 只返回计划，不写库。

    返回 {"dry_run": bool, "plan": ImportPlan, ...}；真正写入时还会带上
    {"exams_created": int, "scores_written": int}。
    """
    if not config.paths.database.is_file():
        raise config_loader.ConfigError(
            f"还没有数据库：{config.paths.database}；请先运行 python3 hub.py init-db --demo。"
        )

    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        plan = build_import_plan(
            conn,
            rows=rows,
            exam_name=exam_name,
            exam_date=exam_date,
            full_score=full_score,
        )
        if dry_run:
            return {"dry_run": True, "plan": plan}
        counts = apply_import_plan(conn, plan)
        return {"dry_run": False, "plan": plan, **counts}
    finally:
        conn.close()
