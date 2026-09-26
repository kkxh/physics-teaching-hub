"""本地只读 API：给看板前端与本地脚本用。

工程约定（ENGINEERING_NOTES 第 8 条，一次做对）：

- 只绑定 `127.0.0.1`，端口默认 8420；
- CORS 只回固定的本机 Origin（`http://127.0.0.1:<port>` / `http://localhost:<port>`）
  并带 `Vary: Origin`，**不写通配符**；
- 第一版只做 GET（只读），写操作走 CLI；
- 只用标准库 `http.server`，不引入 web 框架。

错误一律返回 JSON 错误体 + 合适的 HTTP 状态码（404 找不到、400 参数不对、405 方法不对）。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

import alerts as alerts_module
import config_loader
import db as db_module
import exam as exam_module
import homework as homework_module
import questions as questions_module
import teaching_calendar

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420


class ApiError(Exception):
    """带 HTTP 状态码的错误；处理器会把它翻成 JSON 错误体。"""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Route:
    pattern: re.Pattern[str]
    handler: str


ROUTES: tuple[Route, ...] = (
    Route(re.compile(r"^/api/meta$"), "meta"),
    Route(re.compile(r"^/api/classes$"), "classes"),
    Route(re.compile(r"^/api/exams$"), "exams"),
    Route(re.compile(r"^/api/exam/(?P<exam_key>[^/]+)/stats$"), "exam_stats"),
    Route(re.compile(r"^/api/class/(?P<class_name>[^/]+)/averages$"), "class_averages"),
    Route(re.compile(r"^/api/homework/stats$"), "homework_stats"),
    Route(re.compile(r"^/api/alerts$"), "alerts"),
    Route(re.compile(r"^/api/questions$"), "questions"),
)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def build_meta(config: config_loader.AppConfig, conn: sqlite3.Connection) -> dict[str, Any]:
    calendar = teaching_calendar.TeachingCalendar.from_config(config)
    today = calendar.today()
    phase = calendar.current_phase(today)
    return {
        "project": {
            "name": config.project.name,
            "stage": config.project.stage,
            "stage_label": config.project.stage_label,
            "subject_label": config.project.subject_label,
            "timezone": config.project.timezone,
        },
        "semester": {
            "name": config.semester.name,
            "starts_on": config.semester.starts_on.isoformat(),
            "ends_on": config.semester.ends_on.isoformat(),
        },
        "teaching": {
            "today": today.isoformat(),
            "in_semester": calendar.is_in_semester(today),
            "week": calendar.week_of(today),
            "weeks_total": calendar.weeks_total(),
            "phase": phase.name if phase else None,
        },
        "schema_version": db_module.schema_version(conn),
    }


def build_classes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.name AS name, COUNT(s.id) AS student_count
        FROM classes c LEFT JOIN students s ON s.class_id = c.id
        GROUP BY c.id ORDER BY c.name
        """
    ).fetchall()
    return _rows_to_dicts(list(rows))


def build_exams(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.exam_key AS exam_key, e.name AS name, e.exam_date AS date,
               e.full_score AS full_score, COUNT(es.id) AS student_count,
               AVG(es.score) AS average
        FROM exams e LEFT JOIN exam_scores es ON es.exam_id = e.id
        GROUP BY e.id ORDER BY e.exam_date, e.id
        """
    ).fetchall()
    return _rows_to_dicts(list(rows))


def build_exam_stats(conn: sqlite3.Connection, exam_key: str) -> dict[str, Any]:
    analysis = exam_module.analyze_exam(conn, exam_key=exam_key)
    return {
        "exam_key": analysis.exam_key,
        "name": analysis.exam_name,
        "date": analysis.exam_date,
        "full_score": analysis.full_score,
        "student_count": analysis.student_count,
        "average": analysis.average,
        "highest": analysis.highest,
        "lowest": analysis.lowest,
        "pass_rate": analysis.pass_rate,
        "items": [
            {
                "item_no": item.item_no,
                "full_score": item.full_score,
                "student_count": item.student_count,
                "average": item.average,
                "score_rate": item.score_rate,
                "difficulty_band": item.difficulty_band,
                "discrimination": item.discrimination,
                "discrimination_band": item.discrimination_band,
            }
            for item in analysis.items
        ],
        "error_tags": [
            {"code": code, "label": label, "count": count}
            for code, label, count in analysis.error_tag_counts
        ],
        "problems": list(analysis.problems),
    }


def build_class_averages(conn: sqlite3.Connection, class_name: str) -> dict[str, Any]:
    klass = conn.execute(
        "SELECT id, name FROM classes WHERE name = ?", (class_name,)
    ).fetchone()
    if klass is None:
        raise ApiError(HTTPStatus.NOT_FOUND, f"找不到班级：{class_name}")

    rows = conn.execute(
        """
        SELECT e.exam_key AS exam_key, e.name AS name, e.exam_date AS date,
               COUNT(es.id) AS student_count, AVG(es.score) AS average
        FROM exams e
        LEFT JOIN exam_scores es ON es.exam_id = e.id
        LEFT JOIN students s ON s.id = es.student_id AND s.class_id = ?
        WHERE es.id IS NULL OR s.id IS NOT NULL
        GROUP BY e.id ORDER BY e.exam_date, e.id
        """,
        (int(klass["id"]),),
    ).fetchall()
    student_count = conn.execute(
        "SELECT COUNT(*) AS n FROM students WHERE class_id = ?", (int(klass["id"]),)
    ).fetchone()["n"]
    return {
        "class_name": str(klass["name"]),
        "student_count": int(student_count),
        "exams": _rows_to_dicts(list(rows)),
    }


def build_homework_stats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "class_name": item.class_name,
            "assignment_count": item.assignment_count,
            "expected": item.expected,
            "submitted": item.submitted,
            "late": item.late,
            "missing": item.missing,
            "completion_rate": item.completion_rate,
            "correction_rate": item.correction_rate,
        }
        for item in homework_module.homework_stats(conn)
    ]


def build_alerts(conn: sqlite3.Connection, status: str | None) -> list[dict[str, Any]]:
    if status in (None, "", "all"):
        wanted = None
    elif status in (alerts_module.OPEN, alerts_module.RESOLVED):
        wanted = status
    else:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"status 只能是 {alerts_module.OPEN}、{alerts_module.RESOLVED} 或 all",
        )
    rows = alerts_module.list_alerts(conn, status=wanted)
    return [
        {
            "id": int(row["id"]),
            "student_uid": row["student_uid"],
            "student_name": row["student_name"],
            "class_name": row["class_name"],
            "kind": row["kind"],
            "severity": row["severity"],
            "status": row["status"],
            "description": row["description"],
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
        }
        for row in rows
    ]


def _query_int(query: Mapping[str, list[str]], name: str) -> int | None:
    """取一个整数查询参数；给了但不是整数就 400。"""
    raw = (query.get(name) or [None])[0]
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw).strip()
    text = text[1:] if text.startswith("+") else text
    if not text.lstrip("-").isdigit():
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} 必须是整数；收到：{raw!r}")
    return int(text)


def build_questions(
    conn: sqlite3.Connection,
    query: Mapping[str, list[str]],
) -> dict[str, Any]:
    """题库检索（只读）。

    **不回答案与解析**：本地 API 没有鉴权，答案只走 CLI 的 `--show-answer`。
    """

    def first(name: str) -> str | None:
        raw = (query.get(name) or [None])[0]
        if raw is None:
            return None
        text = str(raw).strip()
        return text or None

    limit = _query_int(query, "limit")
    try:
        items = questions_module.search_questions(
            conn,
            tag=first("tag"),
            qtype=first("type"),
            difficulty=_query_int(query, "difficulty"),
            keyword=first("q"),
            limit=questions_module.DEFAULT_LIST_LIMIT if limit is None else limit,
        )
    except config_loader.ConfigError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    return {
        "count": len(items),
        "items": [questions_module.as_public_dict(item) for item in items],
    }


def dispatch(
    config: config_loader.AppConfig,
    conn: sqlite3.Connection,
    path: str,
    query: Mapping[str, list[str]],
) -> Any:
    """把一次 GET 请求映射到数据；找不到就是 404，参数不对就是 400。"""
    for route in ROUTES:
        match = route.pattern.match(path)
        if match is None:
            continue
        if route.handler == "meta":
            return build_meta(config, conn)
        if route.handler == "classes":
            return build_classes(conn)
        if route.handler == "exams":
            return build_exams(conn)
        if route.handler == "exam_stats":
            exam_key = unquote(match.group("exam_key"))
            try:
                return build_exam_stats(conn, exam_key)
            except config_loader.ConfigError as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
        if route.handler == "class_averages":
            return build_class_averages(conn, unquote(match.group("class_name")))
        if route.handler == "homework_stats":
            return build_homework_stats(conn)
        if route.handler == "alerts":
            status = (query.get("status") or [None])[0]
            return build_alerts(conn, status)
        if route.handler == "questions":
            return build_questions(conn, query)

    raise ApiError(HTTPStatus.NOT_FOUND, f"没有这个接口：{path}")


class ApiHandler(BaseHTTPRequestHandler):
    """每个请求开一条只读连接；配置与允许的 Origin 由 create_server 注入。"""

    app_config: config_loader.AppConfig
    allowed_origins: set[str] = set()
    server_version = "physics-teaching-hub-api/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - 基类签名
        # 默认日志会打到 stderr，这里保持安静，交给调用方决定怎么记录
        return

    def _cors_origin(self) -> str | None:
        origin = self.headers.get("Origin")
        if origin and origin in self.allowed_origins:
            return origin
        return None

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        origin = self._cors_origin()
        if origin is not None:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        parts = urlsplit(self.path)
        try:
            conn = db_module.connect(self.app_config.paths.database)
            try:
                db_module.require_schema(conn)
                payload = dispatch(
                    self.app_config, conn, parts.path, parse_qs(parts.query)
                )
            finally:
                conn.close()
        except ApiError as exc:
            self._send_json(
                exc.status, {"error": {"status": exc.status.value, "message": exc.message}}
            )
            return
        except config_loader.ConfigError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"status": HTTPStatus.BAD_REQUEST.value, "message": str(exc)}},
            )
            return

        self._send_json(HTTPStatus.OK, payload)

    def do_GET(self) -> None:  # noqa: N802 - 基类接口
        self._handle()

    def do_OPTIONS(self) -> None:  # noqa: N802 - 基类接口
        origin = self._cors_origin()
        self.send_response(HTTPStatus.NO_CONTENT.value)
        if origin is not None:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _method_not_allowed(self) -> None:
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {
                "error": {
                    "status": HTTPStatus.METHOD_NOT_ALLOWED.value,
                    "message": "本地 API 第一版只支持 GET（写操作请用 hub.py 的命令）",
                }
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()


def create_server(
    config: config_loader.AppConfig,
    *,
    port: int = DEFAULT_PORT,
    host: str = DEFAULT_HOST,
) -> ThreadingHTTPServer:
    """建一个绑在 127.0.0.1 的服务器；port=0 时由系统分配空闲端口。"""
    if host != DEFAULT_HOST:
        raise config_loader.ConfigError(
            f"本地 API 只允许绑定 {DEFAULT_HOST}（当前传入 {host!r}）——数据不出本机。"
        )

    handler = type(
        "BoundApiHandler",
        (ApiHandler,),
        {
            "app_config": config,
            "allowed_origins": {
                f"http://{DEFAULT_HOST}:{port}",
                f"http://localhost:{port}",
            },
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    # 端口是 0 时系统分配，实际端口要回填给 CORS 白名单
    actual_port = int(server.server_address[1])
    handler.allowed_origins = {
        f"http://{DEFAULT_HOST}:{actual_port}",
        f"http://localhost:{actual_port}",
    }
    server.daemon_threads = True
    return server


def run_server(
    config: config_loader.AppConfig,
    *,
    port: int = DEFAULT_PORT,
    ready: threading.Event | None = None,
) -> ThreadingHTTPServer:
    """启动服务器（阻塞）。测试里用 create_server + 线程自己控制。"""
    server = create_server(config, port=port)
    host, actual_port = server.server_address[0], int(server.server_address[1])
    print(f"本地 API 已启动：http://{host}:{actual_port}/api/meta （只绑定本机）")
    if ready is not None:
        ready.set()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("已停止本地 API。")
    finally:
        server.server_close()
    return server
