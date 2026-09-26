"""P3.0 题库：表约束与数据层原语的测试（题目内容一律用虚构示例）。"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Sequence
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import init_db  # noqa: E402
import questions  # noqa: E402

# 与 config.example.toml 的 [question_bank] tags 同源，示例题只用这些标签
EXAMPLE_TAGS = (
    "运动学图像",
    "匀变速直线运动",
    "牛顿第二定律",
    "受力分析",
    "机械能守恒",
    "欧姆定律",
    "串并联电路",
    "实验数据处理",
)


def make_config(base: Path, *, tags: Sequence[str] | None = EXAMPLE_TAGS) -> config_loader.AppConfig:
    raw: dict[str, Any] = {
        "semester": {"starts_on": "2026-09-01", "ends_on": "2027-01-22"},
        "paths": {"database": "data/q.db", "output_dir": "out"},
        "classes": {"names": ["高一(A)班"]},
        "demo": {"students_per_class": 3},
    }
    if tags is not None:
        raw["question_bank"] = {"tags": list(tags)}
    return config_loader.parse_config(raw, base_dir=base)


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


class QuestionImportTests(unittest.TestCase):
    """P3.1 导入器：JSON / CSV / 演示示例题，dry-run 与执行同条件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config = make_config(self.base)
        config_loader.ensure_directories(self.config)
        self.conn = init_db.connect(self.config.paths.database)
        self.addCleanup(self.conn.close)
        init_db.apply_schema(self.conn)

    def sample_item(self, **overrides: Any) -> dict[str, Any]:
        item: dict[str, Any] = {
            "question_key": "t-1",
            "qtype": "fill",
            "stem": "示例题干",
            "answer": "42",
            "difficulty": 2,
            "source_label": "自制示例",
            "tags": ["欧姆定律"],
        }
        item.update(overrides)
        return item

    def write_json(self, items: list[dict[str, Any]], name: str = "questions.json") -> Path:
        path = self.base / name
        path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        return path

    def tag_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM question_tags").fetchone()["n"])

    def test_json_import_writes_questions_and_tags(self):
        path = self.write_json(
            [
                self.sample_item(question_key="t-1", tags=["欧姆定律", "串并联电路"]),
                self.sample_item(question_key="t-2", qtype="choice", options=["甲", "乙"], answer="甲"),
            ]
        )

        result = questions.import_questions(self.config, json_path=path)

        self.assertFalse(result["dry_run"])
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["total"], 2)
        fetched = questions.fetch_question(self.conn, "t-2")
        assert fetched is not None
        self.assertEqual(fetched.options, ("甲", "乙"))
        self.assertEqual(fetched.tags, ("欧姆定律",))
        self.assertEqual(self.tag_count(), 3)

    def test_csv_import_supports_column_mapping(self):
        path = self.base / "questions.csv"
        path.write_text(
            "题号,题型,题干,答案,选项,难度,标签\n"
            "t-1,fill,示例题干,42,,2,欧姆定律\n"
            "t-2,choice,另一道题,甲,甲|乙,3,欧姆定律|串并联电路\n",
            encoding="utf-8",
        )

        result = questions.import_questions(
            self.config,
            csv_path=path,
            columns_spec="题号=question_key,题型=qtype,题干=stem,答案=answer,选项=options,难度=difficulty,标签=tags",
        )

        self.assertEqual(result["imported"], 2)
        fetched = questions.fetch_question(self.conn, "t-2")
        assert fetched is not None
        self.assertEqual(fetched.options, ("甲", "乙"))
        self.assertEqual(fetched.tags, ("串并联电路", "欧姆定律"))
        self.assertEqual(fetched.difficulty, 3)

    def test_dry_run_reports_without_writing(self):
        path = self.write_json([self.sample_item()])

        result = questions.import_questions(self.config, json_path=path, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["plan"].row_count, 1)
        self.assertEqual(questions.count_questions(self.conn), 0)

    def test_duplicate_key_inside_the_file_aborts_the_batch(self):
        path = self.write_json([self.sample_item(), self.sample_item(stem="另一道题")])

        with self.assertRaises(config_loader.ConfigError) as ctx:
            questions.import_questions(self.config, json_path=path)

        self.assertIn("重复的 question_key", str(ctx.exception))
        self.assertEqual(questions.count_questions(self.conn), 0)
        self.assertEqual(self.tag_count(), 0)

    def test_key_already_in_database_is_caught_by_dry_run_too(self):
        path = self.write_json([self.sample_item()])
        questions.import_questions(self.config, json_path=path)

        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                with self.assertRaises(config_loader.ConfigError) as ctx:
                    questions.import_questions(self.config, json_path=path, dry_run=dry_run)
                self.assertIn("已经有", str(ctx.exception))

        self.assertEqual(questions.count_questions(self.conn), 1)
        self.assertEqual(self.tag_count(), 1)

    def test_tag_outside_the_whitelist_is_rejected(self):
        path = self.write_json([self.sample_item(tags=["别的知识点"])])

        with self.assertRaises(config_loader.ConfigError) as ctx:
            questions.import_questions(self.config, json_path=path)

        message = str(ctx.exception)
        self.assertIn("别的知识点", message)
        self.assertIn("白名单", message)
        self.assertEqual(questions.count_questions(self.conn), 0)

    def test_empty_whitelist_asks_for_configuration(self):
        config = make_config(self.base, tags=None)

        with self.assertRaises(config_loader.ConfigError) as ctx:
            questions.import_questions(config, json_path=self.write_json([self.sample_item()]))

        self.assertIn("[question_bank]", str(ctx.exception))

    def test_file_problems_are_reported(self):
        missing = self.base / "nope.json"
        with self.assertRaises(config_loader.ConfigError) as missing_ctx:
            questions.import_questions(self.config, json_path=missing)
        self.assertIn("找不到题目文件", str(missing_ctx.exception))

        empty = self.write_json([], name="empty.json")
        with self.assertRaises(config_loader.ConfigError) as empty_ctx:
            questions.import_questions(self.config, json_path=empty)
        self.assertIn("没有任何题目", str(empty_ctx.exception))

        broken = self.base / "broken.json"
        broken.write_text("{不是数组}", encoding="utf-8")
        with self.assertRaises(config_loader.ConfigError) as broken_ctx:
            questions.import_questions(self.config, json_path=broken)
        self.assertIn("不是合法的 JSON", str(broken_ctx.exception))

        wrong_shape = self.write_json({"question_key": "t-1"}, name="object.json")
        with self.assertRaises(config_loader.ConfigError) as shape_ctx:
            questions.import_questions(self.config, json_path=wrong_shape)
        self.assertIn("JSON 数组", str(shape_ctx.exception))

    def test_zero_or_multiple_sources_are_rejected(self):
        path = self.write_json([self.sample_item()])

        with self.assertRaises(config_loader.ConfigError):
            questions.import_questions(self.config)

        with self.assertRaises(config_loader.ConfigError):
            questions.import_questions(self.config, json_path=path, csv_path=path)

    def test_demo_source_imports_the_example_set(self):
        result = questions.import_questions(self.config, demo=True)

        example = questions.load_example_questions()
        self.assertEqual(result["imported"], len(example))
        self.assertEqual(result["total"], len(example))
        for question in example:
            fetched = questions.fetch_question(self.conn, question.question_key)
            with self.subTest(question=question.question_key):
                self.assertIsNotNone(fetched)
                assert fetched is not None
                self.assertTrue(set(fetched.tags) <= set(EXAMPLE_TAGS))

    def test_failure_in_the_middle_rolls_the_whole_batch_back(self):
        path = self.write_json(
            [self.sample_item(question_key="t-1"), self.sample_item(question_key="t-2")]
        )
        real_insert = questions.insert_question
        calls = {"n": 0}

        def flaky_insert(conn: sqlite3.Connection, question: questions.Question) -> int:
            calls["n"] += 1
            if calls["n"] == 2:
                raise config_loader.ConfigError("模拟第二道题写入失败")
            return real_insert(conn, question)

        with mock.patch.object(questions, "insert_question", flaky_insert):
            with self.assertRaises(config_loader.ConfigError):
                questions.import_questions(self.config, json_path=path)

        self.assertEqual(questions.count_questions(self.conn), 0)
        self.assertEqual(self.tag_count(), 0)


class ExampleQuestionSetTests(unittest.TestCase):
    """仓库自带的示例题集必须自制、够用、且与示例配置的白名单一致。"""

    def example_config(self) -> config_loader.AppConfig:
        return config_loader.load_config(ROOT / config_loader.EXAMPLE_CONFIG_PATH, env={})

    def test_example_file_is_a_plain_json_array(self):
        raw = json.loads(questions.EXAMPLE_QUESTIONS_PATH.read_text(encoding="utf-8"))

        self.assertIsInstance(raw, list)
        self.assertTrue(1 <= len(raw) <= questions.MAX_EXAMPLE_QUESTIONS)

    def test_example_questions_use_documented_tags(self):
        allowed = set(self.example_config().question_bank.tags)

        self.assertTrue(allowed, msg="config.example.toml 应给出标签白名单示例")
        for question in questions.load_example_questions():
            with self.subTest(question=question.question_key):
                self.assertTrue(set(question.tags) <= allowed, msg=f"{question.tags} 不在白名单里")
                self.assertTrue(1 <= len(question.tags) <= 3)

    def test_example_questions_cover_types_and_difficulties(self):
        example = questions.load_example_questions()

        self.assertEqual(
            {question.qtype for question in example},
            {"choice", "fill", "calculation", "experiment"},
        )
        self.assertEqual(
            {question.difficulty for question in example}, {1, 2, 3, 4, 5}
        )

    def test_examples_are_labelled_self_made(self):
        for question in questions.load_example_questions():
            with self.subTest(question=question.question_key):
                self.assertEqual(question.source_label, "自制示例")


if __name__ == "__main__":
    unittest.main()
