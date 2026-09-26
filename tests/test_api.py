"""本地 API 测试：端点字段、错误码、CORS 与绑定地址。

注意：这些用例会绑定本机回环端口，需要在允许 socket 的环境里跑（CI 上没问题）。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import alerts as alerts_module  # noqa: E402
import api as api_module  # noqa: E402
import config_loader  # noqa: E402
import import_scores  # noqa: E402
import init_db  # noqa: E402
import questions as questions_module  # noqa: E402

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

[question_bank]
tags = ["运动学图像", "匀变速直线运动", "牛顿第二定律", "受力分析", "机械能守恒", "欧姆定律", "串并联电路", "实验数据处理"]

[question_bank.tag_map]
graph_reading = ["运动学图像", "实验数据处理"]
calculation = ["匀变速直线运动", "欧姆定律"]
"""


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config_path = self.base / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})
        init_db.init_database(self.config)
        import_scores.import_demo_scores(self.config)
        alerts_module.scan_alerts(self.config)

        try:
            self.server = api_module.create_server(self.config, port=0)
        except PermissionError as exc:  # 沙箱里可能不允许绑定端口
            self.skipTest(f"当前环境不允许绑定回环端口：{exc}")
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base_url = f"http://127.0.0.1:{self.port}"

    def stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(
        self,
        path: str,
        *,
        origin: str | None = None,
        method: str = "GET",
        data: bytes | None = None,
    ) -> tuple[int, dict[str, str], object]:
        request = urllib.request.Request(self.base_url + path, method=method, data=data)
        if origin:
            request.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
                payload = json.loads(body) if body.strip() else None
                return response.status, dict(response.headers), payload
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8")
                payload = json.loads(body) if body.strip() else None
                return exc.code, dict(exc.headers), payload
            finally:
                exc.close()


class EndpointTests(ApiTestCase):
    def test_meta_endpoint(self):
        status, headers, payload = self.request("/api/meta")

        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(payload["semester"]["name"], "2026-2027 学年第一学期")
        self.assertIn("week", payload["teaching"])
        self.assertIn("phase", payload["teaching"])
        self.assertEqual(payload["schema_version"], "phase3")

    def test_classes_endpoint(self):
        status, _headers, payload = self.request("/api/classes")

        self.assertEqual(status, 200)
        names = {item["name"] for item in payload}
        self.assertEqual(names, {"高一(A)班", "高一(B)班"})
        self.assertTrue(all(item["student_count"] == 3 for item in payload))

    def test_exams_endpoint(self):
        status, _headers, payload = self.request("/api/exams")

        self.assertEqual(status, 200)
        keys = [item["exam_key"] for item in payload]
        self.assertIn("demo-exam-1", keys)
        first = payload[0]
        self.assertIsNotNone(first["average"])
        self.assertEqual(first["student_count"], 6)

    def test_exam_stats_endpoint(self):
        status, _headers, payload = self.request("/api/exam/demo-exam-1/stats")

        self.assertEqual(status, 200)
        self.assertEqual(payload["exam_key"], "demo-exam-1")
        self.assertEqual(len(payload["items"]), 5)
        item = payload["items"][0]
        for key in ("item_no", "score_rate", "difficulty_band", "discrimination"):
            self.assertIn(key, item)

    def test_class_averages_endpoint_handles_chinese_names(self):
        quoted = urllib.parse.quote("高一(A)班")

        status, _headers, payload = self.request(f"/api/class/{quoted}/averages")

        self.assertEqual(status, 200)
        self.assertEqual(payload["class_name"], "高一(A)班")
        self.assertEqual(payload["student_count"], 3)
        self.assertTrue(payload["exams"])
        self.assertIsNotNone(payload["exams"][0]["average"])

    def test_homework_stats_endpoint(self):
        status, _headers, payload = self.request("/api/homework/stats")

        self.assertEqual(status, 200)
        self.assertEqual(len(payload), 2)
        first = payload[0]
        for key in ("class_name", "expected", "submitted", "missing", "completion_rate"):
            self.assertIn(key, first)
        self.assertLessEqual(first["completion_rate"], 1.0)

    def test_alerts_endpoint_and_status_filter(self):
        status, _headers, payload = self.request("/api/alerts?status=open")

        self.assertEqual(status, 200)
        self.assertTrue(payload)
        self.assertTrue(all(item["status"] == "open" for item in payload))

        status, _headers, resolved = self.request("/api/alerts?status=resolved")
        self.assertEqual(status, 200)
        self.assertEqual(resolved, [])


class QuestionDispatchTests(unittest.TestCase):
    """题库检索端点：直接调 dispatch，不依赖绑定端口（沙箱里也能跑）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config_path = self.base / "config.toml"
        self.config_path.write_text(CONFIG_TEXT, encoding="utf-8")
        self.config = config_loader.load_config(self.config_path, env={})
        init_db.init_database(self.config)
        questions_module.import_questions(self.config, demo=True)
        self.conn = init_db.connect(self.config.paths.database)
        self.addCleanup(self.conn.close)

    def dispatch(self, query: str = "") -> object:
        return api_module.dispatch(
            self.config, self.conn, "/api/questions", urllib.parse.parse_qs(query)
        )

    def test_returns_public_fields_only(self):
        payload = self.dispatch("limit=3")

        self.assertEqual(payload["count"], 3)
        self.assertEqual(len(payload["items"]), 3)
        for item in payload["items"]:
            with self.subTest(question=item["question_key"]):
                self.assertNotIn("answer", item)
                self.assertNotIn("analysis", item)
                self.assertIn("stem", item)
                self.assertIn("tags", item)

    def test_filters_by_tag_type_and_keyword(self):
        by_tag = self.dispatch("tag=" + urllib.parse.quote("欧姆定律"))
        self.assertGreaterEqual(by_tag["count"], 1)
        self.assertTrue(all("欧姆定律" in item["tags"] for item in by_tag["items"]))

        by_type = self.dispatch("type=choice")
        self.assertTrue(by_type["items"])
        self.assertTrue(all(item["qtype"] == "choice" for item in by_type["items"]))

        by_keyword = self.dispatch("q=demo-exp-paper-tape")
        self.assertEqual(by_keyword["count"], 1)
        self.assertEqual(by_keyword["items"][0]["question_key"], "demo-exp-paper-tape-accel")

    def test_bad_parameters_are_400(self):
        for query in ("difficulty=9", "difficulty=abc", "limit=0", "type=essay"):
            with self.subTest(query=query):
                with self.assertRaises(api_module.ApiError) as ctx:
                    self.dispatch(query)
                self.assertEqual(ctx.exception.status, HTTPStatus.BAD_REQUEST)

    def test_unknown_route_is_404(self):
        with self.assertRaises(api_module.ApiError) as ctx:
            api_module.dispatch(self.config, self.conn, "/api/没有这个", {})

        self.assertEqual(ctx.exception.status, HTTPStatus.NOT_FOUND)

    def test_recommend_returns_items_with_reasons(self):
        payload = api_module.dispatch(
            self.config,
            self.conn,
            "/api/questions/recommend",
            {"student_uid": ["高一(A)班-01"], "limit": ["3"]},
        )

        self.assertGreaterEqual(payload["count"], 1)
        self.assertLessEqual(payload["count"], 3)
        self.assertEqual(payload["student_uid"], "高一(A)班-01")
        self.assertIsInstance(payload["difficulty_cap"], int)
        self.assertTrue(payload["notes"])
        for item in payload["items"]:
            with self.subTest(question=item["question_key"]):
                self.assertTrue(item["reasons"])
                self.assertNotIn("answer", item)
                self.assertNotIn("analysis", item)

    def test_recommend_requires_a_student_and_validates_limit(self):
        with self.assertRaises(api_module.ApiError) as missing_ctx:
            api_module.dispatch(self.config, self.conn, "/api/questions/recommend", {})
        self.assertEqual(missing_ctx.exception.status, HTTPStatus.BAD_REQUEST)

        with self.assertRaises(api_module.ApiError) as limit_ctx:
            api_module.dispatch(
                self.config,
                self.conn,
                "/api/questions/recommend",
                {"student_uid": ["高一(A)班-01"], "limit": ["0"]},
            )
        self.assertEqual(limit_ctx.exception.status, HTTPStatus.BAD_REQUEST)

    def test_recommend_unknown_student_is_404(self):
        with self.assertRaises(api_module.ApiError) as ctx:
            api_module.dispatch(
                self.config,
                self.conn,
                "/api/questions/recommend",
                {"student_uid": ["没有这个学号"]},
            )

        self.assertEqual(ctx.exception.status, HTTPStatus.NOT_FOUND)


class QuestionEndpointTests(ApiTestCase):
    """走真实 HTTP 的题库端点（沙箱不允许绑端口时会跳过，CI 上会跑）。"""

    def setUp(self):
        super().setUp()
        questions_module.import_questions(self.config, demo=True)

    def test_questions_endpoint_lists_without_answers(self):
        status, _headers, payload = self.request("/api/questions?limit=3")

        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 3)
        self.assertNotIn("answer", payload["items"][0])
        self.assertNotIn("analysis", payload["items"][0])

    def test_questions_endpoint_filters_and_rejects_bad_params(self):
        status, _headers, payload = self.request("/api/questions?type=choice")
        self.assertEqual(status, 200)
        self.assertTrue(payload["items"])
        self.assertTrue(all(item["qtype"] == "choice" for item in payload["items"]))

        bad_status, _headers, bad_payload = self.request("/api/questions?difficulty=9")
        self.assertEqual(bad_status, 400)
        self.assertIn("error", bad_payload)

    def test_recommend_endpoint(self):
        student_uid = urllib.parse.quote("高一(A)班-01")

        status, _headers, payload = self.request(
            f"/api/questions/recommend?student_uid={student_uid}&limit=2"
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["items"])
        self.assertNotIn("answer", payload["items"][0])

        missing_status, _headers, missing = self.request("/api/questions/recommend")
        self.assertEqual(missing_status, 400)
        self.assertIn("error", missing)

        unknown_status, _headers, unknown = self.request(
            "/api/questions/recommend?student_uid=%E6%B2%A1%E6%9C%89%E8%BF%99%E4%B8%AA%E5%AD%A6%E5%8F%B7"
        )
        self.assertEqual(unknown_status, 404)
        self.assertIn("error", unknown)


class ErrorTests(ApiTestCase):
    def test_unknown_exam_returns_404_json(self):
        status, headers, payload = self.request("/api/exam/no-such-exam/stats")

        self.assertEqual(status, 404)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(payload["error"]["status"], 404)
        self.assertIn("找不到考试", payload["error"]["message"])

    def test_unknown_class_and_route_return_404(self):
        status, _headers, payload = self.request(
            f"/api/class/{urllib.parse.quote('不存在的班')}/averages"
        )
        self.assertEqual(status, 404)
        self.assertIn("找不到班级", payload["error"]["message"])

        status, _headers, payload = self.request("/api/nope")
        self.assertEqual(status, 404)
        self.assertIn("没有这个接口", payload["error"]["message"])

    def test_bad_status_parameter_returns_400(self):
        status, _headers, payload = self.request("/api/alerts?status=maybe")

        self.assertEqual(status, 400)
        self.assertIn("status", payload["error"]["message"])

    def test_write_methods_are_rejected(self):
        status, _headers, payload = self.request(
            "/api/classes", method="POST", data=b"{}"
        )

        self.assertEqual(status, 405)
        self.assertIn("只支持 GET", payload["error"]["message"])


class CorsTests(ApiTestCase):
    def test_allowed_origin_is_echoed_with_vary(self):
        origin = f"http://127.0.0.1:{self.port}"

        status, headers, _payload = self.request("/api/classes", origin=origin)

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)
        self.assertEqual(headers.get("Vary"), "Origin")

    def test_localhost_origin_is_also_allowed(self):
        origin = f"http://localhost:{self.port}"

        _status, headers, _payload = self.request("/api/exams", origin=origin)

        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)

    def test_other_origins_get_no_cors_header(self):
        _status, headers, _payload = self.request(
            "/api/exams", origin="http://evil.example"
        )

        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_wildcard_is_never_sent(self):
        for origin in (None, f"http://127.0.0.1:{self.port}", "http://evil.example"):
            with self.subTest(origin=origin):
                _status, headers, _payload = self.request("/api/meta", origin=origin)
                self.assertNotEqual(headers.get("Access-Control-Allow-Origin"), "*")

    def test_options_preflight_only_answers_allowed_origins(self):
        allowed = f"http://127.0.0.1:{self.port}"
        status, headers, _payload = self.request("/api/classes", origin=allowed, method="OPTIONS")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), allowed)
        self.assertIn("GET", headers.get("Access-Control-Allow-Methods", ""))

        status, headers, _payload = self.request(
            "/api/classes", origin="http://evil.example", method="OPTIONS"
        )
        self.assertEqual(status, 204)
        self.assertNotIn("Access-Control-Allow-Origin", headers)


class ServerBindingTests(ApiTestCase):
    def test_server_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_non_loopback_host_is_refused(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            api_module.create_server(self.config, port=0, host="0.0.0.0")

        self.assertIn("只允许绑定", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
