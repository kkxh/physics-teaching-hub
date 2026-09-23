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
import importer
import import_scores
import init_db
import make_report


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
