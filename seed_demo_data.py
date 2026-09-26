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

班级、人数、种子、考试、作业主题与落盘位置都来自配置；示例题集是仓库自带的
自制内容（`examples/questions_demo.json`）。数据集是确定性的——
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
import errors as errors_module

DATASET_VERSION = "demo.v7"

# 错因标签的显示名统一来自 errors.py 的内置词典（单一事实来源）
ERROR_TAGS = tuple(label for _code, label in errors_module.BUILTIN_ERROR_TAGS)

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
    item_count = 5
    for index, exam in enumerate(config.demo.exams, start=1):
        items = [
            {
                "item_no": f"{position}",
                "full_score": float(exam.full_score) / item_count,
                "scores": [],
            }
            for position in range(1, item_count + 1)
        ]
        scores = []
        for _class_name, student_id in roster:
            base = rng.gauss(mu=72.0, sigma=13.0)
            score = max(0, min(exam.full_score, round(base)))
            scores.append({"student_id": student_id, "score": score})
            # 把总分拆到小题：按随机权重分配、每题不超过满分，余数补到还有空位的题上，
            # 保证小题和等于总分（演示数据自洽，分析结果才对得上）
            weights = [rng.random() + 0.2 for _ in range(item_count)]
            total_weight = sum(weights)
            parts = [0] * item_count
            remaining = int(score)
            order = sorted(range(item_count), key=lambda pos: -weights[pos])
            for position in order:
                capacity = int(items[position]["full_score"])
                take = min(int(round(score * weights[position] / total_weight)), capacity)
                take = min(take, remaining)
                parts[position] = take
                remaining -= take
            for position in order:
                if remaining <= 0:
                    break
                capacity = int(items[position]["full_score"])
                add = min(capacity - parts[position], remaining)
                parts[position] += add
                remaining -= add
            for position, item in enumerate(items):
                item["scores"].append(
                    {"student_id": student_id, "score": parts[position]}
                )
        exams.append(
            {
                "key": f"demo-exam-{index}",
                "name": exam.name,
                "date": date_at_progress(
                    config.semester.starts_on, config.semester.ends_on, exam.progress
                ).isoformat(),
                "full_score": exam.full_score,
                "scores": scores,
                "items": items,
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

    # 错因记录：挂在第一份演示作业上（作业与考试二选一，作业先于考试入库）；
    # 行为记录：每个学生 0~2 条
    tag_codes = [code for code, _label in errors_module.BUILTIN_ERROR_TAGS]
    first_assign_key = homework[0]["assign_key"] if homework else None
    error_records: list[dict[str, Any]] = []
    behavior_records: list[dict[str, Any]] = []
    for index, (_class_name, student_id) in enumerate(roster):
        # 保证小名单也能看到演示记录：每 3 个学生至少挂 1 条错因
        if first_assign_key is not None and (index % 3 == 0 or rng.random() < 0.45):
            error_records.append(
                {
                    "student_id": student_id,
                    "assign_key": first_assign_key,
                    "tag": rng.choice(tag_codes),
                    "note": f"{labels.get('terms.error_tag')}记录",
                    "recorded_at": date_at_progress(
                        starts_on, ends_on, 0.5 + (index % 10) / 100
                    ).isoformat(),
                }
            )
        behavior_count = rng.randint(0, 2)
        if not behavior_records and behavior_count == 0:
            behavior_count = 1  # 至少留一条，方便演示 list-behavior
        for _ in range(behavior_count):
            behavior_records.append(
                {
                    "student_id": student_id,
                    "kind": rng.choice(errors_module.BEHAVIOR_KINDS),
                    "detail": "演示观测记录",
                    "recorded_at": date_at_progress(
                        starts_on, ends_on, 0.3 + (index % 20) / 100
                    ).isoformat(),
                }
            )

    return {
        "version": DATASET_VERSION,
        "seed": config.demo.seed,
        "notice": labels.get("demo.notice"),
        "classes": classes,
        "exams": exams,
        "homework": homework,
        "schedule": schedule,
        "error_records": error_records,
        "behavior_records": behavior_records,
        "questions": demo_question_items(),
    }


def demo_question_items() -> list[dict[str, Any]]:
    """演示数据集里的题目：直接复用仓库自带的示例题集。

    函数内 import：questions.py 的 `--demo` 入口要反过来读本模块的演示数据集，
    模块级互相 import 会成环。
    """
    import questions as questions_module

    return [questions_module.as_dict(item) for item in questions_module.load_example_questions()]


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

    problems.extend(_question_problems(dataset, config))

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

    roster_uids = set(student_ids)
    for exam in dataset.get("exams") or []:
        if len(exam.get("scores") or []) != len(student_ids):
            problems.append(f"{exam.get('key')} 的成绩条数与总人数不一致")
        problems.extend(
            _date_problems(exam.get("date"), semester, f"{exam.get('name')} 的日期")
        )

        seen_item_numbers: set[str] = set()
        item_score_count = len(exam.get("scores") or [])
        for item in exam.get("items") or []:
            item_no = str(item.get("item_no") or "")
            label = f"{exam.get('key')} 的小题 {item_no or '（无题号）'}"
            if not item_no:
                problems.append(f"{label} 缺少 item_no")
            elif item_no in seen_item_numbers:
                problems.append(f"{exam.get('key')} 的小题号重复：{item_no}")
            else:
                seen_item_numbers.add(item_no)

            full = item.get("full_score")
            if not isinstance(full, (int, float)) or full <= 0:
                problems.append(f"{label} 的满分不合法：{full!r}")
            item_scores = item.get("scores") or []
            if len(item_scores) != item_score_count:
                problems.append(
                    f"{label} 的得分条数（{len(item_scores)}）与总分数不一致"
                )
            for record in item_scores:
                if str(record.get("student_id") or "") not in roster_uids:
                    problems.append(f"{label} 里有不在名单里的学生")
                value = record.get("score")
                if value is None or value < 0:
                    problems.append(f"{label} 里有缺失或负数的小题得分")
                elif isinstance(full, (int, float)) and value > full:
                    problems.append(f"{label} 里有超过满分的小题得分：{value!r}")

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

    exam_keys = {str(item.get("key") or "") for item in dataset.get("exams") or []}
    allowed_tags = {code for code, _label in errors_module.BUILTIN_ERROR_TAGS} | set(
        config.error_tags
    )

    for item in dataset.get("error_records") or []:
        label = f"错因记录 {item.get('student_id')}/{item.get('tag')}"
        if str(item.get("student_id") or "") not in roster_uids:
            problems.append(f"{label} 的学生不在名单里")
        if str(item.get("tag") or "") not in allowed_tags:
            problems.append(f"{label} 的标签不认识：{item.get('tag')!r}")
        if not item.get("exam_key") and not item.get("assign_key"):
            problems.append(f"{label} 没有挂在考试或作业上")
        if item.get("exam_key") and str(item["exam_key"]) not in exam_keys:
            problems.append(f"{label} 的考试不存在：{item['exam_key']!r}")
        if item.get("assign_key") and str(item["assign_key"]) not in seen_assign_keys:
            problems.append(f"{label} 的作业不存在：{item['assign_key']!r}")
        problems.extend(_date_problems(item.get("recorded_at"), semester, f"{label} 的记录日期"))

    for item in dataset.get("behavior_records") or []:
        label = f"行为记录 {item.get('student_id')}/{item.get('kind')}"
        if str(item.get("student_id") or "") not in roster_uids:
            problems.append(f"{label} 的学生不在名单里")
        if str(item.get("kind") or "") not in errors_module.BEHAVIOR_KINDS:
            problems.append(f"{label} 的行为类型不认识：{item.get('kind')!r}")
        problems.extend(_date_problems(item.get("recorded_at"), semester, f"{label} 的记录日期"))

    if build_dataset(config) != dataset:
        problems.append("数据集不可复现：同一份配置生成了不同结果")

    return problems


def _question_problems(
    dataset: Mapping[str, Any],
    config: config_loader.AppConfig,
) -> list[str]:
    """示例题自检：数量、字段、题型/难度、标签与白名单。

    白名单为空时不比对白名单（使用者还没配题库），但标签本身必须非空。
    """
    import questions as questions_module

    raw_items = dataset.get("questions")
    if not isinstance(raw_items, list) or not raw_items:
        return ["数据集里没有示例题"]

    problems: list[str] = []
    if len(raw_items) > questions_module.MAX_EXAMPLE_QUESTIONS:
        problems.append(
            f"示例题最多 {questions_module.MAX_EXAMPLE_QUESTIONS} 道，实际 {len(raw_items)} 道"
        )

    allowed = set(config.question_bank.tags)
    seen_keys: set[str] = set()
    for index, raw in enumerate(raw_items, start=1):
        label = f"示例题第 {index} 道"
        if not isinstance(raw, Mapping):
            problems.append(f"{label} 不是键值对")
            continue

        key = str(raw.get("question_key") or "").strip()
        if not key:
            problems.append(f"{label} 缺少 question_key")
        elif key in seen_keys:
            problems.append(f"示例题 question_key 重复：{key}")
        else:
            seen_keys.add(key)

        if str(raw.get("qtype") or "").strip() not in questions_module.QTYPES:
            problems.append(f"{label} 的题型不合法：{raw.get('qtype')!r}")
        for field in ("stem", "answer"):
            if not str(raw.get(field) or "").strip():
                problems.append(f"{label} 的 {field} 为空")

        difficulty = raw.get("difficulty")
        if difficulty is not None and not (
            isinstance(difficulty, int) and 1 <= difficulty <= 5
        ):
            problems.append(f"{label} 的难度不合法：{difficulty!r}")

        tags = raw.get("tags")
        if not isinstance(tags, list) or not 1 <= len(tags) <= 3:
            problems.append(f"{label} 的标签应为 1-3 个：{tags!r}")
            continue
        for tag in tags:
            text = str(tag).strip()
            if not text:
                problems.append(f"{label} 有空标签")
            elif allowed and text not in allowed:
                problems.append(f"{label} 的标签 {text!r} 不在 [question_bank] tags 白名单里")
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
        questions = len(dataset.get("questions") or [])
        print(
            f"演示数据自检通过：{len(dataset['classes'])} 个虚构班级 / {students} 名学生"
            f" / {questions} 道自制示例题"
            f"（配置：{config_loader.resolve_cli_config_path(args.config)}）"
        )
        return 0

    output = write_dataset(dataset, config.demo.output)
    print(f"已写入演示数据：{output}（虚构内容，不入 Git）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
