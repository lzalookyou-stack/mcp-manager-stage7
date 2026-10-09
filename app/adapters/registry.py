"""适配器注册表（阶段 6）。

``get_adapter(kind)`` 返回该插件类型的适配器；未知类型**显式失败**，
绝不回退到「通用适配器」假装可用。
"""

from __future__ import annotations

from typing import Iterable

from app.adapters.base import AdapterError, PluginAdapter
from app.adapters.mcp_server import McpServerAdapter
from app.adapters.rules import RulesAdapter
from app.adapters.skill import SkillAdapter
from app.models import PluginKind

_ADAPTERS: tuple[PluginAdapter, ...] = (
    SkillAdapter(),
    RulesAdapter(),
    McpServerAdapter(),
)


def all_adapters() -> list[PluginAdapter]:
    return list(_ADAPTERS)


def get_adapter(kind: PluginKind | str) -> PluginAdapter:
    if isinstance(kind, str):
        try:
            kind = PluginKind(kind)
        except ValueError as exc:
            raise AdapterError(f"未知插件类型：{kind!r}") from exc
    for adapter in _ADAPTERS:
        if adapter.kind is kind:
            return adapter
    raise AdapterError(
        f"插件类型 {kind.value!r} 尚无适配器；"
        f"已支持：{[a.kind.value for a in _ADAPTERS]}。"
        "本系统不为未支持类型提供降级实现。"
    )


def supported_kinds() -> list[str]:
    return [a.kind.value for a in _ADAPTERS]


def detect_kind(files: Iterable[str] | dict[str, bytes]) -> list[str]:
    """返回该文件清单命中的适配器类型（可能为空 / 多个）。"""
    mapping = files if isinstance(files, dict) else {p: b"" for p in files}
    return [a.kind.value for a in _ADAPTERS if a.detect(mapping)]