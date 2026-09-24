"""Phase 2 的统一命令行入口。

用法（全局选项放在子命令之前）：

    python3 hub.py init-db --demo [--rebuild --yes]
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

import config_loader
import db as db_module
import errors as errors_module
import homework as homework_module
import importer
import import_scores
import init_db
import make_report
import profiling


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
    except config_loader.ConfigError as exc:
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
