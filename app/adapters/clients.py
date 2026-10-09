"""AI 客户端 MCP 配置格式注册表（阶段 6）。

**硬性纪律：不假设不同 AI 客户端格式一致。**
每个 profile 必须记录：

- 配置文件位置（提示）；
- 顶层键名（``mcpServers`` / ``servers`` …）；
- **证据等级**与**来源文档 URL**、核查时间。

只有证据等级为【已验证】（``EvidenceLevel.VERIFIED``）的 profile 允许写入
真实客户端配置目录；其余 profile 只能输出片段供人工核对。

本文件中的「已验证」条目均为**实际抓取官方文档正文核对**得到的结果，
核查记录见 ``docs/adapters.md``。
"""

from __future__ import annotations

from app.adapters.base import AdapterError, ClientProfile
from app.models import EvidenceLevel

# 核查时间（UTC，ISO-8601）
_CHECKED_AT = "2026-10-09T15:59Z"

_PROFILES: tuple[ClientProfile, ...] = (
    ClientProfile(
        key="claude_desktop",
        display_name="Claude Desktop",
        config_path_hint=(
            "macOS: ~/Library/Application Support/Claude/claude_desktop_config.json；"
            "Windows: %APPDATA%\\Claude\\claude_desktop_config.json"
        ),
        servers_key="mcpServers",
        evidence=EvidenceLevel.VERIFIED,
        source="https://modelcontextprotocol.io/quickstart/user",
        checked_at=_CHECKED_AT,
        notes=(
            "官方文档正文给出示例：{\"mcpServers\": {\"<名称>\": {\"command\": ..., \"args\": [...]}}}。",
            "修改后需完全退出并重启客户端才会加载新配置。",
        ),
    ),
    ClientProfile(
        key="vscode_workspace",
        display_name="VS Code（工作区，VS Code 格式）",
        config_path_hint="<项目>/.vscode/mcp.json",
        servers_key="servers",
        evidence=EvidenceLevel.VERIFIED,
        source="https://code.visualstudio.com/docs/agent-customization/mcp-servers",
        checked_at=_CHECKED_AT,
        notes=(
            "官方文档：.vscode/mcp.json 使用**顶层 servers 对象**；"
            "该位置已被官方标为 deprecated，仅作兼容保留。",
            "推荐改用可移植格式（见 vscode_portable）。",
        ),
    ),
    ClientProfile(
        key="vscode_portable",
        display_name="VS Code / Copilot（可移植格式）",
        config_path_hint="<项目>/.mcp.json",
        servers_key="mcpServers",
        evidence=EvidenceLevel.VERIFIED,
        source="https://code.visualstudio.com/docs/agent-customization/mcp-servers",
        checked_at=_CHECKED_AT,
        notes=(
            "官方文档：.mcp.json 使用**顶层 mcpServers 对象**，可跨兼容工具使用。",
            "同一文档亦指出 $COPILOT_HOME/mcp-config.json（默认 ~/.copilot/mcp-config.json）"
            "同样使用 mcpServers。",
        ),
    ),
    ClientProfile(
        key="copilot_user",
        display_name="Copilot 用户级配置",
        config_path_hint="$COPILOT_HOME/mcp-config.json（未设置时为 ~/.copilot/mcp-config.json）",
        servers_key="mcpServers",
        evidence=EvidenceLevel.VERIFIED,
        source="https://code.visualstudio.com/docs/agent-customization/mcp-servers",
        checked_at=_CHECKED_AT,
        notes=("官方文档明确该文件使用顶层 mcpServers 对象。",),
    ),
    ClientProfile(
        key="cursor_workspace",
        display_name="Cursor（工作区）",
        config_path_hint="<项目>/.cursor/mcp.json",
        servers_key="mcpServers",
        evidence=EvidenceLevel.UNCHECKED,
        source=None,
        checked_at=None,
        notes=(
            "**未检查**：尚未取得官方文档正文核对，因此**禁止**写入该位置。",
            "仅作为占位 profile 输出片段，供用户自行核对后再手工配置。",
        ),
    ),
)


def list_profiles() -> list[ClientProfile]:
    return list(_PROFILES)


def get_profile(key: str) -> ClientProfile:
    for profile in _PROFILES:
        if profile.key == key:
            return profile
    raise AdapterError(
        f"未知客户端格式：{key!r}；可用：{[p.key for p in _PROFILES]}"
    )


def verified_profiles() -> list[ClientProfile]:
    return [p for p in _PROFILES if p.writable]