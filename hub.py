"""Phase 2 的统一命令行入口。

用法（全局选项放在子命令之前）：

    python3 hub.py init-db --demo [--rebuild --yes]
    python3 hub.py upgrade-db
    python3 hub.py import-questions (--json 文件 | --csv 文件 | --demo)
    python3 hub.py import-scores --demo
    python3 hub.py make-report
    python3 hub.py --config my.toml --db /path/to/other.db make-report

约定：这是薄分发层——参数解析与配置装载在这里，具体逻辑留在各模块的可导入函数里。
Phase 1 的三个脚本（`init_db.py` / `import_scores.py` / `make_report.py`）保留为兼容垫片，
此后新增能力**只加子命令，不加新脚本**。
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sqlite3
import sys
from pathlib import Path

import alerts as alerts_module
import api as api_module
import config_loader
import dashboard as dashboard_module
import db as db_module
import errors as errors_module
import exam as exam_module
import homework as homework_module
import importer
import import_scores
import init_db
import make_report
import profiling
import questions as questions_module
import reports
from labels import LabelError


def add_global_options(
    parser: argparse.ArgumentParser,
    *,
    suppress_defaults: bool = False,
) -> None:
    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument(
        "--config",
        default=default,
        help="配置文件；省略时依次找 config.toml、config.example.toml",
    )
    parser.add_argument(
        "--db",
        default=default,
        help="覆盖配置里的数据库路径（相对当前目录）；用于隔离库与回归检查",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hub.py", description="教学数据中枢的命令行入口（Phase 2 起）"
    )
    add_global_options(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-db", help="建库 + 灌入虚构演示名单")
    add_global_options(init_parser, suppress_defaults=True)
    init_parser.add_argument("--demo", action="store_true", help="导入虚构演示名单")
    init_parser.add_argument("--rebuild", action="store_true", help="删掉旧库重建")
    init_parser.add_argument("--yes", action="store_true", help="确认 --rebuild 的删除动作")

    upgrade_parser = subparsers.add_parser(
        "upgrade-db", help="把存量库升级到当前 schema（只应用迁移，不导入数据）"
    )
    add_global_options(upgrade_parser, suppress_defaults=True)

    questions_parser = subparsers.add_parser(
        "import-questions", help="导入题目（JSON / CSV / 自制示例题）"
    )
    add_global_options(questions_parser, suppress_defaults=True)
    questions_parser.add_argument("--json", default=None, help="题目文件（JSON 数组）")
    questions_parser.add_argument("--csv", default=None, help="题目文件（CSV，UTF-8，带表头）")
    questions_parser.add_argument(
        "--demo", action="store_true", help="导入仓库自带的自制示例题集"
    )
    questions_parser.add_argument(
        "--columns",
        default=None,
        help='CSV 表头映射，如 "题号=question_key,知识点=tags"',
    )
    questions_parser.add_argument("--dry-run", action="store_true", help="只预览，不写库")

    scores_parser = subparsers.add_parser("import-scores", help="导入成绩")
    add_global_options(scores_parser, suppress_defaults=True)
    scores_parser.add_argument(
        "--demo", action="store_true", help="导入配置里 [demo] 生成的虚构数据集"
    )
    scores_parser.add_argument("--csv", default=None, help="成绩表（CSV，UTF-8，带表头）")
    scores_parser.add_argument("--excel", default=None, help="成绩表（Excel .xlsx，带表头）")
    scores_parser.add_argument("--sheet", default=None, help="Excel 工作表名，默认第一个")
    scores_parser.add_argument("--exam", default=None, help="考试名（与日期一起定位考试）")
    scores_parser.add_argument("--exam-date", default=None, help="考试日期，YYYY-MM-DD")
    scores_parser.add_argument(
        "--full-score", type=float, default=100.0, help="满分，默认 100"
    )
    scores_parser.add_argument(
        "--columns",
        default=None,
        help='表头映射，如 "学号=student_uid,姓名=name,分数=score"',
    )
    scores_parser.add_argument("--dry-run", action="store_true", help="只预览，不写库")

    report_parser = subparsers.add_parser("make-report", help="生成 Markdown 报告")
    add_global_options(report_parser, suppress_defaults=True)

    homework_parser = subparsers.add_parser(
        "import-homework", help="导入一次作业的提交情况（CSV）"
    )
    add_global_options(homework_parser, suppress_defaults=True)
    homework_parser.add_argument("--csv", required=True, help="作业提交表（CSV，带表头）")
    homework_parser.add_argument("--assign-key", required=True, help="作业标识，重复导入同一份作业用它")
    homework_parser.add_argument("--class", dest="class_name", required=True, help="作业所属班级")
    homework_parser.add_argument("--topic", required=True, help="作业主题")
    homework_parser.add_argument("--assigned-date", required=True, help="布置日期 YYYY-MM-DD")
    homework_parser.add_argument("--due-date", default=None, help="截止日期 YYYY-MM-DD")
    homework_parser.add_argument(
        "--columns",
        default=None,
        help='表头映射，如 "学号=student_uid,状态=status,订正日期=corrected_on"',
    )
    homework_parser.add_argument("--dry-run", action="store_true", help="只预览，不写库")

    stats_parser = subparsers.add_parser(
        "homework-stats",
        help="按班级统计作业完成情况（完成率 = 已交/应交，分子分母都只算本班当前学生）",
        description=(
            "按班级统计作业完成情况。口径：完成率 = 已交 / 应交，分子与分母都只算"
            "「作业所属班级的当前学生」——转班学生的历史提交不计入任何班级，"
            "避免完成率超过 100%；缺交 = 应交 − 已交；订正率 = 有订正的已交记录 / 已交记录。"
        ),
    )
    add_global_options(stats_parser, suppress_defaults=True)
    stats_parser.add_argument("--class", dest="class_name", default=None, help="只看某个班")

    error_parser = subparsers.add_parser(
        "record-error",
        help="记一条错因（先预览，加 --yes 才写入）",
        description=(
            "记一条错因记录。写入前会校验学生、标签与所挂的考试/作业都存在；"
            "不带 --yes 只打印预览，不会写库。"
        ),
    )
    add_global_options(error_parser, suppress_defaults=True)
    error_parser.add_argument("--student", required=True, help="学生 student_uid")
    error_parser.add_argument("--tag", required=True, help="错因标签代码，如 calculation")
    error_parser.add_argument("--exam-key", default=None, help="挂在哪场考试上")
    error_parser.add_argument("--assign-key", default=None, help="挂在哪份作业上（与 --exam-key 二选一）")
    error_parser.add_argument("--note", default=None, help="备注")
    error_parser.add_argument("--recorded-on", default=None, help="记录日期 YYYY-MM-DD，默认今天")
    error_parser.add_argument("--yes", action="store_true", help="确认写入")

    list_error_parser = subparsers.add_parser(
        "list-errors", help="列出某个学生的错因记录（按时间倒序）"
    )
    add_global_options(list_error_parser, suppress_defaults=True)
    list_error_parser.add_argument("--student", required=True, help="学生 student_uid")
    list_error_parser.add_argument("--tag", default=None, help="只看某个标签代码")

    behavior_parser = subparsers.add_parser(
        "record-behavior",
        help="记一条行为记录（先预览，加 --yes 才写入）",
        description="行为类型见 errors.BUILTIN_BEHAVIOR_KINDS；不带 --yes 只打印预览。",
    )
    add_global_options(behavior_parser, suppress_defaults=True)
    behavior_parser.add_argument("--student", required=True, help="学生 student_uid")
    behavior_parser.add_argument("--kind", required=True, help="行为类型代码")
    behavior_parser.add_argument("--detail", default=None, help="观测细节")
    behavior_parser.add_argument("--recorded-on", default=None, help="记录日期 YYYY-MM-DD，默认今天")
    behavior_parser.add_argument("--yes", action="store_true", help="确认写入")

    list_behavior_parser = subparsers.add_parser(
        "list-behavior", help="列出某个学生的行为记录（按时间倒序）"
    )
    add_global_options(list_behavior_parser, suppress_defaults=True)
    list_behavior_parser.add_argument("--student", required=True, help="学生 student_uid")

    profile_parser = subparsers.add_parser(
        "compute-profile",
        help="计算学生画像（默认只算源数据变过的学生）",
        description=(
            "计算学生画像并写入 ability_scores。维度：成绩水平 / 作业习惯 / 错因控制，"
            "权重在 config.toml 的 [profile] 段；默认只重算源数据变过的学生，"
            "--rebuild 全部重算，--student 只算一个人。"
        ),
    )
    add_global_options(profile_parser, suppress_defaults=True)
    profile_parser.add_argument("--student", default=None, help="只算这个 student_uid")
    profile_parser.add_argument("--rebuild", action="store_true", help="全部重算")

    scan_parser = subparsers.add_parser(
        "scan-alerts",
        help="按 [alerts] 阈值扫描并写入预警（幂等）",
        description=(
            "按阈值扫描预警：连续缺交 / 平均分偏低 / 成绩下滑。同一学生在同一规则上只保留"
            "一条未解决预警；条件消失不会自动关闭（要用 resolve-alert 记录跟进后关闭）。"
        ),
    )
    add_global_options(scan_parser, suppress_defaults=True)
    scan_parser.add_argument("--dry-run", action="store_true", help="只预览，不写库")

    list_alerts_parser = subparsers.add_parser("list-alerts", help="列出预警")
    add_global_options(list_alerts_parser, suppress_defaults=True)
    list_alerts_parser.add_argument(
        "--status", choices=("open", "resolved"), default=None, help="只看某种状态"
    )

    resolve_parser = subparsers.add_parser(
        "resolve-alert", help="解决一条预警（必须写跟进说明）"
    )
    add_global_options(resolve_parser, suppress_defaults=True)
    resolve_parser.add_argument("alert_id", type=int, help="预警 id")
    resolve_parser.add_argument("--note", required=True, help="跟进说明（必填）")
    resolve_parser.add_argument("--outcome", default=None, help="跟进结果")
    resolve_parser.add_argument("--by", dest="recorded_by", default=None, help="跟进人")

    alert_stats_parser = subparsers.add_parser("alert-stats", help="预警统计")
    add_global_options(alert_stats_parser, suppress_defaults=True)

    follow_up_parser = subparsers.add_parser(
        "list-follow-ups", help="列出跟进记录（可按预警或学生过滤）"
    )
    add_global_options(follow_up_parser, suppress_defaults=True)
    follow_up_parser.add_argument("--alert", dest="alert_id", type=int, default=None)
    follow_up_parser.add_argument("--student", dest="student_uid", default=None)

    weekly_parser = subparsers.add_parser(
        "weekly-report",
        help="生成某一周的周报（默认上一周）",
        description=(
            "汇总某一教学周的考试、作业、错因、行为与预警，输出 Markdown 到 outputs/。"
            "默认上一周；--week 用 YYYY-Www 指定，周必须落在学期内。"
        ),
    )
    add_global_options(weekly_parser, suppress_defaults=True)
    weekly_parser.add_argument("--week", default=None, help="如 2026-W42；默认上一周")
    weekly_parser.add_argument("--class", dest="class_name", default=None, help="只看某个班")

    patrol_parser = subparsers.add_parser(
        "phase-patrol",
        help="生成阶段巡检报告（当前进度 / 作业覆盖 / 预警 / 提醒）",
        description=(
            "按当前日期生成阶段巡检：教学周进度、当前教学阶段、作业覆盖、画像平均分、"
            "预警未解决数与异常提醒，输出 Markdown 到 outputs/。"
        ),
    )
    add_global_options(patrol_parser, suppress_defaults=True)

    item_parser = subparsers.add_parser(
        "import-item-scores",
        help="导入某场考试的小题得分（CSV）",
        description=(
            "导入小题得分：列约定 student_uid / name / item_no / score（可选 full_score）。"
            "考试必须已存在（先用 import-scores 建考试）；--item-score 是没给满分时的小题默认满分。"
        ),
    )
    add_global_options(item_parser, suppress_defaults=True)
    item_parser.add_argument("--csv", required=True, help="小题得分表（CSV，带表头）")
    item_parser.add_argument("--exam-key", required=True, help="考试标识")
    item_parser.add_argument(
        "--item-score",
        dest="item_score",
        type=float,
        default=exam_module.DEFAULT_ITEM_FULL_SCORE,
        help=f"小题默认满分（没在表里给 full_score 时用），默认 {exam_module.DEFAULT_ITEM_FULL_SCORE:g}",
    )
    item_parser.add_argument("--columns", default=None, help='表头映射，如 "题号=item_no"')
    item_parser.add_argument("--dry-run", action="store_true", help="只预览，不写库")

    analysis_parser = subparsers.add_parser(
        "exam-analysis", help="生成考试分析（逐题得分率/难度/区分度）"
    )
    add_global_options(analysis_parser, suppress_defaults=True)
    analysis_parser.add_argument("--exam-key", required=True, help="考试标识")

    handout_parser = subparsers.add_parser(
        "make-handout", help="生成讲评讲义（不含试卷原题，题目位置用自制示例题标记占位）"
    )
    add_global_options(handout_parser, suppress_defaults=True)
    handout_parser.add_argument("--exam-key", required=True, help="考试标识")

    serve_parser = subparsers.add_parser(
        "serve",
        help="启动本地只读 API（只绑 127.0.0.1）",
        description=(
            "启动本地只读 API，供看板前端与本地脚本查询：/api/meta、/api/classes、/api/exams、"
            "/api/exam/{exam_key}/stats、/api/class/{班名}/averages、/api/homework/stats、/api/alerts。"
            "只绑定 127.0.0.1；CORS 只回固定的本机 Origin，不用通配符；只支持 GET。"
        ),
    )
    add_global_options(serve_parser, suppress_defaults=True)
    serve_parser.add_argument(
        "--port",
        type=int,
        default=api_module.DEFAULT_PORT,
        help=f"端口，默认 {api_module.DEFAULT_PORT}",
    )

    dashboard_parser = subparsers.add_parser(
        "make-dashboard",
        help="生成可离线打开的教学看板（静态站，数据构建时嵌入）",
        description=(
            "从本地库生成 outputs/dashboard/：index.html + data.json。数据在构建时嵌进 HTML，"
            "双击 index.html 即可离线查看，不依赖 API、不引用任何 CDN；页面文字取自 labels。"
        ),
    )
    add_global_options(dashboard_parser, suppress_defaults=True)

    return parser


def load_config_for_cli(
    explicit_config: str | None = None,
    db_path: str | None = None,
) -> config_loader.AppConfig:
    """装载配置；`--db` 只覆盖数据库路径，其余照配置走。"""
    config = config_loader.load_config(
        config_loader.resolve_cli_config_path(explicit_config)
    )
    if not db_path:
        return config
    override = Path(os.path.abspath(Path(str(db_path)).expanduser()))
    return dataclasses.replace(
        config, paths=dataclasses.replace(config.paths, database=override)
    )


def run_init_db(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    if not args.demo:
        print(
            "[提示] init-db 目前只支持 --demo：请用 python3 hub.py init-db --demo 建演示库。",
            file=sys.stderr,
        )
        return 2

    summary = init_db.init_database(config, rebuild=args.rebuild, confirmed=args.yes)
    for path in summary["removed"]:
        print(f"已删除旧库文件：{path}")
    print(f"已建库：{summary['database']}（schema: {summary['schema_version']}）")
    if summary["migrations"]:
        print(f"已应用迁移：{'、'.join(summary['migrations'])}")
    print(
        f"已导入演示名单：{summary['classes']} 个虚构班级 / {summary['students']} 名学生"
        f"（数据集：{summary['dataset']}）"
    )
    homework_counts = summary["homework"]
    print(
        f"已导入演示作业：{homework_counts['assignments']} 份 / "
        f"{homework_counts['submissions']} 条提交 / {homework_counts['corrections']} 条订正"
    )
    error_counts = summary["errors"]
    print(
        f"已导入演示错因与行为：{error_counts['error_records']} 条错因 / "
        f"{error_counts['behavior_records']} 条行为记录"
    )
    return 0


def run_import_scores(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    if args.csv and args.excel:
        print("[提示] --csv 与 --excel 只能给一个。", file=sys.stderr)
        return 2

    if args.csv or args.excel:
        if not args.exam or not args.exam_date:
            print(
                "[提示] 导入成绩表时必须同时给 --exam 与 --exam-date："
                "考试按「名称 + 日期」匹配，日期不同就是另一场考试。",
                file=sys.stderr,
            )
            return 2
        if args.csv:
            result = importer.import_scores_from_csv(
                config,
                csv_path=args.csv,
                exam_name=args.exam,
                exam_date=args.exam_date,
                full_score=args.full_score,
                dry_run=args.dry_run,
                columns_spec=args.columns,
            )
        else:
            result = importer.import_scores_from_excel(
                config,
                xlsx_path=args.excel,
                exam_name=args.exam,
                exam_date=args.exam_date,
                full_score=args.full_score,
                dry_run=args.dry_run,
                columns_spec=args.columns,
                sheet=args.sheet,
            )
        plan = result["plan"]
        for warning in plan.warnings:
            print(f"[提醒] {warning}")
        if result["dry_run"]:
            state = "已存在" if plan.exam_exists else "将新建"
            students = len({item.student_id for item in plan.scores})
            print(
                f"[dry-run] 考试：{plan.exam_name}（{plan.exam_date}，{state}）；"
                f"待写入 {plan.row_count} 条成绩，涉及 {students} 名学生。"
            )
            print("[dry-run] 没有写入任何数据。")
            return 0

        print(
            f"已导入成绩：{result['scores_written']} 条"
            f"（考试：{plan.exam_name} {plan.exam_date}，"
            f"{'新建考试' if result['exams_created'] else '沿用已有考试'}）"
        )
        if result["full_score_updated"]:
            print(f"已按本次参数更新这场考试的满分：{plan.full_score:g}")
        return 0
    if not args.demo:
        print(
            "[提示] 请明确指定数据来源：--demo 导入虚构演示数据；"
            "真实成绩表用 --csv 或 --excel。",
            file=sys.stderr,
        )
        return 2

    counts = import_scores.import_demo_scores(config)
    print(
        f"已导入演示成绩：{counts['exams']} 场考试 / {counts['scores']} 条成绩"
        f"（数据库：{config.paths.database}）"
    )
    if counts.get("item_scores"):
        print(
            f"已导入演示小题：{counts['items']} 道 / {counts['item_scores']} 条小题得分"
        )
    return 0


def run_make_report(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    path = make_report.generate_report(config)
    print(f"已生成报告：{path}")
    print("提示：报告是 Markdown，可用任意编辑器或浏览器打开；数据不出本机。")
    return 0


def run_import_homework(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    result = homework_module.import_homework_from_csv(
        config,
        csv_path=args.csv,
        assign_key=args.assign_key,
        class_name=args.class_name,
        topic=args.topic,
        assigned_date=args.assigned_date,
        due_date=args.due_date,
        dry_run=args.dry_run,
        columns_spec=args.columns,
    )
    plan = result["plan"]
    for warning in plan.warnings:
        print(f"[提醒] {warning}")

    if result["dry_run"]:
        state = "已存在" if plan.assignment_exists else "将新建"
        corrections = sum(1 for item in plan.rows if item.has_correction)
        print(
            f"[dry-run] 作业：{plan.assign_key}（{plan.class_name} / {plan.topic}，{state}）；"
            f"待写入 {plan.row_count} 条提交记录，其中 {corrections} 条带订正。"
        )
        print("[dry-run] 没有写入任何数据。")
        return 0

    print(
        f"已导入作业提交：{result['submissions_written']} 条"
        f"（作业：{plan.assign_key} {plan.class_name}，"
        f"{'新建作业' if result['assignments_created'] else '沿用已有作业'}，"
        f"订正 {result['corrections']} 条）"
    )
    return 0


def run_homework_stats(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        stats = homework_module.homework_stats(conn, args.class_name)
    finally:
        conn.close()

    if not stats or all(item.assignment_count == 0 for item in stats):
        print("还没有作业数据：先跑 python3 hub.py import-homework …（演示数据可用 init-db --demo）。")
        return 0

    print(homework_module.format_stats_table(stats))
    return 0


def _today_for(config: config_loader.AppConfig) -> str:
    import teaching_calendar

    return teaching_calendar.TeachingCalendar.from_config(config).today().isoformat()


def run_record_error(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    result = errors_module.record_error(
        config,
        student_uid=args.student,
        tag_code=args.tag,
        exam_key=args.exam_key,
        assign_key=args.assign_key,
        note=args.note,
        recorded_at=args.recorded_on or _today_for(config),
        confirmed=args.yes,
    )
    plan = result["plan"]
    target = plan.exam_label or plan.assignment_label or "（未指定）"
    summary = (
        f"学生 {plan.student_name}（{plan.student_uid}）｜错因 {plan.tag_label}"
        f"（{plan.tag_code}）｜关联 {target}｜记录日期 {plan.recorded_at}"
    )
    if plan.note:
        summary += f"｜备注 {plan.note}"

    if not result["confirmed"]:
        print(f"[预览] {summary}")
        print("确认后请加 --yes 才会写入（写入是单事务，失败整体回滚）。")
        return 2

    print(f"已记错因：{summary}")
    return 0


def run_list_errors(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        rows = errors_module.list_errors(
            conn, student_uid=args.student, tag_code=args.tag
        )
    finally:
        conn.close()

    if not rows:
        print(f"{args.student} 还没有符合条件的错因记录。")
        return 0

    print("| 记录日期 | 错因 | 关联 | 备注 |")
    print("| --- | --- | --- | --- |")
    for row in rows:
        target = row.exam_label or row.assignment_label or "—"
        print(f"| {row.recorded_at} | {row.tag_label}（{row.tag_code}） | {target} | {row.note or '—'} |")
    return 0


def run_record_behavior(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    result = errors_module.record_behavior(
        config,
        student_uid=args.student,
        kind=args.kind,
        detail=args.detail,
        recorded_at=args.recorded_on or _today_for(config),
        confirmed=args.yes,
    )
    plan = result["plan"]
    summary = (
        f"学生 {plan.student_name}（{plan.student_uid}）｜行为 {plan.kind}"
        f"｜记录日期 {plan.recorded_at}"
    )
    if plan.detail:
        summary += f"｜细节 {plan.detail}"

    if not result["confirmed"]:
        print(f"[预览] {summary}")
        print("确认后请加 --yes 才会写入。")
        return 2

    print(f"已记行为：{summary}")
    return 0


def run_list_behavior(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        rows = errors_module.list_behavior(conn, student_uid=args.student)
    finally:
        conn.close()

    if not rows:
        print(f"{args.student} 还没有行为记录。")
        return 0

    print("| 记录日期 | 行为类型 | 细节 |")
    print("| --- | --- | --- |")
    for row in rows:
        print(f"| {row['recorded_at']} | {row['kind']} | {row['detail'] or '—'} |")
    return 0


def run_compute_profile(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    result = profiling.compute_profiles(
        config, student_uid=args.student, rebuild=args.rebuild
    )
    print(
        f"已计算 {result['computed']} 名学生的画像"
        f"（跳过 {result['skipped']} 名源数据未变化的，写入 {result['written']} 行，"
        f"computed_at={result['computed_at']} UTC）"
    )
    if result["profiles"]:
        print(profiling.format_profile_table(result["profiles"]))
    return 0


def run_scan_alerts(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    result = alerts_module.scan_alerts(config, dry_run=args.dry_run)
    candidates = result["candidates"]
    if not candidates:
        print("没有命中任何预警规则。")
    for candidate in candidates:
        print(
            f"[{candidate.severity}] {candidate.student_name}（{candidate.student_uid}，"
            f"{candidate.class_name}）｜{candidate.kind}｜{candidate.description}"
        )
    if result["dry_run"]:
        print(f"[dry-run] 命中 {len(candidates)} 条，没有写入任何数据。")
        return 0
    print(
        f"已扫描：命中 {len(candidates)} 条（新建 {result['created']}，刷新 {result['updated']}）；"
        f"当前未解决预警 {result['open_total']} 条。"
    )
    if result["stale"]:
        print(
            f"另有 {result['stale']} 条未解决预警的条件已经消失——不会自动关闭，"
            "确认处理完请用 resolve-alert 写下跟进说明后关闭。"
        )
    return 0


def run_list_alerts(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        rows = alerts_module.list_alerts(conn, status=args.status)
    finally:
        conn.close()

    if not rows:
        print("没有符合条件的预警。")
        return 0
    print(alerts_module.format_alert_table(rows))
    return 0


def run_resolve_alert(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    result = alerts_module.resolve_alert(
        config,
        alert_id=args.alert_id,
        note=args.note,
        outcome=args.outcome,
        recorded_by=args.recorded_by,
    )
    print(
        f"已解决预警 id={result['alert_id']}（resolved_at={result['resolved_at']} UTC），"
        f"跟进说明已记录：{result['note']}"
    )
    return 0


def run_alert_stats(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        stats = alerts_module.alert_stats(conn)
    finally:
        conn.close()

    if not stats["by_status"] and not stats["follow_ups"]:
        print("还没有预警数据：先跑 python3 hub.py scan-alerts。")
        return 0
    print(f"按状态：{stats['by_status'] or '（无）'}")
    print(f"按规则：{stats['by_kind'] or '（无）'}")
    print(f"未解决按级别：{stats['open_by_severity'] or '（无）'}")
    print(f"跟进记录：{stats['follow_ups']} 条")
    return 0


def run_list_follow_ups(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        rows = alerts_module.list_follow_ups(
            conn, alert_id=args.alert_id, student_uid=args.student_uid
        )
    finally:
        conn.close()

    if not rows:
        print("没有符合条件的跟进记录。")
        return 0
    print(alerts_module.format_follow_up_table(rows))
    return 0


def run_weekly_report(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        path = reports.write_weekly_report(
            config, conn, week_label=args.week, class_name=args.class_name
        )
    finally:
        conn.close()
    print(f"已生成周报：{path}")
    return 0


def run_phase_patrol(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        path = reports.write_phase_patrol(config, conn)
    finally:
        conn.close()
    print(f"已生成阶段巡检：{path}")
    return 0


def run_import_item_scores(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    result = exam_module.import_item_scores_from_csv(
        config,
        csv_path=args.csv,
        exam_key=args.exam_key,
        default_item_score=args.item_score,
        dry_run=args.dry_run,
        columns_spec=args.columns,
    )
    plan = result["plan"]
    for warning in plan.warnings:
        print(f"[提醒] {warning}")
    if result["dry_run"]:
        print(
            f"[dry-run] 考试：{plan.exam_name}（{plan.exam_key}）；"
            f"涉及 {len(plan.items)} 道小题、待写入 {plan.row_count} 条小题得分。"
        )
        print("[dry-run] 没有写入任何数据。")
        return 0
    print(
        f"已导入小题得分：{result['item_scores_written']} 条"
        f"（考试：{plan.exam_key}，{result['items_written']} 道小题）"
    )
    return 0


def run_exam_analysis(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        path = exam_module.write_exam_analysis(config, conn, exam_key=args.exam_key)
    finally:
        conn.close()
    print(f"已生成考试分析：{path}")
    return 0


def run_make_handout(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        path = exam_module.write_handout(config, conn, exam_key=args.exam_key)
    finally:
        conn.close()
    print(f"已生成讲评讲义：{path}")
    print(f"提示：讲义不含试卷原题，题目位置用 {exam_module.SAMPLE_QUESTION_MARKER} 标记占位。")
    return 0


def run_serve(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    api_module.run_server(config, port=args.port)
    return 0


def run_upgrade_db(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    summary = init_db.upgrade_database(config)
    if not summary["changed"]:
        print(
            f"数据库已经是最新 schema：{summary['database']}"
            f"（{summary['to']}），不需要升级。"
        )
        return 0
    if summary["migrations"]:
        print(f"已应用迁移：{'、'.join(summary['migrations'])}")
    print(
        f"已升级数据库：{summary['database']}"
        f"（{summary['from'] or '空库'} → {summary['to']}）；原有数据未改动。"
    )
    return 0


def run_import_questions(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    result = questions_module.import_questions(
        config,
        json_path=args.json,
        csv_path=args.csv,
        demo=args.demo,
        columns_spec=args.columns,
        dry_run=args.dry_run,
    )
    plan = result["plan"]
    qtypes: dict[str, int] = {}
    for question in plan.questions:
        qtypes[question.qtype] = qtypes.get(question.qtype, 0) + 1
    breakdown = "、".join(f"{name} {count}" for name, count in sorted(qtypes.items()))

    if result["dry_run"]:
        print(
            f"[dry-run] 来源：{plan.source}；待写入 {plan.row_count} 道题"
            f"（{breakdown}），题库现有 {plan.existing_count} 道。"
        )
        print("[dry-run] 没有写入任何数据。")
        return 0

    print(
        f"已导入题目：{result['imported']} 道（{breakdown}），"
        f"题库现有 {result['total']} 道。"
    )
    print("提示：题目内容是使用者数据，只存在本机数据库里；仓库不分发题库。")
    return 0


def run_make_dashboard(config: config_loader.AppConfig, args: argparse.Namespace) -> int:
    conn = db_module.connect(config.paths.database)
    try:
        db_module.require_schema(conn)
        path = dashboard_module.write_dashboard(config, conn)
    finally:
        conn.close()
    print(f"已生成看板：{path}")
    print("提示：双击 index.html 即可离线查看；数据在构建时嵌入，不依赖 API。")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config_for_cli(getattr(args, "config", None), getattr(args, "db", None))
    except config_loader.ConfigError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "init-db":
            return run_init_db(config, args)
        if args.command == "upgrade-db":
            return run_upgrade_db(config, args)
        if args.command == "import-questions":
            return run_import_questions(config, args)
        if args.command == "import-scores":
            return run_import_scores(config, args)
        if args.command == "make-report":
            return run_make_report(config, args)
        if args.command == "import-homework":
            return run_import_homework(config, args)
        if args.command == "homework-stats":
            return run_homework_stats(config, args)
        if args.command == "record-error":
            return run_record_error(config, args)
        if args.command == "list-errors":
            return run_list_errors(config, args)
        if args.command == "record-behavior":
            return run_record_behavior(config, args)
        if args.command == "list-behavior":
            return run_list_behavior(config, args)
        if args.command == "compute-profile":
            return run_compute_profile(config, args)
        if args.command == "scan-alerts":
            return run_scan_alerts(config, args)
        if args.command == "list-alerts":
            return run_list_alerts(config, args)
        if args.command == "resolve-alert":
            return run_resolve_alert(config, args)
        if args.command == "alert-stats":
            return run_alert_stats(config, args)
        if args.command == "list-follow-ups":
            return run_list_follow_ups(config, args)
        if args.command == "weekly-report":
            return run_weekly_report(config, args)
        if args.command == "phase-patrol":
            return run_phase_patrol(config, args)
        if args.command == "import-item-scores":
            return run_import_item_scores(config, args)
        if args.command == "exam-analysis":
            return run_exam_analysis(config, args)
        if args.command == "make-handout":
            return run_make_handout(config, args)
        if args.command == "serve":
            return run_serve(config, args)
        if args.command == "make-dashboard":
            return run_make_dashboard(config, args)
    except config_loader.ConfigError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    except LabelError as exc:
        # 运行期取文案缺 key：不要抛原始栈，按普通错误处理
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(
            f"[错误] 读写数据库失败：{exc}；"
            "文件可能不是有效的 SQLite 库或已经损坏，可以用 --db 指向别的库，"
            "或重建（init-db --demo --rebuild --yes）。",
            file=sys.stderr,
        )
        return 2

    print(f"[提示] 未知子命令：{args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
