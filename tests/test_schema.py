"""P2.0 正式 schema 的测试：建表清单、外键、UTC 约定、重建流程与迁移机制。"""

from __future__ import annotations

import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import init_db  # noqa: E402

CONFIG_TEXT = "\n".join(
    (
        "[semester]",
        'starts_on = "2026-09-01"',
        'ends_on = "2027-01-22"',
        "[paths]",
        'database = "data/x.db"',
        'output_dir = "out"',
        "[classes]",
        'names = ["高一(A)班"]',
        "[demo]",
        "students_per_class = 3",
    )
)


def make_config(base: Path, **demo: Any) -> config_loader.AppConfig:
    return config_loader.parse_config(
        {
            "semester": {"starts_on": "2026-09-01", "ends_on": "2027-01-22"},
            "paths": {"database": "data/x.db", "output_dir": "out"},
            "classes": {"names": ["高一(A)班"]},
            "demo": {"students_per_class": 3, **demo},
        },
        base_dir=base,
    )


# phase2 的建表文件：不含 Phase 3 才加的题库表
PHASE2_SCHEMA_FILES = tuple(name for name in init_db.SCHEMA_FILES if name != "questions.sql")


def write_phase2_database(path: Path, *, marker: str = "标记班") -> Path:
    """构造一个 phase2 库：跑 phase2 的建表文件、写版本号，并塞一条业务数据。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db.connect(path)
    try:
        for name in PHASE2_SCHEMA_FILES:
            conn.executescript((init_db.SCHEMA_DIR / name).read_text(encoding="utf-8"))
        with conn:
            conn.execute(
                """
                INSERT INTO meta (key, value) VALUES ('schema_version', 'phase2')
                ON CONFLICT (key) DO UPDATE SET value = excluded.value
                """
            )
            conn.execute("INSERT INTO classes (name) VALUES (?)", (marker,))
    finally:
        conn.close()
    return path


class SchemaFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = init_db.connect(Path(self._tmp.name) / "schema.db")
        self.addCleanup(self.conn.close)
        init_db.apply_schema(self.conn)

    def tables(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {str(row["name"]) for row in rows}

    def test_schema_files_are_declared_in_build_order(self):
        self.assertEqual(
            init_db.SCHEMA_FILES,
            (
                "core.sql",
                "scores.sql",
                "homework.sql",
                "errors.sql",
                "profile.sql",
                "alerts.sql",
                "questions.sql",
            ),
        )
        for name in init_db.SCHEMA_FILES:
            with self.subTest(file=name):
                self.assertTrue((init_db.SCHEMA_DIR / name).is_file())
                self.assertTrue(init_db.EXPECTED_TABLES_BY_FILE[name])

    def test_every_declared_table_is_created(self):
        created = self.tables()

        for name, expected in init_db.EXPECTED_TABLES_BY_FILE.items():
            with self.subTest(file=name):
                self.assertTrue(
                    set(expected) <= created, msg=f"{name} 缺少表：{set(expected) - created}"
                )
        self.assertEqual(init_db.schema_version(self.conn), "phase3")

    def test_phase1_tables_keep_working(self):
        # Phase 1 的三条命令依赖这几张表，结构必须还在
        self.assertTrue({"meta", "classes", "students", "exams", "exam_scores"} <= self.tables())

    def test_foreign_keys_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    "INSERT INTO students (student_uid, name, class_id) VALUES (?, ?, ?)",
                    ("x-01", "学生01", 999),
                )

    def test_error_records_need_an_exam_or_assignment(self):
        with self.conn:
            self.conn.execute(
                "INSERT INTO error_tags (code, label) VALUES ('tag', '标签')"
            )
            self.conn.execute(
                "INSERT INTO classes (name) VALUES ('高一(A)班')"
            )
            self.conn.execute(
                "INSERT INTO students (student_uid, name, class_id) VALUES ('x-01', '学生01', 1)"
            )

        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO error_records (student_id, tag_id)
                    VALUES (1, 1)
                    """
                )

    def test_created_at_defaults_are_utc(self):
        with self.conn:
            self.conn.execute("INSERT INTO classes (name) VALUES ('高一(A)班')")
        stored = self.conn.execute("SELECT created_at FROM classes").fetchone()["created_at"]

        parsed = datetime.strptime(str(stored), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
        self.assertLess(delta, 120, msg=f"created_at 看起来不是 UTC：{stored}")

    def test_score_constraints_are_enforced(self):
        with self.conn:
            self.conn.execute("INSERT INTO classes (name) VALUES ('高一(A)班')")
            self.conn.execute(
                "INSERT INTO students (student_uid, name, class_id) VALUES ('x-01', '学生01', 1)"
            )
            self.conn.execute(
                """
                INSERT INTO exams (exam_key, name, exam_date, full_score)
                VALUES ('demo', '演示考试', '2026-10-01', 100)
                """
            )

        cases = (
            "INSERT INTO exams (exam_key, name, exam_date, full_score) VALUES ('bad', '零分考试', '2026-10-02', 0)",
            "INSERT INTO exam_scores (exam_id, student_id, score) VALUES (1, 1, -1)",
        )
        for sql in cases:
            with self.subTest(sql=sql[:48]):
                with self.assertRaises(sqlite3.IntegrityError):
                    with self.conn:
                        self.conn.execute(sql)

    def test_expected_indexes_exist(self):
        names = {
            str(row["name"])
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }

        for expected in (
            "idx_students_class",
            "idx_exam_scores_student",
            "idx_assignments_class",
            "idx_error_records_assignment",
            "idx_alerts_student_kind",
        ):
            with self.subTest(index=expected):
                self.assertIn(expected, names)


class InitDatabaseHelpersMixin:
    def insert_marker_class(self, name: str) -> None:
        conn = sqlite3.connect(self.config.paths.database)
        try:
            with conn:
                conn.execute("INSERT INTO classes (name) VALUES (?)", (name,))
        finally:
            conn.close()

    def has_class(self, name: str) -> bool:
        conn = sqlite3.connect(self.config.paths.database)
        try:
            row = conn.execute("SELECT 1 FROM classes WHERE name = ?", (name,)).fetchone()
        finally:
            conn.close()
        return row is not None


class InitDatabaseTests(InitDatabaseHelpersMixin, unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config = make_config(self.base)

    def write_phase1_database(self) -> Path:
        database = self.config.paths.database
        database.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(database)
        try:
            with conn:
                conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', 'phase1-temp')"
                )
        finally:
            conn.close()
        return database

    def test_phase1_database_requires_rebuild(self):
        database = self.write_phase1_database()

        with self.assertRaises(config_loader.ConfigError) as ctx:
            init_db.init_database(self.config)

        message = str(ctx.exception)
        self.assertIn("--rebuild", message)
        self.assertIn("phase1-temp", message)
        self.assertTrue(database.exists())

    def test_rebuild_needs_confirmation(self):
        self.write_phase1_database()

        with self.assertRaises(config_loader.ConfigError) as ctx:
            init_db.init_database(self.config, rebuild=True)

        self.assertIn("--yes", str(ctx.exception))

    def test_rebuild_replaces_the_database(self):
        database = self.write_phase1_database()

        summary = init_db.init_database(self.config, rebuild=True, confirmed=True)

        self.assertEqual(summary["schema_version"], "phase3")
        self.assertIn(database, summary["removed"])
        conn = init_db.connect(database)
        try:
            self.assertEqual(init_db.schema_version(conn), "phase3")
            students = conn.execute("SELECT COUNT(*) AS n FROM students").fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(students, 3)

    def test_repeated_init_is_idempotent(self):
        first = init_db.init_database(self.config)
        second = init_db.init_database(self.config)

        self.assertEqual((first["classes"], first["students"]), (1, 3))
        self.assertEqual((second["classes"], second["students"]), (1, 3))

    def test_rebuild_replaces_a_current_database_too(self):
        # --rebuild 是对「重建」的明确要求，版本已经是当前版本也要真的重建
        init_db.init_database(self.config)
        self.insert_marker_class("标记班")

        summary = init_db.init_database(self.config, rebuild=True, confirmed=True)

        self.assertTrue(summary["removed"])
        self.assertFalse(self.has_class("标记班"))

    def test_corrupt_database_is_reported(self):
        database = self.config.paths.database
        database.parent.mkdir(parents=True, exist_ok=True)
        database.write_bytes(b"this is not a database")

        with self.assertRaises(config_loader.ConfigError) as ctx:
            init_db.init_database(self.config)

        message = str(ctx.exception)
        self.assertIn("SQLite", message)
        self.assertIn("--rebuild", message)

    def test_rebuild_recovers_from_a_corrupt_database(self):
        database = self.config.paths.database
        database.parent.mkdir(parents=True, exist_ok=True)
        database.write_bytes(b"this is not a database")

        summary = init_db.init_database(self.config, rebuild=True, confirmed=True)

        self.assertIn(database, summary["removed"])
        conn = init_db.connect(database)
        try:
            self.assertEqual(init_db.schema_version(conn), "phase3")
        finally:
            conn.close()

    def test_cli_reports_rebuild(self):
        self.write_phase1_database()
        config_path = self.base / "config.toml"
        config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        err = io.StringIO()

        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            result = init_db.main(["--config", str(config_path), "--demo"])

        self.assertEqual(result, 2)
        self.assertIn("--rebuild", err.getvalue())


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.migrations = self.base / "migrations"
        self.migrations.mkdir()
        patcher = mock.patch.object(init_db, "MIGRATIONS_DIR", self.migrations)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.conn = init_db.connect(self.base / "x.db")
        self.addCleanup(self.conn.close)
        init_db.apply_schema(self.conn)

    def test_migrations_apply_once_and_are_recorded(self):
        (self.migrations / "0001_add_note.sql").write_text(
            "CREATE TABLE IF NOT EXISTS demo_note (id INTEGER PRIMARY KEY, note TEXT);\n",
            encoding="utf-8",
        )

        first = init_db.apply_migrations(self.conn)
        second = init_db.apply_migrations(self.conn)

        self.assertEqual(first, ["0001_add_note.sql"])
        self.assertEqual(second, [])
        recorded = [
            row["name"] for row in self.conn.execute("SELECT name FROM schema_migrations")
        ]
        self.assertEqual(recorded, ["0001_add_note.sql"])

    def test_migrations_run_in_name_order(self):
        (self.migrations / "0002_b.sql").write_text(
            "CREATE TABLE IF NOT EXISTS b (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
        )
        (self.migrations / "0001_a.sql").write_text(
            "CREATE TABLE IF NOT EXISTS a (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
        )

        applied = init_db.apply_migrations(self.conn)

        self.assertEqual(applied, ["0001_a.sql", "0002_b.sql"])


QUESTION_OBJECTS = (
    "questions",
    "question_tags",
    "idx_questions_qtype",
    "idx_question_tags_tag",
)


class UpgradeTests(unittest.TestCase):
    """P3.0 升级链路：phase2 库走迁移不丢数据，phase1-temp 仍要求重建。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config = make_config(self.base)

    def question_objects(self, path: Path) -> dict[tuple[str, str], str]:
        conn = init_db.connect(path)
        try:
            return {
                (str(row["type"]), str(row["name"])): str(row["sql"])
                for row in conn.execute(
                    "SELECT type, name, sql FROM sqlite_master WHERE name IN "
                    f"({', '.join('?' for _ in QUESTION_OBJECTS)})",
                    QUESTION_OBJECTS,
                )
            }
        finally:
            conn.close()

    def test_upgrade_adds_question_tables_and_keeps_data(self):
        database = write_phase2_database(self.config.paths.database)

        summary = init_db.upgrade_database(self.config)

        self.assertTrue(summary["changed"])
        self.assertEqual(summary["from"], "phase2")
        self.assertEqual(summary["to"], init_db.SCHEMA_VERSION)
        self.assertEqual(summary["migrations"], ["0001_questions.sql"])

        conn = init_db.connect(database)
        try:
            self.assertEqual(init_db.schema_version(conn), init_db.SCHEMA_VERSION)
            tables = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertTrue({"questions", "question_tags"} <= tables)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"], 0
            )
            self.assertEqual(
                [str(row["name"]) for row in conn.execute("SELECT name FROM classes")],
                ["标记班"],
                msg="升级不该丢掉原有数据",
            )
            recorded = [
                str(row["name"]) for row in conn.execute("SELECT name FROM schema_migrations")
            ]
        finally:
            conn.close()
        self.assertEqual(recorded, ["0001_questions.sql"])

    def test_upgrade_is_idempotent(self):
        write_phase2_database(self.config.paths.database)

        first = init_db.upgrade_database(self.config)
        second = init_db.upgrade_database(self.config)

        self.assertEqual(first["migrations"], ["0001_questions.sql"])
        self.assertFalse(second["changed"])
        self.assertEqual(second["migrations"], [])

        conn = init_db.connect(self.config.paths.database)
        try:
            recorded = [
                str(row["name"]) for row in conn.execute("SELECT name FROM schema_migrations")
            ]
        finally:
            conn.close()
        self.assertEqual(recorded, ["0001_questions.sql"])

    def test_upgraded_schema_matches_a_fresh_database(self):
        database = write_phase2_database(self.config.paths.database)
        init_db.upgrade_database(self.config)
        upgraded = self.question_objects(database)

        fresh_path = self.base / "fresh.db"
        fresh = init_db.connect(fresh_path)
        try:
            init_db.apply_schema(fresh)
        finally:
            fresh.close()

        self.assertEqual(upgraded, self.question_objects(fresh_path))
        self.assertIn(("table", "questions"), upgraded)
        self.assertIn(("index", "idx_question_tags_tag"), upgraded)

    def test_phase1_database_still_requires_rebuild(self):
        database = self.config.paths.database
        database.parent.mkdir(parents=True, exist_ok=True)
        conn = init_db.connect(database)
        try:
            with conn:
                conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', 'phase1-temp')"
                )
        finally:
            conn.close()

        with self.assertRaises(config_loader.ConfigError) as ctx:
            init_db.upgrade_database(self.config)

        message = str(ctx.exception)
        self.assertIn("phase1-temp", message)
        self.assertIn("--rebuild", message)

    def test_upgrade_needs_an_existing_database(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            init_db.upgrade_database(self.config)

        message = str(ctx.exception)
        self.assertIn("不存在", message)
        self.assertIn("init-db", message)


if __name__ == "__main__":
    unittest.main()
