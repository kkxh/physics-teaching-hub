"""教学周与教学日计算。

Phase 1 的约定（刻意保持简单，节假日与调休不在本阶段范围内）：

- 一周从周一开始；`schedule.starts_on` 所在的那一周是**第 1 教学周**；
- 学期范围（`semester.starts_on` / `semester.ends_on`）之外的日期不算教学周，返回 `None`；
- 学期已开始、但还没到课表起点所在的周时返回 0，表示「还没开课」；
- 上课日由 `schedule.weekdays` 决定（1=周一 … 7=周日）；
- 「今天」按 `project.timezone` 计算，不依赖机器本地时区。

所有判断都接收明确的日期参数，结果可复现；只有 `today()` / `now()` 读系统时钟。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config_loader import AppConfig, ScheduleConfig, SemesterConfig
from stage_profiles import Phase


@dataclass(frozen=True)
class TeachingCalendar:
    semester: SemesterConfig
    schedule: ScheduleConfig
    timezone: str
    phases: tuple[Phase, ...] = ()

    @classmethod
    def from_config(cls, config: AppConfig) -> "TeachingCalendar":
        return cls(
            semester=config.semester,
            schedule=config.schedule,
            timezone=config.project.timezone,
            phases=config.phases,
        )

    @property
    def first_week_start(self) -> date:
        """第 1 教学周的周一（课表起点可能落在这一周的中间）。"""
        return self.schedule.starts_on - timedelta(days=self.schedule.starts_on.isoweekday() - 1)

    def is_in_semester(self, day: date) -> bool:
        return self.semester.starts_on <= day <= self.semester.ends_on

    def week_of(self, day: date) -> int | None:
        """第 N 教学周；不在学期内返回 None，学期内但未开课返回 0。"""
        if not self.is_in_semester(day):
            return None
        index = (day - self.first_week_start).days // 7 + 1
        return max(index, 0)

    def is_teaching_day(self, day: date) -> bool:
        """这一天是否上课：在学期内、已开课，且是配置里的上课日。"""
        week = self.week_of(day)
        if week is None or week < 1:
            return False
        return day.isoweekday() in self.schedule.weekdays

    def current_phase(self, day: date) -> Phase | None:
        """这一天属于哪个教学阶段；不在学期内返回 None。

        阶段首尾相接并完整覆盖学期（见 config_loader 的校验），
        所以学期内的每一天最多命中一个阶段。
        """
        if not self.is_in_semester(day):
            return None
        for phase in self.phases:
            if phase.contains(day):
                return phase
        return None

    def weeks_total(self) -> int:
        """学期覆盖到第几教学周（含不足一周的尾周）。"""
        return (self.semester.ends_on - self.first_week_start).days // 7 + 1

    def today(self) -> date:
        return self.now().date()

    def now(self) -> datetime:
        """按时区取的当前时间；返回带时区的 datetime。"""
        return datetime.now(ZoneInfo(self.timezone))
