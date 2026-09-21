"""配置加载入口。

Phase 1 的核心目标：把「学段、学科、班级、路径、时区」从代码里挪进配置，
让同一套引擎既能服务初中（中考复习），也能服务高中（新授课与高考复习）。

本模块只读使用者本地的 config.toml；仓库里分发的是 config.example.toml 模板。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

VALID_STAGES: tuple[str, ...] = ("high_school", "middle_school")

STAGE_LABELS: dict[str, str] = {
    "high_school": "高中",
    "middle_school": "初中",
}

DEFAULT_CONFIG_PATH = Path("config.toml")
EXAMPLE_CONFIG_PATH = Path("config.example.toml")
CONFIG_ENV_VAR = "PHYSICS_TEACHING_CONFIG"


class ConfigError(ValueError):
    """配置缺失或不合法。"""


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    stage: str
    subject: str
    timezone: str

    @property
    def stage_label(self) -> str:
        """学段的中文名，供报告与界面文案使用。"""
        return STAGE_LABELS[self.stage]


@dataclass(frozen=True)
class PathsConfig:
    database: Path
    output_dir: Path


@dataclass(frozen=True)
class AppConfig:
    project: ProjectConfig
    paths: PathsConfig
    class_names: tuple[str, ...]

    @property
    def is_high_school(self) -> bool:
        return self.project.stage == "high_school"


def resolve_config_path(explicit: str | Path | None = None) -> Path:
    """按「显式参数 → 环境变量 → 默认路径」的顺序确定配置文件位置。"""
    if explicit is not None:
        return Path(explicit)
    from_env = os.environ.get(CONFIG_ENV_VAR)
    if from_env:
        return Path(from_env)
    return DEFAULT_CONFIG_PATH


def load_config(explicit: str | Path | None = None) -> AppConfig:
    path = resolve_config_path(explicit)
    if not path.exists():
        raise ConfigError(
            f"没有找到配置文件：{path}。请先把 {EXAMPLE_CONFIG_PATH.name} 复制为 {path.name}。"
        )
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return parse_config(raw)


def parse_config(raw: Mapping[str, Any]) -> AppConfig:
    """把已解析的 TOML 映射转成配置对象；缺省值向「可运行」倾斜。"""
    project_raw = raw.get("project") or {}
    paths_raw = raw.get("paths") or {}
    classes_raw = raw.get("classes") or {}

    stage = str(project_raw.get("stage") or "high_school")
    if stage not in VALID_STAGES:
        raise ConfigError(
            f"不支持的学段：{stage}；可选值为 {'、'.join(VALID_STAGES)}。"
        )

    project = ProjectConfig(
        name=str(project_raw.get("name") or "物理教学中枢"),
        stage=stage,
        subject=str(project_raw.get("subject") or "physics"),
        timezone=str(project_raw.get("timezone") or "Asia/Shanghai"),
    )

    paths = PathsConfig(
        database=Path(paths_raw.get("database") or "data/physics_teaching.db"),
        output_dir=Path(paths_raw.get("output_dir") or "outputs"),
    )

    class_names = tuple(str(item) for item in (classes_raw.get("names") or ()))

    return AppConfig(project=project, paths=paths, class_names=class_names)
