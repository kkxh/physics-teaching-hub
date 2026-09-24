"""预警与跟进闭环测试：规则命中、幂等、跟进闭环、阈值配置与 --db 隔离。"""

from __future__ import annotations

import contextlib
import io
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import alerts as alerts_module  # noqa: E402
import config_loader  # noqa: E402
import hub  # noqa: E402
import init_db  # noqa: E402

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


class AlertTestCase(unittest.TestCase):
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

    # --- 夹具：三名学生各命中一条不同的规则 ---------------------------------
    def reset_data(self) -> None:
        for table in (
            "follow_ups",
            "alerts",
            "error_records",
            "corrections",
            "homework_submissions",
            "homework_assignments",
            "exam_scores",
            "exams",
        ):
            self.execute(f"DELETE FROM {table}")

    def seed_fixture(self) -> None:
        self.execute(
            """
            INSERT INTO homework_assignments (assign_key, class_id, topic, assigned_date)
            VALUES ('hw-01', 1, '演示作业一', '2026-09-10'),
                   ('hw-02', 1, '演示作业二', '2026-09-20')
            """
        )
        # 学生01：连续两次缺交（命中 homework_missing）
        # 学生02：两次都交（不命中缺交），但平均分偏低
        # 学生03：第二次缺交（只有一次，不命中）
        self.execute(
            """
            INSERT INTO homework_submissions (assignment_id, student_id, status)
            VALUES (1, 1, 'missing'), (2, 1, 'missing'),
                   (1, 2, 'submitted'), (2, 2, 'submitted'),
                   (1, 3, 'submitted'), (2, 3, 'missing')
            """
        )
        self.execute(
            """
            INSERT INTO exams (exam_key, name, exam_date, full_score)
            VALUES ('e-1', '演示期中', '2026-10-10', 100),
                   ('e-2', '演示月考', '2026-10-20', 100)
            """
        )
        self.execute(
            """
            INSERT INTO exam_scores (exam_id, student_id, score)
            VALUES (1, 1, 90), (2, 1, 85),
                   (1, 2, 50), (2, 2, 55),
                   (1, 3, 90), (2, 3, 70)
            """
        )

    # --- 工具 ---------------------------------------------------------------
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

    def alerts_by_student(self) -> dict[str, list[sqlite3.Row]]:
        rows = self.query(
            """
            SELECT a.id, a.kind, a.severity, a.status, s.student_uid
            FROM alerts a JOIN students s ON s.id = a.student_id
            ORDER BY s.student_uid, a.kind
            """
        )
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(str(row["student_uid"]), []).append(row)
        return grouped

    def scan(self, **kwargs):
        return alerts_module.scan_alerts(self.config, **kwargs)


class RuleTests(AlertTestCase):
    def test_each_rule_fires_for_the_right_student(self):
        self.scan()

        grouped = self.alerts_by_student()
        self.assertEqual([row["kind"] for row in grouped["高一(A)班-01"]], ["homework_missing"])
        self.assertEqual([row["kind"] for row in grouped["高一(A)班-02"]], ["low_average"])
        self.assertEqual([row["kind"] for row in grouped["高一(A)班-03"]], ["score_drop"])

    def test_severity_follows_the_threshold_multiple(self):
        self.scan()

        grouped = self.alerts_by_student()
        self.assertEqual(grouped["高一(A)班-01"][0]["severity"], "warning")  # 2 次 = 阈值
        self.assertEqual(grouped["高一(A)班-03"][0]["severity"], "critical")  # 20 分 = 2 倍阈值

    def test_scan_is_idempotent(self):
        first = self.scan()
        second = self.scan()

        self.assertEqual(first["created"], 3)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(len(self.query("SELECT id FROM alerts")), 3)

    def test_severity_refresh_updates_in_place(self):
        self.scan()
        alert_id = self.alerts_by_student()["高一(A)班-01"][0]["id"]
        # 再补两次缺交 → 连续 4 次 = 2 倍阈值 → critical
        self.execute(
            """
            INSERT INTO homework_assignments (assign_key, class_id, topic, assigned_date)
            VALUES ('hw-03', 1, '演示作业三', '2026-09-25'),
                   ('hw-04', 1, '演示作业四', '2026-09-30')
            """
        )
        self.execute(
            """
            INSERT INTO homework_submissions (assignment_id, student_id, status)
            VALUES (3, 1, 'missing'), (4, 1, 'missing'),
                   (3, 2, 'submitted'), (4, 2, 'submitted'),
                   (3, 3, 'submitted'), (4, 3, 'submitted')
            """
        )

        result = self.scan()

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 1)
        rows = self.query(
            "SELECT id, severity FROM alerts WHERE student_id = 1 AND kind = 'homework_missing'"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["id"]), int(alert_id))
        self.assertEqual(rows[0]["severity"], "critical")

    def test_thresholds_come_from_the_config(self):
        relaxed = config_loader.parse_config(
            {
                "semester": {"starts_on": "2026-09-01", "ends_on": "2027-01-22"},
                "paths": {"database": "data/x.db", "output_dir": "out"},
                "classes": {"names": ["高一(A)班"]},
                "demo": {"students_per_class": 3},
                "alerts": {"missing_homework_threshold": 3, "low_average_threshold": 40},
            },
            base_dir=self.base,
        )

        result = alerts_module.scan_alerts(relaxed)

        kinds = {candidate.kind for candidate in result["candidates"]}
        students = {candidate.student_uid for candidate in result["candidates"]}
        self.assertNotIn("homework_missing", kinds)
        self.assertNotIn("高一(A)班-02", students)  # 平均分 52.5 高于新阈值 40
        self.assertIn("score_drop", kinds)

    def test_dry_run_writes_nothing(self):
        result = self.scan(dry_run=True)

        self.assertEqual(len(result["candidates"]), 3)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM alerts")[0]["n"], 0)


class FollowUpTests(AlertTestCase):
    def test_resolve_writes_a_follow_up_in_the_same_transaction(self):
        self.scan()
        alert_id = self.alerts_by_student()["高一(A)班-01"][0]["id"]

        result = alerts_module.resolve_alert(
            self.config,
            alert_id=int(alert_id),
            note="已和家长沟通，本周补交",
            outcome="已补交",
            recorded_by="演示教师",
        )

        self.assertEqual(result["alert_id"], int(alert_id))
        alert = self.query("SELECT status, resolved_at FROM alerts WHERE id = ?", (int(alert_id),))[0]
        follow_ups = self.query(
            "SELECT note, outcome, recorded_by FROM follow_ups WHERE alert_id = ?",
            (int(alert_id),),
        )
        self.assertEqual(alert["status"], "resolved")
        self.assertTrue(alert["resolved_at"])
        self.assertEqual(len(follow_ups), 1)
        self.assertEqual(follow_ups[0]["note"], "已和家长沟通，本周补交")
        self.assertEqual(follow_ups[0]["outcome"], "已补交")
        self.assertEqual(follow_ups[0]["recorded_by"], "演示教师")

    def test_empty_note_is_rejected(self):
        self.scan()
        alert_id = int(self.alerts_by_student()["高一(A)班-01"][0]["id"])

        with self.assertRaises(config_loader.ConfigError) as ctx:
            alerts_module.resolve_alert(self.config, alert_id=alert_id, note="   ")

        self.assertIn("不允许静默解决", str(ctx.exception))
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM follow_ups")[0]["n"], 0)
        self.assertEqual(
            self.query("SELECT status FROM alerts WHERE id = ?", (alert_id,))[0]["status"],
            "open",
        )

    def test_unknown_and_already_resolved_alerts_are_rejected(self):
        self.scan()
        alert_id = int(self.alerts_by_student()["高一(A)班-01"][0]["id"])
        alerts_module.resolve_alert(self.config, alert_id=alert_id, note="跟进过了")

        with self.assertRaises(config_loader.ConfigError) as ctx:
            alerts_module.resolve_alert(self.config, alert_id=alert_id, note="再来一次")
        self.assertIn("已经解决", str(ctx.exception))

        with self.assertRaises(config_loader.ConfigError) as ctx:
            alerts_module.resolve_alert(self.config, alert_id=9999, note="不存在")
        self.assertIn("找不到预警", str(ctx.exception))

    def test_disappearing_condition_does_not_auto_resolve(self):
        self.scan()
        self.execute(
            """
            UPDATE homework_submissions SET status = 'submitted'
            WHERE student_id = 1
            """
        )

        result = self.scan()

        self.assertEqual(result["stale"], 1)
        self.assertEqual(result["created"], 0)
        self.assertEqual(
            self.query("SELECT status FROM alerts WHERE student_id = 1")[0]["status"], "open"
        )
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM follow_ups")[0]["n"], 0)

    def test_list_and_stats(self):
        self.scan()
        alert_id = int(self.alerts_by_student()["高一(A)班-02"][0]["id"])
        alerts_module.resolve_alert(self.config, alert_id=alert_id, note="已面谈")

        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            open_rows = alerts_module.list_alerts(conn, status="open")
            resolved_rows = alerts_module.list_alerts(conn, status="resolved")
            stats = alerts_module.alert_stats(conn)
        finally:
            conn.close()

        self.assertEqual(len(open_rows), 2)
        self.assertEqual(len(resolved_rows), 1)
        self.assertEqual(stats["by_status"], {"open": 2, "resolved": 1})
        self.assertEqual(stats["follow_ups"], 1)
        self.assertEqual(stats["open_by_severity"].get("warning"), 1)

    def test_cli_resolve_and_stats(self):
        self.scan()
        alert_id = int(self.alerts_by_student()["高一(A)班-03"][0]["id"])
        out = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hub.main(
                [
                    "--config",
                    str(self.config_path),
                    "resolve-alert",
                    str(alert_id),
                    "--note",
                    "已讲评并安排订正",
                ]
            )
            stats_code = hub.main(
                ["--config", str(self.config_path), "alert-stats"]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stats_code, 0)
        self.assertIn("已解决预警", out.getvalue())
        self.assertIn("跟进记录：1 条", out.getvalue())


class DatabaseIsolationTests(AlertTestCase):
    def test_db_option_keeps_the_default_database_untouched(self):
        other = self.base / "isolated.db"
        shutil.copy(self.config.paths.database, other)

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = hub.main(
                ["--config", str(self.config_path), "--db", str(other), "scan-alerts"]
            )

        self.assertEqual(code, 0)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM alerts")[0]["n"], 0)

        conn = sqlite3.connect(other)
        try:
            isolated = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(isolated, 3)

    def test_db_option_is_honoured_by_resolve_too(self):
        other = self.base / "isolated.db"
        shutil.copy(self.config.paths.database, other)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            hub.main(["--config", str(self.config_path), "--db", str(other), "scan-alerts"])
            conn = sqlite3.connect(other)
            alert_id = conn.execute("SELECT id FROM alerts ORDER BY id").fetchone()[0]
            conn.close()
            code = hub.main(
                [
                    "--config",
                    str(self.config_path),
                    "--db",
                    str(other),
                    "resolve-alert",
                    str(alert_id),
                    "--note",
                    "隔离库里解决",
                ]
            )

        self.assertEqual(code, 0)
        conn = sqlite3.connect(other)
        try:
            status = conn.execute(
                "SELECT status FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone()[0]
            follow_ups = conn.execute("SELECT COUNT(*) FROM follow_ups").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(status, "resolved")
        self.assertEqual(follow_ups, 1)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM follow_ups")[0]["n"], 0)


if __name__ == "__main__":
    unittest.main()
