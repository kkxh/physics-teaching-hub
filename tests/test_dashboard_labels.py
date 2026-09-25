"""看板文案缺失时的报错行为（自定义文案表是早期版本时最常见）。"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import dashboard as dashboard_module  # noqa: E402
import hub  # noqa: E402
import init_db  # noqa: E402


class MissingDashboardLabelsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        labels_dir = self.base / "my-labels"
        labels_dir.mkdir()
        # 模拟「从早期版本复制来的自定义文案表」：没有 [dashboard] 段
        shipped = (ROOT / "labels" / "zh-CN.toml").read_text(encoding="utf-8")
        old_version = re.split(r"\n# 看板页面文案", shipped)[0]
        (labels_dir / "mine.toml").write_text(old_version, encoding="utf-8")

        config_path = self.base / "config.toml"
        config_path.write_text(
            "\n".join(
                (
                    "[project]",
                    'locale = "mine"',
                    'labels_dir = "my-labels"',
                    "[semester]",
                    'starts_on = "2026-09-01"',
                    'ends_on = "2027-01-22"',
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
        self.config_path = config_path
        self.config = config_loader.load_config(config_path, env={})
        init_db.init_database(self.config)

    def test_module_raises_actionable_config_error(self):
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            with self.assertRaises(config_loader.ConfigError) as ctx:
                dashboard_module.write_dashboard(self.config, conn)
        finally:
            conn.close()

        message = str(ctx.exception)
        self.assertIn("dashboard.title", message)
        self.assertIn("[dashboard]", message)

    def test_cli_reports_without_traceback(self):
        err = io.StringIO()

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = hub.main(["--config", str(self.config_path), "make-dashboard"])

        self.assertEqual(code, 2)
        text = err.getvalue()
        self.assertIn("[错误]", text)
        self.assertIn("[dashboard]", text)
        self.assertNotIn("Traceback", text)


if __name__ == "__main__":
    unittest.main()
