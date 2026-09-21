"""生成完全虚构的演示数据。

公开仓库里的一切演示、截图与测试都必须建立在虚构数据上。
真实课堂数据只存在于维护者的私有系统里，永不进入本仓库。

用法：
    python3 seed_demo_data.py                  # 写入 demo/demo_dataset.json
    python3 seed_demo_data.py --out other.json # 写到别处
    python3 seed_demo_data.py --check          # 只做自检，不落盘（CI 用）

数据集是确定性的：同一个 DEMO_SEED 永远生成同一份内容，便于测试与截图复现。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

DATASET_VERSION = "demo.v1"
DEMO_SEED = 20260921
DEFAULT_OUTPUT = Path("demo/demo_dataset.json")

CLASS_NAMES = ("高一(A)班", "高一(B)班")
STUDENTS_PER_CLASS = 30

EXAMS = (
    {"key": "demo-midterm", "name": "演示期中考试", "date": "2026-04-28", "full_score": 100},
    {"key": "demo-monthly", "name": "演示月考", "date": "2026-05-26", "full_score": 100},
)

HOMEWORK_TOPICS = (
    "演示作业：运动学图像",
    "演示作业：牛顿第二定律",
    "演示作业：机械能守恒",
    "演示作业：电路分析",
)

ERROR_TAGS = ("模型选择", "图像读取", "计算失误", "表达不规范", "概念混淆")

FICTIONAL_NAME_PATTERN = re.compile(r"^学生\d{2}$")


def student_name(index: int) -> str:
    """虚构姓名统一为「学生NN」，避免与任何真实姓名重合。"""
    return f"学生{index:02d}"


def build_dataset(seed: int = DEMO_SEED) -> dict[str, Any]:
    """构建演示数据集；结构稳定，改动需升级 DATASET_VERSION。"""
    rng = random.Random(seed)

    classes: list[dict[str, Any]] = []
    roster: list[tuple[str, str]] = []
    for class_name in CLASS_NAMES:
        students = []
        for position in range(1, STUDENTS_PER_CLASS + 1):
            student = {
                "id": f"{class_name}-{position:02d}",
                "name": student_name(position),
                "seat_no": position,
            }
            students.append(student)
            roster.append((class_name, student["id"]))
        classes.append({"name": class_name, "students": students})

    exams: list[dict[str, Any]] = []
    for exam in EXAMS:
        scores = []
        for _class_name, student_id in roster:
            base = rng.gauss(mu=72.0, sigma=13.0)
            score = max(0, min(exam["full_score"], round(base)))
            scores.append({"student_id": student_id, "score": score})
        exams.append({**exam, "scores": scores})

    homework: list[dict[str, Any]] = []
    for index, topic in enumerate(HOMEWORK_TOPICS):
        class_name = CLASS_NAMES[index % len(CLASS_NAMES)]
        records = []
        for _cls, student_id in roster:
            if not student_id.startswith(class_name):
                continue
            status = rng.choices(
                ("submitted", "late", "missing"), weights=(82, 12, 6), k=1
            )[0]
            record: dict[str, Any] = {"student_id": student_id, "status": status}
            if status == "submitted" and rng.random() < 0.45:
                record["error_tags"] = rng.sample(ERROR_TAGS, k=rng.randint(1, 2))
            records.append(record)
        homework.append(
            {
                "date": f"2026-05-{11 + index:02d}",
                "class": class_name,
                "topic": topic,
                "records": records,
            }
        )

    schedule = [
        {"class": class_name, "weekday": weekday, "period": period}
        for class_name in CLASS_NAMES
        for weekday, period in ((1, "第2节"), (3, "第1节"), (5, "第7节"))
    ]

    return {
        "version": DATASET_VERSION,
        "seed": seed,
        "notice": "本数据集全部为虚构内容，用于演示与测试，不来自任何真实课堂。",
        "classes": classes,
        "exams": exams,
        "homework": homework,
        "schedule": schedule,
    }


def check_dataset(dataset: dict[str, Any]) -> list[str]:
    """返回问题列表；空列表表示通过自检。"""
    problems: list[str] = []

    if dataset.get("version") != DATASET_VERSION:
        problems.append(f"数据集版本不是 {DATASET_VERSION}")

    classes = dataset.get("classes") or []
    if len(classes) != len(CLASS_NAMES):
        problems.append(f"班级数量应为 {len(CLASS_NAMES)}，实际为 {len(classes)}")

    student_ids: list[str] = []
    for klass in classes:
        students = klass.get("students") or []
        if len(students) != STUDENTS_PER_CLASS:
            problems.append(f"{klass.get('name')} 的学生数应为 {STUDENTS_PER_CLASS}")
        for student in students:
            student_ids.append(student["id"])
            if not FICTIONAL_NAME_PATTERN.match(student["name"]):
                problems.append(f"发现非虚构姓名：{student['name']}")

    for exam in dataset.get("exams") or []:
        if len(exam.get("scores") or []) != len(student_ids):
            problems.append(f"{exam.get('key')} 的成绩条数与总人数不一致")

    if build_dataset(dataset.get("seed", DEMO_SEED)) != dataset:
        problems.append("数据集不可复现：同一 seed 生成了不同结果")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成虚构演示数据")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="输出路径")
    parser.add_argument("--seed", type=int, default=DEMO_SEED, help="随机种子")
    parser.add_argument("--check", action="store_true", help="只自检，不落盘")
    args = parser.parse_args(argv)

    dataset = build_dataset(args.seed)
    problems = check_dataset(dataset)
    if problems:
        for problem in problems:
            print(f"[自检失败] {problem}", file=sys.stderr)
        return 1

    if args.check:
        students = sum(len(k["students"]) for k in dataset["classes"])
        print(f"演示数据自检通过：{len(dataset['classes'])} 个虚构班级 / {students} 名学生")
        return 0

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写入演示数据：{output}（虚构内容，不入 Git）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
