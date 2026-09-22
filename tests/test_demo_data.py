"""演示数据生成器的测试：配置驱动、确定性、自检边界与命令行行为。"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import seed_demo_data  # noqa: E402

EXAMPLE_CONFIG = ROOT / config_loader.EXAMPLE_CONFIG_PATH
SEMESTER = {"starts_on": "2026-09-01", "ends_on": "2027-01-22"}

MIDDLE_SCHOOL_RAW: dict[str, Any] = {
    "project": {"stage": "middle_school"},
    "semester": dict(SEMESTER),
    "classes": {"names": ["初三(A)班", "初三(B)班"]},
    "demo": {"seed": 7, "students_per_class": 20},
    "schedule": {
        "starts_on": "2026-09-01",
        "weekdays": [1, 3, 5],
        "periods": ["第1节", "第2节"],
    },
}

TEMP_CONFIG = """
[project]
stage = "middle_school"

[semester]
starts_on = "2026-09-01"
ends_on = "2027-01-22"

[classes]
names = ["初三(A)班"]

[demo]
seed = 11
students_per_class = 6
output = "out/demo.json"
"""


@contextlib.contextmanager
def chdir(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextlib.contextmanager
def quiet():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def example_config() -> config_loader.AppConfig:
    return config_loader.load_config(EXAMPLE_CONFIG, env={})


def middle_school_config() -> config_loader.AppConfig:
    return config_loader.parse_config(MIDDLE_SCHOOL_RAW)


def students_of(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    return [student for klass in dataset["classes"] for student in klass["students"]]


def write_config(base: Path, text: str) -> Path:
    path = base / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


class DatasetTests(unittest.TestCase):
    def test_example_config_builds_a_valid_dataset(self):
        config = example_config()

        dataset = seed_demo_data.build_dataset(config)

        self.assertEqual(seed_demo_data.check_dataset(dataset, config), [])
        self.assertEqual(dataset["version"], seed_demo_data.DATASET_VERSION)
        self.assertEqual(len(dataset["classes"]), len(config.class_names))
        self.assertEqual(
            len(students_of(dataset)),
            len(config.class_names) * config.demo.students_per_class,
        )
        self.assertTrue(dataset["notice"])

    def test_dataset_is_reproducible(self):
        config = example_config()

        self.assertEqual(
            seed_demo_data.build_dataset(config), seed_demo_data.build_dataset(config)
        )

    def test_written_dataset_is_byte_for_byte_identical(self):
        config = example_config()

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = seed_demo_data.write_dataset(
                seed_demo_data.build_dataset(config), base / "a.json"
            )
            second = seed_demo_data.write_dataset(
                seed_demo_data.build_dataset(config), base / "b.json"
            )

            digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest(first), digest(second))

    def test_demo_settings_follow_the_config(self):
        config = middle_school_config()

        dataset = seed_demo_data.build_dataset(config)

        self.assertEqual(
            [klass["name"] for klass in dataset["classes"]], list(config.class_names)
        )
        self.assertEqual(len(students_of(dataset)), 2 * 20)
        self.assertEqual(dataset["seed"], 7)
        self.assertEqual(
            {item["class"] for item in dataset["schedule"]}, set(config.class_names)
        )
        self.assertEqual(
            {item["weekday"] for item in dataset["schedule"]},
            set(config.schedule.weekdays),
        )
        self.assertEqual(
            {item["period"] for item in dataset["schedule"]}, set(config.schedule.periods)
        )
        self.assertEqual(seed_demo_data.check_dataset(dataset, config), [])

    def test_seed_changes_scores_but_not_structure(self):
        config = example_config()
        other = dataclasses.replace(
            config,
            demo=dataclasses.replace(config.demo, seed=config.demo.seed + 1),
        )

        first = seed_demo_data.build_dataset(config)
        second = seed_demo_data.build_dataset(other)

        self.assertEqual(
            [klass["name"] for klass in first["classes"]],
            [klass["name"] for klass in second["classes"]],
        )
        self.assertNotEqual(
            [item["score"] for item in first["exams"][0]["scores"]],
            [item["score"] for item in second["exams"][0]["scores"]],
        )

    def test_demo_dates_stay_inside_the_semester(self):
        for config in (example_config(), middle_school_config()):
            with self.subTest(stage=config.project.stage):
                dataset = seed_demo_data.build_dataset(config)

                for exam in dataset["exams"]:
                    day = date.fromisoformat(exam["date"])
                    self.assertGreaterEqual(day, config.semester.starts_on)
                    self.assertLessEqual(day, config.semester.ends_on)

                homework_days = []
                for item in dataset["homework"]:
                    day = date.fromisoformat(item["date"])
                    self.assertGreaterEqual(day, config.semester.starts_on)
                    self.assertLessEqual(day, config.semester.ends_on)
                    homework_days.append(day)
                self.assertEqual(homework_days, sorted(homework_days))

    def test_configured_exams_change_names_dates_and_full_score(self):
        raw = dict(MIDDLE_SCHOOL_RAW)
        raw["demo"] = {
            "seed": 7,
            "students_per_class": 20,
            "exams": [{"name": "演示摸底考", "progress": 0.25, "full_score": 120}],
        }
        config = config_loader.parse_config(raw)

        dataset = seed_demo_data.build_dataset(config)

        self.assertEqual([exam["name"] for exam in dataset["exams"]], ["演示摸底考"])
        self.assertEqual(dataset["exams"][0]["full_score"], 120)
        self.assertEqual(
            dataset["exams"][0]["date"],
            seed_demo_data.date_at_progress(
                config.semester.starts_on, config.semester.ends_on, 0.25
            ).isoformat(),
        )
        self.assertEqual(seed_demo_data.check_dataset(dataset, config), [])


class CheckDatasetTests(unittest.TestCase):
    def test_missing_classes_are_reported(self):
        config = config_loader.parse_config({"semester": dict(SEMESTER)})

        with self.assertRaises(config_loader.ConfigError) as ctx:
            seed_demo_data.build_dataset(config)

        self.assertIn("classes", str(ctx.exception))

    def test_wrong_student_count_is_reported(self):
        config = example_config()
        dataset = seed_demo_data.build_dataset(config)
        dataset["classes"][0]["students"].pop()

        problems = seed_demo_data.check_dataset(dataset, config)

        self.assertTrue(any("学生数" in problem for problem in problems))

    def test_non_fictional_name_is_reported(self):
        config = example_config()
        dataset = seed_demo_data.build_dataset(config)
        dataset["classes"][0]["students"][0]["name"] = "张同学"

        problems = seed_demo_data.check_dataset(dataset, config)

        self.assertTrue(any("非虚构姓名" in problem for problem in problems))

    def test_date_outside_the_semester_is_reported(self):
        config = example_config()
        dataset = seed_demo_data.build_dataset(config)
        dataset["exams"][0]["date"] = "2026-01-01"

        problems = seed_demo_data.check_dataset(dataset, config)

        self.assertTrue(any("落在学期" in problem for problem in problems))

    def test_version_mismatch_is_reported(self):
        config = example_config()
        dataset = seed_demo_data.build_dataset(config)
        dataset["version"] = "demo.v0"

        problems = seed_demo_data.check_dataset(dataset, config)

        self.assertTrue(any("版本" in problem for problem in problems))


class CommandLineTests(unittest.TestCase):
    def test_check_mode_does_not_write_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = write_config(base, TEMP_CONFIG)

            with quiet():
                code = seed_demo_data.main(["--config", str(path), "--check"])

            self.assertEqual(code, 0)
            self.assertFalse((base / "out").exists())

    def test_default_output_follows_the_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = write_config(base, TEMP_CONFIG)

            with quiet():
                code = seed_demo_data.main(["--config", str(path)])

            written = base / "out" / "demo.json"
            self.assertEqual(code, 0)
            self.assertTrue(written.is_file())
            dataset = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(len(dataset["classes"]), 1)
            self.assertEqual(len(dataset["classes"][0]["students"]), 6)

    def test_out_and_seed_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = write_config(base, TEMP_CONFIG)
            out = base / "elsewhere" / "other.json"

            with quiet():
                code = seed_demo_data.main(
                    ["--config", str(path), "--out", str(out), "--seed", "99"]
                )

            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(out.read_text(encoding="utf-8"))["seed"], 99
            )
            self.assertFalse((base / "out").exists())

    def test_missing_config_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with quiet() as (_out, err):
                code = seed_demo_data.main(
                    ["--config", str(Path(tmp) / "nope.toml")]
                )

        self.assertEqual(code, 2)
        self.assertIn("配置错误", err.getvalue())

    def test_falls_back_to_the_example_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            with chdir(Path(tmp)):
                path = seed_demo_data.resolve_demo_config_path()

                with quiet():
                    code = seed_demo_data.main(["--check"])

        self.assertEqual(path, config_loader.EXAMPLE_CONFIG_FILE)
        self.assertEqual(code, 0)

    def test_config_toml_wins_over_the_example_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_config(base, TEMP_CONFIG)

            with chdir(base):
                path = seed_demo_data.resolve_demo_config_path()

        self.assertEqual(path, config_loader.DEFAULT_CONFIG_PATH)


if __name__ == "__main__":
    unittest.main()
