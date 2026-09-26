"""题库数据层（Phase 3）：题目结构、校验与读写原语。

工程约定（见 docs/ENGINEERING_NOTES.md）：

- 第 4 条：一组写入放同一事务——本模块只发语句，事务边界留给调用方
  （导入器才能把整批题目放进同一个事务，避免嵌套提交把批事务提前提交）；
- 第 6 条：判空用 `is None`；难度没有「0」这个含义，未标难度就是 NULL；
- 第 11 条：数据访问函数接受显式连接，模块内不写死数据库位置。

题目内容是使用者数据：本模块不内置任何题目，仓库里的示例题都是自制的（examples/）。
知识点标签的白名单来自配置（`[question_bank] tags`），与错因标签是两套词典。
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import config_loader
import db as db_module
import importer
import seed_demo_data

# 题型：与 schema/questions.sql 的 CHECK 约束一一对应
QTYPES: tuple[str, ...] = ("choice", "fill", "calculation", "experiment", "other")
DIFFICULTY_MIN = 1
DIFFICULTY_MAX = 5
# 仓库自带的示例题集（自制内容，随仓库分发）；使用者自己的题库不走这里
EXAMPLE_QUESTIONS_PATH = Path(__file__).resolve().parent / "examples" / "questions_demo.json"
MAX_EXAMPLE_QUESTIONS = 15
# CSV 里选项与标签的分隔符
LIST_SEPARATOR = "|"
# 题型显示名（CLI 用）：与错因标签一样，代码里放一份 code → 显示名的字典
QTYPE_LABELS: Mapping[str, str] = {
    "choice": "选择题",
    "fill": "填空题",
    "calculation": "计算题",
    "experiment": "实验题",
    "other": "其他",
}
# 检索一次最多返回多少道（防止把整库拉进终端）
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 200
# 列表里的题干摘要长度
STEM_EXCERPT_LENGTH = 60

# 题目导入的 CSV 表头约定（可用 --columns 覆盖）
DEFAULT_QUESTION_COLUMNS: Mapping[str, str] = {
    "question_key": "question_key",
    "qtype": "qtype",
    "stem": "stem",
    "answer": "answer",
    "options": "options",
    "analysis": "analysis",
    "difficulty": "difficulty",
    "source_label": "source_label",
    "tags": "tags",
}

_QUESTION_COLUMNS = (
    "id, question_key, qtype, stem, options_json, answer, analysis, difficulty, source_label"
)


@dataclass(frozen=True)
class Question:
    """一道题目的完整内容。options 与 tags 用 tuple，保证取出来就是不可变的。"""

    question_key: str
    qtype: str
    stem: str
    answer: str
    options: tuple[str, ...] = ()
    analysis: str | None = None
    difficulty: int | None = None
    source_label: str | None = None
    tags: tuple[str, ...] = ()


def normalize_question(question: Question) -> Question:
    """剥掉首尾空白、校验字段，返回规范化后的题目。"""
    question_key = question.question_key.strip()
    if not question_key:
        raise config_loader.ConfigError("题目缺少 question_key；每道题都要有稳定的标识。")

    qtype = question.qtype.strip()
    if qtype not in QTYPES:
        raise config_loader.ConfigError(
            f"题目 {question_key} 的题型不合法：{question.qtype!r}；"
            f"可用题型：{'、'.join(QTYPES)}。"
        )

    stem = question.stem.strip()
    if not stem:
        raise config_loader.ConfigError(f"题目 {question_key} 的题干为空；拒绝保存。")

    answer = question.answer.strip()
    if not answer:
        raise config_loader.ConfigError(f"题目 {question_key} 的答案为空；拒绝保存。")

    options = tuple(str(item).strip() for item in question.options)
    if any(not item for item in options):
        raise config_loader.ConfigError(f"题目 {question_key} 有空的选项；选项不能是空字符串。")
    if qtype == "choice" and not options:
        raise config_loader.ConfigError(f"题目 {question_key} 是选择题，必须给选项。")

    difficulty = question.difficulty
    if difficulty is not None:
        difficulty = int(difficulty)
        if not DIFFICULTY_MIN <= difficulty <= DIFFICULTY_MAX:
            raise config_loader.ConfigError(
                f"题目 {question_key} 的难度 {difficulty} 超出范围；"
                f"只能填 {DIFFICULTY_MIN}-{DIFFICULTY_MAX}，或者留空表示未标难度。"
            )

    tags: list[str] = []
    for tag in question.tags:
        clean = str(tag).strip()
        if not clean:
            raise config_loader.ConfigError(f"题目 {question_key} 有空的知识点标签。")
        if clean not in tags:
            tags.append(clean)

    return Question(
        question_key=question_key,
        qtype=qtype,
        stem=stem,
        answer=answer,
        options=options,
        analysis=(question.analysis or "").strip() or None,
        difficulty=difficulty,
        source_label=(question.source_label or "").strip() or None,
        tags=tuple(sorted(tags)),
    )


def check_tags_allowed(question: Question, allowed: Sequence[str]) -> None:
    """标签必须落在配置的白名单里。

    白名单为空时不去猜使用者的意图，而是说明怎么配——静默放行会让题库标签慢慢失控。
    """
    if not allowed:
        raise config_loader.ConfigError(
            "还没有配置知识点标签白名单；请先在 config.toml 的 "
            "[question_bank] tags 里列出允许的标签。"
        )
    unknown = [tag for tag in question.tags if tag not in allowed]
    if unknown:
        raise config_loader.ConfigError(
            f"题目 {question.question_key} 用了不在白名单里的标签：{'、'.join(unknown)}；"
            f"可用标签：{'、'.join(allowed)}。"
        )


def insert_question(conn: sqlite3.Connection, question: Question) -> int:
    """写入一道题（含标签），返回题目 id。**不提交**：事务由调用方负责。"""
    normalized = normalize_question(question)
    if fetch_question(conn, normalized.question_key) is not None:
        raise config_loader.ConfigError(
            f"题库里已经有 question_key = {normalized.question_key!r} 的题目；"
            "重复导入会整批中止，请改 key 或先删掉旧题。"
        )

    try:
        cursor = conn.execute(
            f"""
            INSERT INTO questions
                (question_key, qtype, stem, options_json, answer, analysis, difficulty, source_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized.question_key,
                normalized.qtype,
                normalized.stem,
                _dump_options(normalized.options) if normalized.options else None,
                normalized.answer,
                normalized.analysis,
                normalized.difficulty,
                normalized.source_label,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise config_loader.ConfigError(
            f"写入题目 {normalized.question_key!r} 失败：{exc}；请检查题目字段。"
        ) from exc

    question_id = int(cursor.lastrowid)
    for tag in normalized.tags:
        conn.execute(
            "INSERT INTO question_tags (question_id, tag) VALUES (?, ?)",
            (question_id, tag),
        )
    return question_id


def fetch_question(conn: sqlite3.Connection, question_key: str) -> Question | None:
    """按 question_key 取题；不存在返回 None（判空用 is None）。"""
    row = conn.execute(
        f"SELECT {_QUESTION_COLUMNS} FROM questions WHERE question_key = ?",
        (question_key,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_question(conn, row)


def fetch_questions(conn: sqlite3.Connection, question_keys: Sequence[str]) -> list[Question]:
    """按一批 key 取题；有任何一个不存在就报错，避免「组卷少一道题」这种静默错误。"""
    questions: list[Question] = []
    missing: list[str] = []
    for key in question_keys:
        question = fetch_question(conn, key)
        if question is None:
            missing.append(key)
        else:
            questions.append(question)
    if missing:
        raise config_loader.ConfigError(
            f"题库里找不到这些 question_key：{'、'.join(missing)}；"
            "请核对 key，或用 list-questions 查一遍。"
        )
    return questions


def count_questions(conn: sqlite3.Connection) -> int:
    """题库里的题目数量。"""
    return int(conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"])


def qtype_label(qtype: str) -> str:
    """题型代码 → 显示名；不认识的代码原样返回（不吞掉信息）。"""
    return QTYPE_LABELS.get(qtype, qtype)


def search_questions(
    conn: sqlite3.Connection,
    *,
    tag: str | None = None,
    qtype: str | None = None,
    difficulty: int | None = None,
    keyword: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[Question]:
    """按标签 / 题型 / 难度 / 关键词检索题目。

    关键词只在 `question_key` 与题干里匹配，**不搜答案与解析**——
    否则答案里的字眼会从检索结果里漏出去。

    没有匹配时返回空列表；题库为空也不是错误（「没有数据 ≠ 差数据」，NOTES 第 13 条）。
    """
    tag = (tag or "").strip() or None
    qtype = (qtype or "").strip() or None
    keyword = (keyword or "").strip() or None

    if qtype is not None and qtype not in QTYPES:
        raise config_loader.ConfigError(
            f"题型只能是 {'、'.join(QTYPES)}；收到：{qtype!r}。"
        )
    if difficulty is not None and not DIFFICULTY_MIN <= int(difficulty) <= DIFFICULTY_MAX:
        raise config_loader.ConfigError(
            f"难度只能是 {DIFFICULTY_MIN}-{DIFFICULTY_MAX}；收到：{difficulty!r}。"
        )
    if limit is None or int(limit) < 1:
        raise config_loader.ConfigError("返回条数至少为 1。")
    if int(limit) > MAX_LIST_LIMIT:
        raise config_loader.ConfigError(
            f"返回条数最多 {MAX_LIST_LIMIT}；一次别取太多，可以缩小筛选条件。"
        )

    conditions: list[str] = []
    params: list[Any] = []
    if tag is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM question_tags qt "
            "WHERE qt.question_id = q.id AND qt.tag = ?)"
        )
        params.append(tag)
    if qtype is not None:
        conditions.append("q.qtype = ?")
        params.append(qtype)
    if difficulty is not None:
        conditions.append("q.difficulty = ?")
        params.append(int(difficulty))
    if keyword is not None:
        # 转义 % 与 _，让用户输入按字面匹配（NOTES 第 5 条）
        pattern = f"%{importer.escape_like(keyword)}%"
        conditions.append(
            "(q.question_key LIKE ? ESCAPE '\\' OR q.stem LIKE ? ESCAPE '\\')"
        )
        params.extend([pattern, pattern])

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT {_QUESTION_COLUMNS} FROM questions q{where} "
        "ORDER BY q.question_key LIMIT ?",
        (*params, int(limit)),
    ).fetchall()
    return [_row_to_question(conn, row) for row in rows]


def as_public_dict(question: Question) -> dict[str, Any]:
    """对外字段（本地 API、看板）：**不含答案与解析**。

    本地 API 没有鉴权，答案只走 CLI 的显式 `--show-answer` 开关。
    """
    return {
        "question_key": question.question_key,
        "qtype": question.qtype,
        "stem": question.stem,
        "difficulty": question.difficulty,
        "source_label": question.source_label,
        "tags": list(question.tags),
    }


def format_questions(
    questions: Sequence[Question],
    *,
    show_answer: bool = False,
    stem_length: int = STEM_EXCERPT_LENGTH,
) -> str:
    """把检索结果排成 Markdown 表格；答案默认不出现，`show_answer` 才加两列。"""
    header = ["题目", "题型", "难度", "知识点", "题干"]
    if show_answer:
        header += ["答案", "解析"]

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for question in questions:
        row = [
            question.question_key,
            qtype_label(question.qtype),
            "—" if question.difficulty is None else str(question.difficulty),
            "、".join(question.tags) or "—",
            _excerpt(question.stem, stem_length),
        ]
        if show_answer:
            row += [question.answer, question.analysis or "—"]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _excerpt(text: str, length: int) -> str:
    """把题干压成单行摘要：换行折成空格，超长截断加省略号。"""
    flat = " ".join(text.split())
    if length <= 0 or len(flat) <= length:
        return flat
    return flat[:length] + "…"


@dataclass(frozen=True)
class QuestionImportPlan:
    """一次题目导入的预览：来源、待写入题目、题库现有数量。"""

    source: str
    questions: tuple[Question, ...]
    existing_count: int

    @property
    def row_count(self) -> int:
        return len(self.questions)


def as_dict(question: Question) -> dict[str, Any]:
    """把题目转成可 JSON 序列化的字典（选项与标签用数组）。"""
    return {
        "question_key": question.question_key,
        "qtype": question.qtype,
        "stem": question.stem,
        "answer": question.answer,
        "options": list(question.options),
        "analysis": question.analysis,
        "difficulty": question.difficulty,
        "source_label": question.source_label,
        "tags": list(question.tags),
    }


def load_example_questions() -> tuple[Question, ...]:
    """读仓库自带的示例题集（`examples/questions_demo.json`，自制内容）。

    使用者自己的题库走 `--json` / `--csv`，不走这里；这里只保证演示与测试有题可用。
    """
    if not EXAMPLE_QUESTIONS_PATH.is_file():
        raise config_loader.ConfigError(
            f"找不到示例题集：{EXAMPLE_QUESTIONS_PATH}；仓库文件不完整。"
        )
    try:
        raw = json.loads(EXAMPLE_QUESTIONS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise config_loader.ConfigError(
            f"示例题集不是合法的 JSON：{EXAMPLE_QUESTIONS_PATH}（{exc}）。"
        ) from exc
    if not isinstance(raw, list) or not raw:
        raise config_loader.ConfigError(
            f"示例题集应是非空的 JSON 数组：{EXAMPLE_QUESTIONS_PATH}。"
        )
    if len(raw) > MAX_EXAMPLE_QUESTIONS:
        raise config_loader.ConfigError(
            f"示例题集最多 {MAX_EXAMPLE_QUESTIONS} 道，实际 {len(raw)} 道："
            f"{EXAMPLE_QUESTIONS_PATH}。"
        )

    questions = tuple(
        _question_from_mapping(item, f"示例题集第 {index} 道")
        for index, item in enumerate(raw, start=1)
    )
    for question in questions:
        if not 1 <= len(question.tags) <= 3:
            raise config_loader.ConfigError(
                f"示例题 {question.question_key} 应打 1-3 个知识点标签，"
                f"实际 {len(question.tags)} 个。"
            )
    keys = [question.question_key for question in questions]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise config_loader.ConfigError(
            f"示例题集里有重复的 question_key：{'、'.join(duplicated)}。"
        )
    return questions


def parse_questions_json(path: str | Path) -> tuple[Question, ...]:
    """读 JSON 题目文件：顶层是数组，每题一个对象。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise config_loader.ConfigError(f"找不到题目文件：{file_path}")
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise config_loader.ConfigError(
            f"题目文件不是合法的 JSON：{file_path}（{exc}）。"
        ) from exc
    if not isinstance(raw, list):
        raise config_loader.ConfigError(
            f"题目文件应是 JSON 数组（每题一个对象）：{file_path}。"
        )
    if not raw:
        raise config_loader.ConfigError(f"题目文件里没有任何题目：{file_path}。")
    return tuple(
        _question_from_mapping(item, f"{file_path.name} 第 {index} 道")
        for index, item in enumerate(raw, start=1)
    )


def parse_questions_csv(
    path: str | Path,
    columns_spec: str | None = None,
) -> tuple[Question, ...]:
    """读 CSV 题目文件（UTF-8，带表头）；选项与标签用竖线分隔。"""
    file_path = Path(path)
    if not file_path.is_file():
        raise config_loader.ConfigError(f"找不到题目文件：{file_path}")

    columns = importer.parse_columns(columns_spec, DEFAULT_QUESTION_COLUMNS)
    questions: list[Question] = []
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        present = importer.resolve_columns(
            headers,
            columns,
            str(file_path),
            required=("question_key", "qtype", "stem", "answer"),
            require_student=False,
        )
        for row_number, row in enumerate(reader, start=2):
            raw = {
                field: importer.field_value(row, present, field)
                for field in DEFAULT_QUESTION_COLUMNS
            }
            if not any(value for value in raw.values()):
                continue
            questions.append(
                _question_from_mapping(raw, f"{file_path.name} 第 {row_number} 行")
            )

    if not questions:
        raise config_loader.ConfigError(f"题目文件里没有任何题目：{file_path}。")
    return tuple(questions)


def build_import_plan(
    conn: sqlite3.Connection,
    questions: Sequence[Question],
    *,
    allowed_tags: Sequence[str],
    source: str,
) -> QuestionImportPlan:
    """校验一批题目：字段、批内重复、库里已有、标签白名单都在这一步拦下。

    dry-run 与真正执行共用这份计划，所以预览能发现的问题执行时一定会遇到。
    """
    if not questions:
        raise config_loader.ConfigError(f"{source} 里没有任何题目。")

    seen: dict[str, int] = {}
    normalized: list[Question] = []
    for index, question in enumerate(questions, start=1):
        item = normalize_question(question)
        if item.question_key in seen:
            raise config_loader.ConfigError(
                f"{source} 里有重复的 question_key：{item.question_key!r}"
                f"（第 {seen[item.question_key]} 道与第 {index} 道）。"
            )
        seen[item.question_key] = index
        normalized.append(item)

    for item in normalized:
        if fetch_question(conn, item.question_key) is not None:
            raise config_loader.ConfigError(
                f"题库里已经有 question_key = {item.question_key!r} 的题目；"
                "重复导入会整批中止，请改 key 或先删掉旧题。"
            )
        check_tags_allowed(item, allowed_tags)

    return QuestionImportPlan(
        source=source,
        questions=tuple(normalized),
        existing_count=count_questions(conn),
    )


def apply_import_plan(conn: sqlite3.Connection, plan: QuestionImportPlan) -> dict[str, int]:
    """按计划整批写入：单事务，出错整体回滚。"""
    with conn:
        for question in plan.questions:
            insert_question(conn, question)
    return {"imported": len(plan.questions), "total": count_questions(conn)}


def import_questions(
    config: config_loader.AppConfig,
    *,
    json_path: str | Path | None = None,
    csv_path: str | Path | None = None,
    demo: bool = False,
    columns_spec: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """导入题目的总入口：三种来源选一个，dry-run 与执行走同一份计划。"""
    chosen = [
        name
        for name, given in (
            ("--json", json_path is not None),
            ("--csv", csv_path is not None),
            ("--demo", bool(demo)),
        )
        if given
    ]
    if len(chosen) != 1:
        raise config_loader.ConfigError(
            "题目来源请三选一：--json 文件 / --csv 文件 / --demo"
            "（演示导入固定走 --demo，避免把演示数据当用户题目导进去）。"
        )

    if json_path is not None:
        questions = parse_questions_json(json_path)
        source = f"JSON 文件 {json_path}"
    elif csv_path is not None:
        questions = parse_questions_csv(csv_path, columns_spec)
        source = f"CSV 文件 {csv_path}"
    else:
        dataset = seed_demo_data.load_or_create_dataset(config)
        raw_items = dataset.get("questions") or []
        if not raw_items:
            raise config_loader.ConfigError(
                "演示数据集里没有题目；请重跑 seed_demo_data.py 重新生成。"
            )
        questions = tuple(
            _question_from_mapping(item, f"演示示例题 {index}")
            for index, item in enumerate(raw_items, start=1)
        )
        source = f"自制示例题集（{config.demo.output}）"

    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        plan = build_import_plan(
            conn,
            questions,
            allowed_tags=config.question_bank.tags,
            source=source,
        )
        if dry_run:
            return {"dry_run": True, "plan": plan}
        counts = apply_import_plan(conn, plan)
        return {"dry_run": False, "plan": plan, **counts}
    finally:
        conn.close()


def _question_from_mapping(raw: Any, label: str) -> Question:
    """把 JSON 对象或 CSV 一行转成题目；出错时带上「第几道 / 第几行」。"""
    if not isinstance(raw, Mapping):
        raise config_loader.ConfigError(f"{label} 不是一个对象（键值对）。")

    difficulty_raw = raw.get("difficulty")
    difficulty: int | None
    if difficulty_raw is None or str(difficulty_raw).strip() == "":
        difficulty = None
    else:
        try:
            difficulty = int(str(difficulty_raw).strip())
        except ValueError as exc:
            raise config_loader.ConfigError(
                f"{label} 的 difficulty 不是整数：{difficulty_raw!r}。"
            ) from exc

    key = str(raw.get("question_key") or "").strip()
    try:
        return normalize_question(
            Question(
                question_key=key,
                qtype=str(raw.get("qtype") or ""),
                stem=str(raw.get("stem") or ""),
                answer=str(raw.get("answer") or ""),
                options=_split_list(raw.get("options")),
                analysis=_text(raw.get("analysis")),
                difficulty=difficulty,
                source_label=_text(raw.get("source_label")),
                tags=_split_list(raw.get("tags")),
            )
        )
    except config_loader.ConfigError as exc:
        raise config_loader.ConfigError(f"{label}（{key or '缺少 question_key'}）：{exc}") from exc


def _split_list(raw: Any) -> tuple[str, ...]:
    """把选项 / 标签统一成元组：JSON 用数组，CSV 用竖线分隔。"""
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        items = [str(item) for item in raw]
    else:
        items = str(raw).split(LIST_SEPARATOR)
    return tuple(item.strip() for item in items if item.strip())


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _row_to_question(conn: sqlite3.Connection, row: sqlite3.Row) -> Question:
    tags = tuple(
        str(item["tag"])
        for item in conn.execute(
            "SELECT tag FROM question_tags WHERE question_id = ? ORDER BY tag",
            (row["id"],),
        )
    )
    return Question(
        question_key=str(row["question_key"]),
        qtype=str(row["qtype"]),
        stem=str(row["stem"]),
        answer=str(row["answer"]),
        options=_load_options(row["options_json"]),
        analysis=row["analysis"],
        difficulty=None if row["difficulty"] is None else int(row["difficulty"]),
        source_label=row["source_label"],
        tags=tags,
    )


def _dump_options(options: Sequence[str]) -> str:
    return json.dumps(list(options), ensure_ascii=False)


def _load_options(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise config_loader.ConfigError(
            f"库里的 options_json 不是合法 JSON：{exc}；这道题的选项读不出来。"
        ) from exc
    if not isinstance(parsed, list):
        raise config_loader.ConfigError("库里的 options_json 不是数组；这道题的选项读不出来。")
    return tuple(str(item) for item in parsed)
