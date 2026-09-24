"""作业与订正测试：完成率口径（含转班回归）、CSV 导入、订正与统计。"""

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
import homework as homework_module  # noqa: E402
import hub  # noqa: E402
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
names = ["高一(A)班", "高一(B)班"]

[demo]
students_per_class = 2
"""


class HomeworkTestCase(unittest.TestCase):
    # 演示库自带作业数据；测导入/统计的用例先清空，演示数据相关的用例保留
    clear_demo_homework = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config_path = self.base / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})
        init_db.init_database(self.config)
        if self.clear_demo_homework:
            self.clear_homework()

    def clear_homework(self) -> None:
        conn = sqlite3.connect(self.config.paths.database)
        try:
            with conn:
                conn.execute("DELETE FROM corrections")
                conn.execute("DELETE FROM homework_submissions")
                conn.execute("DELETE FROM homework_assignments")
        finally:
            conn.close()

    def write_csv(self, text: str, name: str = "homework.csv") -> Path:
        path = self.base / name
        path.write_text(text, encoding="utf-8")
        return path

    def import_csv(self, csv_text: str, **kwargs):
        params = {
            "csv_path": self.write_csv(csv_text),
            "assign_key": "hw-01",
            "class_name": "高一(A)班",
            "topic": "演示作业：运动学图像",
            "assigned_date": "2026-09-30",
            "due_date": "2026-10-02",
        }
        params.update(kwargs)
        return homework_module.import_homework_from_csv(self.config, **params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def stats(self, class_name: str | None = None):
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            by_class = {
                item.class_name: item
                for item in homework_module.homework_stats(conn, class_name)
            }
        finally:
            conn.close()
        return by_class

    def move_student(self, student_uid: str, class_name: str) -> None:
        conn = sqlite3.connect(self.config.paths.database)
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE students SET class_id = (SELECT id FROM classes WHERE name = ?)
                    WHERE student_uid = ?
                    """,
                    (class_name, student_uid),
                )
        finally:
            conn.close()


class CompletionRateTests(HomeworkTestCase):
    def test_rates_count_only_current_students_of_the_assignment_class(self):
        self.import_csv(
            "student_uid,status\n"
            "高一(A)班-01,submitted\n"
            "高一(A)班-02,submitted\n"
        )

        # 学生转去 B 班：他这条提交既不该算 A 班（否则完成率 200%），也不属于 B 班
        self.move_student("高一(A)班-02", "高一(B)班")

        stats = self.stats()
        class_a = stats["高一(A)班"]
        class_b = stats["高一(B)班"]

        self.assertEqual(class_a.expected, 1)
        self.assertEqual(class_a.submitted, 1)
        self.assertEqual(class_a.completion_rate, 1.0)
        self.assertLessEqual(class_a.completion_rate, 1.0)

        self.assertEqual(class_b.assignment_count, 0)
        self.assertEqual(class_b.expected, 0)
        self.assertEqual(class_b.submitted, 0)

    def test_missing_equals_expected_minus_submitted(self):
        self.import_csv(
            "student_uid,status\n"
            "高一(A)班-01,submitted\n"
            "高一(A)班-02,missing\n"
        )

        class_a = self.stats()["高一(A)班"]

        self.assertEqual((class_a.expected, class_a.submitted, class_a.missing), (2, 1, 1))
        self.assertEqual(class_a.completion_rate, 0.5)

    def test_late_counts_as_submitted_and_is_listed_separately(self):
        self.import_csv(
            "student_uid,status\n"
            "高一(A)班-01,late\n"
            "高一(A)班-02,missing\n"
        )

        class_a = self.stats()["高一(A)班"]

        self.assertEqual(class_a.submitted, 1)
        self.assertEqual(class_a.late, 1)
        self.assertEqual(class_a.completion_rate, 0.5)

    def test_students_without_any_row_count_as_missing(self):
        self.import_csv("student_uid,status\n高一(A)班-01,submitted\n")

        class_a = self.stats()["高一(A)班"]

        self.assertEqual((class_a.expected, class_a.submitted, class_a.missing), (2, 1, 1))


class CorrectionTests(HomeworkTestCase):
    def test_correction_rate_counts_corrected_submissions(self):
        self.import_csv(
            "student_uid,status,corrected_on,note\n"
            "高一(A)班-01,submitted,2026-10-03,订正记录：计算失误\n"
            "高一(A)班-02,submitted,,\n"
        )

        class_a = self.stats()["高一(A)班"]

        self.assertEqual(class_a.corrected, 1)
        self.assertEqual(class_a.correction_rate, 0.5)
        rows = self.query("SELECT note FROM corrections")
        self.assertEqual([row["note"] for row in rows], ["订正记录：计算失误"])

    def test_correction_on_missing_status_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            self.import_csv(
                "student_uid,status,corrected_on\n高一(A)班-01,missing,2026-10-03\n"
            )

        self.assertIn("不该有订正", str(ctx.exception))
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM homework_submissions")[0]["n"], 0)

    def test_reimport_keeps_provided_corrections(self):
        self.import_csv(
            "student_uid,status,corrected_on\n高一(A)班-01,submitted,2026-10-03\n"
        )
        self.import_csv("student_uid,status\n高一(A)班-01,submitted\n")

        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM corrections")[0]["n"], 1)


class HomeworkImportTests(HomeworkTestCase):
    def test_import_writes_assignment_and_submissions(self):
        result = self.import_csv(
            "student_uid,status\n"
            "高一(A)班-01,submitted\n"
            "高一(A)班-02,late\n"
        )

        self.assertEqual(result["assignments_created"], 1)
        self.assertEqual(result["submissions_written"], 2)
        assignment = self.query(
            "SELECT assign_key, topic, assigned_date, due_date FROM homework_assignments"
        )[0]
        self.assertEqual(assignment["assign_key"], "hw-01")
        self.assertEqual(assignment["due_date"], "2026-10-02")

    def test_reimport_is_idempotent(self):
        text = "student_uid,status\n高一(A)班-01,submitted\n高一(A)班-02,missing\n"
        first = self.import_csv(text)
        second = self.import_csv(text)

        self.assertEqual(first["assignments_created"], 1)
        self.assertEqual(second["assignments_created"], 0)
        counts = self.query(
            """
            SELECT (SELECT COUNT(*) FROM homework_assignments) AS a,
                   (SELECT COUNT(*) FROM homework_submissions) AS s
            """
        )[0]
        self.assertEqual((counts["a"], counts["s"]), (1, 2))

    def test_dry_run_writes_nothing(self):
        plan = self.import_csv(
            "student_uid,status\n高一(A)班-01,submitted\n", dry_run=True
        )

        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["plan"].row_count, 1)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM homework_assignments")[0]["n"], 0)

    def test_unknown_student_and_unknown_class_are_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            self.import_csv("student_uid,status\n高一(A)班-99,submitted\n")
        self.assertIn("找不到学生", str(ctx.exception))

        with self.assertRaises(config_loader.ConfigError) as ctx:
            self.import_csv(
                "student_uid,status\n高一(A)班-01,submitted\n",
                class_name="不存在的班",
            )
        self.assertIn("找不到班级", str(ctx.exception))

    def test_bad_status_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            self.import_csv("student_uid,status\n高一(A)班-01,unknown\n")

        self.assertIn("状态不认识", str(ctx.exception))

    def test_assign_key_belonging_to_another_class_is_rejected(self):
        self.import_csv("student_uid,status\n高一(A)班-01,submitted\n")

        with self.assertRaises(config_loader.ConfigError) as ctx:
            self.import_csv(
                "student_uid,status\n高一(B)班-01,submitted\n",
                class_name="高一(B)班",
            )

        self.assertIn("已属于别的班级", str(ctx.exception))

    def test_column_mapping_allows_custom_headers(self):
        result = self.import_csv(
            "学号,状态,订正日期\n高一(A)班-01,submitted,2026-10-03\n",
            columns_spec="学号=student_uid,状态=status,订正日期=corrected_on",
        )

        self.assertEqual(result["corrections"], 1)

    def test_cli_dry_run_prints_and_writes_nothing(self):
        csv_path = self.write_csv("student_uid,status\n高一(A)班-01,submitted\n")
        out = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hub.main(
                [
                    "--config",
                    str(self.config_path),
                    "import-homework",
                    "--csv",
                    str(csv_path),
                    "--assign-key",
                    "hw-01",
                    "--class",
                    "高一(A)班",
                    "--topic",
                    "演示作业：运动学图像",
                    "--assigned-date",
                    "2026-09-30",
                    "--dry-run",
                ]
            )

        self.assertEqual(code, 0)
        self.assertIn("[dry-run]", out.getvalue())
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM homework_assignments")[0]["n"], 0)


class DemoHomeworkTests(HomeworkTestCase):
    clear_demo_homework = False

    def test_demo_import_fills_homework_tables(self):
        dataset = seed_demo_data.build_dataset(self.config)

        expected_assignments = len(dataset["homework"])
        expected_submissions = sum(len(item["records"]) for item in dataset["homework"])
        expected_corrections = sum(
            1
            for item in dataset["homework"]
            for record in item["records"]
            if record.get("correction")
        )

        counts = self.query(
            """
            SELECT (SELECT COUNT(*) FROM homework_assignments) AS a,
                   (SELECT COUNT(*) FROM homework_submissions) AS s,
                   (SELECT COUNT(*) FROM corrections) AS c
            """
        )[0]

        self.assertEqual((counts["a"], counts["s"], counts["c"]),
                         (expected_assignments, expected_submissions, expected_corrections))

    def test_demo_stats_are_consistent(self):
        for stats in self.stats().values():
            with self.subTest(class_name=stats.class_name):
                self.assertEqual(stats.submitted + stats.missing, stats.expected)
                self.assertLessEqual(stats.completion_rate, 1.0)
                self.assertGreater(stats.assignment_count, 0)

    def test_homework_stats_cli_prints_a_table(self):
        out = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hub.main(["--config", str(self.config_path), "homework-stats"])

        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("| 班级 |", text)
        self.assertIn("完成率", text)
        self.assertIn("高一(A)班", text)


if __name__ == "__main__":
    unittest.main()
