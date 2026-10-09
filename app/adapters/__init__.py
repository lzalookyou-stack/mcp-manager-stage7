"""插件适配器（阶段 6）。

- ``base``      抽象：``PluginAdapter`` / ``ClientProfile`` / ``AdapterReport``
- ``skill``     文件型 Skill
- ``rules``     文件型规则 / 指令
- ``mcp_server`` MCP Server（生成客户端配置片段）
- ``clients``   客户端格式注册表（含证据等级与来源文档）
- ``registry``  按插件类型取适配器
"""

from __future__ import annotations

from app.adapters.base import (
    AdapterError,
    AdapterReport,
    ClientProfile,
    PluginAdapter,
    RenderedFile,
)
from app.adapters.clients import (
    get_profile,
    list_profiles,
    verified_profiles,
)
from app.adapters.mcp_server import McpServerAdapter
from app.adapters.registry import (
    all_adapters,
    detect_kind,
    get_adapter,
    supported_kinds,
)
from app.adapters.rules import RulesAdapter
from app.adapters.skill import SkillAdapter

__all__ = [
    "AdapterError",
    "AdapterReport",
    "ClientProfile",
    "PluginAdapter",
    "RenderedFile",
    "SkillAdapter",
    "RulesAdapter",
    "McpServerAdapter",
    "get_adapter",
    "all_adapters",
    "supported_kinds",
    "detect_kind",
    "list_profiles",
    "get_profile",
    "verified_profiles",
]