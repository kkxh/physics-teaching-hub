"""统一 CLI（hub.py）的测试：子命令、--db 覆盖、以及旧脚本垫片的等价性。"""

from __future__ import annotations

import contextlib
import io
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import hub  # noqa: E402
import init_db  # noqa: E402

CONFIG_TEXT = """
[project]
stage = "high_school"

[semester]
starts_on = "2026-09-01"
ends_on = "2027-01-22"

[paths]
database = "data/physics.db"
output_dir = "out"

[classes]
names = ["高一(A)班"]

[demo]
students_per_class = 4
"""


class HubCliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        conf = self.base / "conf"
        conf.mkdir()
        self.config_path = conf / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})

    def run_hub(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "hub.py"), *args],
            cwd=cwd or self.base,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_script(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / script), *args],
            cwd=self.base,
            capture_output=True,
            text=True,
            check=False,
        )


class SubcommandTests(HubCliTestCase):
    def test_three_subcommands_run_the_full_loop(self):
        steps = (
            ("init-db", "--demo"),
            ("import-scores", "--demo"),
            ("make-report",),
        )
        for step in steps:
            with self.subTest(command=step[0]):
                result = self.run_hub("--config", str(self.config_path), *step)
                self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

        self.assertTrue(self.config.paths.database.is_file())
        report = self.config.paths.output_dir / "phase1_report.md"
        self.assertTrue(report.is_file())
        self.assertIn("教学周", report.read_text(encoding="utf-8"))

    def test_global_options_also_work_after_the_subcommand(self):
        result = self.run_hub("init-db", "--config", str(self.config_path), "--demo")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(self.config.paths.database.is_file())

    def test_db_option_redirects_writes(self):
        other = self.base / "elsewhere" / "isolated.db"

        result = self.run_hub("--config", str(self.config_path), "--db", str(other), "init-db", "--demo")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(other.is_file())
        self.assertFalse(self.config.paths.database.exists())
        conn = sqlite3.connect(other)
        try:
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(version, "phase3")

    def test_import_scores_requires_an_explicit_source(self):
        self.run_hub("--config", str(self.config_path), "init-db", "--demo")

        result = self.run_hub("--config", str(self.config_path), "import-scores")

        self.assertEqual(result.returncode, 2)
        self.assertIn("--demo", result.stderr)
        conn = sqlite3.connect(self.config.paths.database)
        try:
            scores = conn.execute("SELECT COUNT(*) FROM exam_scores").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(scores, 0)

    def test_import_scores_csv_needs_exam_metadata(self):
        result = self.run_hub(
            "--config", str(self.config_path), "import-scores", "--csv", "scores.csv"
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--exam", result.stderr)

    def test_init_db_without_demo_is_rejected(self):
        result = self.run_hub("--config", str(self.config_path), "init-db")

        self.assertEqual(result.returncode, 2)
        self.assertIn("--demo", result.stderr)

    def test_corrupt_database_reports_a_friendly_error(self):
        database = self.config.paths.database
        database.parent.mkdir(parents=True, exist_ok=True)
        database.write_bytes(b"this is not a database")

        result = self.run_hub("--config", str(self.config_path), "make-report")

        self.assertEqual(result.returncode, 2)
        self.assertIn("SQLite", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class UpgradeCommandTests(HubCliTestCase):
    """P3.0：存量 phase2 库的子命令行为（拒绝乱用 / 升级 / 幂等）。"""

    def write_phase2_database(self) -> None:
        database = self.config.paths.database
        database.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(database)
        try:
            for name in init_db.SCHEMA_FILES:
                if name == "questions.sql":
                    continue
                conn.executescript((init_db.SCHEMA_DIR / name).read_text(encoding="utf-8"))
            with conn:
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', 'phase2')"
                )
                conn.execute("INSERT INTO classes (name) VALUES ('标记班')")
        finally:
            conn.close()

    def test_stale_database_is_told_to_run_upgrade_db(self):
        self.write_phase2_database()

        result = self.run_hub("--config", str(self.config_path), "make-report")

        self.assertEqual(result.returncode, 2)
        self.assertIn("upgrade-db", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_upgrade_db_applies_migrations_without_demo_data(self):
        self.write_phase2_database()

        result = self.run_hub("--config", str(self.config_path), "upgrade-db")

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("0001_questions.sql", result.stdout)
        conn = sqlite3.connect(self.config.paths.database)
        try:
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
            classes = [row[0] for row in conn.execute("SELECT name FROM classes")]
        finally:
            conn.close()
        self.assertEqual(version, "phase3")
        self.assertTrue({"questions", "question_tags"} <= tables)
        self.assertEqual(classes, ["标记班"], msg="upgrade-db 不该顺带导入演示名单")

    def test_upgrade_db_on_a_current_database_is_a_no_op(self):
        self.run_hub("--config", str(self.config_path), "init-db", "--demo")

        result = self.run_hub("--config", str(self.config_path), "upgrade-db")

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("不需要升级", result.stdout)

    def test_upgrade_db_reports_a_missing_database(self):
        result = self.run_hub("--config", str(self.config_path), "upgrade-db")

        self.assertEqual(result.returncode, 2)
        self.assertIn("init-db", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


QUESTION_TAGS = (
    "运动学图像",
    "匀变速直线运动",
    "牛顿第二定律",
    "受力分析",
    "机械能守恒",
    "欧姆定律",
    "串并联电路",
    "实验数据处理",
)


class ImportQuestionsCommandTests(HubCliTestCase):
    """P3.1：import-questions 的来源校验、dry-run 与演示导入。"""

    def questions_config(self) -> Path:
        # 放在与原配置同一个目录，相对路径（data/physics.db）才解析到同一个库
        path = self.config_path.parent / "questions_config.toml"
        tags = ", ".join(f'"{tag}"' for tag in QUESTION_TAGS)
        path.write_text(
            CONFIG_TEXT + f"\n[question_bank]\ntags = [{tags}]\n", encoding="utf-8"
        )
        return path

    def question_count(self) -> int:
        conn = sqlite3.connect(self.config.paths.database)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0])
        finally:
            conn.close()

    def test_source_is_required(self):
        config_path = self.questions_config()
        self.run_hub("--config", str(config_path), "init-db", "--demo")

        result = self.run_hub("--config", str(config_path), "import-questions")

        self.assertEqual(result.returncode, 2)
        self.assertIn("--demo", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_demo_import_after_a_dry_run(self):
        config_path = self.questions_config()
        self.run_hub("--config", str(config_path), "init-db", "--demo")

        preview = self.run_hub(
            "--config", str(config_path), "import-questions", "--demo", "--dry-run"
        )
        self.assertEqual(preview.returncode, 0, msg=preview.stdout + preview.stderr)
        self.assertIn("[dry-run]", preview.stdout)
        self.assertEqual(self.question_count(), 0)

        result = self.run_hub("--config", str(config_path), "import-questions", "--demo")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("已导入题目", result.stdout)
        self.assertGreater(self.question_count(), 0)

        again = self.run_hub("--config", str(config_path), "import-questions", "--demo")
        self.assertEqual(again.returncode, 2)
        self.assertIn("已经有", again.stderr)


class ShimTests(HubCliTestCase):
    """Phase 1 的三个脚本继续可用，效果与 hub 子命令一致。"""

    def test_old_scripts_still_run_the_loop(self):
        for script, extra in (
            ("init_db.py", ("--demo",)),
            ("import_scores.py", ()),
            ("make_report.py", ()),
        ):
            with self.subTest(script=script):
                result = self.run_script(script, "--config", str(self.config_path), *extra)
                self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

        self.assertTrue(self.config.paths.database.is_file())
        self.assertTrue((self.config.paths.output_dir / "phase1_report.md").is_file())

    def test_shim_and_subcommand_write_the_same_rows(self):
        self.run_script("init_db.py", "--config", str(self.config_path), "--demo")
        self.run_script("import_scores.py", "--config", str(self.config_path))
        counts = self.row_counts(self.config.paths.database)

        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "hub.db"
            for step in (("init-db", "--demo"), ("import-scores", "--demo")):
                result = self.run_hub(
                    "--config", str(self.config_path), "--db", str(other), *step
                )
                self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(self.row_counts(other), counts)

    def row_counts(self, database: Path) -> dict[str, int]:
        conn = sqlite3.connect(database)
        try:
            return {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("classes", "students", "exams", "exam_scores")
            }
        finally:
            conn.close()


class HubFunctionTests(HubCliTestCase):
    def test_load_config_for_cli_overrides_only_the_database(self):
        config = hub.load_config_for_cli(str(self.config_path), str(self.base / "x.db"))

        self.assertEqual(config.paths.database, self.base / "x.db")
        self.assertEqual(config.paths.output_dir, self.config.paths.output_dir)
        self.assertEqual(config.class_names, self.config.class_names)

    def test_main_parses_and_dispatches(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hub.main(
                ["--config", str(self.config_path), "init-db", "--demo"]
            )

        self.assertEqual(code, 0)
        self.assertIn("已建库", out.getvalue())


if __name__ == "__main__":
    unittest.main()
