"""教学周与教学日计算的测试。"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import teaching_calendar  # noqa: E402

SEMESTER = {
    "name": "2026-2027 学年第一学期",
    "starts_on": "2026-09-01",
    "ends_on": "2027-01-22",
}


def calendar_for(**schedule: object) -> teaching_calendar.TeachingCalendar:
    """按给定课表配置造一个教学日历；不传就是学期起点 + 周一至周五。"""
    config = config_loader.parse_config({"semester": dict(SEMESTER), "schedule": dict(schedule)})
    return teaching_calendar.TeachingCalendar.from_config(config)


class WeekTests(unittest.TestCase):
    def test_first_teaching_week_starts_on_the_monday_of_the_schedule_start(self):
        calendar = calendar_for()

        # 2026-09-01 是周二，它所在那一周的周一是 08-31。
        self.assertEqual(calendar.first_week_start, date(2026, 8, 31))
        self.assertEqual(calendar.week_of(date(2026, 9, 1)), 1)
        self.assertEqual(calendar.week_of(date(2026, 9, 6)), 1)
        self.assertEqual(calendar.week_of(date(2026, 9, 7)), 2)

    def test_dates_outside_the_semester_have_no_week(self):
        calendar = calendar_for()

        self.assertIsNone(calendar.week_of(date(2026, 8, 31)))
        self.assertIsNone(calendar.week_of(date(2027, 1, 23)))
        self.assertTrue(calendar.is_in_semester(date(2026, 9, 1)))
        self.assertTrue(calendar.is_in_semester(date(2027, 1, 22)))
        self.assertFalse(calendar.is_in_semester(date(2027, 1, 23)))

    def test_days_before_the_timetable_starts_are_week_zero(self):
        late_start = config_loader.parse_config(
            {
                "semester": {"starts_on": "2026-08-24", "ends_on": "2027-01-22"},
                "schedule": {"starts_on": "2026-09-01"},
            }
        )
        calendar = teaching_calendar.TeachingCalendar.from_config(late_start)

        self.assertEqual(calendar.week_of(date(2026, 8, 24)), 0)
        self.assertEqual(calendar.week_of(date(2026, 8, 30)), 0)
        self.assertEqual(calendar.week_of(date(2026, 8, 31)), 1)

    def test_weeks_total_counts_a_partial_last_week(self):
        self.assertEqual(calendar_for().weeks_total(), 21)

        short = config_loader.parse_config(
            {
                "semester": {"starts_on": "2026-09-01", "ends_on": "2026-09-30"},
                "schedule": {"starts_on": "2026-09-14"},
            }
        )
        calendar = teaching_calendar.TeachingCalendar.from_config(short)

        self.assertEqual(calendar.week_of(date(2026, 9, 13)), 0)
        self.assertEqual(calendar.week_of(date(2026, 9, 14)), 1)
        self.assertEqual(calendar.week_of(date(2026, 9, 21)), 2)
        self.assertEqual(calendar.week_of(date(2026, 9, 30)), 3)
        self.assertEqual(calendar.weeks_total(), 3)


class TeachingDayTests(unittest.TestCase):
    def test_teaching_days_follow_the_configured_weekdays(self):
        calendar = calendar_for(weekdays=[1, 3, 5])

        self.assertFalse(calendar.is_teaching_day(date(2026, 9, 1)))  # 周二
        self.assertTrue(calendar.is_teaching_day(date(2026, 9, 2)))  # 周三
        self.assertTrue(calendar.is_teaching_day(date(2026, 9, 4)))  # 周五
        self.assertFalse(calendar.is_teaching_day(date(2026, 9, 5)))  # 周六

    def test_days_outside_the_semester_are_not_teaching_days(self):
        calendar = calendar_for(weekdays=[1])

        self.assertFalse(calendar.is_teaching_day(date(2026, 8, 31)))
        self.assertFalse(calendar.is_teaching_day(date(2027, 1, 25)))

    def test_days_before_the_timetable_starts_are_not_teaching_days(self):
        config = config_loader.parse_config(
            {
                "semester": {"starts_on": "2026-08-24", "ends_on": "2027-01-22"},
                "schedule": {"starts_on": "2026-09-01", "weekdays": [1]},
            }
        )
        calendar = teaching_calendar.TeachingCalendar.from_config(config)

        self.assertFalse(calendar.is_teaching_day(date(2026, 8, 24)))  # 学期内，但还没开课
        self.assertTrue(calendar.is_teaching_day(date(2026, 8, 31)))


class ClockTests(unittest.TestCase):
    def test_now_uses_the_configured_timezone(self):
        config = config_loader.parse_config(
            {
                "semester": dict(SEMESTER),
                "project": {"timezone": "America/New_York"},
            }
        )
        calendar = teaching_calendar.TeachingCalendar.from_config(config)

        now = calendar.now()

        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(getattr(now.tzinfo, "key", None), "America/New_York")
        self.assertEqual(calendar.today(), now.date())
        self.assertLessEqual(abs((calendar.today() - date.today()).days), 1)

    def test_timezone_comes_from_the_config(self):
        self.assertEqual(calendar_for().timezone, "Asia/Shanghai")


if __name__ == "__main__":
    unittest.main()
