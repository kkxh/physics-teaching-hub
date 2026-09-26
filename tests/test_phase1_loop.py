"""Phase 1 最小闭环的端到端测试：三条命令 → 数据库 → 报告。"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import import_scores  # noqa: E402
import init_db  # noqa: E402
import seed_demo_data  # noqa: E402

CONFIG_TEXT = """
[project]
stage = "high_school"

[semester]
name = "2026-2027 学年第一学期"
starts_on = "2026-09-01"
ends_on = "2027-01-22"

[paths]
database = "data/physics.db"
output_dir = "out"

[classes]
names = ["高一(A)班", "高一(B)班"]

[demo]
seed = 20240921
students_per_class = 6

[schedule]
starts_on = "2026-09-01"
weekdays = [1, 3, 5]
periods = ["第1节", "第2节"]
"""

LOOP_COMMANDS = (
    ("init_db.py", ("--demo",)),
    ("import_scores.py", ()),
    ("make_report.py", ()),
)


@contextlib.contextmanager
def quiet():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


class LoopTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        conf = self.base / "conf"
        conf.mkdir()
        self.config_path = conf / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})

    def run_script(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / script),
                "--config",
                str(self.config_path),
                *args,
            ],
            cwd=self.base,  # 故意换一个工作目录：路径都该按配置文件解析
            capture_output=True,
            text=True,
            check=False,
        )

    def run_loop(self) -> Path:
        for script, extra in LOOP_COMMANDS:
            result = self.run_script(script, *extra)
            self.assertEqual(
                result.returncode,
                0,
                msg=f"{script} 失败：{result.stdout}{result.stderr}",
            )
        return self.config.paths.output_dir / "phase1_report.md"

    def table_counts(self) -> dict[str, int]:
        conn = sqlite3.connect(self.config.paths.database)
        try:
            return {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("classes", "students", "exams", "exam_scores")
            }
        finally:
            conn.close()

    def meta(self, key: str) -> str | None:
        conn = sqlite3.connect(self.config.paths.database)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return None if row is None else str(row[0])
        finally:
            conn.close()

    def write_dataset(self, dataset: dict[str, Any]) -> Path:
        return seed_demo_data.write_dataset(dataset, self.config.demo.output)


class LoopTests(LoopTestCase):
    def test_three_commands_produce_a_report(self):
        report_path = self.run_loop()

        self.assertTrue(self.config.paths.database.is_file())
        self.assertTrue(report_path.is_file())

        text = report_path.read_text(encoding="utf-8")
        self.assertIn(self.config.semester.name, text)
        for class_name in self.config.class_names:
            self.assertIn(class_name, text)
        self.assertIn("教学周", text)
        self.assertIn(self.config.demo.exams[0].name, text)
        # 报告要能安全分享：里面只出现相对路径，不带本机绝对路径
        self.assertNotIn(str(self.config.paths.database), text)
        self.assertIn("data/physics.db", text)

        conn = sqlite3.connect(self.config.paths.database)
        try:
            average = conn.execute(
                """
                SELECT AVG(s.score) FROM exam_scores s
                JOIN exams e ON e.id = s.exam_id
                WHERE e.exam_key = ?
                """,
                ("demo-exam-1",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertIn(f"| {float(average):.1f} |", text)

    def test_loop_is_idempotent(self):
        self.run_loop()
        first = self.table_counts()

        self.run_loop()
        second = self.table_counts()

        self.assertEqual(first, second)
        self.assertEqual(second["classes"], len(self.config.class_names))
        self.assertEqual(
            second["students"],
            len(self.config.class_names) * self.config.demo.students_per_class,
        )
        self.assertEqual(second["exams"], len(self.config.demo.exams))

    def test_schema_is_marked_phase3(self):
        self.run_loop()

        self.assertEqual(self.meta("schema_version"), "phase3")
        self.assertEqual(self.meta("dataset_version"), seed_demo_data.DATASET_VERSION)

        # Phase 1 的临时 schema 声明留在历史文档与迁移说明里
        plan = (ROOT / "docs" / "PHASE1_PLAN.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
        self.assertIn("临时 schema", plan)
        self.assertIn("临时 schema", roadmap)
        migrations_readme = (ROOT / "schema" / "migrations" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("幂等", migrations_readme)

    def test_import_scores_requires_the_database(self):
        result = self.run_script("import_scores.py")

        self.assertEqual(result.returncode, 2)
        self.assertIn("init-db", result.stderr)
        self.assertFalse(self.config.paths.database.exists())

    def test_make_report_requires_the_database(self):
        result = self.run_script("make_report.py")

        self.assertEqual(result.returncode, 2)
        self.assertIn("init-db", result.stderr)

    def test_init_db_requires_the_demo_flag(self):
        result = self.run_script("init_db.py")

        self.assertEqual(result.returncode, 2)
        self.assertIn("--demo", result.stderr)

    def test_non_fictional_names_are_rejected(self):
        dataset = seed_demo_data.build_dataset(self.config)
        dataset["classes"][0]["students"][0]["name"] = "张三"
        self.write_dataset(dataset)

        result = self.run_script("init_db.py", "--demo")

        self.assertEqual(result.returncode, 2)
        self.assertIn("Phase 1 仅支持虚构演示数据", result.stderr)
        self.assertFalse(self.config.paths.database.exists())

    def test_dataset_mismatch_is_rejected(self):
        dataset = seed_demo_data.build_dataset(self.config)
        dataset["seed"] = int(dataset["seed"]) + 1
        self.write_dataset(dataset)

        result = self.run_script("init_db.py", "--demo")

        self.assertEqual(result.returncode, 2)
        self.assertIn("不一致", result.stderr)

    def test_broken_dataset_json_is_reported(self):
        self.config.demo.output.parent.mkdir(parents=True, exist_ok=True)
        self.config.demo.output.write_text("{ not json", encoding="utf-8")

        result = self.run_script("init_db.py", "--demo")

        self.assertEqual(result.returncode, 2)
        self.assertIn("JSON", result.stderr)

    def test_runtime_dependencies_are_intentional(self):
        lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        dependencies = [
            line.strip() for line in lines if line.strip() and not line.strip().startswith("#")
        ]

        # 允许清单：加新依赖必须同时改这里，逼自己在评审里解释一遍为什么标准库不够
        self.assertEqual(dependencies, ["openpyxl>=3.1"])


class ScoreImportTests(unittest.TestCase):
    """直接测导入函数，覆盖命令行走不到的边界。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = init_db.connect(Path(self._tmp.name) / "loop.db")
        self.addCleanup(self.conn.close)
        init_db.apply_schema(self.conn)
        with quiet():
            init_db.import_roster(
                self.conn,
                {
                    "version": seed_demo_data.DATASET_VERSION,
                    "seed": 1,
                    "classes": [
                        {
                            "name": "高一(A)班",
                            "students": [
                                {"id": "高一(A)班-01", "name": "学生01", "seat_no": 1}
                            ],
                        }
                    ],
                },
            )

    def test_zero_score_is_imported(self):
        counts = import_scores.import_exam_scores(
            self.conn,
            {
                "exams": [
                    {
                        "key": "demo-zero",
                        "name": "演示零分考试",
                        "date": "2026-10-01",
                        "full_score": 100,
                        "scores": [{"student_id": "高一(A)班-01", "score": 0}],
                    }
                ]
            },
        )
        row = self.conn.execute("SELECT score FROM exam_scores").fetchone()

        self.assertEqual(counts["scores"], 1)
        self.assertEqual(row["score"], 0)

    def test_missing_score_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            import_scores.import_exam_scores(
                self.conn,
                {
                    "exams": [
                        {
                            "key": "demo-missing",
                            "name": "演示缺分考试",
                            "date": "2026-10-01",
                            "full_score": 100,
                            "scores": [{"student_id": "高一(A)班-01"}],
                        }
                    ]
                },
            )

        self.assertIn("缺少分数", str(ctx.exception))

    def test_unknown_student_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            import_scores.import_exam_scores(
                self.conn,
                {
                    "exams": [
                        {
                            "key": "demo-unknown",
                            "name": "演示陌生学生",
                            "date": "2026-10-01",
                            "full_score": 100,
                            "scores": [{"student_id": "高一(A)班-99", "score": 80}],
                        }
                    ]
                },
            )

        self.assertIn("没有这个学生", str(ctx.exception))

    def test_foreign_keys_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO exam_scores (exam_id, student_id, score)
                    VALUES (?, ?, ?)
                    """,
                    (999, 999, 60),
                )


if __name__ == "__main__":
    unittest.main()
