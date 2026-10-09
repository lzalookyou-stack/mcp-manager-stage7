"""MCP Server 适配器（阶段 6）。

MCP Server 是**可执行**类插件：本系统**不**自动安装其依赖、**不**自动运行它，
只做两件事：

1. 静态判定该仓库是否像一个 MCP Server（入口文件 / 依赖清单 / SDK 依赖）；
2. 生成**目标客户端格式**的配置片段（由 ``ClientProfile`` 决定键名与路径）。

安全立场：
- 生成的片段中**绝不包含**任何凭据；``env`` 只接受显式传入且已脱敏的键值；
- 只有【已验证】的客户端格式允许写入（``ClientProfile.assert_writable``）；
- 无法确定入口命令时**拒绝生成**片段，而不是猜一个 ``command``。
"""

from __future__ import annotations

from typing import Any, Mapping

from app.adapters.base import AdapterError, ClientProfile, PluginAdapter, _text
from app.models import EvidenceLevel, Plugin, PluginKind

# 常见 MCP Server 入口文件（小写比较）
ENTRY_FILENAMES = frozenset(
    {
        "server.py",
        "main.py",
        "server.js",
        "server.mjs",
        "index.js",
        "index.mjs",
        "index.ts",
        "server.ts",
    }
)
# 依赖清单
MANIFEST_FILENAMES = frozenset(
    {"pyproject.toml", "requirements.txt", "package.json", "cargo.toml", "go.mod"}
)
# 指示这是 MCP 实现的依赖/关键词
MCP_HINTS = ("modelcontextprotocol", "mcp.server", "mcp_server", "@mcp", "fastmcp")


class McpServerAdapter(PluginAdapter):
    kind = PluginKind.MCP_SERVER
    label = "MCP Server"

    def entry_candidates(self, files: Mapping[str, bytes]) -> list[str]:
        return [
            p
            for p in sorted(files)
            if p.rsplit("/", 1)[-1].lower() in ENTRY_FILENAMES
        ]

    def manifest_candidates(self, files: Mapping[str, bytes]) -> list[str]:
        return [
            p
            for p in sorted(files)
            if p.rsplit("/", 1)[-1].lower() in MANIFEST_FILENAMES
        ]

    def _mcp_hint_files(self, files: Mapping[str, bytes]) -> list[str]:
        hits: list[str] = []
        for path, raw in files.items():
            name = path.rsplit("/", 1)[-1].lower()
            if name not in MANIFEST_FILENAMES and name not in ENTRY_FILENAMES:
                continue
            text = raw.decode("utf-8", errors="replace").lower()
            if any(hint in text for hint in MCP_HINTS):
                hits.append(path)
        return sorted(hits)

    def detect(self, files: Mapping[str, bytes]) -> bool:
        if not self.entry_candidates(files):
            return False
        return bool(self.manifest_candidates(files) or self._mcp_hint_files(files))

    def validate(
        self, plugin: Plugin, files: Mapping[str, bytes]
    ) -> tuple[list[str], list[str]]:
        problems: list[str] = []
        notes: list[str] = []

        entries = self.entry_candidates(files)
        manifests = self.manifest_candidates(files)
        hints = self._mcp_hint_files(files)

        if not entries:
            problems.append("未找到可识别的入口文件（server.py / index.js 等）。")
        if not manifests:
            notes.append("未找到依赖清单：无法自动推断启动命令，需人工确认。")
        if not hints:
            notes.append(
                "在入口文件或依赖清单中**未发现** MCP 相关依赖（modelcontextprotocol 等）；"
                "该仓库可能不是 MCP Server，或依赖声明方式不在识别范围内。"
            )
        if len(entries) > 1:
            notes.append(
                "存在多个入口文件：" + "、".join(entries) + "；本系统不替用户选择。"
            )

        notes.append(
            "MCP Server 为**可执行**插件：本系统不会安装其依赖，也不会自动运行它；"
            "启动命令必须由用户确认后再写入客户端配置。"
        )
        return problems, notes

    def client_config(
        self,
        plugin: Plugin,
        profile: ClientProfile,
        *,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """生成该客户端格式的 MCP 配置片段。

        - 键名由 ``profile.servers_key`` 决定（不同客户端**不同**）；
        - ``command`` 必须显式给出，绝不猜测；
        - ``env`` 只允许显式传入（调用方负责确保不含凭据）。
        """
        if not isinstance(command, str) or not command.strip():
            raise AdapterError("必须显式提供启动命令，本系统不猜测 command。")
        name = plugin.slug or plugin.id
        entry: dict[str, Any] = {"command": command}
        if args:
            entry["args"] = list(args)
        if env:
            entry["env"] = dict(env)
        return {profile.servers_key: {name: entry}}

    def describe(self) -> dict[str, Any]:
        info = super().describe()
        info["evidence"] = EvidenceLevel.INFERRED.value
        info["requires"] = ["入口文件 + 依赖清单或 MCP 依赖声明"]
        info["executable"] = True
        return info