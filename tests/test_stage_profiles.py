"""学段 profile 与教学阶段的测试。"""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import stage_profiles  # noqa: E402
import teaching_calendar  # noqa: E402

SEMESTER_STARTS = date(2026, 9, 1)
SEMESTER_ENDS = date(2027, 1, 22)
SEMESTER = {
    "name": "2026-2027 学年第一学期",
    "starts_on": "2026-09-01",
    "ends_on": "2027-01-22",
}

# 一个月的短学期 + 三段自定义阶段，用来验证边界与校验规则。
SHORT_SEMESTER = {"starts_on": "2026-09-01", "ends_on": "2026-09-30"}
CUSTOM_PHASES = [
    {"name": "新授课", "starts_on": "2026-09-01", "ends_on": "2026-09-15"},
    {"name": "专题复习", "starts_on": "2026-09-16", "ends_on": "2026-09-24"},
    {"name": "考前冲刺", "starts_on": "2026-09-25", "ends_on": "2026-09-30"},
]


def short_semester_config(phases: Any = None) -> config_loader.AppConfig:
    raw: dict[str, Any] = {"semester": dict(SHORT_SEMESTER)}
    if phases is not None:
        raw["phases"] = phases
    return config_loader.parse_config(raw)


class ProfileDataTests(unittest.TestCase):
    def test_every_valid_stage_has_a_profile_with_a_label(self):
        for stage in config_loader.VALID_STAGES:
            with self.subTest(stage=stage):
                profile = stage_profiles.get_profile(stage)

                self.assertEqual(profile.stage, stage)
                self.assertEqual(profile.label, stage_profiles.STAGE_LABELS[stage])
                self.assertTrue(profile.label.strip())

    def test_phase_templates_are_usable(self):
        for stage in config_loader.VALID_STAGES:
            with self.subTest(stage=stage):
                templates = stage_profiles.get_profile(stage).phase_templates

                self.assertGreaterEqual(len(templates), 2)
                names = [template.name for template in templates]
                self.assertTrue(all(name.strip() for name in names))
                self.assertEqual(len(set(names)), len(names))
                # 只有最后一段允许是「一直到学期结束」。
                for template in templates[:-1]:
                    self.assertIsInstance(template.weeks, int)
                    self.assertGreater(template.weeks, 0)

    def test_default_phases_tile_the_semester(self):
        for stage in config_loader.VALID_STAGES:
            with self.subTest(stage=stage):
                phases = stage_profiles.default_phases(stage, SEMESTER_STARTS, SEMESTER_ENDS)

                self.assertGreaterEqual(len(phases), 2)
                self.assertEqual(phases[0].starts_on, SEMESTER_STARTS)
                self.assertEqual(phases[-1].ends_on, SEMESTER_ENDS)
                for phase in phases:
                    self.assertLessEqual(phase.starts_on, phase.ends_on)
                    self.assertGreaterEqual(phase.starts_on, SEMESTER_STARTS)
                    self.assertLessEqual(phase.ends_on, SEMESTER_ENDS)
                for previous, current in zip(phases, phases[1:]):
                    self.assertEqual(
                        current.starts_on - previous.ends_on, timedelta(days=1)
                    )

    def test_default_phases_differ_between_stages(self):
        high = [
            phase.name
            for phase in stage_profiles.default_phases(
                "high_school", SEMESTER_STARTS, SEMESTER_ENDS
            )
        ]
        middle = [
            phase.name
            for phase in stage_profiles.default_phases(
                "middle_school", SEMESTER_STARTS, SEMESTER_ENDS
            )
        ]

        self.assertNotEqual(high, middle)
        self.assertTrue(set(high) - set(middle))

    def test_short_semester_keeps_one_phase_covering_it(self):
        phases = stage_profiles.default_phases(
            "high_school", SEMESTER_STARTS, date(2026, 9, 10)
        )

        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0].name, "新授课")
        self.assertEqual(phases[0].starts_on, SEMESTER_STARTS)
        self.assertEqual(phases[0].ends_on, date(2026, 9, 10))

    def test_default_phases_leave_a_usable_last_phase(self):
        # 默认切分不能把尾段挤成两三天：每个阶段至少一整周。
        for stage in config_loader.VALID_STAGES:
            with self.subTest(stage=stage):
                phases = stage_profiles.default_phases(stage, SEMESTER_STARTS, SEMESTER_ENDS)

                for phase in phases:
                    length = (phase.ends_on - phase.starts_on).days + 1
                    self.assertGreaterEqual(length, 7, msg=f"{phase.name} 只有 {length} 天")

    def test_unknown_stage_lookup_is_rejected(self):
        with self.assertRaises(KeyError):
            stage_profiles.get_profile("kindergarten")


class ConfiguredPhaseTests(unittest.TestCase):
    def test_example_config_falls_back_to_stage_defaults(self):
        config = config_loader.load_config(ROOT / config_loader.EXAMPLE_CONFIG_PATH, env={})

        self.assertEqual(
            config.phases,
            stage_profiles.default_phases(
                "high_school", config.semester.starts_on, config.semester.ends_on
            ),
        )
        self.assertGreaterEqual(len(config.phases), 2)

    def test_config_phases_override_the_stage_defaults(self):
        config = short_semester_config(CUSTOM_PHASES)

        self.assertEqual(
            [phase.name for phase in config.phases], ["新授课", "专题复习", "考前冲刺"]
        )
        self.assertEqual(config.phases[0].starts_on, date(2026, 9, 1))
        self.assertEqual(config.phases[-1].ends_on, date(2026, 9, 30))

    def test_empty_phase_list_falls_back_to_defaults(self):
        config = short_semester_config([])

        self.assertEqual(
            config.phases,
            stage_profiles.default_phases("high_school", date(2026, 9, 1), date(2026, 9, 30)),
        )

    def test_middle_school_profile_is_used_when_configured(self):
        config = config_loader.parse_config(
            {
                "project": {"stage": "middle_school"},
                "semester": dict(SEMESTER),
            }
        )

        self.assertEqual(
            config.phases,
            stage_profiles.default_phases("middle_school", SEMESTER_STARTS, SEMESTER_ENDS),
        )
        self.assertIn("中考复习", [phase.name for phase in config.phases])

    def test_current_phase_covers_boundaries(self):
        calendar = teaching_calendar.TeachingCalendar.from_config(
            short_semester_config(CUSTOM_PHASES)
        )

        expected = {
            date(2026, 9, 1): "新授课",
            date(2026, 9, 15): "新授课",
            date(2026, 9, 16): "专题复习",
            date(2026, 9, 24): "专题复习",
            date(2026, 9, 25): "考前冲刺",
            date(2026, 9, 30): "考前冲刺",
        }
        for day, name in expected.items():
            with self.subTest(day=day):
                phase = calendar.current_phase(day)
                self.assertIsNotNone(phase)
                self.assertEqual(phase.name, name)

    def test_current_phase_outside_the_semester_is_none(self):
        calendar = teaching_calendar.TeachingCalendar.from_config(
            short_semester_config(CUSTOM_PHASES)
        )

        self.assertIsNone(calendar.current_phase(date(2026, 8, 31)))
        self.assertIsNone(calendar.current_phase(date(2026, 10, 1)))


class InvalidPhaseTests(unittest.TestCase):
    def assert_rejected(self, phases: Any, *needles: str) -> str:
        with self.assertRaises(config_loader.ConfigError) as ctx:
            short_semester_config(phases)
        message = str(ctx.exception)
        for needle in needles:
            self.assertIn(needle, message)
        return message

    def test_overlapping_phases_are_rejected(self):
        self.assert_rejected(
            [
                {"name": "新授课", "starts_on": "2026-09-01", "ends_on": "2026-09-20"},
                {"name": "专题复习", "starts_on": "2026-09-15", "ends_on": "2026-09-30"},
            ],
            "重叠",
            "新授课",
            "专题复习",
        )

    def test_gap_between_phases_is_rejected(self):
        self.assert_rejected(
            [
                {"name": "新授课", "starts_on": "2026-09-01", "ends_on": "2026-09-15"},
                {"name": "专题复习", "starts_on": "2026-09-17", "ends_on": "2026-09-30"},
            ],
            "缺口",
            "新授课",
            "专题复习",
        )

    def test_first_phase_must_start_with_the_semester(self):
        self.assert_rejected(
            [{"name": "新授课", "starts_on": "2026-09-02", "ends_on": "2026-09-30"}],
            "学期开头",
        )

    def test_last_phase_must_end_with_the_semester(self):
        self.assert_rejected(
            [{"name": "新授课", "starts_on": "2026-09-01", "ends_on": "2026-09-29"}],
            "学期结尾",
        )

    def test_phase_outside_the_semester_is_rejected(self):
        self.assert_rejected(
            [{"name": "新授课", "starts_on": "2026-08-31", "ends_on": "2026-09-30"}],
            "超出学期范围",
        )

    def test_phase_end_before_start_is_rejected(self):
        self.assert_rejected(
            [{"name": "新授课", "starts_on": "2026-09-10", "ends_on": "2026-09-05"}],
            "ends_on",
        )

    def test_blank_phase_name_is_rejected(self):
        self.assert_rejected(
            [{"name": "   ", "starts_on": "2026-09-01", "ends_on": "2026-09-30"}],
            "phases[0].name",
        )

    def test_duplicate_phase_names_are_rejected(self):
        self.assert_rejected(
            [
                {"name": "新授课", "starts_on": "2026-09-01", "ends_on": "2026-09-15"},
                {"name": "新授课", "starts_on": "2026-09-16", "ends_on": "2026-09-30"},
            ],
            "重名",
        )

    def test_out_of_order_phases_are_rejected(self):
        self.assert_rejected(
            [
                {"name": "专题复习", "starts_on": "2026-09-16", "ends_on": "2026-09-30"},
                {"name": "新授课", "starts_on": "2026-09-01", "ends_on": "2026-09-15"},
            ],
            "按时间顺序",
        )

    def test_missing_phase_date_is_rejected(self):
        self.assert_rejected(
            [
                {"name": "新授课", "starts_on": "2026-09-01", "ends_on": "2026-09-15"},
                {"name": "专题复习", "starts_on": "2026-09-16"},
            ],
            "phases[1].ends_on",
        )

    def test_phases_must_be_a_list_of_tables(self):
        self.assert_rejected(5, "表数组")
        self.assert_rejected(["新授课"], "phases[0]")


class DocumentationTests(unittest.TestCase):
    def test_readme_documents_the_phase_configuration(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("[[phases]]", readme)

    def test_example_config_shows_the_phase_override(self):
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")

        self.assertIn("[[phases]]", text)


if __name__ == "__main__":
    unittest.main()
