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


def make_config(
    base: Path,
    *,
    tags: Sequence[str] | None = EXAMPLE_TAGS,
    tag_map: dict[str, list[str]] | None = None,
) -> config_loader.AppConfig:
    raw: dict[str, Any] = {
        "semester": {"starts_on": "2026-09-01", "ends_on": "2027-01-22"},
        "paths": {"database": "data/q.db", "output_dir": "out"},
        "classes": {"names": ["高一(A)班"]},
        "demo": {"students_per_class": 3},
    }
    if tags is not None:
        section: dict[str, Any] = {"tags": list(tags)}
        if tag_map:
            section["tag_map"] = tag_map
        raw["question_bank"] = section
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


class QuestionSearchTests(unittest.TestCase):
    """P3.2 检索：过滤条件、关键词转义、答案默认隐藏。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config = make_config(self.base)
        config_loader.ensure_directories(self.config)
        self.conn = init_db.connect(self.config.paths.database)
        self.addCleanup(self.conn.close)
        init_db.apply_schema(self.conn)
        with self.conn:
            questions.insert_question(
                self.conn,
                questions.Question(
                    question_key="s-1",
                    qtype="choice",
                    stem="用欧姆定律求通过电阻的电流",
                    answer="B",
                    options=("A. 1 A", "B. 2 A"),
                    analysis="由 I = U / R 得。",
                    difficulty=2,
                    tags=("欧姆定律",),
                ),
            )
            questions.insert_question(
                self.conn,
                questions.Question(
                    question_key="s-2",
                    qtype="calculation",
                    stem="求串联电路的总电阻",
                    answer="20 Ω",
                    difficulty=4,
                    tags=("串并联电路",),
                ),
            )
            questions.insert_question(
                self.conn,
                questions.Question(
                    question_key="s-3",
                    qtype="fill",
                    stem="字面匹配 100% 与 a_b",
                    answer="占位答案",
                    tags=("欧姆定律",),
                ),
            )
            questions.insert_question(
                self.conn,
                questions.Question(
                    question_key="s-4",
                    qtype="fill",
                    stem="这道题没有标难度",
                    answer="占位答案",
                    tags=("欧姆定律",),
                ),
            )

    def keys(self, **filters: Any) -> list[str]:
        return [
            item.question_key
            for item in questions.search_questions(self.conn, **filters)
        ]

    def test_filters_by_tag_type_and_difficulty(self):
        self.assertEqual(self.keys(tag="欧姆定律"), ["s-1", "s-3", "s-4"])
        self.assertEqual(self.keys(qtype="calculation"), ["s-2"])
        self.assertEqual(self.keys(difficulty=2), ["s-1"])
        self.assertEqual(self.keys(tag="欧姆定律", qtype="fill"), ["s-3", "s-4"])

    def test_keyword_matches_key_and_stem_only(self):
        self.assertEqual(self.keys(keyword="串联"), ["s-2"])
        self.assertEqual(self.keys(keyword="s-3"), ["s-3"])
        # 关键词不搜答案：s-2 的答案是「20 Ω」，但题干里没有，所以搜不到
        self.assertEqual(self.keys(keyword="20 Ω"), [])

    def test_like_wildcards_are_matched_literally(self):
        self.assertEqual(self.keys(keyword="100%"), ["s-3"])
        self.assertEqual(self.keys(keyword="a_b"), ["s-3"])
        self.assertEqual(self.keys(keyword="%"), ["s-3"], msg="% 应按字面匹配，不能通配")
        self.assertEqual(self.keys(keyword="_"), ["s-3"], msg="_ 应按字面匹配，不能通配")

    def test_limit_keeps_a_stable_order(self):
        self.assertEqual(self.keys(limit=2), ["s-1", "s-2"])
        self.assertEqual(self.keys(limit=1), ["s-1"])

    def test_unmarked_difficulty_is_not_matched_by_a_difficulty_filter(self):
        for level in range(1, 6):
            with self.subTest(difficulty=level):
                self.assertNotIn("s-4", self.keys(difficulty=level))

        table = questions.format_questions(questions.search_questions(self.conn, keyword="没有标难度"))
        self.assertIn("—", table)

    def test_invalid_filters_are_rejected(self):
        cases = (
            {"qtype": "essay"},
            {"difficulty": 0},
            {"difficulty": 6},
            {"limit": 0},
            {"limit": questions.MAX_LIST_LIMIT + 1},
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(config_loader.ConfigError):
                    questions.search_questions(self.conn, **case)

    def test_public_dict_hides_answer_and_analysis(self):
        item = questions.search_questions(self.conn, keyword="s-1")[0]

        payload = questions.as_public_dict(item)

        self.assertNotIn("answer", payload)
        self.assertNotIn("analysis", payload)
        self.assertEqual(payload["question_key"], "s-1")
        self.assertEqual(payload["tags"], ["欧姆定律"])

    def test_format_hides_answers_unless_asked(self):
        items = questions.search_questions(self.conn, keyword="s-1")

        hidden = questions.format_questions(items)
        shown = questions.format_questions(items, show_answer=True)

        self.assertNotIn("| 答案 |", hidden)
        self.assertNotIn("由 I = U / R 得。", hidden)
        self.assertIn("| 答案 | 解析 |", shown)
        self.assertIn("由 I = U / R 得。", shown)
        self.assertEqual(hidden.count("\n"), 2, msg="一道题应只占表头两行 + 一行")

    def test_format_excerpt_is_single_line_and_truncated(self):
        item = questions.search_questions(self.conn, keyword="s-2")[0]

        table = questions.format_questions([item], stem_length=4)

        self.assertIn("求串联电…", table)


class RecommendationTests(unittest.TestCase):
    """P3.3 推荐：标签命中、难度档、退化路径与只读性。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config = make_config(
            self.base,
            tag_map={
                "graph_reading": ["运动学图像"],
                "calculation": ["欧姆定律"],
            },
        )
        config_loader.ensure_directories(self.config)
        self.conn = init_db.connect(self.config.paths.database)
        self.addCleanup(self.conn.close)
        init_db.apply_schema(self.conn)

        with self.conn:
            self.conn.execute("INSERT INTO classes (name) VALUES ('高一(A)班')")
            self.conn.execute(
                "INSERT INTO homework_assignments (assign_key, class_id, topic, assigned_date) "
                "VALUES ('hw-01', 1, '演示作业', '2026-09-30')"
            )
            for index, code in enumerate(("graph_reading", "calculation"), start=1):
                self.conn.execute(
                    "INSERT INTO error_tags (code, label) VALUES (?, ?)",
                    (code, f"标签{index}"),
                )

    def add_student(self, uid: str, name: str = "学生01") -> int:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO students (student_uid, name, class_id) VALUES (?, ?, 1)",
                (uid, name),
            )
        return int(cursor.lastrowid)

    def add_errors(self, student_id: int, code: str, times: int) -> None:
        tag_id = self.conn.execute(
            "SELECT id FROM error_tags WHERE code = ?", (code,)
        ).fetchone()["id"]
        with self.conn:
            for _ in range(times):
                self.conn.execute(
                    "INSERT INTO error_records (student_id, assignment_id, tag_id, recorded_at) "
                    "VALUES (?, 1, ?, '2026-10-01')",
                    (student_id, int(tag_id)),
                )

    def set_profile(self, student_id: int, **dimensions: float) -> None:
        with self.conn:
            for dimension, score in dimensions.items():
                self.conn.execute(
                    "INSERT INTO ability_scores (student_id, dimension, score) VALUES (?, ?, ?)",
                    (student_id, dimension, score),
                )

    def add_question(
        self,
        key: str,
        *,
        tags: tuple[str, ...],
        difficulty: int | None = 2,
    ) -> None:
        with self.conn:
            questions.insert_question(
                self.conn,
                questions.Question(
                    question_key=key,
                    qtype="fill",
                    stem=f"{key} 的题干",
                    answer=f"{key} 的答案",
                    difficulty=difficulty,
                    tags=tags,
                ),
            )

    def keys(self, result: questions.RecommendationResult) -> list[str]:
        return [item.question.question_key for item in result.items]

    def test_recommends_tag_matched_questions_first(self):
        student_id = self.add_student("s-01")
        self.add_errors(student_id, "graph_reading", 2)
        self.set_profile(student_id, overall=75.0, error_control=58.0)
        self.add_question("q-kinematics", tags=("运动学图像",), difficulty=2)
        self.add_question("q-ohm", tags=("欧姆定律",), difficulty=2)

        result = questions.recommend_questions(self.conn, self.config, student_uid="s-01")

        self.assertEqual(self.keys(result)[0], "q-kinematics")
        self.assertEqual(dict(result.tag_hits), {"运动学图像": 2})
        first = result.items[0]
        self.assertIn("标签1", "；".join(first.reasons))
        self.assertIn("运动学图像", "；".join(first.reasons))

    def test_difficulty_cap_comes_from_the_profile(self):
        student_id = self.add_student("s-01")
        self.add_errors(student_id, "graph_reading", 1)
        self.set_profile(student_id, overall=45.0, error_control=80.0)
        self.add_question("q-easy", tags=("运动学图像",), difficulty=1)
        self.add_question("q-hard", tags=("运动学图像",), difficulty=5)

        result = questions.recommend_questions(self.conn, self.config, student_uid="s-01")

        self.assertEqual(result.difficulty_cap, 2)
        self.assertEqual(self.keys(result), ["q-easy"], msg="难度 5 超出档位 +1，应被过滤")
        self.assertFalse([note for note in result.notes if "放宽" in note])

    def test_only_hard_questions_fall_back_with_a_note(self):
        student_id = self.add_student("s-01")
        self.add_errors(student_id, "graph_reading", 1)
        self.set_profile(student_id, overall=45.0)
        self.add_question("q-hard", tags=("运动学图像",), difficulty=5)

        result = questions.recommend_questions(self.conn, self.config, student_uid="s-01")

        self.assertEqual(self.keys(result), ["q-hard"])
        self.assertTrue([note for note in result.notes if "放宽难度限制" in note])

    def test_challenge_tier_is_allowed_but_ranked_below_the_band(self):
        """档位 + 1 的题算挑战题：可以入选，但排在档内题后面（R4 观察项定稿口径）。"""
        student_id = self.add_student("s-01")
        self.add_errors(student_id, "graph_reading", 1)
        self.set_profile(student_id, overall=85.0)
        self.add_question("q-in-band", tags=("运动学图像",), difficulty=3)
        self.add_question("q-challenge", tags=("运动学图像",), difficulty=5)

        result = questions.recommend_questions(self.conn, self.config, student_uid="s-01")

        self.assertEqual(result.difficulty_cap, 4)
        self.assertEqual(self.keys(result), ["q-in-band", "q-challenge"])
        challenge = result.items[1]
        self.assertIn("略高于当前档位", "；".join(challenge.reasons))
        in_band = result.items[0]
        self.assertIn("在能力档内", "；".join(in_band.reasons))

    def test_student_without_profile_uses_the_default_cap(self):
        student_id = self.add_student("s-01")
        self.add_errors(student_id, "graph_reading", 1)
        self.add_question("q-mid", tags=("运动学图像",), difficulty=3)

        result = questions.recommend_questions(self.conn, self.config, student_uid="s-01")

        self.assertEqual(result.difficulty_cap, 3)
        self.assertTrue([note for note in result.notes if "难度档按默认" in note])

    def test_missing_error_data_degrades_with_a_note(self):
        self.add_student("s-01")
        self.add_question("q-mid", tags=("运动学图像",), difficulty=2)
        self.add_question("q-other", tags=("欧姆定律",), difficulty=2)

        result = questions.recommend_questions(self.conn, self.config, student_uid="s-01")

        self.assertTrue(
            [note for note in result.notes if "还没有错因记录" in note],
            msg=f"应明示退化路径：{result.notes}",
        )
        self.assertEqual(self.keys(result), ["q-mid", "q-other"])
        self.assertEqual(dict(result.tag_hits), {})

    def test_unmapped_error_tags_are_skipped_with_a_note(self):
        student_id = self.add_student("s-01")
        self.add_errors(student_id, "calculation", 0)  # 先建一个无映射的场景
        with self.conn:
            self.conn.execute(
                "INSERT INTO error_tags (code, label) VALUES ('concept_confusion', '标签3')"
            )
        self.add_errors(student_id, "concept_confusion", 2)
        self.add_question("q-ohm", tags=("欧姆定律",), difficulty=2)

        result = questions.recommend_questions(self.conn, self.config, student_uid="s-01")

        self.assertTrue(
            [note for note in result.notes if "还没有映射到知识点" in note and "标签3" in note]
        )
        self.assertEqual(dict(result.tag_hits), {})
        self.assertEqual(self.keys(result), ["q-ohm"])

    def test_unknown_student_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            questions.recommend_questions(self.conn, self.config, student_uid="没有这个学号")

        self.assertIn("找不到学生", str(ctx.exception))

    def test_limit_is_respected_and_validated(self):
        student_id = self.add_student("s-01")
        self.add_errors(student_id, "graph_reading", 1)
        for index in range(1, 4):
            self.add_question(f"q-{index}", tags=("运动学图像",), difficulty=2)

        self.assertEqual(
            len(questions.recommend_questions(self.conn, self.config, student_uid="s-01", limit=2).items),
            2,
        )
        for bad in (0, questions.MAX_LIST_LIMIT + 1):
            with self.subTest(limit=bad):
                with self.assertRaises(config_loader.ConfigError):
                    questions.recommend_questions(
                        self.conn, self.config, student_uid="s-01", limit=bad
                    )

    def test_recommendation_is_read_only(self):
        student_id = self.add_student("s-01")
        self.add_errors(student_id, "graph_reading", 3)
        self.set_profile(student_id, overall=70.0, error_control=50.0)
        self.add_question("q-kinematics", tags=("运动学图像",), difficulty=2)

        def snapshot() -> tuple[tuple[str, int], ...]:
            return tuple(
                (table, int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]))
                for table in ("questions", "question_tags", "error_records", "ability_scores")
            )

        before = snapshot()
        questions.recommend_questions(self.conn, self.config, student_uid="s-01")
        after = snapshot()

        self.assertEqual(before, after, msg="推荐必须只读")

    def test_format_hides_answers_by_default(self):
        student_id = self.add_student("s-01")
        self.add_errors(student_id, "graph_reading", 1)
        self.add_question("q-kinematics", tags=("运动学图像",), difficulty=2)

        result = questions.recommend_questions(self.conn, self.config, student_uid="s-01")

        hidden = questions.format_recommendations(result)
        shown = questions.format_recommendations(result, show_answer=True)

        self.assertIn("推荐理由", hidden)
        self.assertNotIn("| 答案 |", hidden)
        self.assertIn("q-kinematics", hidden)
        self.assertIn("| 答案 | 解析 |", shown)


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
