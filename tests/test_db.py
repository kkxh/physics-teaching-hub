"""数据层测试：连接约定、schema 检查，以及「连接只走 db.py」的守护。"""

from __future__ import annotations

import ast
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import db as db_module  # noqa: E402
import init_db  # noqa: E402


def connect_calls(path: Path) -> list[int]:
    """找出文件里 `sqlite3.connect(...)` 的调用行号。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "connect"
            and isinstance(func.value, ast.Name)
            and func.value.id == "sqlite3"
        ):
            lines.append(node.lineno)
    return lines


class ConnectTests(unittest.TestCase):
    def test_connect_enables_foreign_keys_and_row_factory(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db_module.connect(Path(tmp) / "x.db")
            self.addCleanup(conn.close)

            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertIs(conn.row_factory, sqlite3.Row)

    def test_schema_version_is_none_for_an_empty_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db_module.connect(Path(tmp) / "empty.db")
            self.addCleanup(conn.close)

            self.assertIsNone(db_module.schema_version(conn))

    def test_require_schema_accepts_the_current_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db_module.connect(Path(tmp) / "x.db")
            self.addCleanup(conn.close)
            init_db.apply_schema(conn)

            db_module.require_schema(conn)

            self.assertEqual(db_module.schema_version(conn), db_module.SCHEMA_VERSION)

    def test_require_schema_explains_phase1_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db_module.connect(Path(tmp) / "old.db")
            self.addCleanup(conn.close)
            with conn:
                conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', 'phase1-temp')"
                )

            with self.assertRaises(config_loader.ConfigError) as ctx:
                db_module.require_schema(conn)

            message = str(ctx.exception)
            self.assertIn("phase1-temp", message)
            self.assertIn("--rebuild", message)

    def test_require_schema_explains_missing_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db_module.connect(Path(tmp) / "empty.db")
            self.addCleanup(conn.close)

            with self.assertRaises(config_loader.ConfigError) as ctx:
                db_module.require_schema(conn)

            self.assertIn("init_db.py", str(ctx.exception))


class ConnectionGuardTests(unittest.TestCase):
    """除 db.py 外，模块里不该再自己开连接（否则外键 PRAGMA 会被绕过）。"""

    def test_only_db_module_opens_connections(self):
        offenders: list[str] = []
        for path in sorted(ROOT.glob("*.py")):
            if path.name == "db.py":
                continue
            for lineno in connect_calls(path):
                offenders.append(f"{path.name}:{lineno}")

        self.assertEqual(
            offenders,
            [],
            msg=f"这些地方自己开了 sqlite 连接，请改用 db.connect：{offenders}",
        )

    def test_guard_actually_detects_a_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.py"
            sample.write_text(
                "import sqlite3\nconn = sqlite3.connect('x.db')\n", encoding="utf-8"
            )

            self.assertEqual(connect_calls(sample), [2])


if __name__ == "__main__":
    unittest.main()
