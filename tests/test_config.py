"""配置加载的测试：示例配置、白名单环境变量与非法取值。"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402

SEMESTER = {"starts_on": "2026-09-01", "ends_on": "2027-01-22"}

# config.example.toml 与 README「配置」小节必须同时覆盖的字段。
DOCUMENTED_FIELDS: dict[str, tuple[str, ...]] = {
    "project": ("name", "stage", "subject", "timezone", "locale"),
    "paths": ("database", "output_dir"),
    "semester": ("name", "starts_on", "ends_on"),
    "classes": ("names",),
    "schedule": ("starts_on", "weekdays", "periods"),
    "demo": ("seed", "students_per_class", "output"),
}

# 环境变量白名单：改动它就是改公开契约，必须同时更新 README 与这里。
EXPECTED_ENV_NAMES = {
    "PHYSICS_TEACHING_CONFIG",
    "PHYSICS_TEACHING_STAGE",
    "PHYSICS_TEACHING_TIMEZONE",
    "PHYSICS_TEACHING_DATABASE",
    "PHYSICS_TEACHING_OUTPUT_DIR",
}


@contextlib.contextmanager
def chdir(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def minimal_raw(**sections: Any) -> dict[str, Any]:
    """最小可用配置：只给猜不出来的必填项——学期起止。"""
    raw: dict[str, Any] = {"semester": dict(SEMESTER)}
    raw.update(sections)
    return raw


def section_body(text: str, section: str) -> str:
    """取出 TOML 里 [section] 到下一个 [ 之间的正文。"""
    start = text.index(f"[{section}]") + len(section) + 2
    rest = text[start:]
    end = rest.find("\n[")
    return rest if end == -1 else rest[:end]


class ExampleConfigTests(unittest.TestCase):
    def test_example_config_parses_with_expected_values(self):
        config = config_loader.load_config(ROOT / config_loader.EXAMPLE_CONFIG_PATH, env={})

        self.assertEqual(config.project.name, "物理教学中枢")
        self.assertEqual(config.project.stage, "high_school")
        self.assertEqual(config.project.stage_label, "高中")
        self.assertTrue(config.is_high_school)
        self.assertEqual(config.project.timezone, "Asia/Shanghai")
        self.assertEqual(config.project.locale, "zh-CN")
        self.assertEqual(config.project.subject_label, "物理")
        self.assertEqual(config.labels.locale, "zh-CN")
        self.assertEqual(len(config.class_names), 2)
        self.assertEqual(config.semester.starts_on, date(2026, 9, 1))
        self.assertLess(config.semester.starts_on, config.semester.ends_on)
        self.assertEqual(config.schedule.starts_on, config.semester.starts_on)
        self.assertEqual(config.schedule.weekdays, (1, 2, 3, 4, 5))
        self.assertTrue(config.schedule.periods)
        self.assertEqual(config.base_dir, ROOT)
        self.assertEqual(config.paths.database, ROOT / "data" / "physics_teaching.db")
        self.assertEqual(config.paths.output_dir, ROOT / "outputs")

    def test_example_config_covers_every_documented_field(self):
        text = config_loader.EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        for section, fields in DOCUMENTED_FIELDS.items():
            body = section_body(text, section)
            for field in fields:
                self.assertIn(
                    f"{field} =",
                    body,
                    msg=f"config.example.toml 的 [{section}] 缺少字段 {field}",
                )
                self.assertIn(
                    f"{section}.{field}",
                    readme,
                    msg=f"README 的「配置」小节没有写 {section}.{field}",
                )

    def test_missing_config_file_reports_actionable_error(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            config_loader.load_config("does-not-exist.toml", env={})

        self.assertIn("config.example.toml", str(ctx.exception))


class StageTests(unittest.TestCase):
    def test_middle_school_stage_is_supported(self):
        config = config_loader.parse_config(
            minimal_raw(project={"stage": "middle_school"}, classes={"names": ["初三(A)班"]})
        )

        self.assertEqual(config.project.stage_label, "初中")
        self.assertFalse(config.is_high_school)
        self.assertEqual(config.class_names, ("初三(A)班",))

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            config_loader.parse_config(minimal_raw(project={"stage": "kindergarten"}))

        message = str(ctx.exception)
        self.assertIn("project.stage", message)
        self.assertIn("kindergarten", message)


class SemesterTests(unittest.TestCase):
    def test_semester_dates_are_required(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            config_loader.parse_config({})

        self.assertIn("semester.starts_on", str(ctx.exception))

    def test_toml_date_literals_are_accepted(self):
        config = config_loader.parse_config(
            {
                "semester": {
                    "starts_on": date(2026, 9, 1),
                    "ends_on": date(2027, 1, 22),
                }
            }
        )

        self.assertEqual(config.semester.starts_on, date(2026, 9, 1))
        self.assertEqual(config.semester.ends_on, date(2027, 1, 22))

    def test_bad_date_format_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            config_loader.parse_config(
                {"semester": {"starts_on": "2026-13-01", "ends_on": "2027-01-22"}}
            )

        message = str(ctx.exception)
        self.assertIn("semester.starts_on", message)
        self.assertIn("2026-13-01", message)

    def test_ends_on_before_starts_on_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            config_loader.parse_config(
                {"semester": {"starts_on": "2027-01-22", "ends_on": "2026-09-01"}}
            )

        message = str(ctx.exception)
        self.assertIn("semester.ends_on", message)
        self.assertIn("semester.starts_on", message)

    def test_semester_name_defaults_from_dates(self):
        config = config_loader.parse_config(minimal_raw())

        self.assertEqual(config.semester.name, "2026-2027 学年学期")


class ScheduleTests(unittest.TestCase):
    def test_schedule_defaults_to_semester_start_and_weekdays(self):
        config = config_loader.parse_config(minimal_raw())

        self.assertEqual(config.schedule.starts_on, config.semester.starts_on)
        self.assertEqual(config.schedule.weekdays, (1, 2, 3, 4, 5))
        self.assertEqual(config.schedule.periods, ())

    def test_schedule_start_can_differ_from_semester_start(self):
        config = config_loader.parse_config(
            minimal_raw(
                semester={"starts_on": "2026-08-24", "ends_on": "2027-01-22"},
                schedule={"starts_on": "2026-09-01"},
            )
        )

        self.assertEqual(config.semester.starts_on, date(2026, 8, 24))
        self.assertEqual(config.schedule.starts_on, date(2026, 9, 1))

    def test_schedule_start_after_semester_end_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            config_loader.parse_config(minimal_raw(schedule={"starts_on": "2027-02-01"}))

        message = str(ctx.exception)
        self.assertIn("schedule.starts_on", message)
        self.assertIn("semester.ends_on", message)

    def test_weekday_out_of_range_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            config_loader.parse_config(minimal_raw(schedule={"weekdays": [1, 8]}))

        message = str(ctx.exception)
        self.assertIn("schedule.weekdays", message)
        self.assertIn("8", message)

    def test_weekday_type_and_emptiness_are_checked(self):
        for bad in ("12345", [], [1, 1], [True]):
            with self.subTest(weekdays=bad):
                with self.assertRaises(config_loader.ConfigError):
                    config_loader.parse_config(minimal_raw(schedule={"weekdays": bad}))

    def test_periods_are_parsed_and_validated(self):
        config = config_loader.parse_config(
            minimal_raw(schedule={"periods": ["第1节", "第2节"]})
        )
        self.assertEqual(config.schedule.periods, ("第1节", "第2节"))

        for bad in ("第1节", ["第1节", "第1节"], [""]):
            with self.subTest(periods=bad):
                with self.assertRaises(config_loader.ConfigError):
                    config_loader.parse_config(minimal_raw(schedule={"periods": bad}))


class ClassNameTests(unittest.TestCase):
    def test_blank_class_name_is_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            config_loader.parse_config(minimal_raw(classes={"names": ["高一(A)班", "  "]}))

        self.assertIn("classes.names", str(ctx.exception))

    def test_duplicate_class_names_are_rejected(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            config_loader.parse_config(
                minimal_raw(classes={"names": ["高一(A)班", "高一(A)班"]})
            )

        self.assertIn("classes.names", str(ctx.exception))

    def test_missing_classes_stay_empty(self):
        config = config_loader.parse_config(minimal_raw())

        self.assertEqual(config.class_names, ())


class DemoConfigTests(unittest.TestCase):
    def test_demo_defaults_are_usable(self):
        config = config_loader.parse_config(minimal_raw())

        self.assertEqual(config.demo.seed, config_loader.DEFAULT_DEMO_SEED)
        self.assertEqual(
            config.demo.students_per_class, config_loader.DEFAULT_STUDENTS_PER_CLASS
        )
        self.assertTrue(config.demo.exams)
        self.assertTrue(config.demo.homework_topics)

    def test_demo_settings_are_read_from_the_config(self):
        config = config_loader.parse_config(
            minimal_raw(
                demo={
                    "seed": 7,
                    "students_per_class": 20,
                    "output": "demo/other.json",
                    "exams": [{"name": "演示摸底考", "progress": 0.25, "full_score": 120}],
                    "homework_topics": ["运动学图像"],
                }
            ),
            base_dir=Path("/tmp/base"),
        )

        self.assertEqual(config.demo.seed, 7)
        self.assertEqual(config.demo.students_per_class, 20)
        self.assertEqual(config.demo.output, Path("/tmp/base/demo/other.json"))
        self.assertEqual(config.demo.exams[0].name, "演示摸底考")
        self.assertEqual(config.demo.exams[0].progress, 0.25)
        self.assertEqual(config.demo.exams[0].full_score, 120)
        self.assertEqual(config.demo.homework_topics, ("运动学图像",))

    def test_invalid_demo_values_are_rejected(self):
        cases = {
            "seed": ({"seed": "2026"}, "demo.seed"),
            "students_per_class 类型": ({"students_per_class": 2.5}, "students_per_class"),
            "students_per_class 取值": ({"students_per_class": 0}, "大于 0"),
            "exams 结构": ({"exams": ["演示考试"]}, "demo.exams[0]"),
            "exams 进度": ({"exams": [{"name": "演示考试", "progress": 1.5}]}, "progress"),
            "exams 满分": ({"exams": [{"name": "演示考试", "full_score": 0}]}, "full_score"),
            "homework_topics": ({"homework_topics": ["  "]}, "homework_topics[0]"),
        }

        for label, (demo, needle) in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(config_loader.ConfigError) as ctx:
                    config_loader.parse_config(minimal_raw(demo=demo))
                self.assertIn(needle, str(ctx.exception))


class EnvOverrideTests(unittest.TestCase):
    def test_env_whitelist_is_exactly_the_documented_names(self):
        names = set(config_loader.ENV_OVERRIDES) | {config_loader.CONFIG_ENV_VAR}
        self.assertEqual(names, EXPECTED_ENV_NAMES)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in EXPECTED_ENV_NAMES:
            self.assertIn(name, readme, msg=f"README 没有写环境变量 {name}")

    def test_whitelisted_env_vars_override_file_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = config_loader.parse_config(
                minimal_raw(
                    project={"stage": "high_school", "timezone": "Asia/Shanghai"},
                    paths={"database": "data/a.db", "output_dir": "outputs"},
                ),
                env={
                    "PHYSICS_TEACHING_STAGE": "middle_school",
                    "PHYSICS_TEACHING_TIMEZONE": "America/New_York",
                    "PHYSICS_TEACHING_DATABASE": "data/b.db",
                    "PHYSICS_TEACHING_OUTPUT_DIR": "reports",
                },
                base_dir=base,
            )

            self.assertEqual(config.project.stage, "middle_school")
            self.assertEqual(config.project.timezone, "America/New_York")
            self.assertEqual(config.paths.database, base / "data" / "b.db")
            self.assertEqual(config.paths.output_dir, base / "reports")

    def test_env_vars_outside_the_whitelist_are_ignored(self):
        baseline = config_loader.parse_config(minimal_raw(project={"stage": "middle_school"}))
        noisy: Mapping[str, str] = {
            "PHYSICS_TEACHING_FOO": "1",
            "PHYSICS_TEACHING_SEMESTER_STARTS_ON": "1999-01-01",
            "PHYSICS_TEACHING_CLASSES": "高三(Z)班",
            "PATH": "/usr/bin",
        }

        self.assertEqual(
            config_loader.parse_config(
                minimal_raw(project={"stage": "middle_school"}), env=noisy
            ),
            baseline,
        )

    def test_blank_env_values_do_not_override(self):
        config = config_loader.parse_config(
            minimal_raw(project={"stage": "middle_school"}),
            env={"PHYSICS_TEACHING_STAGE": "   "},
        )

        self.assertEqual(config.project.stage, "middle_school")

    def test_config_path_env_var_selects_the_file(self):
        env = {"PHYSICS_TEACHING_CONFIG": "from-env.toml"}

        self.assertEqual(
            config_loader.resolve_config_path("explicit.toml", env),
            Path("explicit.toml"),
        )
        self.assertEqual(config_loader.resolve_config_path(None, env), Path("from-env.toml"))
        self.assertEqual(
            config_loader.resolve_config_path(None, {}), config_loader.DEFAULT_CONFIG_PATH
        )

    def test_load_config_applies_env_overrides(self):
        config = config_loader.load_config(
            config_loader.EXAMPLE_CONFIG_PATH,
            env={"PHYSICS_TEACHING_TIMEZONE": "UTC", "PHYSICS_TEACHING_STAGE": "middle_school"},
        )

        self.assertEqual(config.project.timezone, "UTC")
        self.assertEqual(config.project.stage_label, "初中")
        self.assertEqual(len(config.class_names), 2)

    def test_invalid_env_values_are_rejected_like_file_values(self):
        with self.assertRaises(config_loader.ConfigError) as ctx:
            config_loader.parse_config(
                minimal_raw(), env={"PHYSICS_TEACHING_TIMEZONE": "Not/AZone"}
            )

        self.assertIn("project.timezone", str(ctx.exception))


class PathResolutionTests(unittest.TestCase):
    def test_relative_paths_resolve_against_the_config_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf_dir = Path(tmp) / "conf"
            conf_dir.mkdir()
            (conf_dir / "config.toml").write_text(
                "\n".join(
                    (
                        "[semester]",
                        'starts_on = "2026-09-01"',
                        'ends_on = "2027-01-22"',
                        "[paths]",
                        'database = "data/x.db"',
                        'output_dir = "out"',
                    )
                ),
                encoding="utf-8",
            )
            elsewhere = Path(tmp) / "elsewhere"
            elsewhere.mkdir()

            with chdir(elsewhere):
                config = config_loader.load_config(conf_dir / "config.toml", env={})

            self.assertEqual(config.base_dir, conf_dir)
            self.assertEqual(config.paths.database, conf_dir / "data" / "x.db")
            self.assertEqual(config.paths.output_dir, conf_dir / "out")

    def test_resolution_does_not_depend_on_the_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            conf = base / "config.toml"
            conf.write_text(
                "\n".join(
                    (
                        "[semester]",
                        'starts_on = "2026-09-01"',
                        'ends_on = "2027-01-22"',
                        "[paths]",
                        'database = "data/x.db"',
                    )
                ),
                encoding="utf-8",
            )

            with chdir(base):
                first = config_loader.load_config(conf, env={})
            with chdir(Path(tempfile.gettempdir())):
                second = config_loader.load_config(conf, env={})

            self.assertEqual(first, second)
            self.assertEqual(first.paths.database, base / "data" / "x.db")

    def test_absolute_paths_are_kept_as_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            absolute_db = base / "absolute" / "kept.db"

            config = config_loader.parse_config(
                minimal_raw(
                    paths={"database": str(absolute_db), "output_dir": str(base / "out")}
                ),
                base_dir=base / "conf",
            )

            self.assertEqual(config.paths.database, absolute_db)
            self.assertEqual(config.paths.output_dir, base / "out")

    def test_tilde_is_expanded_to_the_home_directory(self):
        config = config_loader.parse_config(
            minimal_raw(paths={"database": "~/kept.db", "output_dir": "~/out"})
        )

        self.assertEqual(config.paths.database, Path.home() / "kept.db")
        self.assertEqual(config.paths.output_dir, Path.home() / "out")

    def test_parse_config_defaults_base_dir_to_the_current_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            with chdir(base):
                config = config_loader.parse_config(minimal_raw())

            self.assertEqual(config.base_dir, base)
            self.assertEqual(config.paths.database, base / "data" / "physics_teaching.db")
            self.assertEqual(config.paths.output_dir, base / "outputs")

    def test_ensure_directories_creates_missing_parents(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = config_loader.parse_config(
                minimal_raw(paths={"database": "nested/db/x.db", "output_dir": "reports"}),
                base_dir=base,
            )
            self.assertFalse((base / "nested").exists())

            database_dir, output_dir = config_loader.ensure_directories(config)

            self.assertEqual(database_dir, base / "nested" / "db")
            self.assertEqual(output_dir, base / "reports")
            self.assertTrue(database_dir.is_dir())
            self.assertTrue(output_dir.is_dir())
            # 幂等：再调一次也不报错
            config_loader.ensure_directories(config)


if __name__ == "__main__":
    unittest.main()
