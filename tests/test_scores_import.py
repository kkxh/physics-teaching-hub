"""成绩导入器测试：身份匹配、dry-run、事务回滚、幂等与考试匹配口径。"""

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
import hub  # noqa: E402
import importer  # noqa: E402
import init_db  # noqa: E402

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
students_per_class = 3
"""


class ImportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config_path = self.base / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})
        init_db.init_database(self.config)

    def write_csv(self, text: str, name: str = "scores.csv") -> Path:
        path = self.base / name
        path.write_text(text, encoding="utf-8")
        return path

    def import_csv(self, csv_text: str, **kwargs):
        path = self.write_csv(csv_text)
        params = {
            "csv_path": path,
            "exam_name": "演示月考",
            "exam_date": "2026-11-05",
        }
        params.update(kwargs)
        return importer.import_scores_from_csv(self.config, **params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def counts(self) -> dict[str, int]:
        conn = sqlite3.connect(self.config.paths.database)
        try:
            return {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("exams", "exam_scores")
            }
        finally:
            conn.close()


class IdentityTests(ImportTestCase):
    def test_ambiguous_name_is_rejected(self):
        # 演示名单里两个班各有一个「学生01」，按姓名导入必须报错
        with self.assertRaises(config_loader.ConfigError) as ctx:
            self.import_csv("name,score\n学生01,88\n")

        message = str(ctx.exception)
        self.assertIn("匹配到 2 名学生", message)
        self.assertIn("student_uid", message)
        self.assertEqual(self.counts(), {"exams": 0, "exam_scores": 0})

    def test_unknown_uid_is_rejected_without_partial_writes(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            self.import_csv(
                "student_uid,score\n"
                "高一(A)班-01,90\n"
                "高一(A)班-99,80\n"
            )

        self.assertIn("找不到学生", str(ctx.exception))
        self.assertEqual(self.counts(), {"exams": 0, "exam_scores": 0})

    def test_uid_import_succeeds_and_keeps_zero_scores(self):
        result = self.import_csv(
            "student_uid,score\n"
            "高一(A)班-01,0\n"
            "高一(B)班-02,88.5\n"
        )

        self.assertEqual(result["scores_written"], 2)
        rows = self.query("SELECT score FROM exam_scores ORDER BY score")
        self.assertEqual([row["score"] for row in rows], [0.0, 88.5])

    def test_name_import_works_when_unique(self):
        # 只有一班有「学生03」，不会歧义
        conn = sqlite3.connect(self.config.paths.database)
        try:
            with conn:
                conn.execute("DELETE FROM students WHERE student_uid = '高一(B)班-03'")
        finally:
            conn.close()

        result = self.import_csv("name,score\n学生03,77\n")

        self.assertEqual(result["scores_written"], 1)


class ScoreValueTests(ImportTestCase):
    def test_empty_score_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            self.import_csv("student_uid,score\n高一(A)班-01,\n")

        self.assertIn("没有分数", str(ctx.exception))

    def test_non_numeric_and_negative_scores_are_rejected(self):
        for bad in ("abc", "-5"):
            with self.subTest(score=bad):
                with self.assertRaises(config_loader.ConfigError):
                    self.import_csv(f"student_uid,score\n高一(A)班-01,{bad}\n")


class CsvShapeTests(ImportTestCase):
    def test_missing_columns_are_reported(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            self.import_csv("学号,分数\n高一(A)班-01,90\n")

        message = str(ctx.exception)
        self.assertIn("缺少分数列", message)
        self.assertIn("--columns", message)

    def test_columns_mapping_allows_custom_headers(self):
        result = self.import_csv(
            "学号,姓名,分数\n高一(A)班-01,学生01,66\n",
            columns_spec="学号=student_uid,姓名=name,分数=score",
        )

        self.assertEqual(result["scores_written"], 1)

    def test_empty_file_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            self.import_csv("student_uid,score\n")

        self.assertIn("没有任何成绩行", str(ctx.exception))


class DryRunTests(ImportTestCase):
    def test_dry_run_writes_nothing_and_counts_match(self):
        plan_result = self.import_csv(
            "student_uid,score\n高一(A)班-01,90\n高一(A)班-02,80\n",
            dry_run=True,
        )

        self.assertTrue(plan_result["dry_run"])
        self.assertEqual(plan_result["plan"].row_count, 2)
        self.assertFalse(plan_result["plan"].exam_exists)
        self.assertEqual(self.counts(), {"exams": 0, "exam_scores": 0})

        written = self.import_csv(
            "student_uid,score\n高一(A)班-01,90\n高一(A)班-02,80\n"
        )
        self.assertEqual(written["scores_written"], plan_result["plan"].row_count)

    def test_cli_dry_run_reports_and_writes_nothing(self):
        csv_path = self.write_csv("student_uid,score\n高一(A)班-01,90\n")
        out = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hub.main(
                [
                    "--config",
                    str(self.config_path),
                    "import-scores",
                    "--csv",
                    str(csv_path),
                    "--exam",
                    "演示月考",
                    "--exam-date",
                    "2026-11-05",
                    "--dry-run",
                ]
            )

        self.assertEqual(code, 0)
        self.assertIn("[dry-run]", out.getvalue())
        self.assertEqual(self.counts(), {"exams": 0, "exam_scores": 0})


class ExamMatchingTests(ImportTestCase):
    def test_same_name_and_date_reuses_one_exam(self):
        self.import_csv("student_uid,score\n高一(A)班-01,90\n")
        second = self.import_csv("student_uid,score\n高一(A)班-02,70\n")

        self.assertEqual(second["exams_created"], 0)
        self.assertEqual(self.counts(), {"exams": 1, "exam_scores": 2})

    def test_other_date_creates_another_exam(self):
        self.import_csv("student_uid,score\n高一(A)班-01,90\n")
        other = self.import_csv(
            "student_uid,score\n高一(A)班-01,60\n", exam_date="2026-11-06"
        )

        self.assertEqual(other["exams_created"], 1)
        self.assertEqual(self.counts(), {"exams": 2, "exam_scores": 2})

    def test_repeated_import_is_idempotent(self):
        text = "student_uid,score\n高一(A)班-01,90\n高一(A)班-02,80\n"
        first = self.import_csv(text)
        second = self.import_csv(text)

        self.assertEqual(first["scores_written"], 2)
        self.assertEqual(second["scores_written"], 2)
        self.assertEqual(self.counts(), {"exams": 1, "exam_scores": 2})

    def test_duplicate_rows_in_one_file_keep_the_last_score(self):
        result = self.import_csv(
            "student_uid,score\n高一(A)班-01,50\n高一(A)班-01,90\n"
        )

        self.assertEqual(result["scores_written"], 2)
        self.assertIn("覆盖", "".join(result["plan"].warnings))
        rows = self.query("SELECT score FROM exam_scores")
        self.assertEqual([row["score"] for row in rows], [90.0])

    def test_changed_full_score_is_updated_with_a_warning(self):
        self.import_csv("student_uid,score\n高一(A)班-01,80\n", full_score=100)

        second = self.import_csv("student_uid,score\n高一(A)班-01,90\n", full_score=150)

        self.assertEqual(second["full_score_updated"], 1)
        self.assertIn("满分", "".join(second["plan"].warnings))
        stored = self.query("SELECT full_score FROM exams")[0]["full_score"]
        self.assertEqual(stored, 150.0)

    def test_same_full_score_needs_no_update(self):
        self.import_csv("student_uid,score\n高一(A)班-01,80\n", full_score=100)

        second = self.import_csv("student_uid,score\n高一(A)班-02,70\n", full_score=100)

        self.assertEqual(second["full_score_updated"], 0)
        self.assertEqual(second["plan"].warnings, ())

    def test_dry_run_does_not_update_the_full_score(self):
        self.import_csv("student_uid,score\n高一(A)班-01,80\n", full_score=100)

        plan = self.import_csv("student_uid,score\n高一(A)班-01,90\n", full_score=150, dry_run=True)

        self.assertIn("满分", "".join(plan["plan"].warnings))
        stored = self.query("SELECT full_score FROM exams")[0]["full_score"]
        self.assertEqual(stored, 100.0)


class LikeEscapeTests(ImportTestCase):
    def test_escape_like_handles_wildcards_and_backslash(self):
        self.assertEqual(importer.escape_like("100%_x"), "100\\%\\_x")
        self.assertEqual(importer.escape_like("a\\b"), "a\\\\b")

    def test_partial_name_with_underscore_does_not_over_match(self):
        conn = sqlite3.connect(self.config.paths.database)
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO students (student_uid, name, class_id)
                    VALUES ('x-underscore', '学_生', 1)
                    """
                )
        finally:
            conn.close()

        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            matches = importer.find_students_by_name(conn, "生_1")
        finally:
            conn.close()

        # 不转义的话 `_` 会当成任意单字符，把「学生01」之类也匹配进来
        self.assertEqual([row["student_uid"] for row in matches], [])

        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            matches = importer.find_students_by_name(conn, "_生")
        finally:
            conn.close()
        self.assertEqual([row["student_uid"] for row in matches], ["x-underscore"])


if __name__ == "__main__":
    unittest.main()
