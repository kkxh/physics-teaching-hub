"""看板测试：数据与库一致、文案来自 labels、无绝对路径/CDN、页面脚本能跑。"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import alerts as alerts_module  # noqa: E402
import config_loader  # noqa: E402
import dashboard as dashboard_module  # noqa: E402
import hub  # noqa: E402
import import_scores  # noqa: E402
import init_db  # noqa: E402

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
students_per_class = 3
"""

# 页面脚本依赖的极简 DOM 桩：够跑通渲染，并把最终 DOM 打出来给 Python 断言
DOM_STUB = """
const PAYLOAD = process.argv[2];
const nodes = {};
function makeNode(tag) {
  return {
    tagName: tag, children: [], style: {}, className: "", colSpan: 0,
    textContent: "", appendChild(child) { this.children.push(child); return child; },
    addEventListener() {},
  };
}
global.document = {
  getElementById(id) { if (!nodes[id]) { nodes[id] = makeNode("div"); } return nodes[id]; },
  createElement(tag) { return makeNode(tag); },
};
nodes["dashboard-data"] = { textContent: PAYLOAD };
"""
RENDER_REPORT = """
console.log(JSON.stringify(nodes));
"""


def load_dashboard_script(html: str) -> str:
    """取出页面里那段应用脚本（不是数据块）。"""
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    if not blocks:
        raise AssertionError("页面里没有内联脚本")
    return blocks[-1]


class DashboardTestCase(unittest.TestCase):
    seed_accounts = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config_path = self.base / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})
        init_db.init_database(self.config)
        if self.seed_accounts:
            import_scores.import_demo_scores(self.config)
            alerts_module.scan_alerts(self.config)

    def build(self) -> tuple[Path, dict]:
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            index_path = dashboard_module.write_dashboard(self.config, conn)
        finally:
            conn.close()
        data = json.loads(
            (self.config.paths.output_dir / "dashboard" / "data.json").read_text(
                encoding="utf-8"
            )
        )
        return index_path, data

    def query(self, sql: str) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


class DataTests(DashboardTestCase):
    def test_data_matches_database(self):
        _index, data = self.build()

        self.assertEqual(data["counts"]["classes"], 2)
        self.assertEqual(data["counts"]["students"], 6)
        average = self.query("SELECT AVG(score) AS a FROM exam_scores WHERE exam_id = 1")[0]["a"]
        exam = next(item for item in data["exams"] if item["exam_key"] == "demo-exam-1")
        self.assertAlmostEqual(float(exam["average"]), float(average), places=6)

        open_alerts = self.query("SELECT COUNT(*) AS n FROM alerts WHERE status = 'open'")[0]["n"]
        self.assertEqual(data["counts"]["alerts"], int(open_alerts))
        self.assertEqual(len(data["alerts"]), int(open_alerts))

        homework = {item["class_name"]: item for item in data["homework"]}
        class_a = homework["高一(A)班"]
        submitted = self.query(
            """
            SELECT COUNT(*) AS n FROM homework_submissions sub
            JOIN homework_assignments a ON a.id = sub.assignment_id
            JOIN students s ON s.id = sub.student_id AND s.class_id = a.class_id
            WHERE a.class_id = 1 AND sub.status IN ('submitted', 'late')
            """
        )[0]["n"]
        self.assertEqual(class_a["submitted"], submitted)

    def test_class_averages_are_included_for_filtering(self):
        _index, data = self.build()

        self.assertIn("高一(A)班", data["classAverages"])
        self.assertIn("demo-exam-1", data["classAverages"]["高一(A)班"])

    def test_written_files_have_no_absolute_path_or_cdn(self):
        index_path, _data = self.build()
        html = index_path.read_text(encoding="utf-8")
        data_text = (
            self.config.paths.output_dir / "dashboard" / "data.json"
        ).read_text(encoding="utf-8")

        for text in (html, data_text):
            self.assertNotIn("/Users/", text)
            self.assertNotIn(str(self.base), text)
        self.assertNotIn("https://", html)
        self.assertNotIn("http://cdn", html)
        self.assertNotIn("<script src=", html)

    def test_labels_are_injected_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            custom = directory / "labels-custom.toml"
            custom.write_text(
                "\n".join(
                    (
                        "[project]",
                        'default_name = "演示中枢"',
                        "[dashboard]",
                        'title = "演示看板标题"',
                        'empty_value = "（无）"',
                    )
                ),
                encoding="utf-8",
            )
            # 自定义文案表只需要覆盖看板标题与空值；其余 key 从自带表补齐
            base_labels = (ROOT / "labels" / "zh-CN.toml").read_text(encoding="utf-8")
            merged = base_labels.replace('title = "教学看板"', 'title = "演示看板标题"').replace(
                'empty_value = "—"', 'empty_value = "（无）"'
            )
            custom.write_text(merged, encoding="utf-8")

            config = config_loader.parse_config(
                {
                    "semester": {"starts_on": "2026-09-01", "ends_on": "2027-01-22"},
                    "paths": {"database": "data/x.db", "output_dir": "out"},
                    "classes": {"names": ["高一(A)班"]},
                    "demo": {"students_per_class": 3},
                    "project": {"labels_dir": str(directory), "locale": "labels-custom"},
                },
                base_dir=self.base,
            )

            index_path, data = self.build_with(config)

        html = index_path.read_text(encoding="utf-8")
        self.assertIn("演示看板标题", html)
        self.assertEqual(data["labels"]["empty_value"], "（无）")

    def build_with(self, config: config_loader.AppConfig) -> tuple[Path, dict]:
        conn = sqlite3.connect(config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            index_path = dashboard_module.write_dashboard(config, conn)
        finally:
            conn.close()
        data = json.loads(
            (config.paths.output_dir / "dashboard" / "data.json").read_text(encoding="utf-8")
        )
        return index_path, data

    def test_template_handles_empty_values(self):
        index_path, _data = self.build()
        html = index_path.read_text(encoding="utf-8")

        self.assertIn("function fmtScore", html)
        self.assertIn("function fmtRate", html)
        self.assertIn('return EMPTY', html)


class EmptyDatabaseTests(DashboardTestCase):
    seed_accounts = False

    def test_empty_database_still_builds(self):
        index_path, data = self.build()

        self.assertEqual(data["counts"], {"classes": 2, "students": 6, "exams": 0, "alerts": 0})
        self.assertEqual(data["exams"], [])
        self.assertEqual(data["alerts"], [])
        self.assertTrue(index_path.is_file())

    def test_cli_writes_dashboard(self):
        out = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hub.main(["--config", str(self.config_path), "make-dashboard"])

        self.assertEqual(code, 0)
        self.assertIn("已生成看板", out.getvalue())
        self.assertTrue(
            (self.config.paths.output_dir / "dashboard" / "index.html").is_file()
        )


@unittest.skipUnless(shutil.which("node"), "需要 node 才能跑页面脚本")
class ScriptRuntimeTests(DashboardTestCase):
    def run_script(self, html: str, payload: dict) -> str:
        script = DOM_STUB + load_dashboard_script(html) + RENDER_REPORT
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "harness.js"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["node", str(path), json.dumps(payload, ensure_ascii=False)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return result.stdout

    def test_script_renders_real_data_without_throwing(self):
        index_path, data = self.build()

        rendered = self.run_script(index_path.read_text(encoding="utf-8"), data)

        self.assertIn("高一(A)班", rendered)
        self.assertIn("演示期中考试", rendered)

    def test_script_renders_empty_data_gracefully(self):
        index_path, data = self.build()
        empty = dict(data)
        empty["exams"] = []
        empty["alerts"] = []
        empty["homework"] = []
        empty["classAverages"] = {}

        rendered = self.run_script(index_path.read_text(encoding="utf-8"), empty)

        self.assertIn(data["labels"]["no_data"], rendered)
        self.assertNotIn("null", rendered)


if __name__ == "__main__":
    unittest.main()
