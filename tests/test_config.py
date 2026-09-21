"""配置加载的测试：示例配置、白名单环境变量与非法取值。"""

from __future__ import annotations

import sys
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
    "project": ("name", "stage", "subject", "timezone"),
    "paths": ("database", "output_dir"),
    "semester": ("name", "starts_on", "ends_on"),
    "classes": ("names",),
    "schedule": ("starts_on", "weekdays", "periods"),
}

# 环境变量白名单：改动它就是改公开契约，必须同时更新 README 与这里。
EXPECTED_ENV_NAMES = {
    "PHYSICS_TEACHING_CONFIG",
    "PHYSICS_TEACHING_STAGE",
    "PHYSICS_TEACHING_TIMEZONE",
    "PHYSICS_TEACHING_DATABASE",
    "PHYSICS_TEACHING_OUTPUT_DIR",
}


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
        config = config_loader.load_config(config_loader.EXAMPLE_CONFIG_PATH, env={})

        self.assertEqual(config.project.name, "物理教学中枢")
        self.assertEqual(config.project.stage, "high_school")
        self.assertEqual(config.project.stage_label, "高中")
        self.assertTrue(config.is_high_school)
        self.assertEqual(config.project.timezone, "Asia/Shanghai")
        self.assertEqual(len(config.class_names), 2)
        self.assertEqual(config.semester.starts_on, date(2026, 9, 1))
        self.assertLess(config.semester.starts_on, config.semester.ends_on)
        self.assertEqual(config.schedule.starts_on, config.semester.starts_on)
        self.assertEqual(config.schedule.weekdays, (1, 2, 3, 4, 5))
        self.assertTrue(config.schedule.periods)

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
            minimal_raw(project={"stage": "middle_school"}, classes={"names": ["初三(1)班"]})
        )

        self.assertEqual(config.project.stage_label, "初中")
        self.assertFalse(config.is_high_school)
        self.assertEqual(config.class_names, ("初三(1)班",))

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


class EnvOverrideTests(unittest.TestCase):
    def test_env_whitelist_is_exactly_the_documented_names(self):
        names = set(config_loader.ENV_OVERRIDES) | {config_loader.CONFIG_ENV_VAR}
        self.assertEqual(names, EXPECTED_ENV_NAMES)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in EXPECTED_ENV_NAMES:
            self.assertIn(name, readme, msg=f"README 没有写环境变量 {name}")

    def test_whitelisted_env_vars_override_file_values(self):
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
        )

        self.assertEqual(config.project.stage, "middle_school")
        self.assertEqual(config.project.timezone, "America/New_York")
        self.assertEqual(config.paths.database, Path("data/b.db"))
        self.assertEqual(config.paths.output_dir, Path("reports"))

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


if __name__ == "__main__":
    unittest.main()
