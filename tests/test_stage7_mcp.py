"""阶段 7 测试：MCP 集成。

覆盖：
- 工具清单完整（发现 / 检查 / 对比 / 计划 / 状态）；
- **刻意不提供** confirm / execute 工具（Agent 无法伪造授权）；
- 操作申请只生成**待确认**计划，且响应中不含任何令牌；
- 计划全文可读（含文件清单、命令、权限、审查免责声明）；
- 与网页共用同一 service 层（调用 MCP 工具后，网页侧可见同一状态）；
- 未配置 GitHub 客户端时 search_projects 显式失败，**不返回空列表**；
- 全流程**不产生任何文件写入**。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.adapters import supported_kinds
from app.config import load_settings
from app.install import Installer, InstallService, ManagedRoots
from app.install.provider import MemoryFileProvider
from app.mcp_server.server import build_server
from app.models import Plugin, PluginKind
from app.runtime import Runtime

SKILL_FILES = {
    "SKILL.md": (
        "---\nname: demo-skill\ndescription: 演示\n---\n\n# 用法\n"
    ).encode(),
}

# 阶段 7 要求暴露的工具
REQUIRED_TOOLS = {
    "search_projects",
    "inspect_project",
    "compare_projects",
    "get_install_plan",
    "list_installed",
    "inspect_plugin",
    "request_operation",
    "get_operation_status",
    "list_operation_history",
}

# 绝不存在的工具（Agent 不得确认 / 执行）
FORBIDDEN_TOOLS = {
    "confirm_operation",
    "confirm_install",
    "execute_operation",
    "execute_install",
    "install_plugin",
    "uninstall_plugin",
    "rollback_plugin",
}


@pytest.fixture()
def mcp_runtime(tmp_path: Path):
    s = load_settings(
        host="127.0.0.1",
        port=8765,
        db_path=tmp_path / "t.db",
        data_dir=tmp_path / "data",
    )
    rt = Runtime.create(s)
    roots = ManagedRoots.create(s.data_dir)
    installer = Installer(
        rt.conn, roots, provider_factory=lambda plugin: MemoryFileProvider(dict(SKILL_FILES))
    )
    rt.installs = InstallService(
        rt.conn, installer, plugin_lookup=rt.plugins.get, confirmation_ttl=600
    )
    yield rt
    rt.close()


@pytest.fixture()
def server(mcp_runtime):
    return build_server(mcp_runtime)


def tool_names(server) -> list[str]:
    return sorted(t.name for t in asyncio.run(server.list_tools()))


def call(server, name: str, args: dict | None = None) -> dict:
    result = asyncio.run(server.call_tool(name, args or {}))
    payload = getattr(result, "structured_content", None)
    if payload is not None:
        return payload
    text = result.content[0].text  # type: ignore[union-attr]
    return json.loads(text)


def seed(rt: Runtime, kind: PluginKind = PluginKind.SKILL, slug: str = "demo-skill"):
    return rt.plugins.upsert(
        Plugin.new(
            source=f"github:acme/{slug}",
            slug=slug,
            name=f"acme/{slug}",
            kind=kind,
            pinned_ref="a" * 40,
        ),
        actor="user",
    )


# --------------------------------------------------------------------------- #
# 工具清单
# --------------------------------------------------------------------------- #
def test_required_tools_present(server):
    names = set(tool_names(server))
    missing = REQUIRED_TOOLS - names
    assert not missing, f"缺少工具：{sorted(missing)}"


def test_agent_cannot_confirm_or_execute(server):
    """Agent 侧**没有**确认 / 执行工具：授权只能来自受信任网页。"""
    names = set(tool_names(server))
    assert not (FORBIDDEN_TOOLS & names), (
        f"不得暴露这些工具：{sorted(FORBIDDEN_TOOLS & names)}"
    )


def test_tools_have_descriptions(server):
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.description, f"{tool.name} 缺少描述"


# --------------------------------------------------------------------------- #
# 读取类工具
# --------------------------------------------------------------------------- #
def test_list_plugins_and_stats(server, mcp_runtime):
    seed(mcp_runtime)
    listed = call(server, "list_plugins", {"limit": 10})
    assert listed["ok"] is True and listed["count"] == 1
    stats = call(server, "get_stats")
    assert stats["stats"]["total"] == 1


def test_get_plugin_not_found(server):
    payload = call(server, "get_plugin", {"plugin_id": "nope"})
    assert payload["ok"] is False and payload["error"] == "not_found"


def test_list_installed_empty(server):
    payload = call(server, "list_installed")
    assert payload["ok"] is True and payload["count"] == 0


def test_inspect_plugin_reports_adapter(server, mcp_runtime):
    p = seed(mcp_runtime)
    payload = call(server, "inspect_plugin", {"plugin_id": p.id})
    assert payload["ok"] is True
    assert payload["kind"] == "skill"
    assert payload["adapter"]["adapter"] == "SkillAdapter"
    assert any(c["key"] == "claude_desktop" for c in payload["clients"])


def test_inspect_plugin_unsupported_kind(server, mcp_runtime):
    p = seed(mcp_runtime, PluginKind.AGENT_PLUGIN, slug="agent-x")
    payload = call(server, "inspect_plugin", {"plugin_id": p.id})
    assert payload["ok"] is False and payload["error"] == "adapter_rejected"
    assert "尚无适配器" in payload["detail"]


def test_search_projects_fails_loudly_without_github(server):
    """未配置 GitHub 客户端：显式失败，**绝不**返回空列表假装「没找到」。"""
    payload = call(server, "search_projects", {"query": "memory mcp"})
    assert payload["ok"] is False
    assert payload["error"] == "search_unavailable"


def test_compare_projects_requires_ids(server):
    payload = call(server, "compare_projects", {"plugin_ids": []})
    assert payload["ok"] is False and payload["error"] == "invalid_request"


def test_compare_projects_missing_plugin(server):
    payload = call(server, "compare_projects", {"plugin_ids": ["nope"]})
    assert payload["ok"] is False and payload["error"] == "not_found"


# --------------------------------------------------------------------------- #
# 操作申请：只生成计划
# --------------------------------------------------------------------------- #
def test_request_install_unknown_plugin(server):
    payload = call(server, "request_install", {"plugin_id": "nope", "reason": "test"})
    assert payload["ok"] is False and payload["error"] == "not_found"


def test_request_operation_rejects_unknown_action(server, mcp_runtime):
    p = seed(mcp_runtime)
    payload = call(
        server, "request_operation", {"plugin_id": p.id, "action": "delete-everything"}
    )
    assert payload["ok"] is False and payload["error"] == "invalid_request"


def test_request_operation_creates_plan_without_token(server, mcp_runtime, tmp_path):
    p = seed(mcp_runtime)
    payload = call(
        server, "request_operation", {"plugin_id": p.id, "action": "install"}
    )
    assert payload["ok"] is True
    assert payload["status"] == "awaiting_confirmation"
    # Agent **拿不到**任何令牌
    assert "confirmation_token" not in payload
    assert "token" not in json.dumps(payload, ensure_ascii=False)
    # 且**没有**任何文件被写入
    roots = ManagedRoots.create(mcp_runtime.settings.data_dir)
    assert not roots.plugin_dir(p.id).exists()
    assert not roots.config_path(p.id).exists()


def test_get_install_plan_is_readable_and_sealed(server, mcp_runtime):
    p = seed(mcp_runtime)
    created = call(server, "request_operation", {"plugin_id": p.id, "action": "install"})
    op_id = created["operation_id"]

    plan = call(server, "get_install_plan", {"operation_id": op_id})
    assert plan["ok"] is True
    body = plan["plan"]
    assert body["plugin_id"] == p.id
    assert body["pinned_ref"] == "a" * 40
    assert body["commands"] == []              # 默认不执行任何安装脚本
    assert body["spawns_process"] is False
    assert body["network_required"] is True
    assert plan["plan_digest"] and len(plan["plan_digest"]) == 64
    # 审查摘要必须带免责声明（不得暗示「已确认安全」）
    assert body["review"]["disclaimer"]


def test_get_install_plan_unknown_operation(server):
    payload = call(server, "get_install_plan", {"operation_id": "nope"})
    assert payload["ok"] is False and payload["error"] == "not_found"


def test_operation_status_has_no_token(server, mcp_runtime):
    p = seed(mcp_runtime)
    created = call(server, "request_operation", {"plugin_id": p.id, "action": "install"})
    op_id = created["operation_id"]

    status = call(server, "get_operation_status", {"operation_id": op_id})
    assert status["ok"] is True
    assert status["status"] == "awaiting_confirmation"
    assert status["logs"], "应有步骤日志"
    dumped = json.dumps(status, ensure_ascii=False)
    assert "confirmation_token" not in dumped


def test_list_operation_history(server, mcp_runtime):
    p = seed(mcp_runtime)
    call(server, "request_operation", {"plugin_id": p.id, "action": "install"})
    history = call(server, "list_operation_history", {"plugin_id": p.id})
    assert history["ok"] is True and history["count"] >= 1
    assert history["items"][0]["action"] == "install"


def test_agent_request_is_audited_as_agent(server, mcp_runtime):
    p = seed(mcp_runtime)
    call(server, "request_operation", {"plugin_id": p.id, "action": "install"})
    records = mcp_runtime.plugins.list_audit(limit=20)
    assert any(
        r.actor == "agent" and r.action == "plugin.request_install" and r.outcome == "ok"
        for r in records
    )


# --------------------------------------------------------------------------- #
# 与网页共用同一 service 层
# --------------------------------------------------------------------------- #
def test_mcp_and_web_share_state(server, mcp_runtime):
    """MCP 生成的操作在网页侧（同一 service 实例）可见，且仍未被执行。"""
    p = seed(mcp_runtime)
    created = call(server, "request_operation", {"plugin_id": p.id, "action": "install"})
    op_id = created["operation_id"]

    # 网页侧读取同一个 InstallService
    op = mcp_runtime.installs.get(op_id)
    assert op.id == op_id
    assert op.status == "awaiting_confirmation"
    assert op.actor == "agent"
    # 未确认 → 无法执行
    from app.install.service import InstallServiceError

    with pytest.raises(InstallServiceError):
        mcp_runtime.installs.execute(op_id, token="forged")


def test_adapter_kinds_match_registry(server):
    assert set(supported_kinds()) == {"skill", "rules_instructions", "mcp_server"}
