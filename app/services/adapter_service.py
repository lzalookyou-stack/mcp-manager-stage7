"""适配器服务（阶段 6）。

把「适配器」接到插件条目上，提供**只读预览**：

- ``describe()``：列出已支持的类型与客户端格式（含证据等级与来源文档）；
- ``preview(plugin_id, ...)``：拉取该条目的文件清单 → 用对应适配器校验 →
  返回问题 / 提示 / 客户端配置片段。

**本服务不写任何文件**：客户端配置的写入必须走阶段 5 的安装闭环
（生成计划 → 用户确认 → 执行），从而复用同一套授权与快照机制。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from app.adapters import (
    AdapterError,
    AdapterReport,
    ClientProfile,
    get_adapter,
    get_profile,
    list_profiles,
)
from app.install.provider import FileProvider, ProviderError
from app.models import EvidenceLevel, Plugin


class AdapterService:
    """适配器预览（只读）。"""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        provider_factory: Callable[[Plugin], FileProvider],
    ) -> None:
        self._conn = conn
        self._provider_factory = provider_factory

    # ------------------------------------------------------------------ #
    def describe(self) -> dict[str, Any]:
        from app.adapters import all_adapters

        return {
            "adapters": [a.describe() for a in all_adapters()],
            "clients": [
                {
                    "key": p.key,
                    "display_name": p.display_name,
                    "config_path_hint": p.config_path_hint,
                    "servers_key": p.servers_key,
                    "evidence": p.evidence.value,
                    "writable": p.writable,
                    "source": p.source,
                    "checked_at": p.checked_at,
                    "notes": list(p.notes),
                }
                for p in list_profiles()
            ],
        }

    # ------------------------------------------------------------------ #
    def collect_files(self, plugin: Plugin) -> tuple[dict[str, bytes], list[str]]:
        """拉取该插件的文件内容；返回 ``(files, skipped)``。

        覆盖缺口（超限 / 读取失败）**如实返回**，不假装已全部读取。
        """
        provider = self._provider_factory(plugin)
        entries = provider.list_entries()
        files: dict[str, bytes] = {}
        skipped: list[str] = list(provider.skipped)
        for entry in entries:
            try:
                files[entry.path] = provider.read(entry.path)
            except ProviderError as exc:
                skipped.append(f"{entry.path}（读取失败：{exc}）")
        return files, skipped

    def preview(
        self,
        plugin: Plugin,
        *,
        kind: str | None = None,
        profile_key: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """用适配器校验该插件，并（可选）生成客户端配置片段。"""
        target_kind = kind or plugin.kind.value
        adapter = get_adapter(target_kind)

        files, skipped = self.collect_files(plugin)
        problems, notes = adapter.validate(plugin, files)
        detected = adapter.detect(files)

        report = AdapterReport(
            kind=target_kind,
            adapter=type(adapter).__name__,
            detected=detected,
            problems=tuple(problems),
            notes=tuple(notes),
            evidence=EvidenceLevel.INFERRED,
            extras={"skipped": skipped},
        )

        payload: dict[str, Any] = {
            "plugin_id": plugin.id,
            "declared_kind": plugin.kind.value,
            "kind": target_kind,
            "adapter": report.adapter,
            "detected": report.detected,
            "ok": report.ok,
            "problems": list(report.problems),
            "notes": list(report.notes),
            "skipped": skipped,
            "evidence": report.evidence.value,
        }

        if profile_key:
            profile: ClientProfile = get_profile(profile_key)
            payload["client"] = {
                "key": profile.key,
                "servers_key": profile.servers_key,
                "evidence": profile.evidence.value,
                "writable": profile.writable,
                "config_path_hint": profile.config_path_hint,
                "source": profile.source,
            }
            if command:
                snippet = adapter.client_config(
                    plugin, profile, command=command, args=args or []
                )
                payload["client_config"] = snippet
                payload["client_config_json"] = json.dumps(
                    snippet, ensure_ascii=False, indent=2
                )
            else:
                payload["client_config"] = None
                payload["client_config_note"] = (
                    "未提供启动命令，因此**不生成**配置片段"
                    "（本系统不猜测 command）。"
                )
        return payload


__all__ = ["AdapterService", "AdapterError"]