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

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

import config_loader

# 题型：与 schema/questions.sql 的 CHECK 约束一一对应
QTYPES: tuple[str, ...] = ("choice", "fill", "calculation", "experiment", "other")
DIFFICULTY_MIN = 1
DIFFICULTY_MAX = 5

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
