"""文案表：用户可见的教学文案集中在 labels/<locale>.toml，代码里只出现 key。

- 表里只放**面向使用者的教学文案**（学科名、学段名、阶段名、术语、演示数据提示语）；
  CLI 日志与报错信息不进这张表。
- 嵌套表会拍平成 `stages.high_school` 这样的 key；每一项都必须是字符串。
- 取文案统一走 `Labels.get()`：缺 key 时报错会带上文件路径与 key，不会悄悄回退成空字符串。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DEFAULT_LOCALE = "zh-CN"

# 仓库自带的文案表目录：跟着代码走，不跟随使用者的工作目录。
LABELS_DIR = Path(__file__).resolve().parent / "labels"


class LabelError(LookupError):
    """文案表不可用、格式不对或缺 key。"""


@dataclass(frozen=True)
class Labels:
    locale: str
    path: Path
    values: Mapping[str, str]

    def get(self, key: str, fallback: str | None = None) -> str:
        """取一条文案；没写 fallback 时缺 key 直接报错。"""
        try:
            return self.values[key]
        except KeyError as exc:
            if fallback is not None:
                return fallback
            raise LabelError(
                f"文案表 {self.path} 里没有 key：{key!r}。请补上这条文案，或检查拼写。"
            ) from exc

    def keys(self) -> tuple[str, ...]:
        return tuple(self.values)


def load_labels(
    locale: str = DEFAULT_LOCALE,
    labels_dir: str | Path | None = None,
) -> Labels:
    """读取 labels/<locale>.toml；labels_dir 省略时用仓库自带的那一份。"""
    directory = Path(labels_dir) if labels_dir is not None else LABELS_DIR
    path = directory / f"{locale}.toml"
    if not path.is_file():
        raise LabelError(
            f"没有找到文案表：{path}（locale={locale!r}）。"
            f"请检查 [project] locale，或把 {locale}.toml 放进 {directory}。"
        )

    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    values: dict[str, str] = {}
    try:
        _flatten(raw, "", values)
    except LabelError as exc:
        raise LabelError(f"文案表 {path} 有问题：{exc}") from exc
    return Labels(locale=locale, path=path, values=values)


def _flatten(raw: Mapping[str, Any], prefix: str, values: dict[str, str]) -> None:
    for key, value in raw.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            _flatten(value, dotted, values)
            continue
        if not isinstance(value, str):
            raise LabelError(
                f"{dotted} 不是字符串（{value!r}）；文案表的每一项都必须是字符串。"
            )
        text = value.strip()
        if not text:
            raise LabelError(f"{dotted} 是空的；请补上文案或删掉这一项。")
        values[dotted] = text
