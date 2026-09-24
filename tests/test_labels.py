"""文案表测试：覆盖面、缺 key 行为、替换文案的效果，以及硬编码扫描。"""

from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import labels as labels_module  # noqa: E402
import seed_demo_data  # noqa: E402
import stage_profiles  # noqa: E402

SEMESTER = {"starts_on": "2026-09-01", "ends_on": "2027-01-22"}

# 文案表至少要覆盖这些 key：学科、学段、阶段、常用术语、演示数据文案。
REQUIRED_KEYS = (
    "project.default_name",
    "subjects.physics",
    "stages.high_school",
    "stages.middle_school",
    "phases.new_lesson",
    "phases.unit_review",
    "phases.first_review",
    "phases.second_review",
    "phases.final_sprint",
    "phases.exam_review",
    "terms.semester",
    "terms.teaching_week",
    "terms.homework",
    "terms.correction",
    "terms.error_tag",
    "demo.notice",
    "demo.student_name_prefix",
    "demo.homework_prefix",
)

CUSTOM_LOCALE = "test-zh"
CUSTOM_LABELS = """
[project]
default_name = "自定义教学中枢"

[subjects]
physics = "物理科"

[stages]
high_school = "高级中学"
middle_school = "初级中学"

[phases]
new_lesson = "新课"
unit_review = "单元复习课"
first_review = "一轮"
second_review = "二轮"
final_sprint = "冲刺"
exam_review = "升学复习"

[terms]
semester = "学期"
teaching_week = "教学周"
homework = "作业"
correction = "订正"
error_tag = "错因"
student = "学生"
class = "班级"

[demo]
notice = "这份演示数据全部虚构。"
student_name_prefix = "学员"
homework_prefix = "演示练习"
"""

# 这些词必须来自文案表，不能硬编码在代码里。
FORBIDDEN_WORDS = ("物理", "高中", "初中")
SCANNED_MODULES = (
    "config_loader.py",
    "db.py",
    "errors.py",
    "hub.py",
    "homework.py",
    "importer.py",
    "import_scores.py",
    "init_db.py",
    "labels.py",
    "make_report.py",
    "stage_profiles.py",
    "teaching_calendar.py",
    "seed_demo_data.py",
)


def string_literals(path: Path) -> list[tuple[int, str]]:
    """文件里的字符串字面量（含 f-string 的固定片段），跳过 docstring。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    docstrings: set[int] = set()
    doc_owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, doc_owners):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))

    literals: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                literals.append((node.lineno, node.value))
    return literals


def write_fixture_labels(directory: Path, locale: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{locale}.toml"
    path.write_text(text, encoding="utf-8")
    return path


class ShippedLabelsTests(unittest.TestCase):
    def test_zh_cn_covers_the_required_keys(self):
        labels = labels_module.load_labels()

        for key in REQUIRED_KEYS:
            with self.subTest(key=key):
                self.assertTrue(labels.get(key).strip())

    def test_every_value_is_a_non_empty_string(self):
        labels = labels_module.load_labels()

        self.assertGreater(len(labels.keys()), 10)
        for key in labels.keys():
            with self.subTest(key=key):
                value = labels.get(key)
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip())

    def test_missing_key_reports_the_file_and_the_key(self):
        labels = labels_module.load_labels()

        with self.assertRaises(labels_module.LabelError) as ctx:
            labels.get("terms.nothing_here")

        message = str(ctx.exception)
        self.assertIn(str(labels.path), message)
        self.assertIn("terms.nothing_here", message)

    def test_unknown_locale_reports_the_expected_path(self):
        with self.assertRaises(labels_module.LabelError) as ctx:
            labels_module.load_labels("en-US")

        message = str(ctx.exception)
        self.assertIn("en-US.toml", message)
        self.assertIn("locale", message)

    def test_non_string_value_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_fixture_labels(
                directory, CUSTOM_LOCALE, '[terms]\nsemester = 2026\n'
            )

            with self.assertRaises(labels_module.LabelError) as ctx:
                labels_module.load_labels(CUSTOM_LOCALE, directory)

        message = str(ctx.exception)
        self.assertIn("terms.semester", message)
        self.assertIn(CUSTOM_LOCALE, message)

    def test_blank_value_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_fixture_labels(directory, CUSTOM_LOCALE, '[terms]\nsemester = "   "\n')

            with self.assertRaises(labels_module.LabelError) as ctx:
                labels_module.load_labels(CUSTOM_LOCALE, directory)

        self.assertIn("terms.semester", str(ctx.exception))


class CustomLabelsTests(unittest.TestCase):
    def test_custom_labels_change_the_demo_wording(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_fixture_labels(directory, CUSTOM_LOCALE, CUSTOM_LABELS)
            config = config_loader.parse_config(
                {
                    "project": {"locale": CUSTOM_LOCALE, "labels_dir": str(directory)},
                    "semester": dict(SEMESTER),
                    "classes": {"names": ["初三(A)班"]},
                    "demo": {"students_per_class": 3},
                }
            )

            dataset = seed_demo_data.build_dataset(config)

            self.assertEqual(dataset["notice"], "这份演示数据全部虚构。")
            students = [s for k in dataset["classes"] for s in k["students"]]
            self.assertTrue(all(s["name"].startswith("学员") for s in students))
            self.assertTrue(
                all(item["topic"].startswith("演示练习") for item in dataset["homework"])
            )
            self.assertEqual(seed_demo_data.check_dataset(dataset, config), [])

    def test_custom_labels_change_the_config_wording(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_fixture_labels(directory, CUSTOM_LOCALE, CUSTOM_LABELS)

            config = config_loader.parse_config(
                {
                    "project": {"locale": CUSTOM_LOCALE, "labels_dir": str(directory)},
                    "semester": dict(SEMESTER),
                }
            )

            self.assertEqual(config.labels.locale, CUSTOM_LOCALE)
            self.assertEqual(config.project.name, "自定义教学中枢")
            self.assertEqual(config.project.stage_label, "高级中学")
            self.assertEqual(config.project.subject_label, "物理科")
            self.assertEqual(
                [phase.name for phase in config.phases][:2], ["新课", "一轮"]
            )

    def test_labels_dir_resolves_relative_to_the_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_fixture_labels(base / "my-labels", CUSTOM_LOCALE, CUSTOM_LABELS)
            (base / "config.toml").write_text(
                "\n".join(
                    (
                        "[project]",
                        f'locale = "{CUSTOM_LOCALE}"',
                        'labels_dir = "my-labels"',
                        "[semester]",
                        'starts_on = "2026-09-01"',
                        'ends_on = "2027-01-22"',
                    )
                ),
                encoding="utf-8",
            )

            config = config_loader.load_config(base / "config.toml", env={})

            self.assertEqual(config.project.name, "自定义教学中枢")
            self.assertEqual(config.labels.path, base / "my-labels" / f"{CUSTOM_LOCALE}.toml")

    def test_missing_labels_directory_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(config_loader.ConfigError) as ctx:
                config_loader.parse_config(
                    {
                        "project": {"labels_dir": str(Path(tmp) / "nowhere")},
                        "semester": dict(SEMESTER),
                    }
                )

        message = str(ctx.exception)
        self.assertIn("没有找到文案表", message)
        self.assertIn("zh-CN.toml", message)

    def test_missing_phase_key_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            text = "\n".join(
                line
                for line in CUSTOM_LABELS.splitlines()
                if not line.startswith("final_sprint")
            )
            write_fixture_labels(directory, CUSTOM_LOCALE, text)

            with self.assertRaises(config_loader.ConfigError) as ctx:
                config_loader.parse_config(
                    {
                        "project": {"locale": CUSTOM_LOCALE, "labels_dir": str(directory)},
                        "semester": dict(SEMESTER),
                    }
                )

        message = str(ctx.exception)
        self.assertIn("phases.final_sprint", message)
        self.assertIn(f"{CUSTOM_LOCALE}.toml", message)


class HardcodedWordingTests(unittest.TestCase):
    def test_scanned_modules_exist(self):
        for name in SCANNED_MODULES:
            with self.subTest(module=name):
                self.assertTrue((ROOT / name).is_file(), msg=f"{name} 不存在，扫描清单该更新了")

    def test_modules_do_not_hardcode_subject_or_stage_words(self):
        hits: list[str] = []
        for name in SCANNED_MODULES:
            for lineno, text in string_literals(ROOT / name):
                for word in FORBIDDEN_WORDS:
                    if word in text:
                        hits.append(f"{name}:{lineno} 出现「{word}」：{text!r}")

        self.assertEqual(
            hits,
            [],
            msg="学科与学段字样必须来自 labels/zh-CN.toml，不要写进代码：\n" + "\n".join(hits),
        )

    def test_scan_ignores_docstrings_but_catches_code_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.py"
            sample.write_text(
                '"""物理 高中 初中 都写在 docstring 里，应当被忽略。"""\n'
                'VALUE = "高中"\n',
                encoding="utf-8",
            )

            literals = [text for _lineno, text in string_literals(sample)]

        self.assertNotIn("物理 高中 初中 都写在 docstring 里，应当被忽略。", literals)
        self.assertIn("高中", literals)

    def test_stage_profiles_use_label_keys(self):
        for stage in config_loader.VALID_STAGES:
            with self.subTest(stage=stage):
                profile = stage_profiles.get_profile(stage)

                self.assertTrue(profile.label_key.startswith("stages."))
                for template in profile.phase_templates:
                    self.assertTrue(template.name_key.startswith("phases."))


if __name__ == "__main__":
    unittest.main()
