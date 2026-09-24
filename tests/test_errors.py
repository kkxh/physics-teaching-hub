"""错因与行为记录测试：先验存在、单事务、标签字典、预览与查询。"""

from __future__ import annotations

import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import errors as errors_module  # noqa: E402
import hub  # noqa: E402
import import_scores  # noqa: E402
import init_db  # noqa: E402
import seed_demo_data  # noqa: E402

CONFIG_TEXT = """
[semester]
starts_on = "2026-09-01"
ends_on = "2027-01-22"

[paths]
database = "data/x.db"
output_dir = "out"

[classes]
names = ["高一(A)班"]

[demo]
students_per_class = 2

[error_tags]
calculation = "算错"
experiment_design = "实验设计"
"""


class ErrorTestCase(unittest.TestCase):
    clear_records = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config_path = self.base / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})
        init_db.init_database(self.config)
        # 考试由 import-scores 那一步写入（错因要挂在考试上，测试里要先有考试）
        import_scores.import_demo_scores(self.config)
        if self.clear_records:
            self.clear_records_tables()

    def clear_records_tables(self) -> None:
        conn = sqlite3.connect(self.config.paths.database)
        try:
            with conn:
                conn.execute("DELETE FROM error_records")
                conn.execute("DELETE FROM behavior_records")
        finally:
            conn.close()

    def execute(self, sql: str, params: tuple = ()) -> None:
        conn = sqlite3.connect(self.config.paths.database)
        try:
            with conn:
                conn.execute(sql, params)
        finally:
            conn.close()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def count(self, table: str) -> int:
        return int(self.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])

    def first_exam_key(self) -> str:
        return str(self.query("SELECT exam_key FROM exams ORDER BY id")[0]["exam_key"])

    def first_assign_key(self) -> str:
        return str(
            self.query("SELECT assign_key FROM homework_assignments ORDER BY id")[0][
                "assign_key"
            ]
        )


class ErrorWriteTests(ErrorTestCase):
    def test_preview_does_not_write(self):
        result = errors_module.record_error(
            self.config,
            student_uid="高一(A)班-01",
            tag_code="calculation",
            exam_key=self.first_exam_key(),
            recorded_at="2026-11-05",
        )

        self.assertFalse(result["confirmed"])
        self.assertEqual(self.count("error_records"), 0)

    def test_confirmed_write_is_single_transaction(self):
        result = errors_module.record_error(
            self.config,
            student_uid="高一(A)班-01",
            tag_code="calculation",
            exam_key=self.first_exam_key(),
            note="演示：算错",
            recorded_at="2026-11-05",
            confirmed=True,
        )

        self.assertEqual(result["error_records_written"], 1)
        row = self.query(
            """
            SELECT er.note, er.recorded_at, t.code, e.exam_key, s.student_uid
            FROM error_records er
            JOIN error_tags t ON t.id = er.tag_id
            JOIN students s ON s.id = er.student_id
            LEFT JOIN exams e ON e.id = er.exam_id
            """
        )[0]
        self.assertEqual(
            (
                row["note"],
                row["recorded_at"],
                row["code"],
                row["exam_key"],
                row["student_uid"],
            ),
            ("演示：算错", "2026-11-05", "calculation", self.first_exam_key(), "高一(A)班-01"),
        )

    def test_can_attach_to_a_homework_assignment(self):
        result = errors_module.record_error(
            self.config,
            student_uid="高一(A)班-02",
            tag_code="concept_confusion",
            assign_key=self.first_assign_key(),
            recorded_at="2026-11-06",
            confirmed=True,
        )

        self.assertEqual(result["error_records_written"], 1)
        row = self.query(
            """
            SELECT a.assign_key FROM error_records er
            JOIN homework_assignments a ON a.id = er.assignment_id
            """
        )[0]
        self.assertEqual(row["assign_key"], self.first_assign_key())

    def test_unknown_student_exam_and_assignment_are_rejected(self):
        cases = (
            ({"student_uid": "高一(A)班-99", "exam_key": "demo-exam-1"}, "找不到学生"),
            ({"student_uid": "高一(A)班-01", "exam_key": "no-such-exam"}, "找不到考试"),
            ({"student_uid": "高一(A)班-01", "assign_key": "no-such-hw"}, "找不到作业"),
        )
        for kwargs, needle in cases:
            with self.subTest(needle=needle):
                with self.assertRaises(config_loader.ConfigError) as ctx:
                    errors_module.record_error(
                        self.config,
                        tag_code="calculation",
                        recorded_at="2026-11-05",
                        confirmed=True,
                        **kwargs,
                    )
                self.assertIn(needle, str(ctx.exception))

        self.assertEqual(self.count("error_records"), 0)

    def test_exam_and_assignment_are_mutually_exclusive_and_required(self):
        for kwargs in (
            {},
            {"exam_key": "demo-exam-1", "assign_key": "demo-hw-01"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(config_loader.ConfigError) as ctx:
                    errors_module.record_error(
                        self.config,
                        student_uid="高一(A)班-01",
                        tag_code="calculation",
                        recorded_at="2026-11-05",
                        confirmed=True,
                        **kwargs,
                    )
                self.assertIn("二选一", str(ctx.exception))

    def test_failed_apply_rolls_back(self):
        # 构造一个指向不存在考试的合法计划，验证写入前的复核会拒绝且不留残行
        draft = errors_module.ErrorRecordPlan(
            student_id=1,
            student_uid="高一(A)班-01",
            student_name="学生01",
            tag_code="calculation",
            tag_label="算错",
            exam_id=9999,
            exam_label="不存在的考试",
            assignment_id=None,
            assignment_label=None,
            note=None,
            recorded_at="2026-11-05",
        )
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            with self.assertRaises(config_loader.ConfigError):
                errors_module.apply_error_plan(conn, draft)
        finally:
            conn.close()

        self.assertEqual(self.count("error_records"), 0)


class TagDictionaryTests(ErrorTestCase):
    def test_builtin_tags_are_synced(self):
        codes = {
            row["code"] for row in self.query("SELECT code FROM error_tags")
        }

        self.assertTrue({code for code, _label in errors_module.BUILTIN_ERROR_TAGS} <= codes)

    def test_config_extends_and_overrides_labels(self):
        labels = {
            row["code"]: row["label"] for row in self.query("SELECT code, label FROM error_tags")
        }

        self.assertEqual(labels["experiment_design"], "实验设计")
        self.assertEqual(labels["calculation"], "算错")

    def test_unknown_tag_lists_available_codes(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            errors_module.record_error(
                self.config,
                student_uid="高一(A)班-01",
                tag_code="no_such_tag",
                exam_key=self.first_exam_key(),
                recorded_at="2026-11-05",
            )

        message = str(ctx.exception)
        self.assertIn("没有这个错因标签", message)
        self.assertIn("calculation", message)
        self.assertIn("[error_tags]", message)

    def test_custom_tag_can_be_used(self):
        result = errors_module.record_error(
            self.config,
            student_uid="高一(A)班-01",
            tag_code="experiment_design",
            exam_key=self.first_exam_key(),
            recorded_at="2026-11-05",
            confirmed=True,
        )

        self.assertEqual(result["plan"].tag_label, "实验设计")
        self.assertEqual(self.count("error_records"), 1)


class ListErrorTests(ErrorTestCase):
    def setUp(self):
        super().setUp()
        for day in ("2026-11-05", "2026-11-07", "2026-11-06"):
            errors_module.record_error(
                self.config,
                student_uid="高一(A)班-01",
                tag_code="calculation",
                exam_key=self.first_exam_key(),
                note=f"演示：{day}",
                recorded_at=day,
                confirmed=True,
            )
        errors_module.record_error(
            self.config,
            student_uid="高一(A)班-02",
            tag_code="model_choice",
            exam_key=self.first_exam_key(),
            recorded_at="2026-11-08",
            confirmed=True,
        )

    def test_sorted_by_date_desc_and_scoped_to_student(self):
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            rows = errors_module.list_errors(conn, student_uid="高一(A)班-01")
        finally:
            conn.close()

        self.assertEqual([row.recorded_at for row in rows], ["2026-11-07", "2026-11-06", "2026-11-05"])
        self.assertTrue(all(row.student_uid == "高一(A)班-01" for row in rows))
        self.assertTrue(all(row.tag_code == "calculation" for row in rows))

    def test_filter_by_tag(self):
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            rows = errors_module.list_errors(
                conn, student_uid="高一(A)班-01", tag_code="model_choice"
            )
        finally:
            conn.close()

        self.assertEqual(rows, [])

    def test_unknown_student_is_reported(self):
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            with self.assertRaises(config_loader.ConfigError):
                errors_module.list_errors(conn, student_uid="高一(A)班-99")
        finally:
            conn.close()

    def test_cli_lists_errors(self):
        out = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hub.main(
                [
                    "--config",
                    str(self.config_path),
                    "list-errors",
                    "--student",
                    "高一(A)班-01",
                ]
            )

        self.assertEqual(code, 0)
        self.assertIn("| 记录日期 |", out.getvalue())


class BehaviorTests(ErrorTestCase):
    def test_record_and_list_behavior(self):
        result = errors_module.record_behavior(
            self.config,
            student_uid="高一(A)班-01",
            kind="class_participation",
            detail="演示：主动讲题",
            recorded_at="2026-11-05",
            confirmed=True,
        )
        self.assertEqual(result["behavior_records_written"], 1)

        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            rows = errors_module.list_behavior(conn, student_uid="高一(A)班-01")
        finally:
            conn.close()

        self.assertEqual([row["kind"] for row in rows], ["class_participation"])
        self.assertEqual(rows[0]["detail"], "演示：主动讲题")

    def test_preview_writes_nothing(self):
        result = errors_module.record_behavior(
            self.config,
            student_uid="高一(A)班-01",
            kind="experiment",
            recorded_at="2026-11-05",
        )

        self.assertFalse(result["confirmed"])
        self.assertEqual(self.count("behavior_records"), 0)

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            errors_module.record_behavior(
                self.config,
                student_uid="高一(A)班-01",
                kind="no_such_kind",
                recorded_at="2026-11-05",
            )

        self.assertIn("没有这种行为类型", str(ctx.exception))

    def test_cli_requires_yes_before_writing(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = hub.main(
                [
                    "--config",
                    str(self.config_path),
                    "record-behavior",
                    "--student",
                    "高一(A)班-01",
                    "--kind",
                    "experiment",
                ]
            )

        self.assertEqual(code, 2)
        self.assertEqual(self.count("behavior_records"), 0)


class DemoRecordsTests(ErrorTestCase):
    clear_records = False

    def test_demo_records_are_imported(self):
        dataset = seed_demo_data.build_dataset(self.config)

        self.assertEqual(self.count("error_records"), len(dataset["error_records"]))
        self.assertEqual(self.count("behavior_records"), len(dataset["behavior_records"]))
        self.assertEqual(dataset["version"], seed_demo_data.DATASET_VERSION)

    def test_every_demo_error_record_points_at_a_real_target(self):
        rows = self.query(
            """
            SELECT er.id, er.exam_id, er.assignment_id, t.code
            FROM error_records er JOIN error_tags t ON t.id = er.tag_id
            """
        )

        self.assertTrue(rows)
        for row in rows:
            with self.subTest(record=row["id"]):
                self.assertTrue(row["exam_id"] is not None or row["assignment_id"] is not None)
                self.assertTrue(row["code"])


if __name__ == "__main__":
    unittest.main()
