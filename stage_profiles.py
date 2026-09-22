"""学段 profile：学段名与默认教学阶段。

「教学跑道」在不同学段不一样：初中是新授课 → 单元复习 → 中考复习，
高中会走新授课 → 一轮 → 二轮 → 考前冲刺。这里只定义**默认值**：
按学期起止切成首尾相接的若干段，最后一个阶段吸收余下的尾周；
使用者在 config.toml 里写 [[phases]] 就能整体覆盖（校验在 config_loader.py）。

默认日期用 7 天一段的简单切分，不处理节假日调休，也不与课表周次对齐；
这些默认值够用即可，真实作息由使用者用自己的 [[phases]] 覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping

ONE_WEEK = timedelta(days=7)


@dataclass(frozen=True)
class Phase:
    """一段教学阶段；日期是闭区间。"""

    name: str
    starts_on: date
    ends_on: date

    def contains(self, day: date) -> bool:
        return self.starts_on <= day <= self.ends_on


@dataclass(frozen=True)
class PhaseTemplate:
    """默认阶段的模板：名称 + 周数；weeks 为 None 表示「一直到学期结束」。"""

    name: str
    weeks: int | None


@dataclass(frozen=True)
class StageProfile:
    stage: str
    label: str
    phase_templates: tuple[PhaseTemplate, ...]


PROFILES: Mapping[str, StageProfile] = {
    "high_school": StageProfile(
        stage="high_school",
        label="高中",
        phase_templates=(
            PhaseTemplate("新授课", 9),
            PhaseTemplate("一轮复习", 5),
            PhaseTemplate("二轮专题", 3),
            PhaseTemplate("考前冲刺", None),
        ),
    ),
    "middle_school": StageProfile(
        stage="middle_school",
        label="初中",
        phase_templates=(
            PhaseTemplate("新授课", 9),
            PhaseTemplate("单元复习", 4),
            PhaseTemplate("中考复习", None),
        ),
    ),
}

VALID_STAGES: tuple[str, ...] = tuple(PROFILES)

STAGE_LABELS: Mapping[str, str] = {
    stage: profile.label for stage, profile in PROFILES.items()
}


def get_profile(stage: str) -> StageProfile:
    try:
        return PROFILES[stage]
    except KeyError as exc:
        raise KeyError(f"未知学段：{stage!r}；可选值为 {'、'.join(PROFILES)}。") from exc


def default_phases(stage: str, starts_on: date, ends_on: date) -> tuple[Phase, ...]:
    """按学段 profile 把学期切成首尾相接的阶段；最后一段到学期结束。"""
    phases: list[Phase] = []
    cursor = starts_on
    templates = get_profile(stage).phase_templates

    for index, template in enumerate(templates):
        if cursor > ends_on:
            break
        last_template = index == len(templates) - 1
        if template.weeks is None or last_template:
            phase_end = ends_on
        else:
            phase_end = min(cursor + ONE_WEEK * template.weeks - timedelta(days=1), ends_on)
        phases.append(Phase(name=template.name, starts_on=cursor, ends_on=phase_end))
        cursor = phase_end + timedelta(days=1)

    return tuple(phases)
