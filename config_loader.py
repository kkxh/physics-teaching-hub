"""配置加载入口。

Phase 1 的核心目标：把「学段、学科、班级、学期、课表、路径、时区」从代码里
挪进配置，让同一套引擎既能服务初中（中考复习），也能服务高中（新授课与高考复习）。

本模块只读使用者本地的 config.toml；仓库里分发的是 config.example.toml 模板。
环境变量只认 ENV_OVERRIDES 里列出的白名单，其余 PHYSICS_TEACHING_* 一律忽略。
相对路径一律以配置文件所在目录为基准解析，结果与当前工作目录无关。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from labels import DEFAULT_LOCALE, LabelError, Labels, load_labels
from stage_profiles import VALID_STAGES, Phase, default_phases, get_profile

DEFAULT_CONFIG_PATH = Path("config.toml")
EXAMPLE_CONFIG_PATH = Path("config.example.toml")
# 仓库自带示例配置的绝对路径：跟着代码走，不跟随使用者的工作目录。
EXAMPLE_CONFIG_FILE = Path(__file__).resolve().parent / EXAMPLE_CONFIG_PATH
CONFIG_ENV_VAR = "PHYSICS_TEACHING_CONFIG"

DEFAULT_STAGE = "high_school"
DEFAULT_SUBJECT = "physics"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_WEEKDAYS: tuple[int, ...] = (1, 2, 3, 4, 5)

# 演示数据的缺省值：够跑通「建库 → 导入 → 报告」这条最小闭环即可。
DEFAULT_DEMO_SEED = 20260921
DEFAULT_STUDENTS_PER_CLASS = 30
DEFAULT_DEMO_OUTPUT = "demo/demo_dataset.json"
DEFAULT_HOMEWORK_TOPICS: tuple[str, ...] = (
    "运动学图像",
    "牛顿第二定律",
    "机械能守恒",
    "电路分析",
)

# 环境变量白名单：变量名 → 它覆盖的配置项（dotted key）。
# 只有这里列出的变量能参与配置；白名单之外的变量一律忽略，
# 这样「配置从哪来」始终可追踪，也不会因为机器上的杂散变量改变行为。
ENV_OVERRIDES: Mapping[str, str] = {
    "PHYSICS_TEACHING_STAGE": "project.stage",
    "PHYSICS_TEACHING_TIMEZONE": "project.timezone",
    "PHYSICS_TEACHING_DATABASE": "paths.database",
    "PHYSICS_TEACHING_OUTPUT_DIR": "paths.output_dir",
}


class ConfigError(ValueError):
    """配置缺失或不合法。"""


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    stage: str
    subject: str
    timezone: str
    locale: str = DEFAULT_LOCALE
    # 下面两个是显示名，解析时就按文案表取好：学段名走 profile 的 key，学科名走 subjects.<subject>。
    stage_label: str = ""
    subject_label: str = ""


@dataclass(frozen=True)
class PathsConfig:
    database: Path
    output_dir: Path


@dataclass(frozen=True)
class AppConfig:
    project: ProjectConfig
    paths: PathsConfig
    semester: SemesterConfig
    schedule: ScheduleConfig
    demo: DemoConfig
    class_names: tuple[str, ...]
    labels: Labels
    # 错因标签字典的扩展：代码 → 显示名（内置词典在 errors.py）
    error_tags: Mapping[str, str] = field(default_factory=dict)
    # 相对路径的解析基准：load_config 传配置文件所在目录，直接调用 parse_config 时为当前工作目录。
    base_dir: Path = Path(".")
    # 教学阶段：配置里写了 [[phases]] 就用配置的，否则用学段 profile 的默认阶段。
    phases: tuple[Phase, ...] = ()

    @property
    def is_high_school(self) -> bool:
        return self.project.stage == "high_school"


@dataclass(frozen=True)
class SemesterConfig:
    """学期范围：教学周计算与阶段划分都落在这个区间里。"""

    name: str
    starts_on: date
    ends_on: date


@dataclass(frozen=True)
class ScheduleConfig:
    """课表：起点（第 1 教学周所在周）、上课日与节次标签。"""

    starts_on: date
    weekdays: tuple[int, ...]
    periods: tuple[str, ...]


@dataclass(frozen=True)
class DemoExam:
    """演示考试：progress 是学期进度（0~1），用来把日期落在学期内。"""

    name: str
    progress: float
    full_score: int


@dataclass(frozen=True)
class DemoConfig:
    """演示数据生成器的参数；生成的 JSON 不入 Git。"""

    seed: int
    students_per_class: int
    output: Path
    exams: tuple[DemoExam, ...]
    homework_topics: tuple[str, ...]


DEFAULT_DEMO_EXAMS: tuple[DemoExam, ...] = (
    DemoExam(name="演示期中考试", progress=0.45, full_score=100),
    DemoExam(name="演示月考", progress=0.8, full_score=100),
)


def resolve_config_path(
    explicit: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """按「显式参数 → 环境变量 → 默认路径」的顺序确定配置文件位置。"""
    if explicit is not None:
        return Path(explicit)
    source = os.environ if env is None else env
    from_env = source.get(CONFIG_ENV_VAR)
    if from_env:
        return Path(from_env)
    return DEFAULT_CONFIG_PATH


def resolve_cli_config_path(
    explicit: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """命令行用哪份配置：--config → 环境变量 → 当前目录 config.toml → 仓库自带示例配置。

    最后一档是为了让全新 clone 不动配置就能跑通演示；示例配置跟着代码走，
    与当前工作目录无关。显式指定或设了环境变量时不做这个回退。
    """
    source = os.environ if env is None else env
    if explicit is None and not source.get(CONFIG_ENV_VAR):
        if not DEFAULT_CONFIG_PATH.is_file() and EXAMPLE_CONFIG_FILE.is_file():
            return EXAMPLE_CONFIG_FILE
    return resolve_config_path(explicit, source)


def load_config(
    explicit: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> AppConfig:
    """读取配置文件，并叠加白名单环境变量。"""
    source = os.environ if env is None else env
    path = resolve_config_path(explicit, source)
    if not path.exists():
        raise ConfigError(
            f"没有找到配置文件：{path}。请先把 {EXAMPLE_CONFIG_PATH.name} 复制为 {path.name}。"
        )
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return parse_config(raw, env=source, base_dir=path.parent)


def parse_config(
    raw: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
    base_dir: str | Path | None = None,
) -> AppConfig:
    """把已解析的 TOML 映射转成配置对象，并校验取值。

    传 env 时，先在内存里叠加白名单环境变量再校验；env 为 None 表示只看文件内容
    （排查配置问题时用这个模式，结果不受当前 shell 影响）。
    base_dir 是相对路径的解析基准；为 None 时取当前工作目录。绝对路径保持原样，
    以 ~ 开头的路径按使用者的家目录展开。
    缺省值向「可运行」倾斜；学期起止这类猜不出来的信息必须显式填写。
    """
    if env is not None:
        raw = _with_env_overrides(raw, env)

    base = Path(os.path.abspath(Path.cwd() if base_dir is None else base_dir))

    project_raw = raw.get("project") or {}
    paths_raw = raw.get("paths") or {}
    classes_raw = raw.get("classes") or {}
    semester_raw = raw.get("semester") or {}
    schedule_raw = raw.get("schedule") or {}
    demo_raw = raw.get("demo") or {}
    error_tags_raw = raw.get("error_tags") or {}

    locale = str(project_raw.get("locale") or DEFAULT_LOCALE).strip() or DEFAULT_LOCALE
    labels = _load_labels(project_raw.get("labels_dir"), base, locale)

    stage = _parse_stage(project_raw.get("stage"), labels)
    subject = str(project_raw.get("subject") or DEFAULT_SUBJECT).strip() or DEFAULT_SUBJECT

    project = ProjectConfig(
        name=str(project_raw.get("name") or "").strip() or labels.get("project.default_name"),
        stage=stage,
        subject=subject,
        timezone=_parse_timezone(project_raw.get("timezone")),
        locale=locale,
        stage_label=labels.get(get_profile(stage).label_key),
        subject_label=labels.get(f"subjects.{subject}", fallback=subject),
    )

    paths = PathsConfig(
        database=_resolve_path(paths_raw.get("database") or "data/physics_teaching.db", base),
        output_dir=_resolve_path(paths_raw.get("output_dir") or "outputs", base),
    )

    semester = _parse_semester(semester_raw)
    schedule = _parse_schedule(schedule_raw, semester)
    phases = _parse_phases(raw.get("phases"), semester, stage, labels)
    demo = _parse_demo(demo_raw, base)
    error_tags = _parse_error_tags(error_tags_raw)

    return AppConfig(
        project=project,
        paths=paths,
        semester=semester,
        schedule=schedule,
        demo=demo,
        class_names=_parse_class_names(classes_raw.get("names")),
        labels=labels,
        base_dir=base,
        phases=phases,
        error_tags=error_tags,
    )


def ensure_directories(config: AppConfig) -> tuple[Path, Path]:
    """建好数据库目录与输出目录（首次运行用），返回这两个目录。

    两个目录都在 .gitignore 的拒绝清单里；创建它们不会把生成物带进版本库。
    """
    database_dir = config.paths.database.parent
    database_dir.mkdir(parents=True, exist_ok=True)
    config.paths.output_dir.mkdir(parents=True, exist_ok=True)
    return database_dir, config.paths.output_dir


def _resolve_path(raw: Any, base_dir: Path) -> Path:
    """相对路径相对 base_dir 解析；绝对路径与 ~ 开头的路径按使用者写的来。"""
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path
    return Path(os.path.abspath(base_dir / path))


def _with_env_overrides(raw: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    """把白名单环境变量叠加到原始配置上；空白值视为「没设置」。"""
    merged: dict[str, Any] = dict(raw)
    for env_name, dotted_key in ENV_OVERRIDES.items():
        value = str(env.get(env_name) or "").strip()
        if not value:
            continue
        section, _, key = dotted_key.partition(".")
        section_raw = dict(merged.get(section) or {})
        section_raw[key] = value
        merged[section] = section_raw
    return merged


def _parse_stage(raw: Any, labels: Labels) -> str:
    stage = str(raw or DEFAULT_STAGE).strip()
    if stage not in VALID_STAGES:
        options = []
        for name in VALID_STAGES:
            options.append(f"{name}（{labels.get(f'stages.{name}', fallback=name)}）")
        raise ConfigError(
            f"配置项 project.stage 取值不对：{stage!r}；可选值为 {'、'.join(options)}。"
        )
    return stage


def _load_labels(raw_dir: Any, base_dir: Path, locale: str) -> Labels:
    """加载文案表：labels_dir 没写就用仓库自带的那一份，写了就按配置目录解析。"""
    if str(raw_dir or "").strip():
        directory: Path | None = _resolve_path(raw_dir, base_dir)
    else:
        directory = None
    try:
        return load_labels(locale=locale, labels_dir=directory)
    except LabelError as exc:
        raise ConfigError(str(exc)) from exc


def _parse_timezone(raw: Any) -> str:
    timezone = str(raw or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"配置项 project.timezone 不是可用的 IANA 时区名：{timezone!r}；"
            f"应填写如 {DEFAULT_TIMEZONE} 这样的名字。"
        ) from exc
    return timezone


def _parse_date(raw: Any, field: str) -> date:
    """接受 TOML 日期字面量（2026-09-01）与字符串（"2026-09-01"）两种写法。"""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw or "").strip()
    if not text:
        raise ConfigError(
            f"缺少配置项 {field}；应为 YYYY-MM-DD 格式的日期，如 2026-09-01。"
        )
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ConfigError(
            f"配置项 {field} 的日期无法解析：{text!r}；应为 YYYY-MM-DD 格式，如 2026-09-01。"
        ) from exc


def _parse_semester(raw: Mapping[str, Any]) -> SemesterConfig:
    starts_on = _parse_date(raw.get("starts_on"), "semester.starts_on")
    ends_on = _parse_date(raw.get("ends_on"), "semester.ends_on")
    if ends_on < starts_on:
        raise ConfigError(
            f"配置项 semester.ends_on（{ends_on.isoformat()}）早于 "
            f"semester.starts_on（{starts_on.isoformat()}）。"
        )
    name = str(raw.get("name") or "").strip() or _default_semester_name(starts_on, ends_on)
    return SemesterConfig(name=name, starts_on=starts_on, ends_on=ends_on)


def _default_semester_name(starts_on: date, ends_on: date) -> str:
    if starts_on.year == ends_on.year:
        return f"{starts_on.year} 学年学期"
    return f"{starts_on.year}-{ends_on.year} 学年学期"


def _parse_schedule(raw: Mapping[str, Any], semester: SemesterConfig) -> ScheduleConfig:
    starts_raw = raw.get("starts_on")
    starts_on = (
        _parse_date(starts_raw, "schedule.starts_on")
        if str(starts_raw or "").strip()
        else semester.starts_on
    )
    if starts_on > semester.ends_on:
        raise ConfigError(
            f"配置项 schedule.starts_on（{starts_on.isoformat()}）晚于 "
            f"semester.ends_on（{semester.ends_on.isoformat()}）；课表起点应落在学期内。"
        )
    return ScheduleConfig(
        starts_on=starts_on,
        weekdays=_parse_weekdays(raw.get("weekdays")),
        periods=_parse_periods(raw.get("periods")),
    )


def _parse_weekdays(raw: Any) -> tuple[int, ...]:
    if raw is None:
        return DEFAULT_WEEKDAYS
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ConfigError(
            "配置项 schedule.weekdays 应为整数数组，如 [1, 2, 3, 4, 5]"
            "（1=周一 … 7=周日）。"
        )
    values: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ConfigError(
                f"配置项 schedule.weekdays 只能填 1-7 的整数，收到的不是整数：{item!r}。"
            )
        if not 1 <= item <= 7:
            raise ConfigError(
                f"配置项 schedule.weekdays 超出范围：{item}；取值范围为 1-7（1=周一 … 7=周日）。"
            )
        values.append(item)
    if not values:
        raise ConfigError("配置项 schedule.weekdays 至少要有一个上课日。")
    if len(set(values)) != len(values):
        raise ConfigError(f"配置项 schedule.weekdays 不能重复：{values}。")
    return tuple(values)


def _parse_periods(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ConfigError(
            "配置项 schedule.periods 应为字符串数组，如 [\"第1节\", \"第2节\"]；"
            "留空表示由后续模块决定默认节次。"
        )
    periods: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            raise ConfigError("配置项 schedule.periods 里不能有空节次名。")
        periods.append(text)
    if len(set(periods)) != len(periods):
        raise ConfigError(f"配置项 schedule.periods 不能重复：{periods}。")
    return tuple(periods)


def _parse_class_names(raw: Any) -> tuple[str, ...]:
    names: list[str] = []
    for item in raw or ():
        name = str(item).strip()
        if not name:
            raise ConfigError("配置项 classes.names 里不能有空班名。")
        names.append(name)
    if len(set(names)) != len(names):
        raise ConfigError(f"配置项 classes.names 里有重复班名：{names}。")
    return tuple(names)


def _parse_phases(
    raw: Any,
    semester: SemesterConfig,
    stage: str,
    labels: Labels,
) -> tuple[Phase, ...]:
    """解析 [[phases]]；不写或写空数组时用学段 profile 的默认阶段。"""
    if raw is None or raw == []:
        try:
            return default_phases(stage, semester.starts_on, semester.ends_on, labels=labels)
        except LabelError as exc:
            raise ConfigError(str(exc)) from exc
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ConfigError(
            "配置项 phases 应为 [[phases]] 表数组，每张表至少写 name、starts_on、ends_on。"
        )

    phases: list[Phase] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ConfigError(f"配置项 phases[{index}] 应是一张 [[phases]] 表。")
        name = str(item.get("name") or "").strip()
        if not name:
            raise ConfigError(
                f"配置项 phases[{index}].name 不能为空；每个阶段都要有名字。"
            )
        phases.append(
            Phase(
                name=name,
                starts_on=_parse_date(item.get("starts_on"), f"phases[{index}].starts_on"),
                ends_on=_parse_date(item.get("ends_on"), f"phases[{index}].ends_on"),
            )
        )

    _validate_phases(phases, semester)
    return tuple(phases)


def _validate_phases(phases: list[Phase], semester: SemesterConfig) -> None:
    """阶段必须按时间排列、首尾相接、不重叠不留缺口，并完整覆盖学期。"""
    seen: dict[str, int] = {}
    for index, phase in enumerate(phases):
        if phase.name in seen:
            raise ConfigError(
                f"配置项 phases[{index}].name 与 phases[{seen[phase.name]}].name 重名："
                f"{phase.name!r}；阶段名必须唯一。"
            )
        seen[phase.name] = index
        if phase.ends_on < phase.starts_on:
            raise ConfigError(
                f"配置项 phases[{index}]（{phase.name}）的 ends_on"
                f"（{phase.ends_on.isoformat()}）早于 starts_on"
                f"（{phase.starts_on.isoformat()}）。"
            )
        if phase.starts_on < semester.starts_on or phase.ends_on > semester.ends_on:
            raise ConfigError(
                f"配置项 phases[{index}]（{phase.name}）超出学期范围"
                f"（{semester.starts_on.isoformat()} ~ {semester.ends_on.isoformat()}）。"
            )

    for index in range(1, len(phases)):
        previous, current = phases[index - 1], phases[index]
        if current.starts_on < previous.starts_on:
            raise ConfigError(
                f"配置项 phases 要按时间顺序排列：{current.name}"
                f"（{current.starts_on.isoformat()}）排在了 {previous.name}"
                f"（{previous.starts_on.isoformat()}）后面。"
            )
        if current.starts_on <= previous.ends_on:
            raise ConfigError(
                f"配置项 phases 有重叠：{previous.name} 到 "
                f"{previous.ends_on.isoformat()}，{current.name} 从 "
                f"{current.starts_on.isoformat()} 开始。"
            )
        if current.starts_on > previous.ends_on + timedelta(days=1):
            raise ConfigError(
                f"配置项 phases 之间有缺口：{previous.name} 结束于 "
                f"{previous.ends_on.isoformat()}，{current.name} 从 "
                f"{current.starts_on.isoformat()} 才开始。"
            )

    if phases[0].starts_on != semester.starts_on:
        raise ConfigError(
            f"配置项 phases 没有覆盖学期开头：第一个阶段 {phases[0].name} 从 "
            f"{phases[0].starts_on.isoformat()} 开始，而学期在 "
            f"{semester.starts_on.isoformat()} 就开始了。"
        )
    if phases[-1].ends_on != semester.ends_on:
        raise ConfigError(
            f"配置项 phases 没有覆盖学期结尾：最后一个阶段 {phases[-1].name} 结束于 "
            f"{phases[-1].ends_on.isoformat()}，而学期到 "
            f"{semester.ends_on.isoformat()} 才结束。"
        )


def _parse_demo(raw: Mapping[str, Any], base_dir: Path) -> DemoConfig:
    """解析 [demo]；不写就用缺省值，够跑通最小闭环。"""
    seed_raw = raw.get("seed", DEFAULT_DEMO_SEED)
    if isinstance(seed_raw, bool) or not isinstance(seed_raw, int):
        raise ConfigError(f"配置项 demo.seed 应为整数，收到的不是整数：{seed_raw!r}。")

    per_class_raw = raw.get("students_per_class", DEFAULT_STUDENTS_PER_CLASS)
    if isinstance(per_class_raw, bool) or not isinstance(per_class_raw, int):
        raise ConfigError(
            f"配置项 demo.students_per_class 应为正整数，收到的不是整数：{per_class_raw!r}。"
        )
    if per_class_raw <= 0:
        raise ConfigError(
            f"配置项 demo.students_per_class 必须大于 0，当前为 {per_class_raw}。"
        )

    return DemoConfig(
        seed=seed_raw,
        students_per_class=per_class_raw,
        output=_resolve_path(raw.get("output") or DEFAULT_DEMO_OUTPUT, base_dir),
        exams=_parse_demo_exams(raw.get("exams")),
        homework_topics=_parse_demo_topics(raw.get("homework_topics")),
    )


def _parse_demo_exams(raw: Any) -> tuple[DemoExam, ...]:
    if raw is None or raw == []:
        return DEFAULT_DEMO_EXAMS
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ConfigError(
            "配置项 demo.exams 应为 [[demo.exams]] 表数组，每张表写 name、progress、full_score。"
        )

    exams: list[DemoExam] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ConfigError(f"配置项 demo.exams[{index}] 应是一张 [[demo.exams]] 表。")

        name = str(item.get("name") or "").strip()
        if not name:
            raise ConfigError(f"配置项 demo.exams[{index}].name 不能为空。")

        progress_raw = item.get("progress", 0.5)
        if isinstance(progress_raw, bool) or not isinstance(progress_raw, (int, float)):
            raise ConfigError(
                f"配置项 demo.exams[{index}].progress 应为 0~1 的小数，收到：{progress_raw!r}。"
            )
        progress = float(progress_raw)
        if not 0.0 <= progress <= 1.0:
            raise ConfigError(
                f"配置项 demo.exams[{index}].progress 超出范围：{progress}；取值 0~1，"
                "表示这场考试在学期里的进度。"
            )

        full_score_raw = item.get("full_score", 100)
        if isinstance(full_score_raw, bool) or not isinstance(full_score_raw, int):
            raise ConfigError(
                f"配置项 demo.exams[{index}].full_score 应为正整数，收到：{full_score_raw!r}。"
            )
        if full_score_raw <= 0:
            raise ConfigError(
                f"配置项 demo.exams[{index}].full_score 必须大于 0，当前为 {full_score_raw}。"
            )

        exams.append(DemoExam(name=name, progress=progress, full_score=full_score_raw))

    return tuple(exams)


def _parse_demo_topics(raw: Any) -> tuple[str, ...]:
    if raw is None or raw == []:
        return DEFAULT_HOMEWORK_TOPICS
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ConfigError(
            "配置项 demo.homework_topics 应为字符串数组，例如 [\"运动学图像\", \"电路分析\"]。"
        )

    topics: list[str] = []
    for index, item in enumerate(raw):
        topic = str(item).strip()
        if not topic:
            raise ConfigError(f"配置项 demo.homework_topics[{index}] 不能为空。")
        topics.append(topic)
    return tuple(topics)


def _parse_error_tags(raw: Any) -> dict[str, str]:
    """解析 [error_tags]：代码 → 显示名；内置词典在 errors.py，这里只放扩展与覆盖。"""
    if not isinstance(raw, Mapping):
        raise ConfigError(
            '配置项 error_tags 应是一张表：[error_tags] 里每行写 代码 = "显示名"。'
        )

    tags: dict[str, str] = {}
    for code, label in raw.items():
        key = str(code).strip()
        text = str(label).strip()
        if not key or any(char.isspace() for char in key):
            raise ConfigError(f"配置项 error_tags 里的代码不合法：{code!r}；代码里不能有空格。")
        if not text:
            raise ConfigError(f"配置项 error_tags 里的 {key} 缺少显示名。")
        tags[key] = text
    return tags
