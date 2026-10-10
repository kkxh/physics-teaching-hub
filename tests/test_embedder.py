"""Phase 5 实验分支 pilot：向量嵌入后端的测试（题目内容一律用虚构示例）。"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Sequence
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import embedder  # noqa: E402
import init_db  # noqa: E402
import questions  # noqa: E402


class FakeEmbedder:
    """确定性假后端：按文本查表返回向量，不碰网络。"""

    def __init__(self, table: dict[str, list[float]], name: str = "fake-test"):
        self._table = table
        self._name = name

    @property
    def model_name(self) -> str:
        return self._name

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(self._table[str(t)]) for t in texts]


def make_question(key: str, stem: str) -> questions.Question:
    return questions.Question(
        question_key=key,
        qtype="fill",
        stem=stem,
        answer="略",
    )


class CosineTests(unittest.TestCase):
    def test_identical_is_one(self):
        self.assertAlmostEqual(embedder.cosine_similarity([1.0, 2.0], [1.0, 2.0]), 1.0)

    def test_orthogonal_is_zero(self):
        self.assertAlmostEqual(embedder.cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_opposite_is_minus_one(self):
        self.assertAlmostEqual(embedder.cosine_similarity([1.0, 0.0], [-1.0, 0.0]), -1.0)

    def test_zero_vector_is_zero(self):
        self.assertEqual(embedder.cosine_similarity([0.0, 0.0], [1.0, 2.0]), 0.0)

    def test_dim_mismatch_raises(self):
        with self.assertRaises(ValueError):
            embedder.cosine_similarity([1.0], [1.0, 2.0])


class PackTests(unittest.TestCase):
    def test_roundtrip(self):
        vector = [0.1, -2.5, 3.75]
        blob = embedder.pack_vector(vector)
        back = embedder.unpack_vector(blob)
        self.assertEqual(len(back), 3)
        for a, b in zip(vector, back):
            self.assertAlmostEqual(a, b, places=5)

    def test_bad_length_raises(self):
        with self.assertRaises(ValueError):
            embedder.unpack_vector(b"\x00\x01\x02")


class RankTests(unittest.TestCase):
    def test_orders_by_similarity_desc(self):
        ranked = embedder.rank_by_similarity(
            [1.0, 0.0], [(1, [1.0, 0.0]), (2, [0.0, 1.0]), (3, [0.7, 0.7])], top_k=3
        )
        self.assertEqual([cid for cid, _ in ranked], [1, 3, 2])

    def test_top_k_truncates(self):
        ranked = embedder.rank_by_similarity(
            [1.0, 0.0], [(1, [1.0, 0.0]), (2, [0.9, 0.1])], top_k=1
        )
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0][0], 1)

    def test_empty_or_nonpositive_returns_empty(self):
        self.assertEqual(embedder.rank_by_similarity([1.0], [], top_k=5), [])
        self.assertEqual(
            embedder.rank_by_similarity([1.0], [(1, [1.0])], top_k=0), []
        )


class OllamaEmbedderTests(unittest.TestCase):
    def test_unreachable_host_raises_embedder_error(self):
        bad = embedder.OllamaEmbedder(host="http://127.0.0.1:1", timeout=2.0)
        with self.assertRaises(embedder.EmbedderError):
            bad.embed(["题干"])

    def test_empty_input_returns_empty_without_network(self):
        bad = embedder.OllamaEmbedder(host="http://127.0.0.1:1", timeout=2.0)
        self.assertEqual(bad.embed([]), [])

    def test_prefix_is_prepended_to_prompt(self):
        import io
        import json as json_module

        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json_module.dumps({"embedding": [0.1, 0.2]}).encode()

        def fake_urlopen(request, timeout=None):
            captured["body"] = json_module.loads(request.data.decode())
            return FakeResponse()

        with mock.patch.object(
            embedder.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            oe = embedder.OllamaEmbedder(prefix=embedder.OllamaEmbedder.SIMILARITY_PREFIX)
            vectors = oe.embed(["题干"])

        self.assertEqual(vectors, [[0.1, 0.2]])
        self.assertEqual(
            captured["body"]["prompt"],
            embedder.OllamaEmbedder.SIMILARITY_PREFIX + "题干",
        )
        self.assertEqual(captured["body"]["model"], "embeddinggemma")


class SimilarQuestionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = init_db.connect(Path(self._tmp.name) / "q.db")
        self.addCleanup(self.conn.close)
        init_db.apply_schema(self.conn)
        with self.conn:
            for key, stem in (
                ("q-n1", "牛顿第二定律求加速度"),
                ("q-o1", "欧姆定律求电流"),
                ("q-n2", "牛顿第二定律求合力"),
            ):
                questions.insert_question(self.conn, make_question(key, stem))
        self.fake = FakeEmbedder(
            {
                "牛顿第二定律求加速度": [1.0, 0.0],
                "欧姆定律求电流": [0.0, 1.0],
                "牛顿第二定律求合力": [0.9, 0.2],
                "牛顿定律": [1.0, 0.0],
            }
        )
        questions.embed_question_vectors(
            self.conn,
            [
                make_question("q-n1", "牛顿第二定律求加速度"),
                make_question("q-o1", "欧姆定律求电流"),
                make_question("q-n2", "牛顿第二定律求合力"),
            ],
            self.fake,
        )

    def test_returns_ranked_questions(self):
        results = questions.similar_questions(
            self.conn, stem="牛顿定律", top_k=2, embedder=self.fake
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0].question_key, "q-n1")
        self.assertEqual(results[1][0].question_key, "q-n2")
        self.assertGreater(results[0][1], results[1][1])

    def test_empty_vector_store_returns_empty(self):
        conn2 = init_db.connect(Path(self._tmp.name) / "empty.db")
        self.addCleanup(conn2.close)
        init_db.apply_schema(conn2)
        self.assertEqual(
            questions.similar_questions(
                conn2, stem="牛顿定律", top_k=5, embedder=self.fake
            ),
            [],
        )

    def test_reembed_overwrites_without_duplicates(self):
        questions.embed_question_vectors(
            self.conn, [make_question("q-n1", "牛顿第二定律求加速度")], self.fake
        )
        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM question_embeddings WHERE question_id = "
            "(SELECT id FROM questions WHERE question_key = 'q-n1')"
        ).fetchone()["n"]
        self.assertEqual(count, 1)

    def test_missing_question_raises(self):
        with self.assertRaises(config_loader.ConfigError):
            questions.embed_question_vectors(
                self.conn, [make_question("q-nope", "不在库里")], self.fake
            )


class SuggestTagsTests(unittest.TestCase):
    def test_ranks_tags_by_similarity(self):
        fake = FakeEmbedder(
            {
                "质量 2kg 受 6N 力求加速度": [1.0, 0.0],
                "牛顿第二定律": [0.9, 0.1],
                "欧姆定律": [0.0, 1.0],
                "实验数据处理": [0.1, 0.9],
            }
        )
        ranked = questions.suggest_tags(
            stem="质量 2kg 受 6N 力求加速度",
            tags=["牛顿第二定律", "欧姆定律", "实验数据处理"],
            top_k=2,
            embedder=fake,
        )
        self.assertEqual([t for t, _ in ranked], ["牛顿第二定律", "实验数据处理"])

    def test_empty_tags_returns_empty(self):
        fake = FakeEmbedder({"题干": [1.0]})
        self.assertEqual(
            questions.suggest_tags(stem="题干", tags=[], top_k=3, embedder=fake), []
        )


class FindDuplicatesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = init_db.connect(Path(self._tmp.name) / "q.db")
        self.addCleanup(self.conn.close)
        init_db.apply_schema(self.conn)
        with self.conn:
            for key, stem in (
                ("q-a", "题干甲"),
                ("q-b", "题干乙"),
                ("q-c", "题干丙"),
            ):
                questions.insert_question(self.conn, make_question(key, stem))
        # q-a 与 q-b 几乎相同（疑似重复），q-c 差异大
        fake = FakeEmbedder(
            {"题干甲": [1.0, 0.0], "题干乙": [0.99, 0.01], "题干丙": [0.0, 1.0]}
        )
        questions.embed_question_vectors(
            self.conn,
            [make_question("q-a", "题干甲"), make_question("q-b", "题干乙"),
             make_question("q-c", "题干丙")],
            fake,
        )

    def test_finds_near_duplicate_pair(self):
        pairs = questions.find_duplicates(self.conn, threshold=0.95)
        self.assertEqual(len(pairs), 1)
        self.assertEqual({pairs[0][0], pairs[0][1]}, {"q-a", "q-b"})
        self.assertGreaterEqual(pairs[0][2], 0.95)

    def test_high_threshold_finds_nothing(self):
        self.assertEqual(questions.find_duplicates(self.conn, threshold=1.0), [])

    def test_bad_threshold_raises(self):
        with self.assertRaises(config_loader.ConfigError):
            questions.find_duplicates(self.conn, threshold=0.0)

    def test_check_import_duplicates_only_flags_new_vs_existing(self):
        pairs = questions.check_import_duplicates(
            self.conn, ["q-a"], threshold=0.95
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0], "q-a")
        self.assertEqual(pairs[0][1], "q-b")

    def test_format_similar_questions(self):
        q = make_question("q-a", "题干甲")
        text = questions.format_similar_questions([(q, 0.9123)])
        self.assertIn("0.912", text)
        self.assertIn("q-a", text)
        self.assertNotIn("略", text)  # 答案默认隐藏


def make_cli_config(base: Path) -> config_loader.AppConfig:
    raw = {
        "semester": {"starts_on": "2026-09-01", "ends_on": "2027-01-22"},
        "paths": {"database": "data/q.db", "output_dir": "out"},
        "classes": {"names": ["高一(A)班"]},
        "demo": {"students_per_class": 3},
        "question_bank": {"tags": ["牛顿第二定律", "欧姆定律"]},
    }
    (base / "data").mkdir(parents=True, exist_ok=True)
    return config_loader.parse_config(raw, base_dir=base)


class SimilarQuestionsCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config = make_cli_config(self.base)
        conn = init_db.connect(self.config.paths.database)
        try:
            init_db.apply_schema(conn)
            with conn:
                questions.insert_question(
                    conn, make_question("q-1", "牛顿第二定律求加速度")
                )
        finally:
            conn.close()

    def _args(self, **overrides):
        import argparse

        params = {"stem": "牛顿定律", "top": 5, "show_answer": False}
        params.update(overrides)
        return argparse.Namespace(**params)

    def test_no_vectors_gives_actionable_exit_2(self):
        import hub

        result = hub.run_similar_questions(self.config, self._args())
        self.assertEqual(result, 2)

    def test_ollama_down_is_actionable_without_traceback(self):
        import hub

        conn = init_db.connect(self.config.paths.database)
        try:
            fake = FakeEmbedder({"牛顿第二定律求加速度": [1.0, 0.0], "牛顿定律": [1.0, 0.0]})
            questions.embed_question_vectors(
                conn, [make_question("q-1", "牛顿第二定律求加速度")], fake
            )
        finally:
            conn.close()
        bad = embedder.OllamaEmbedder(host="http://127.0.0.1:1", timeout=2.0)
        with mock.patch.object(embedder, "OllamaEmbedder", return_value=bad):
            with self.assertRaises(config_loader.ConfigError) as ctx:
                hub.run_similar_questions(self.config, self._args())
        self.assertIn("Ollama", str(ctx.exception))

    def test_happy_path_prints_table(self):
        import hub
        import io
        from contextlib import redirect_stdout

        conn = init_db.connect(self.config.paths.database)
        try:
            fake = FakeEmbedder({"牛顿第二定律求加速度": [1.0, 0.0], "牛顿定律": [1.0, 0.0]})
            questions.embed_question_vectors(
                conn, [make_question("q-1", "牛顿第二定律求加速度")], fake
            )
        finally:
            conn.close()
        with mock.patch.object(embedder, "OllamaEmbedder", return_value=fake):
            buf = io.StringIO()
            with redirect_stdout(buf):
                result = hub.run_similar_questions(self.config, self._args())
        self.assertEqual(result, 0)
        self.assertIn("q-1", buf.getvalue())
        self.assertIn("相似度", buf.getvalue())


class ImportEmbedHookTests(unittest.TestCase):
    def test_ollama_down_warns_but_import_succeeds(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        raw = {
            "semester": {"starts_on": "2026-09-01", "ends_on": "2027-01-22"},
            "paths": {"database": "data/q.db", "output_dir": "out"},
            "classes": {"names": ["高一(A)班"]},
            "demo": {"students_per_class": 3},
            "question_bank": {"tags": ["牛顿第二定律"]},
        }
        config = config_loader.parse_config(raw, base_dir=base)
        (base / "data").mkdir(parents=True, exist_ok=True)
        conn = init_db.connect(config.paths.database)
        try:
            init_db.apply_schema(conn)
        finally:
            conn.close()
        payload = [
            {
                "question_key": "e-1",
                "qtype": "fill",
                "stem": "质量 2 kg 的物体受 6 N 恒力，加速度多大？",
                "answer": "3 m/s²",
                "tags": ["牛顿第二定律"],
            }
        ]
        json_path = base / "qs.json"
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        bad = embedder.OllamaEmbedder(host="http://127.0.0.1:1", timeout=2.0)
        with mock.patch.object(embedder, "OllamaEmbedder", return_value=bad):
            result = questions.import_questions(
                config, json_path=json_path, embed=True
            )
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["embedded"], 0)
        self.assertIsNotNone(result["embed_warning"])


if __name__ == "__main__":
    unittest.main()
