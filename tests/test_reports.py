"""周报与阶段巡检测试：汇总数字对得上、周次校验、报告不含本机绝对路径。"""

from __future__ import annotations

import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import alerts as alerts_module  # noqa: E402
import config_loader  # noqa: E402
import hub  # noqa: E402
import init_db  # noqa: E402
import reports  # noqa: E402

CONFIG_TEXT = """
[semester]
name = "2026-2027 学年第一学期"
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

# 2026-W42 = 2026-10-12 ~ 10-18
TARGET_WEEK = "2026-W42"


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config_path = self.base / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})
        init_db.init_database(self.config)
        self.reset_data()
        self.seed_fixture()

    def reset_data(self) -> None:
        for table in (
            "follow_ups",
            "alerts",
            "error_records",
            "behavior_records",
            "corrections",
            "homework_submissions",
            "homework_assignments",
            "exam_scores",
            "exams",
        ):
            self.execute(f"DELETE FROM {table}")

    def seed_fixture(self) -> None:
        # 目标周内：一场考试、两份作业（分属两个班）、两条错因、一条行为、一条预警
        self.execute(
            """
            INSERT INTO exams (exam_key, name, exam_date, full_score)
            VALUES ('e-week', '演示周考', '2026-10-14', 100)
            """
        )
        self.execute(
            """
            INSERT INTO exam_scores (exam_id, student_id, score)
            SELECT 1, id, 80 FROM students
            """
        )
        self.execute(
            """
            INSERT INTO homework_assignments (assign_key, class_id, topic, assigned_date)
            VALUES ('hw-week-a', 1, '演示作业甲', '2026-10-13'),
                   ('hw-week-b', 2, '演示作业乙', '2026-10-15')
            """
        )
        self.execute(
            """
            INSERT INTO homework_submissions (assignment_id, student_id, status)
            SELECT 1, id, 'submitted' FROM students WHERE class_id = 1
            """
        )
        self.execute(
            """
            INSERT INTO homework_submissions (assignment_id, student_id, status)
            SELECT 2, id, 'missing' FROM students WHERE class_id = 2
            """
        )
        self.execute(
            """
            INSERT INTO error_records (student_id, assignment_id, tag_id, recorded_at)
            SELECT 1, 1, id, '2026-10-14' FROM error_tags WHERE code = 'calculation'
            """
        )
        self.execute(
            """
            INSERT INTO behavior_records (student_id, kind, detail, recorded_at)
            VALUES (1, 'class_participation', '演示：主动讲题', '2026-10-14')
            """
        )
        self.execute(
            """
            INSERT INTO alerts (student_id, kind, severity, status, description, created_at)
            VALUES (1, 'score_drop', 'warning', 'open', '演示预警', '2026-10-14 08:00:00')
            """
        )

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

    def numbers(self, **kwargs):
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            return reports.collect_weekly_numbers(
                self.config, conn, week_label=kwargs.pop("week_label", TARGET_WEEK), **kwargs
            )
        finally:
            conn.close()


class WeekWindowTests(ReportTestCase):
    def test_parse_week_label(self):
        window = reports.parse_week_label(TARGET_WEEK)

        self.assertEqual(window.start, date(2026, 10, 12))
        self.assertEqual(window.end, date(2026, 10, 18))
        self.assertEqual(window.label, TARGET_WEEK)

    def test_malformed_week_label_is_rejected(self):
        for bad in ("2026-42", "W42", "abc"):
            with self.subTest(label=bad):
                with self.assertRaises(config_loader.ConfigError) as ctx:
                    reports.parse_week_label(bad)
                self.assertIn("YYYY-Www", str(ctx.exception))

    def test_week_outside_the_semester_is_rejected(self):
        # 2027-W08 在学期结束（2027-01-22）之后
        with self.assertRaises(config_loader.ConfigError) as ctx:
            self.numbers(week_label="2027-W08")

        message = str(ctx.exception)
        self.assertIn("不在学期内", message)
        self.assertIn("2027-01-22", message)

    def test_default_week_is_the_previous_one(self):
        today = date(2026, 10, 21)  # 周三

        label = reports.previous_week_label(today)

        self.assertEqual(label, TARGET_WEEK)


class WeeklyNumbersTests(ReportTestCase):
    def test_numbers_match_direct_sql(self):
        numbers = self.numbers()

        # 第 1 周从 2026-08-31（课表起点那一周的周一）算起，10-12 起是第 7 周
        self.assertEqual(numbers.teaching_week, 7)
        self.assertEqual(numbers.phase_name, "新授课")

        exam = numbers.exams[0]
        expected_average = self.query(
            "SELECT AVG(score) AS a FROM exam_scores WHERE exam_id = 1"
        )[0]["a"]
        self.assertEqual(exam["exam_name"], "演示周考")
        self.assertEqual(float(exam["average"]), float(expected_average))

        by_key = {item["assign_key"]: item for item in numbers.homework}
        self.assertEqual(int(by_key["hw-week-a"]["submitted"]), 2)
        self.assertEqual(int(by_key["hw-week-a"]["expected"]), 2)
        self.assertEqual(int(by_key["hw-week-b"]["submitted"]), 0)

        self.assertEqual(numbers.errors_by_tag, (("calculation", "计算失误", 1),))
        self.assertEqual(numbers.behavior_count, 1)
        self.assertEqual(numbers.alerts_open, 1)
        self.assertEqual(numbers.alerts_created, 1)
        self.assertEqual(numbers.alerts_resolved, 0)

    def test_class_filter_limits_every_section(self):
        numbers = self.numbers(class_name="高一(B)班")

        self.assertEqual([item["assign_key"] for item in numbers.homework], ["hw-week-b"])
        self.assertEqual(numbers.errors_by_tag, ())
        self.assertEqual(numbers.behavior_count, 0)
        self.assertEqual(numbers.alerts_open, 0)

    def test_report_renders_the_numbers(self):
        numbers = self.numbers()

        text = reports.render_weekly_report(self.config, numbers)

        self.assertIn("2026-W42", text)
        self.assertIn("演示周考", text)
        self.assertIn("演示作业甲", text)
        self.assertIn("计算失误", text)
        self.assertIn("第 7 教学周", text)

    def test_written_report_has_no_absolute_path(self):
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            path = reports.write_weekly_report(self.config, conn, week_label=TARGET_WEEK)
        finally:
            conn.close()

        text = path.read_text(encoding="utf-8")
        self.assertEqual(path.name, f"weekly_{TARGET_WEEK}.md")
        self.assertNotIn(str(self.base), text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn(str(self.config.paths.database), text)

    def test_cli_writes_the_report(self):
        out = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hub.main(
                [
                    "--config",
                    str(self.config_path),
                    "weekly-report",
                    "--week",
                    TARGET_WEEK,
                ]
            )

        self.assertEqual(code, 0)
        self.assertIn("已生成周报", out.getvalue())
        self.assertTrue(
            (self.config.paths.output_dir / f"weekly_{TARGET_WEEK}.md").is_file()
        )


class PhasePatrolTests(ReportTestCase):
    def patrol(self, today: date):
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            return reports.collect_phase_patrol(self.config, conn, today=today)
        finally:
            conn.close()

    def test_patrol_collects_progress_coverage_and_alerts(self):
        patrol = self.patrol(date(2026, 10, 14))

        self.assertTrue(patrol["in_semester"])
        self.assertEqual(patrol["teaching_week"], 7)
        self.assertEqual(patrol["phase_name"], "新授课")
        stats = {item.class_name: item for item in patrol["classes"]}
        self.assertEqual(stats["高一(A)班"].completion_rate, 1.0)
        self.assertEqual(stats["高一(B)班"].completion_rate, 0.0)
        self.assertEqual(patrol["alert_stats"]["by_status"], {"open": 1})

    def test_patrol_renders_tables_and_reminders(self):
        patrol = self.patrol(date(2026, 10, 14))

        text = reports.render_phase_patrol(self.config, patrol)

        self.assertIn("第 7 教学周 / 共 21 周", text)
        self.assertIn("当前阶段：新授课", text)
        self.assertIn("高一(A)班", text)
        self.assertIn("作业完成率 0.0%，低于 80%", text)
        self.assertIn("还没有算过画像", text)

    def test_patrol_written_report_has_no_absolute_path(self):
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            path = reports.write_phase_patrol(self.config, conn, today=date(2026, 10, 14))
        finally:
            conn.close()

        text = path.read_text(encoding="utf-8")
        self.assertEqual(path.name, "phase_patrol_2026-10-14.md")
        self.assertNotIn(str(self.base), text)
        self.assertNotIn("/Users/", text)

    def test_patrol_outside_the_semester_is_flagged(self):
        patrol = self.patrol(date(2027, 6, 1))

        text = reports.render_phase_patrol(self.config, patrol)

        self.assertFalse(patrol["in_semester"])
        self.assertIn("今天不在学期内", text)

    def test_cli_uses_today_and_writes_a_file(self):
        # 单独造一个「今天落在学期内」的配置，避免测试依赖真实日期
        today = date.today()
        dynamic_dir = self.base / "dynamic"
        dynamic_dir.mkdir()
        config_path = dynamic_dir / "config.toml"
        config_path.write_text(
            "\n".join(
                (
                    "[semester]",
                    'name = "演示学期"',
                    f'starts_on = "{(today - timedelta(days=30)).isoformat()}"',
                    f'ends_on = "{(today + timedelta(days=60)).isoformat()}"',
                    "[paths]",
                    'database = "data/x.db"',
                    'output_dir = "out"',
                    "[classes]",
                    'names = ["高一(A)班"]',
                    "[demo]",
                    "students_per_class = 2",
                )
            ),
            encoding="utf-8",
        )
        dynamic = config_loader.load_config(config_path, env={})
        init_db.init_database(dynamic)
        out = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hub.main(["--config", str(config_path), "phase-patrol"])

        self.assertEqual(code, 0)
        self.assertIn("已生成阶段巡检", out.getvalue())
        self.assertTrue(
            (dynamic.paths.output_dir / f"phase_patrol_{today.isoformat()}.md").is_file()
        )


if __name__ == "__main__":
    unittest.main()
