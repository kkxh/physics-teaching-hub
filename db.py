"""数据层公共入口：连接、schema 检查、表是否存在的判断。

工程约定（见 docs/ENGINEERING_NOTES.md）：

- 第 3 条：每条连接显式开启外键；schema 完整性按完整表清单校验；
- 第 11 条：数据访问函数接受显式连接或 `db_path`，模块内不写死数据库位置。

守护测试断言：除本模块外，仓库里不出现 `sqlite3.connect(`——所有连接都从这里走。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import config_loader

# 当前正式 schema 版本；建库与检查都以此为准。
SCHEMA_VERSION = "phase2"
# Phase 1 的临时 schema：表名与字段不承诺兼容，遇到它必须显式重建。
PHASE1_SCHEMA_VERSION = "phase1-temp"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开数据库连接：行按名字取，并显式开启外键（SQLite 默认是关的）。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise _database_error(exc) from exc
    return row is not None


def schema_version(conn: sqlite3.Connection) -> str | None:
    """库里的 schema 版本；没有 meta 表或没写版本时返回 None。"""
    if not table_exists(conn, "meta"):
        return None
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise _database_error(exc) from exc
    return None if row is None else str(row["value"])


def _database_error(exc: sqlite3.DatabaseError) -> config_loader.ConfigError:
    """把「文件不是 SQLite 库 / 已损坏」这类底层错误翻成可操作的提示。"""
    return config_loader.ConfigError(
        f"读数据库失败：{exc}；文件可能不是有效的 SQLite 库或已经损坏。"
        "可以加 --rebuild --yes 重建（会删掉这个文件），或用 --db 指向别的库。"
    )


def require_schema(
    conn: sqlite3.Connection,
    expected: str = SCHEMA_VERSION,
) -> None:
    """确认库是当前版本；版本不对就报错并给出可操作的下一步。"""
    actual = schema_version(conn)
    if actual == expected:
        return
    if actual == PHASE1_SCHEMA_VERSION:
        raise config_loader.ConfigError(
            f"这个库还是 Phase 1 的临时 schema（{PHASE1_SCHEMA_VERSION}），"
            f"当前要求 {expected}；请用 python3 hub.py init-db --demo --rebuild --yes 重建。"
        )
    raise config_loader.ConfigError(
        f"数据库 schema 版本是 {actual or '未知'}，当前要求 {expected}；"
        "请先运行 python3 hub.py init-db --demo 建库（必要时加 --rebuild --yes 重建）。"
    )
