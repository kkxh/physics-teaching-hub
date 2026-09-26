"""考试链路测试：小题导入、得分率/难度/区分度、讲义与版权标记。"""

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
import exam as exam_module  # noqa: E402
import hub  # noqa: E402
import import_scores  # noqa: E402
import init_db  # noqa: E402
import questions as questions_module  # noqa: E402
import seed_demo_data  # noqa: E402

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
students_per_class = 4
"""


class ExamTestCase(unittest.TestCase):
    clear_exams = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config_path = self.base / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})
        init_db.init_database(self.config)
        import_scores.import_demo_scores(self.config)
        if self.clear_exams:
            self.clear_exam_data()

    def clear_exam_data(self) -> None:
        for table in ("item_scores", "exam_items", "exam_scores", "exams"):
            self.execute(f"DELETE FROM {table}")

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

    def write_csv(self, text: str, name: str = "items.csv") -> Path:
        path = self.base / name
        path.write_text(text, encoding="utf-8")
        return path

    def seed_small_exam(self) -> None:
        """4 名学生、2 道小题的确定性夹具，用来手工验算分析结果。"""
        self.execute(
            """
            INSERT INTO exams (exam_key, name, exam_date, full_score)
            VALUES ('e-small', '演示小考', '2026-10-14', 100)
            """
        )
        for index, (total, first, second) in enumerate(
            ((100, 50, 50), (80, 40, 40), (60, 30, 30), (40, 0, 40)), start=1
        ):
            self.execute(
                """
                INSERT INTO exam_scores (exam_id, student_id, score)
                VALUES (1, ?, ?)
                """,
                (index, total),
            )
            for item_no, value in (("1", first), ("2", second)):
                self.execute(
                    """
                    INSERT INTO exam_items (exam_id, item_no, full_score)
                    VALUES (1, ?, 50)
                    ON CONFLICT (exam_id, item_no) DO NOTHING
                    """,
                    (item_no,),
                )
                self.execute(
                    """
                    INSERT INTO item_scores (item_id, student_id, score)
                    VALUES (
                        (SELECT id FROM exam_items WHERE exam_id = 1 AND item_no = ?),
                        ?, ?
                    )
                    """,
                    (item_no, index, value),
                )

    def analysis(self, exam_key: str) -> exam_module.ExamAnalysis:
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            return exam_module.analyze_exam(conn, exam_key=exam_key)
        finally:
            conn.close()


class ItemImportTests(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.execute(
            """
            INSERT INTO exams (exam_key, name, exam_date, full_score)
            VALUES ('e-import', '演示导入考', '2026-10-14', 100)
            """
        )

    def test_dry_run_and_real_import_agree(self):
        csv_path = self.write_csv(
            "student_uid,item_no,score\n"
            "高一(A)班-01,1,18\n"
            "高一(A)班-01,2,20\n"
            "高一(A)班-02,1,10\n"
        )

        plan = exam_module.import_item_scores_from_csv(
            self.config,
            csv_path=csv_path,
            exam_key="e-import",
            default_item_score=20.0,
            dry_run=True,
        )
        self.assertEqual(plan["plan"].row_count, 3)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM item_scores")[0]["n"], 0)

        result = exam_module.import_item_scores_from_csv(
            self.config, csv_path=csv_path, exam_key="e-import", default_item_score=20.0
        )
        self.assertEqual(result["item_scores_written"], plan["plan"].row_count)
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM exam_items")[0]["n"], 2)

    def test_reimport_is_idempotent(self):
        csv_path = self.write_csv("student_uid,item_no,score\n高一(A)班-01,1,15\n")

        exam_module.import_item_scores_from_csv(
            self.config, csv_path=csv_path, exam_key="e-import", default_item_score=20.0
        )
        exam_module.import_item_scores_from_csv(
            self.config, csv_path=csv_path, exam_key="e-import", default_item_score=20.0
        )

        rows = self.query("SELECT item_id, student_id, score FROM item_scores")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["score"], 15.0)

    def test_full_score_column_overrides_default(self):
        csv_path = self.write_csv(
            "student_uid,item_no,score,full_score\n高一(A)班-01,1,18,20\n"
        )

        exam_module.import_item_scores_from_csv(
            self.config, csv_path=csv_path, exam_key="e-import"
        )

        item = self.query("SELECT full_score FROM exam_items")[0]
        self.assertEqual(item["full_score"], 20.0)

    def test_default_item_score_is_used_when_missing(self):
        csv_path = self.write_csv("student_uid,item_no,score\n高一(A)班-01,1,6\n")

        exam_module.import_item_scores_from_csv(
            self.config,
            csv_path=csv_path,
            exam_key="e-import",
            default_item_score=12.0,
        )

        self.assertEqual(self.query("SELECT full_score FROM exam_items")[0]["full_score"], 12.0)

    def test_unknown_exam_or_student_is_rejected_without_writes(self):
        csv_path = self.write_csv("student_uid,item_no,score\n高一(A)班-01,1,10\n")
        with self.assertRaises(config_loader.ConfigError) as ctx:
            exam_module.import_item_scores_from_csv(
                self.config, csv_path=csv_path, exam_key="no-such-exam"
            )
        self.assertIn("找不到考试", str(ctx.exception))

        bad_csv = self.write_csv("student_uid,item_no,score\n高一(A)班-99,1,10\n", "bad.csv")
        with self.assertRaises(config_loader.ConfigError) as ctx:
            exam_module.import_item_scores_from_csv(
                self.config, csv_path=bad_csv, exam_key="e-import"
            )
        self.assertIn("找不到学生", str(ctx.exception))
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM item_scores")[0]["n"], 0)

    def test_score_above_full_score_is_rejected(self):
        csv_path = self.write_csv("student_uid,item_no,score\n高一(A)班-01,1,25\n")

        with self.assertRaises(config_loader.ConfigError) as ctx:
            exam_module.import_item_scores_from_csv(
                self.config,
                csv_path=csv_path,
                exam_key="e-import",
                default_item_score=20.0,
            )

        self.assertIn("超过该题满分", str(ctx.exception))

    def test_missing_and_negative_scores_are_rejected(self):
        for text in (
            "student_uid,item_no,score\n高一(A)班-01,1,\n",
            "student_uid,item_no,score\n高一(A)班-01,1,-3\n",
        ):
            with self.subTest(csv=text):
                csv_path = self.write_csv(text, "bad-score.csv")
                with self.assertRaises(config_loader.ConfigError):
                    exam_module.import_item_scores_from_csv(
                        self.config, csv_path=csv_path, exam_key="e-import"
                    )

    def test_custom_columns(self):
        csv_path = self.write_csv(
            "学号,题号,得分\n高一(A)班-01,1,14\n", "custom.csv"
        )

        result = exam_module.import_item_scores_from_csv(
            self.config,
            csv_path=csv_path,
            exam_key="e-import",
            default_item_score=20.0,
            columns_spec="学号=student_uid,题号=item_no,得分=score",
        )

        self.assertEqual(result["item_scores_written"], 1)


class AnalysisTests(ExamTestCase):
    def test_hand_verified_numbers(self):
        self.seed_small_exam()

        analysis = self.analysis("e-small")

        self.assertEqual(analysis.student_count, 4)
        self.assertEqual(analysis.average, 70.0)
        self.assertEqual((analysis.highest, analysis.lowest), (100.0, 40.0))
        self.assertEqual(analysis.pass_rate, 0.75)

        first, second = analysis.items
        # 小题 1：平均 30/50 → 得分率 0.6（中）；高分组 s1 = 1.0，低分组 s4 = 0.0 → D = 1.0
        self.assertEqual((first.average, first.score_rate, first.difficulty_band), (30.0, 0.6, "中"))
        self.assertEqual((first.high_rate, first.low_rate, first.discrimination), (1.0, 0.0, 1.0))
        self.assertEqual(first.discrimination_band, "好")
        # 小题 2：平均 40/50 → 得分率 0.8（易）；高分组 1.0，低分组 0.8 → D = 0.2
        self.assertEqual((second.average, second.score_rate, second.difficulty_band), (40.0, 0.8, "易"))
        self.assertEqual((second.high_rate, second.low_rate), (1.0, 0.8))
        self.assertAlmostEqual(second.discrimination, 0.2, places=6)
        self.assertEqual(second.discrimination_band, "可接受")

    def test_zero_score_item_is_handled(self):
        self.seed_small_exam()
        self.execute("UPDATE item_scores SET score = 0 WHERE item_id = 1")

        item = self.analysis("e-small").items[0]

        self.assertEqual(item.average, 0.0)
        self.assertEqual(item.score_rate, 0.0)
        self.assertEqual(item.difficulty_band, "难")
        self.assertEqual((item.high_rate, item.low_rate), (0.0, 0.0))

    def test_missing_item_scores_are_reported(self):
        self.seed_small_exam()
        self.execute("DELETE FROM item_scores WHERE item_id = 2 AND student_id = 4")

        analysis = self.analysis("e-small")

        self.assertTrue(any("小题得分" in text for text in analysis.problems))

    def test_exam_linked_error_records_show_up(self):
        self.seed_small_exam()
        self.execute(
            """
            INSERT INTO error_records (student_id, exam_id, tag_id, recorded_at)
            SELECT 1, 1, id, '2026-10-15' FROM error_tags WHERE code = 'calculation'
            """
        )

        analysis = self.analysis("e-small")

        self.assertEqual(analysis.error_tag_counts, (("calculation", "计算失误", 1),))

    def test_analysis_report_has_no_absolute_path(self):
        self.seed_small_exam()
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            path = exam_module.write_exam_analysis(self.config, conn, exam_key="e-small")
        finally:
            conn.close()

        text = path.read_text(encoding="utf-8")
        self.assertEqual(path.name, "exam_analysis_e-small.md")
        self.assertIn("高低分组法", text)
        self.assertNotIn(str(self.base), text)
        self.assertNotIn("/Users/", text)


class HandoutTests(ExamTestCase):
    def test_handout_uses_self_made_marker_only(self):
        self.seed_small_exam()
        conn = sqlite3.connect(self.config.paths.database)
        conn.row_factory = sqlite3.Row
        try:
            path = exam_module.write_handout(self.config, conn, exam_key="e-small")
        finally:
            conn.close()

        text = path.read_text(encoding="utf-8")
        self.assertIn("不含试卷原题", text)
        # 两道小题各一处 + 开头说明里的一处
        self.assertEqual(text.count(exam_module.SAMPLE_QUESTION_MARKER), 3)
        self.assertIn("讲评顺序", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn(str(self.base), text)

    def test_cli_generates_both_reports(self):
        self.seed_small_exam()
        out = io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            analysis_code = hub.main(
                [
                    "--config",
                    str(self.config_path),
                    "exam-analysis",
                    "--exam-key",
                    "e-small",
                ]
            )
            handout_code = hub.main(
                [
                    "--config",
                    str(self.config_path),
                    "make-handout",
                    "--exam-key",
                    "e-small",
                ]
            )

        self.assertEqual((analysis_code, handout_code), (0, 0))
        self.assertTrue(
            (self.config.paths.output_dir / "exam_analysis_e-small.md").is_file()
        )
        self.assertTrue((self.config.paths.output_dir / "handout_e-small.md").is_file())
        self.assertIn("【自制示例题】", out.getvalue())


class QuestionHandoutTests(ExamTestCase):
    """P3.4：题库组卷讲义（按 key / 按推荐），默认不含答案。"""

    def add_questions(self) -> None:
        conn = init_db.connect(self.config.paths.database)
        try:
            with conn:
                questions_module.insert_question(
                    conn,
                    questions_module.Question(
                        question_key="q-self-1",
                        qtype="choice",
                        stem="自制示例题干一",
                        answer="A",
                        options=("A. 甲", "B. 乙"),
                        analysis="示例解析一",
                        difficulty=2,
                        source_label="自制示例",
                        tags=("欧姆定律",),
                    ),
                )
                questions_module.insert_question(
                    conn,
                    questions_module.Question(
                        question_key="q-self-2",
                        qtype="calculation",
                        stem="自制示例题干二",
                        answer="42",
                        difficulty=4,
                        source_label="自制示例",
                        tags=("牛顿第二定律",),
                    ),
                )
        finally:
            conn.close()

    def handout_files(self) -> list[Path]:
        out = self.config.paths.output_dir
        return sorted(out.glob("handout_questions_*.md")) if out.is_dir() else []

    def run_hub(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = hub.main(["--config", str(self.config_path), *args])
        return code, out.getvalue(), err.getvalue()

    def test_keys_mode_hides_answers_by_default(self):
        self.add_questions()

        code, out, err = self.run_hub(
            "make-handout", "--question-keys", "q-self-1,q-self-2"
        )

        self.assertEqual(code, 0, msg=out + err)
        files = self.handout_files()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "handout_questions_q-self-1_2道.md")
        text = files[0].read_text(encoding="utf-8")
        self.assertIn("# 题目讲义", text)
        self.assertIn("自制示例题干一", text)
        self.assertIn("- A. 甲", text, msg="选项属于题面，应当在讲义里")
        self.assertIn("来源：自制示例", text, msg="自制题应带来源标记")
        self.assertNotIn("## 答案与解析", text)
        self.assertNotIn("示例解析一", text, msg="默认不该出现解析")
        self.assertNotIn("/Users/", text)
        self.assertNotIn(str(self.base), text)

    def test_with_answer_appends_the_answer_section(self):
        self.add_questions()

        code, out, err = self.run_hub(
            "make-handout", "--question-keys", "q-self-1", "--with-answer"
        )

        self.assertEqual(code, 0, msg=out + err)
        text = self.handout_files()[0].read_text(encoding="utf-8")
        self.assertIn("## 答案与解析", text)
        self.assertIn("示例解析一", text)

    def test_unknown_key_writes_nothing(self):
        self.add_questions()

        code, _out, err = self.run_hub(
            "make-handout", "--question-keys", "q-self-1,没有这道题"
        )

        self.assertEqual(code, 2)
        self.assertIn("没有这道题", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.handout_files(), [], msg="失败时不该留下半成品")

    def test_modes_are_mutually_exclusive(self):
        self.add_questions()

        code, _out, err = self.run_hub(
            "make-handout", "--exam-key", "e-small", "--question-keys", "q-self-1"
        )
        self.assertEqual(code, 2)
        self.assertIn("三选一", err)

        code, _out, err = self.run_hub(
            "make-handout", "--exam-key", "e-small", "--with-answer"
        )
        self.assertEqual(code, 2)
        self.assertIn("--with-answer", err)

        code, _out, err = self.run_hub(
            "make-handout", "--question-keys", "q-self-1", "--limit", "2"
        )
        self.assertEqual(code, 2)
        self.assertIn("--limit", err)

    def test_recommend_mode_writes_a_handout(self):
        self.add_questions()
        student_uid = self.query(
            "SELECT student_uid FROM students ORDER BY id LIMIT 1"
        )[0]["student_uid"]

        code, out, err = self.run_hub(
            "make-handout", "--recommend-for", str(student_uid), "--limit", "2"
        )

        self.assertEqual(code, 0, msg=out + err)
        files = self.handout_files()
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertIn("# 题目讲义", text)
        self.assertIn("推荐对象", text)
        self.assertNotIn("## 答案与解析", text)


class DemoItemTests(ExamTestCase):
    clear_exams = False

    def test_demo_item_scores_are_imported_and_consistent(self):
        dataset = seed_demo_data.build_dataset(self.config)

        items = self.query("SELECT COUNT(*) AS n FROM exam_items")[0]["n"]
        scores = self.query("SELECT COUNT(*) AS n FROM item_scores")[0]["n"]
        expected_items = sum(len(exam["items"]) for exam in dataset["exams"])
        expected_scores = sum(
            len(item["scores"]) for exam in dataset["exams"] for item in exam["items"]
        )
        self.assertEqual((items, scores), (expected_items, expected_scores))
        self.assertEqual(dataset["version"], seed_demo_data.DATASET_VERSION)

        # 演示数据自洽：每名学生的小题得分之和等于总分
        mismatch = self.query(
            """
            SELECT es.student_id, es.score AS total, SUM(isc.score) AS item_total
            FROM exam_scores es
            JOIN exam_items ei ON ei.exam_id = es.exam_id
            JOIN item_scores isc ON isc.item_id = ei.id AND isc.student_id = es.student_id
            GROUP BY es.exam_id, es.student_id
            HAVING es.score != SUM(isc.score)
            """
        )
        self.assertEqual(mismatch, [])

    def test_demo_analysis_runs(self):
        analysis = self.analysis("demo-exam-1")

        self.assertEqual(len(analysis.items), 5)
        self.assertTrue(all(item.score_rate is not None for item in analysis.items))


if __name__ == "__main__":
    unittest.main()
