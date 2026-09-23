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
        self.assertEqual(version, "phase2")

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

    def test_import_scores_csv_is_not_implemented_yet(self):
        result = self.run_hub(
            "--config", str(self.config_path), "import-scores", "--csv", "scores.csv"
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("P2.1", result.stderr)

    def test_init_db_without_demo_is_rejected(self):
        result = self.run_hub("--config", str(self.config_path), "init-db")

        self.assertEqual(result.returncode, 2)
        self.assertIn("--demo", result.stderr)


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
