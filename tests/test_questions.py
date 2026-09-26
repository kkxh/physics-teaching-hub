"""P3.0 题库：表约束与数据层原语的测试（题目内容一律用虚构示例）。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import init_db  # noqa: E402
import questions  # noqa: E402


class QuestionSchemaTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = init_db.connect(Path(self._tmp.name) / "schema.db")
        self.addCleanup(self.conn.close)
        init_db.apply_schema(self.conn)

    def insert_raw(self, **overrides: object) -> None:
        values: dict[str, object] = {
            "question_key": "q-1",
            "qtype": "fill",
            "stem": "题干示例",
            "options_json": None,
            "answer": "答案示例",
            "analysis": None,
            "difficulty": None,
            "source_label": None,
        }
        values.update(overrides)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        with self.conn:
            self.conn.execute(
                f"INSERT INTO questions ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )

    def test_tables_and_indexes_exist(self):
        names = {
            str(row["name"])
            for row in self.conn.execute("SELECT name FROM sqlite_master")
        }

        for expected in (
            "questions",
            "question_tags",
            "idx_questions_qtype",
            "idx_question_tags_tag",
        ):
            with self.subTest(name=expected):
                self.assertIn(expected, names)

    def test_invalid_qtype_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_raw(qtype="essay")

    def test_blank_stem_and_answer_are_rejected(self):
        for column in ("stem", "answer"):
            with self.subTest(column=column):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_raw(question_key=f"q-{column}", **{column: "   "})

    def test_choice_needs_options(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_raw(qtype="choice")

        self.insert_raw(qtype="choice", options_json='["甲", "乙"]')

    def test_difficulty_allows_null_and_rejects_out_of_range(self):
        self.insert_raw(question_key="q-null")

        for bad in (0, 6, -1):
            with self.subTest(difficulty=bad):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_raw(question_key=f"q-{bad}", difficulty=bad)

        for good in (1, 3, 5):
            with self.subTest(difficulty=good):
                self.insert_raw(question_key=f"q-good-{good}", difficulty=good)

    def test_deleting_a_question_cascades_to_tags(self):
        self.insert_raw()
        with self.conn:
            self.conn.execute("INSERT INTO question_tags (question_id, tag) VALUES (1, '标签甲')")
            self.conn.execute("DELETE FROM questions WHERE id = 1")

        remaining = self.conn.execute("SELECT COUNT(*) AS n FROM question_tags").fetchone()["n"]
        self.assertEqual(remaining, 0)


class QuestionLayerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = init_db.connect(Path(self._tmp.name) / "layer.db")
        self.addCleanup(self.conn.close)
        init_db.apply_schema(self.conn)

    def sample(self, **overrides: object) -> questions.Question:
        values: dict[str, object] = {
            "question_key": "demo-q1",
            "qtype": "choice",
            "stem": "  示例题干  ",
            "answer": " A ",
            "options": ("甲", "乙"),
            "analysis": " 示例解析 ",
            "difficulty": 3,
            "source_label": "自制示例",
            "tags": ("欧姆定律", "串并联电路"),
        }
        values.update(overrides)
        return questions.Question(**values)  # type: ignore[arg-type]

    def tag_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM question_tags").fetchone()["n"])

    def test_round_trip_normalizes_fields(self):
        with self.conn:
            question_id = questions.insert_question(self.conn, self.sample())

        self.assertEqual(question_id, 1)
        fetched = questions.fetch_question(self.conn, "demo-q1")
        assert fetched is not None
        self.assertEqual(fetched.stem, "示例题干")
        self.assertEqual(fetched.answer, "A")
        self.assertEqual(fetched.options, ("甲", "乙"))
        self.assertEqual(fetched.analysis, "示例解析")
        self.assertEqual(fetched.difficulty, 3)
        self.assertEqual(fetched.tags, ("串并联电路", "欧姆定律"))
        self.assertEqual(questions.count_questions(self.conn), 1)

    def test_unmarked_difficulty_stays_null(self):
        with self.conn:
            questions.insert_question(self.conn, self.sample(difficulty=None))

        fetched = questions.fetch_question(self.conn, "demo-q1")
        assert fetched is not None
        self.assertIsNone(fetched.difficulty)

    def test_invalid_questions_are_rejected_before_write(self):
        cases = (
            ("缺 key", self.sample(question_key="   ")),
            ("题型错", self.sample(qtype="essay")),
            ("空题干", self.sample(stem="   ")),
            ("空答案", self.sample(answer="  ")),
            ("选择题没选项", self.sample(options=())),
            ("难度越界", self.sample(difficulty=9)),
            ("空标签", self.sample(tags=("",))),
        )
        for label, question in cases:
            with self.subTest(case=label):
                with self.assertRaises(config_loader.ConfigError):
                    questions.insert_question(self.conn, question)

        self.assertEqual(questions.count_questions(self.conn), 0)
        self.assertEqual(self.tag_count(), 0)

    def test_duplicate_key_is_rejected_and_nothing_written(self):
        with self.conn:
            questions.insert_question(self.conn, self.sample())

        with self.assertRaises(config_loader.ConfigError) as ctx:
            with self.conn:
                questions.insert_question(self.conn, self.sample(stem="另一道题"))

        self.assertIn("question_key", str(ctx.exception))
        self.assertEqual(questions.count_questions(self.conn), 1)
        self.assertEqual(self.tag_count(), 2)

    def test_failed_batch_rolls_back_without_residue(self):
        """导入器会把整批放在一个事务里：批内失败时题目与标签都不能留。"""
        with self.assertRaises(RuntimeError):
            with self.conn:
                questions.insert_question(self.conn, self.sample())
                raise RuntimeError("模拟批导入中途失败")

        self.assertEqual(questions.count_questions(self.conn), 0)
        self.assertEqual(self.tag_count(), 0)

    def test_tag_whitelist_check_explains_how_to_configure(self):
        question = questions.normalize_question(self.sample())

        with self.assertRaises(config_loader.ConfigError) as empty_ctx:
            questions.check_tags_allowed(question, ())
        self.assertIn("[question_bank]", str(empty_ctx.exception))

        with self.assertRaises(config_loader.ConfigError) as unknown_ctx:
            questions.check_tags_allowed(question, ("别的知识点",))
        self.assertIn("白名单", str(unknown_ctx.exception))

        questions.check_tags_allowed(question, ("欧姆定律", "串并联电路"))

    def test_fetch_questions_reports_missing_keys(self):
        with self.conn:
            questions.insert_question(self.conn, self.sample())

        self.assertEqual(len(questions.fetch_questions(self.conn, ["demo-q1"])), 1)

        with self.assertRaises(config_loader.ConfigError) as ctx:
            questions.fetch_questions(self.conn, ["demo-q1", "没有这道题"])

        self.assertIn("没有这道题", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
