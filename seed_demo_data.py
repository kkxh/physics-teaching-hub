"""生成完全虚构的演示数据。

公开仓库里的一切演示、截图与测试都必须建立在虚构数据上。
真实课堂数据只存在于维护者的私有系统里，永不进入本仓库。

用法：
    python3 seed_demo_data.py                     # 按配置写入 [demo] output
    python3 seed_demo_data.py --config my.toml    # 指定配置文件
    python3 seed_demo_data.py --out other.json    # 写到别处（相对当前目录）
    python3 seed_demo_data.py --check             # 只做自检，不落盘（CI 用）

配置文件按「--config → config.toml → config.example.toml」的顺序找：没有 config.toml 时
用仓库自带的示例配置，保证 clone 下来就能跑。

班级、人数、种子、考试、作业主题与落盘位置都来自配置；数据集是确定性的——
同一份配置永远生成同一份内容，便于测试与截图复现。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

import config_loader

DATASET_VERSION = "demo.v4"

ERROR_TAGS = ("模型选择", "图像读取", "计算失误", "表达不规范", "概念混淆")

# 作业提交状态：与 schema/homework.sql 的 CHECK 约束一致
HOMEWORK_STATUSES = ("submitted", "late", "missing")


def fictional_name_pattern(prefix: str) -> re.Pattern[str]:
    """虚构姓名模式：前缀 + 两位序号，例如 `学生01`。"""
    return re.compile(rf"^{re.escape(prefix)}\d{{2}}$")


def student_name(index: int, prefix: str) -> str:
    """虚构姓名统一为「前缀+两位序号」，避免与任何真实姓名重合。"""
    return f"{prefix}{index:02d}"


def date_at_progress(starts_on: date, ends_on: date, progress: float) -> date:
    """按学期进度取日期：0 是学期第一天，1 是最后一天，结果一定落在学期内。"""
    span = (ends_on - starts_on).days
    offset = round(span * progress)
    return starts_on + timedelta(days=min(max(offset, 0), span))


def load_config_for_demo(
    explicit: str | Path | None = None,
    *,
    seed: int | None = None,
    output: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> config_loader.AppConfig:
    """读配置；--seed / --out 只覆盖演示数据相关的字段。"""
    config = config_loader.load_config(
        config_loader.resolve_cli_config_path(explicit, env), env=env
    )
    demo = config.demo
    if seed is not None:
        demo = dataclasses.replace(demo, seed=seed)
    if output is not None:
        # --out 是命令行参数，相对当前工作目录解析。
        demo = dataclasses.replace(
            demo, output=Path(os.path.abspath(Path(str(output)).expanduser()))
        )
    if demo is config.demo:
        return config
    return dataclasses.replace(config, demo=demo)


def build_dataset(config: config_loader.AppConfig) -> dict[str, Any]:
    """按配置构建演示数据集；同一份配置永远生成同一份内容。

    结构稳定，改动需升级 DATASET_VERSION。演示文案（提示语、姓名前缀、作业标题前缀）
    按配置里的文案表取。
    """
    labels = config.labels
    name_prefix = labels.get("demo.student_name_prefix")
    homework_prefix = labels.get("demo.homework_prefix")
    if not config.class_names:
        raise config_loader.ConfigError(
            "演示数据需要班级：请在配置文件的 [classes] names 里至少写一个虚构班名。"
        )

    rng = random.Random(config.demo.seed)

    classes: list[dict[str, Any]] = []
    roster: list[tuple[str, str]] = []
    for class_name in config.class_names:
        students = []
        for position in range(1, config.demo.students_per_class + 1):
            student = {
                "id": f"{class_name}-{position:02d}",
                "name": student_name(position, name_prefix),
                "seat_no": position,
            }
            students.append(student)
            roster.append((class_name, student["id"]))
        classes.append({"name": class_name, "students": students})

    exams: list[dict[str, Any]] = []
    for index, exam in enumerate(config.demo.exams, start=1):
        scores = []
        for _class_name, student_id in roster:
            base = rng.gauss(mu=72.0, sigma=13.0)
            score = max(0, min(exam.full_score, round(base)))
            scores.append({"student_id": student_id, "score": score})
        exams.append(
            {
                "key": f"demo-exam-{index}",
                "name": exam.name,
                "date": date_at_progress(
                    config.semester.starts_on, config.semester.ends_on, exam.progress
                ).isoformat(),
                "full_score": exam.full_score,
                "scores": scores,
            }
        )

    topics = config.demo.homework_topics
    starts_on = config.semester.starts_on
    ends_on = config.semester.ends_on
    homework: list[dict[str, Any]] = []
    for index, topic in enumerate(topics):
        class_name = config.class_names[index % len(config.class_names)]
        assigned_on = date_at_progress(starts_on, ends_on, (index + 1) / (len(topics) + 1))
        due_on = min(assigned_on + timedelta(days=2), ends_on)
        records = []
        for _cls, student_id in roster:
            if not student_id.startswith(class_name):
                continue
            status = rng.choices(
                HOMEWORK_STATUSES, weights=(82, 12, 6), k=1
            )[0]
            record: dict[str, Any] = {"student_id": student_id, "status": status}
            if status in ("submitted", "late"):
                handed_in = min(
                    assigned_on + timedelta(days=1 if status == "submitted" else 3),
                    ends_on,
                )
                record["submitted_on"] = handed_in.isoformat()
                if rng.random() < 0.45:
                    record["correction"] = {
                        "corrected_on": min(handed_in + timedelta(days=2), ends_on).isoformat(),
                        "note": f"{labels.get('terms.correction')}记录：{rng.choice(ERROR_TAGS)}",
                    }
            records.append(record)
        homework.append(
            {
                "assign_key": f"demo-hw-{index + 1:02d}",
                "class": class_name,
                "topic": f"{homework_prefix}：{topic}",
                "assigned_date": assigned_on.isoformat(),
                "due_date": due_on.isoformat(),
                "records": records,
            }
        )

    periods = config.schedule.periods or (None,)
    schedule = [
        {
            "class": class_name,
            "weekday": weekday,
            "period": periods[index % len(periods)],
        }
        for class_name in config.class_names
        for index, weekday in enumerate(config.schedule.weekdays)
    ]

    return {
        "version": DATASET_VERSION,
        "seed": config.demo.seed,
        "notice": labels.get("demo.notice"),
        "classes": classes,
        "exams": exams,
        "homework": homework,
        "schedule": schedule,
    }


def check_dataset(
    dataset: dict[str, Any],
    config: config_loader.AppConfig,
) -> list[str]:
    """返回问题列表；空列表表示通过自检。要给同一份 config 才能复核可复现性。"""
    labels = config.labels
    pattern = fictional_name_pattern(labels.get("demo.student_name_prefix"))
    semester = config.semester
    problems: list[str] = []

    if dataset.get("version") != DATASET_VERSION:
        problems.append(f"数据集版本不是 {DATASET_VERSION}")

    classes = dataset.get("classes") or []
    if len(classes) != len(config.class_names):
        problems.append(f"班级数量应为 {len(config.class_names)}，实际为 {len(classes)}")

    student_ids: list[str] = []
    for klass in classes:
        students = klass.get("students") or []
        if len(students) != config.demo.students_per_class:
            problems.append(
                f"{klass.get('name')} 的学生数应为 {config.demo.students_per_class}，"
                f"实际为 {len(students)}"
            )
        for student in students:
            student_ids.append(student["id"])
            if not pattern.match(student["name"]):
                problems.append(f"发现非虚构姓名：{student['name']}")

    for exam in dataset.get("exams") or []:
        if len(exam.get("scores") or []) != len(student_ids):
            problems.append(f"{exam.get('key')} 的成绩条数与总人数不一致")
        problems.extend(
            _date_problems(exam.get("date"), semester, f"{exam.get('name')} 的日期")
        )

    seen_assign_keys: set[str] = set()
    for item in dataset.get("homework") or []:
        label = f"作业 {item.get('assign_key') or item.get('topic') or '（无标识）'}"
        assign_key = str(item.get("assign_key") or "")
        if not assign_key:
            problems.append(f"{label} 缺少 assign_key")
        elif assign_key in seen_assign_keys:
            problems.append(f"作业标识重复：{assign_key}")
        else:
            seen_assign_keys.add(assign_key)

        if str(item.get("class") or "") not in config.class_names:
            problems.append(f"{label} 的班级不在配置里：{item.get('class')!r}")
        if not str(item.get("topic") or "").strip():
            problems.append(f"{label} 缺少主题")

        problems.extend(
            _date_problems(item.get("assigned_date"), semester, f"{label} 的布置日期")
        )
        problems.extend(_date_problems(item.get("due_date"), semester, f"{label} 的截止日期"))
        if (
            item.get("assigned_date")
            and item.get("due_date")
            and str(item["due_date"]) < str(item["assigned_date"])
        ):
            problems.append(f"{label} 的截止日期早于布置日期")

        for record in item.get("records") or []:
            status = record.get("status")
            if status not in HOMEWORK_STATUSES:
                problems.append(f"{label} 里有非法状态：{status!r}")
            if record.get("submitted_on"):
                problems.extend(
                    _date_problems(record["submitted_on"], semester, f"{label} 的提交日期")
                )
            correction = record.get("correction")
            if correction:
                if status not in ("submitted", "late"):
                    problems.append(f"{label} 里未交的记录不该有订正")
                elif not correction.get("corrected_on"):
                    problems.append(f"{label} 的订正缺少 corrected_on")

    if build_dataset(config) != dataset:
        problems.append("数据集不可复现：同一份配置生成了不同结果")

    return problems


def _date_problems(raw: Any, semester: config_loader.SemesterConfig, label: str) -> list[str]:
    if not raw:
        return [f"{label}缺失"]
    day = date.fromisoformat(str(raw))
    if not semester.starts_on <= day <= semester.ends_on:
        return [
            f"{label} {day.isoformat()} 落在学期 "
            f"{semester.starts_on.isoformat()} ~ {semester.ends_on.isoformat()} 之外"
        ]
    return []


def write_dataset(dataset: dict[str, Any], path: Path) -> Path:
    """落盘演示数据；父目录不存在就建。同一份数据集写出来逐字节一致。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def load_or_create_dataset(
    config: config_loader.AppConfig,
    *,
    regenerate: bool = False,
) -> dict[str, Any]:
    """读配置里指定的演示数据集；文件不存在（或要求重新生成）就先按配置生成再落盘。"""
    path = config.demo.output
    if regenerate or not path.is_file():
        dataset = build_dataset(config)
        write_dataset(dataset, path)
        return dataset
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise config_loader.ConfigError(
            f"演示数据集不是合法的 JSON：{path}（{exc}）；"
            "删掉它或重跑 seed_demo_data.py 重新生成。"
        ) from exc


def ensure_fictional_names(
    dataset: Mapping[str, Any],
    config: config_loader.AppConfig,
) -> None:
    """名单必须全是虚构姓名；Phase 1 不接受任何真实姓名。"""
    pattern = fictional_name_pattern(
        config.labels.get("demo.student_name_prefix")
    )
    bad = [
        str(student.get("name") or "")
        for klass in dataset.get("classes") or []
        for student in klass.get("students") or []
        if not pattern.match(str(student.get("name") or ""))
    ]
    if bad:
        shown = "、".join(bad[:3])
        more = f" 等 {len(bad)} 个" if len(bad) > 3 else ""
        raise config_loader.ConfigError(
            f"Phase 1 仅支持虚构演示数据：发现非虚构姓名 {shown}{more}；"
            "请用 seed_demo_data.py 生成的数据集。"
        )


def validate_dataset_for_import(
    dataset: Mapping[str, Any],
    config: config_loader.AppConfig,
) -> None:
    """导入前的把关：只接受虚构演示数据，而且必须与当前配置对得上。"""
    ensure_fictional_names(dataset, config)

    if dataset.get("version") != DATASET_VERSION:
        raise config_loader.ConfigError(
            f"演示数据集版本不是 {DATASET_VERSION}：{config.demo.output}；"
            "删掉它或重跑 seed_demo_data.py 重新生成。"
        )

    if build_dataset(config) != dataset:
        raise config_loader.ConfigError(
            f"演示数据集与当前配置不一致：{config.demo.output}；"
            "改过班级、人数或 seed 之后请重跑 seed_demo_data.py 重新生成。"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成虚构演示数据")
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件；省略时依次找 config.toml、config.example.toml",
    )
    parser.add_argument("--out", default=None, help="覆盖 [demo] output；相对当前目录")
    parser.add_argument("--seed", type=int, default=None, help="覆盖 [demo] seed")
    parser.add_argument("--check", action="store_true", help="只自检，不落盘")
    args = parser.parse_args(argv)

    try:
        config = load_config_for_demo(args.config, seed=args.seed, output=args.out)
        dataset = build_dataset(config)
    except config_loader.ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2

    problems = check_dataset(dataset, config)
    if problems:
        for problem in problems:
            print(f"[自检失败] {problem}", file=sys.stderr)
        return 1

    if args.check:
        students = sum(len(k["students"]) for k in dataset["classes"])
        print(
            f"演示数据自检通过：{len(dataset['classes'])} 个虚构班级 / {students} 名学生"
            f"（配置：{config_loader.resolve_cli_config_path(args.config)}）"
        )
        return 0

    output = write_dataset(dataset, config.demo.output)
    print(f"已写入演示数据：{output}（虚构内容，不入 Git）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
