"""学生画像测试：纯函数、权重、增量重算与落库幂等。"""

from __future__ import annotations

import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import hub  # noqa: E402
import import_scores  # noqa: E402
import init_db  # noqa: E402
import profiling  # noqa: E402

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
students_per_class = 3
"""


class PureFunctionTests(unittest.TestCase):
    def test_score_level_averages_rates(self):
        self.assertEqual(profiling.score_level(()), 0.0)
        self.assertEqual(
            profiling.score_level((profiling.ExamScoreInput(100, 100),)), 100.0
        )
        self.assertEqual(
            profiling.score_level(
                (profiling.ExamScoreInput(80, 100), profiling.ExamScoreInput(60, 100))
            ),
            70.0,
        )
        # 超出满分的分数按满分算，负分按 0 算
        self.assertEqual(
            profiling.score_level(
                (profiling.ExamScoreInput(120, 100), profiling.ExamScoreInput(-10, 100))
            ),
            50.0,
        )
        self.assertEqual(profiling.score_level((profiling.ExamScoreInput(80, 0),)), 0.0)

    def test_homework_habit_weights_completion_more_than_punctuality(self):
        self.assertEqual(profiling.homework_habit(()), 0.0)
        self.assertEqual(
            profiling.homework_habit(
                (profiling.HomeworkInput("submitted"), profiling.HomeworkInput("submitted"))
            ),
            100.0,
        )
        mixed = (
            profiling.HomeworkInput("submitted"),
            profiling.HomeworkInput("late"),
            profiling.HomeworkInput("missing"),
            profiling.HomeworkInput("missing"),
        )
        self.assertEqual(profiling.homework_habit(mixed), 50.0)

    def test_error_control_penalises_density(self):
        self.assertEqual(profiling.error_control(0, 4), 100.0)
        self.assertEqual(profiling.error_control(1, 5), 96.0)
        self.assertEqual(profiling.error_control(1, 1), 80.0)
        self.assertEqual(profiling.error_control(10, 1), 0.0)
        # 没有考试也没有作业时按 1 个工作量算，不会除零
        self.assertEqual(profiling.error_control(1, 0), 80.0)

    def test_compute_profile_uses_weights_and_falls_back_to_equal_weights(self):
        data = profiling.StudentProfileInput(
            student_uid="高一(A)班-01",
            exam_scores=(profiling.ExamScoreInput(100, 100),),
            homework=(profiling.HomeworkInput("missing"),),
            error_count=0,
        )

        default = profiling.compute_profile(data)
        self.assertEqual(default["score_level"], 100.0)
        self.assertEqual(default["homework_habit"], 0.0)
        self.assertEqual(default["error_control"], 100.0)
        # 0.5/0.3/0.2 加权：(100*0.5 + 0*0.3 + 100*0.2) / 1.0
        self.assertEqual(default["overall"], 70.0)

        heavy_score = profiling.compute_profile(data, {"score_level": 1.0, "homework_habit": 0.0, "error_control": 0.0})
        self.assertEqual(heavy_score["overall"], 100.0)

        equal = profiling.compute_profile(data, {"score_level": 0, "homework_habit": 0, "error_control": 0})
        self.assertAlmostEqual(equal["overall"], (100 + 0 + 100) / 3, places=6)

    def test_pure_module_does_not_open_connections(self):
        source = (ROOT / "profiling.py").read_text(encoding="utf-8")

        self.assertNotIn("sqlite3.connect(", source)

    def test_dimension_without_data_is_not_computed(self):
        no_homework = profiling.StudentProfileInput(
            student_uid="高一(A)班-01",
            exam_scores=(profiling.ExamScoreInput(75, 100),),
        )

        dimensions = profiling.compute_dimensions(no_homework)

        self.assertNotIn("homework_habit", dimensions)
        self.assertIn("score_level", dimensions)

    def test_overall_uses_only_dimensions_with_data(self):
        data = profiling.StudentProfileInput(
            student_uid="高一(A)班-01",
            exam_scores=(profiling.ExamScoreInput(70, 100),),
        )

        profile = profiling.compute_profile(data, {"score_level": 0.5, "homework_habit": 0.5, "error_control": 0.0})

        # 只有 score_level 有数据 → 综合分等于它，而不是被「没数据的作业」拉低
        self.assertEqual(profile["overall"], 70.0)

    def test_student_without_any_data_gets_only_overall(self):
        empty = profiling.StudentProfileInput(student_uid="高一(A)班-09")

        profile = profiling.compute_profile(empty)

        self.assertEqual(profile, {"overall": 0.0})

    def test_table_shows_dash_for_missing_dimension(self):
        table = profiling.format_profile_table(
            {"高一(A)班-01": {"score_level": 70.0, "overall": 70.0}}
        )

        self.assertIn("| 高一(A)班-01 | 70.0 | — | — | 70.0 |", table)


class ProfileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config_path = self.base / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})
        init_db.init_database(self.config)
        import_scores.import_demo_scores(self.config)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def execute(self, sql: str, params: tuple = ()) -> None:
        conn = sqlite3.connect(self.config.paths.database)
        try:
            with conn:
                conn.execute(sql, params)
        finally:
            conn.close()

    def test_first_run_writes_all_dimensions(self):
        result = profiling.compute_profiles(self.config)

        self.assertEqual(result["computed"], 3)
        self.assertEqual(result["written"], 3 * 4)  # 三个维度 + 综合分
        rows = self.query("SELECT DISTINCT dimension FROM ability_scores")
        self.assertEqual(
            {row["dimension"] for row in rows},
            set(config_loader.PROFILE_DIMENSIONS) | {"overall"},
        )

    def test_second_run_skips_unchanged_students(self):
        profiling.compute_profiles(self.config)

        result = profiling.compute_profiles(self.config)

        self.assertEqual(result["computed"], 0)
        self.assertEqual(result["skipped"], 3)
        self.assertEqual(result["written"], 0)

    def test_only_changed_students_are_recomputed(self):
        profiling.compute_profiles(self.config)
        later = (datetime.now(timezone.utc) + timedelta(seconds=30)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        student = self.query("SELECT id FROM students ORDER BY id")[0]
        exam = self.query("SELECT id FROM exams ORDER BY id")[0]
        self.execute(
            """
            UPDATE exam_scores SET score = ?, created_at = ?
            WHERE exam_id = ? AND student_id = ?
            """,
            (55.0, later, int(exam["id"]), int(student["id"])),
        )

        result = profiling.compute_profiles(self.config)

        self.assertEqual(result["computed"], 1)
        self.assertEqual(list(result["profiles"]), ["高一(A)班-01"])

    def test_rebuild_recomputes_everyone(self):
        profiling.compute_profiles(self.config)

        result = profiling.compute_profiles(self.config, rebuild=True)

        self.assertEqual(result["computed"], 3)
        self.assertEqual(result["skipped"], 0)

    def test_single_student_recomputes_that_student_only(self):
        profiling.compute_profiles(self.config)

        result = profiling.compute_profiles(self.config, student_uid="高一(A)班-02")

        self.assertEqual(list(result["profiles"]), ["高一(A)班-02"])
        self.assertEqual(result["computed"], 1)

    def test_unknown_student_is_reported(self):
        with self.assertRaises(config_loader.ConfigError):
            profiling.compute_profiles(self.config, student_uid="高一(A)班-99")

    def test_more_errors_lower_the_error_control_dimension(self):
        profiling.compute_profiles(self.config)
        before = {
            row["student_id"]: row["score"]
            for row in self.query(
                "SELECT student_id, score FROM ability_scores WHERE dimension = 'error_control'"
            )
        }
        student = self.query("SELECT id FROM students ORDER BY id")[0]
        tag = self.query("SELECT id FROM error_tags ORDER BY id")[0]
        exam = self.query("SELECT id FROM exams ORDER BY id")[0]
        for _ in range(3):
            self.execute(
                """
                INSERT INTO error_records (student_id, exam_id, tag_id, recorded_at)
                VALUES (?, ?, ?, '2026-11-10')
                """,
                (int(student["id"]), int(exam["id"]), int(tag["id"])),
            )

        profiling.compute_profiles(self.config, rebuild=True)

        after = {
            row["student_id"]: row["score"]
            for row in self.query(
                "SELECT student_id, score FROM ability_scores WHERE dimension = 'error_control'"
            )
        }
        self.assertLess(after[int(student["id"])], before[int(student["id"])])

    def test_config_weights_change_the_overall_score(self):
        profiling.compute_profiles(self.config)
        before = {
            row["student_id"]: row["score"]
            for row in self.query("SELECT student_id, score FROM ability_scores WHERE dimension = 'overall'")
        }

        weighted = config_loader.parse_config(
            {
                "semester": {"starts_on": "2026-09-01", "ends_on": "2027-01-22"},
                "paths": {"database": "data/x.db", "output_dir": "out"},
                "classes": {"names": ["高一(A)班"]},
                "demo": {"students_per_class": 3},
                "profile": {"score_level": 1.0, "homework_habit": 0.0, "error_control": 0.0},
            },
            base_dir=self.base,
        )
        profiling.compute_profiles(weighted, rebuild=True)

        after = {
            row["student_id"]: row["score"]
            for row in self.query("SELECT student_id, score FROM ability_scores WHERE dimension = 'overall'")
        }
        self.assertNotEqual(before, after)

    def test_cli_prints_a_profile_table(self):
        out = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hub.main(["--config", str(self.config_path), "compute-profile"])

        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("已计算 3 名学生的画像", text)
        self.assertIn("| 学生 | 成绩水平 | 作业习惯 | 错因控制 | 综合分 |", text)


if __name__ == "__main__":
    unittest.main()
