"""Excel（.xlsx）成绩导入测试；没装 openpyxl 时自动跳过。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import importer  # noqa: E402
import init_db  # noqa: E402

try:  # 运行时依赖；CI 会装，本地没装就跳过（缺依赖的报错另有用例覆盖）
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

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


@unittest.skipUnless(openpyxl is not None, "需要 openpyxl（pip install -r requirements.txt）")
class ExcelImportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config_path = self.base / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})
        init_db.init_database(self.config)

    def write_workbook(self, sheets: dict[str, list[list[object]]]) -> Path:
        path = self.base / "scores.xlsx"
        workbook = openpyxl.Workbook()
        default = workbook.active
        first = True
        for title, rows in sheets.items():
            sheet = default if first else workbook.create_sheet()
            sheet.title = title
            for row in rows:
                sheet.append(row)
            first = False
        workbook.save(path)
        return path

    def counts(self) -> tuple[int, int]:
        import sqlite3

        conn = sqlite3.connect(self.config.paths.database)
        try:
            exams = conn.execute("SELECT COUNT(*) FROM exams").fetchone()[0]
            scores = conn.execute("SELECT COUNT(*) FROM exam_scores").fetchone()[0]
        finally:
            conn.close()
        return exams, scores

    def test_first_sheet_is_used_by_default(self):
        path = self.write_workbook(
            {"成绩": [["student_uid", "score"], ["高一(A)班-01", 88], ["高一(A)班-02", 0]]}
        )

        result = importer.import_scores_from_excel(
            self.config, xlsx_path=path, exam_name="十月月考", exam_date="2026-10-15"
        )

        self.assertEqual(result["scores_written"], 2)
        self.assertEqual(self.counts(), (1, 2))

    def test_named_sheet_can_be_selected(self):
        path = self.write_workbook(
            {
                "说明": [["这个表不是成绩"]],
                "成绩": [["student_uid", "score"], ["高一(A)班-03", 75]],
            }
        )

        result = importer.import_scores_from_excel(
            self.config,
            xlsx_path=path,
            exam_name="十月月考",
            exam_date="2026-10-15",
            sheet="成绩",
        )

        self.assertEqual(result["scores_written"], 1)

    def test_unknown_sheet_is_reported(self):
        path = self.write_workbook({"成绩": [["student_uid", "score"], ["高一(A)班-01", 80]]})

        with self.assertRaises(config_loader.ConfigError) as ctx:
            importer.import_scores_from_excel(
                self.config,
                xlsx_path=path,
                exam_name="十月月考",
                exam_date="2026-10-15",
                sheet="不存在",
            )

        message = str(ctx.exception)
        self.assertIn("没有工作表", message)
        self.assertIn("成绩", message)

    def test_empty_score_cell_is_rejected(self):
        path = self.write_workbook(
            {"成绩": [["student_uid", "score"], ["高一(A)班-01", None]]}
        )

        with self.assertRaises(config_loader.ConfigError) as ctx:
            importer.import_scores_from_excel(
                self.config, xlsx_path=path, exam_name="十月月考", exam_date="2026-10-15"
            )

        self.assertIn("没有分数", str(ctx.exception))
        self.assertEqual(self.counts(), (0, 0))

    def test_custom_headers_and_dry_run(self):
        path = self.write_workbook(
            {"成绩": [["学号", "分数"], ["高一(A)班-01", 91]]}
        )

        plan = importer.import_scores_from_excel(
            self.config,
            xlsx_path=path,
            exam_name="十月月考",
            exam_date="2026-10-15",
            columns_spec="学号=student_uid,分数=score",
            dry_run=True,
        )

        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["plan"].row_count, 1)
        self.assertEqual(self.counts(), (0, 0))

    def test_trailing_empty_rows_are_ignored(self):
        path = self.write_workbook(
            {"成绩": [["student_uid", "score"], ["高一(A)班-01", 70], [None, None]]}
        )

        result = importer.import_scores_from_excel(
            self.config, xlsx_path=path, exam_name="十月月考", exam_date="2026-10-15"
        )

        self.assertEqual(result["scores_written"], 1)


class MissingDependencyTests(unittest.TestCase):
    def test_missing_openpyxl_is_reported_with_install_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.xlsx"
            path.write_bytes(b"not really xlsx")

            with mock.patch.dict(sys.modules, {"openpyxl": None}):
                with self.assertRaises(config_loader.ConfigError) as ctx:
                    importer.read_score_rows_from_excel(path, importer.parse_columns(None))

        message = str(ctx.exception)
        self.assertIn("openpyxl", message)
        self.assertIn("requirements.txt", message)


if __name__ == "__main__":
    unittest.main()
