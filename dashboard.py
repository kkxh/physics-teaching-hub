"""本地看板：把库里的数据构建成一个可离线打开的静态站。

- 产物：`outputs/dashboard/index.html` + `outputs/dashboard/data.json`；
- 数据**构建时嵌入** HTML（`<script type="application/json">`），所以双击 `index.html`
  就能看，不需要起服务、也不依赖 P2.8 的 API；
- 页面文字全部从 `labels/<locale>.toml` 的 `[dashboard]` 段取，模板只留 `{{占位符}}`；
- 没有第三方 CDN：HTML/CSS/JS 都在一个文件里，离线可用；
- 展示约定：空值统一显示 labels 里的 `empty_value`（默认「—」），排序与统计跳过 null。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Mapping

import api as api_module
import config_loader
import db as db_module
from labels import LabelError

DATA_FILENAME = "data.json"
HTML_FILENAME = "index.html"
DASHBOARD_LABEL_KEYS: tuple[str, ...] = (
    "title",
    "subtitle",
    "scope_all",
    "filter_class",
    "section_overview",
    "section_exam_trend",
    "section_homework",
    "section_alerts",
    "card_classes",
    "card_students",
    "card_exams",
    "card_alerts",
    "column_class",
    "column_students",
    "column_exam",
    "column_date",
    "column_average",
    "column_assignments",
    "column_completion",
    "column_correction",
    "column_student",
    "column_severity",
    "column_kind",
    "column_description",
    "label_semester",
    "label_week",
    "label_phase",
    "label_generated_at",
    "empty_value",
    "no_data",
    "footer_note",
)
MAX_ALERT_ROWS = 20


def collect_labels(config: config_loader.AppConfig) -> dict[str, str]:
    """看板要用的文案：缺 key 直接报错，不静默用默认值。"""
    try:
        return {
            key: config.labels.get(f"dashboard.{key}") for key in DASHBOARD_LABEL_KEYS
        }
    except LabelError as exc:
        raise config_loader.ConfigError(
            f"{exc}（看板需要 [dashboard] 段；如果你的文案表是早期版本复制来的，"
            "请把仓库 labels/zh-CN.toml 的 [dashboard] 段补进去）"
        ) from exc


def collect_dashboard_data(
    config: config_loader.AppConfig,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """把看板要展示的数据一次性取出来（复用 API 层的查询，形状保持一致）。"""
    meta = api_module.build_meta(config, conn)
    classes = api_module.build_classes(conn)
    exams = api_module.build_exams(conn)
    homework = api_module.build_homework_stats(conn)
    alerts = api_module.build_alerts(conn, "open")
    # 班级 × 考试 的平均分：让前端按班级筛选考试时不用再猜
    class_averages: dict[str, dict[str, float | None]] = {}
    for item in classes:
        detail = api_module.build_class_averages(conn, str(item["name"]))
        class_averages[str(item["name"])] = {
            str(exam["exam_key"]): exam["average"] for exam in detail["exams"]
        }
    return {
        "meta": meta,
        "labels": collect_labels(config),
        "classes": classes,
        "exams": exams,
        "homework": homework,
        "alerts": alerts,
        "classAverages": class_averages,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "counts": {
            "classes": len(classes),
            "students": sum(int(item.get("student_count") or 0) for item in classes),
            "exams": len(exams),
            "alerts": len(alerts),
        },
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}}</title>
<style>
:root { color-scheme: light dark; --line: #d8d8d8; --muted: #6b6b6b; --accent: #3a7afe; }
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; margin: 0; padding: 24px; line-height: 1.5; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 28px 0 8px; }
.sub { color: var(--muted); font-size: 13px; margin-bottom: 16px; }
.cards { display: flex; flex-wrap: wrap; gap: 12px; }
.card { border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; min-width: 120px; }
.card b { display: block; font-size: 22px; }
.card span { color: var(--muted); font-size: 13px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { border-bottom: 1px solid var(--line); padding: 6px 8px; text-align: left; }
th { color: var(--muted); font-weight: 600; }
select { font-size: 14px; padding: 4px 8px; }
.bar { background: var(--accent); height: 10px; border-radius: 5px; min-width: 2px; }
.bar-wrap { background: rgba(127,127,127,.2); border-radius: 5px; width: 120px; }
.muted { color: var(--muted); }
footer { margin-top: 32px; color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<h1>{{title}}</h1>
<div class="sub">{{subtitle}}｜{{label_semester}}: {{semester}}｜{{label_week}}: {{week}}｜{{label_phase}}: {{phase}}｜{{label_generated_at}}: {{generated_at}}</div>
<div class="cards" id="cards"></div>
<p><label for="class-filter">{{filter_class}}</label>
<select id="class-filter"><option value="">{{scope_all}}</option></select></p>
<h2>{{section_exam_trend}}</h2>
<table id="exam-table"></table>
<h2>{{section_homework}}</h2>
<table id="homework-table"></table>
<h2>{{section_alerts}}</h2>
<table id="alert-table"></table>
<footer>{{footer_note}}</footer>
<script id="dashboard-data" type="application/json">{{data_json}}</script>
<script>
const DATA = JSON.parse(document.getElementById("dashboard-data").textContent);
const L = DATA.labels;
const EMPTY = L.empty_value;

// 空值统一走这里：null / undefined / 非数字都显示占位符，不会崩
function fmtScore(value, digits) {
  if (value === null || value === undefined || value === "" || isNaN(Number(value))) return EMPTY;
  const number = Number(value);
  const fixed = digits === undefined ? 1 : digits;
  return fixed === 0 ? String(Math.round(number)) : number.toFixed(fixed);
}
function fmtRate(value) {
  if (value === null || value === undefined || isNaN(Number(value))) return EMPTY;
  return (Number(value) * 100).toFixed(1) + "%";
}
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function renderCards() {
  const cards = [
    [L.card_classes, DATA.counts.classes],
    [L.card_students, DATA.counts.students],
    [L.card_exams, DATA.counts.exams],
    [L.card_alerts, DATA.counts.alerts],
  ];
  const host = document.getElementById("cards");
  host.textContent = "";
  cards.forEach(([label, value]) => {
    const box = el("div", undefined, "card");
    box.appendChild(el("b", fmtScore(value, 0)));
    box.appendChild(el("span", label));
    host.appendChild(box);
  });
}

function renderClassFilter() {
  const select = document.getElementById("class-filter");
  DATA.classes.forEach(item => {
    const option = el("option", item.name);
    option.value = item.name;
    select.appendChild(option);
  });
  select.addEventListener("change", () => render(select.value));
}

function renderExams(className) {
  const table = document.getElementById("exam-table");
  table.textContent = "";
  const head = el("tr");
  [L.column_exam, L.column_date, L.column_average].forEach(text => head.appendChild(el("th", text)));
  table.appendChild(head);

  const rows = DATA.exams
    .map(exam => ({ exam: exam, average: exam.average === null || exam.average === undefined ? null : Number(exam.average) }))
    .filter(row => row.average !== null)
    .sort((a, b) => String(a.exam.date).localeCompare(String(b.exam.date)));
  const shown = rows.filter(row => !className || classHasExam(className, row.exam.exam_key));

  if (shown.length === 0) {
    const row = el("tr");
    const cell = el("td", L.no_data, "muted");
    cell.colSpan = 3;
    row.appendChild(cell);
    table.appendChild(row);
    return;
  }
  shown.forEach(item => {
    const row = el("tr");
    row.appendChild(el("td", item.exam.name));
    row.appendChild(el("td", item.exam.date));
    const cell = el("td");
    const wrap = el("div", undefined, "bar-wrap");
    const ratio = item.exam.full_score ? item.average / item.exam.full_score : 0;
    const bar = el("div", undefined, "bar");
    bar.style.width = Math.max(2, Math.min(100, ratio * 100)) + "%";
    wrap.appendChild(bar);
    cell.appendChild(wrap);
    cell.appendChild(el("div", fmtScore(item.average)));
    row.appendChild(cell);
    table.appendChild(row);
  });
}

function classHasExam(className, examKey) {
  const averages = DATA.classAverages && DATA.classAverages[className];
  if (!averages) return true;
  return Object.prototype.hasOwnProperty.call(averages, examKey);
}

function renderHomework(className) {
  const table = document.getElementById("homework-table");
  table.textContent = "";
  const head = el("tr");
  [L.column_class, L.column_assignments, L.column_completion, L.column_correction].forEach(text => head.appendChild(el("th", text)));
  table.appendChild(head);

  const rows = DATA.homework.filter(item => !className || item.class_name === className);
  if (rows.length === 0) {
    const row = el("tr");
    const cell = el("td", L.no_data, "muted");
    cell.colSpan = 4;
    row.appendChild(cell);
    table.appendChild(row);
    return;
  }
  rows.forEach(item => {
    const row = el("tr");
    row.appendChild(el("td", item.class_name));
    row.appendChild(el("td", fmtScore(item.assignment_count, 0)));
    row.appendChild(el("td", fmtRate(item.completion_rate)));
    row.appendChild(el("td", fmtRate(item.correction_rate)));
    table.appendChild(row);
  });
}

function renderAlerts(className) {
  const table = document.getElementById("alert-table");
  table.textContent = "";
  const head = el("tr");
  [L.column_severity, L.column_student, L.column_kind, L.column_description].forEach(text => head.appendChild(el("th", text)));
  table.appendChild(head);

  const rows = DATA.alerts
    .filter(item => !className || item.class_name === className)
    .slice(0, {{max_alert_rows}});
  if (rows.length === 0) {
    const row = el("tr");
    const cell = el("td", L.no_data, "muted");
    cell.colSpan = 4;
    row.appendChild(cell);
    table.appendChild(row);
    return;
  }
  rows.forEach(item => {
    const row = el("tr");
    row.appendChild(el("td", item.severity || EMPTY));
    row.appendChild(el("td", item.student_name + "（" + item.student_uid + "）"));
    row.appendChild(el("td", item.kind));
    row.appendChild(el("td", item.description || EMPTY));
    table.appendChild(row);
  });
}

function render(className) {
  renderExams(className);
  renderHomework(className);
  renderAlerts(className);
}

renderCards();
renderClassFilter();
render("");
</script>
</body>
</html>
"""


def render_index(
    config: config_loader.AppConfig,
    data: Mapping[str, Any],
) -> str:
    """把数据与文案灌进模板；模板里的 {{占位符}} 逐个替换。"""
    labels = data["labels"]
    meta = data["meta"]
    teaching = meta["teaching"]
    replacements = {
        "{{title}}": labels["title"],
        "{{subtitle}}": labels["subtitle"],
        "{{scope_all}}": labels["scope_all"],
        "{{filter_class}}": labels["filter_class"],
        "{{section_exam_trend}}": labels["section_exam_trend"],
        "{{section_homework}}": labels["section_homework"],
        "{{section_alerts}}": labels["section_alerts"],
        "{{label_semester}}": labels["label_semester"],
        "{{label_week}}": labels["label_week"],
        "{{label_phase}}": labels["label_phase"],
        "{{label_generated_at}}": labels["label_generated_at"],
        "{{footer_note}}": labels["footer_note"],
        "{{semester}}": meta["semester"]["name"],
        "{{week}}": str(teaching["week"]) if teaching["week"] else labels["empty_value"],
        "{{phase}}": teaching["phase"] or labels["empty_value"],
        "{{generated_at}}": data["generated_at"],
        "{{max_alert_rows}}": str(MAX_ALERT_ROWS),
        # JSON 里可能有 </script> 之类的片段，转义一下避免提前结束脚本块
        "{{data_json}}": escape(json.dumps(data, ensure_ascii=False), quote=False),
    }
    html = HTML_TEMPLATE
    for token, value in replacements.items():
        html = html.replace(token, str(value))
    return html


def write_dashboard(
    config: config_loader.AppConfig,
    conn: sqlite3.Connection,
) -> Path:
    """写出看板静态站，返回 index.html 的路径。"""
    data = collect_dashboard_data(config, conn)
    target = config.paths.output_dir / "dashboard"
    target.mkdir(parents=True, exist_ok=True)
    (target / DATA_FILENAME).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    index_path = target / HTML_FILENAME
    index_path.write_text(render_index(config, data), encoding="utf-8")
    return index_path
