"""阶段 6 测试：插件适配器。

覆盖：
- 三类适配器的识别与校验（Skill / Rules / MCP Server）；
- 未支持类型**显式失败**（不提供降级实现）；
- 客户端格式注册表：只有【已验证】的 profile 允许写入；
- 不同客户端的顶层键名**确实不同**（mcpServers vs servers）；
- MCP Server 片段生成：不猜 command、不含凭据；
- 适配器预览为**只读**，不写盘。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import (
    AdapterError,
    McpServerAdapter,
    RulesAdapter,
    SkillAdapter,
    detect_kind,
    get_adapter,
    get_profile,
    list_profiles,
    supported_kinds,
    verified_profiles,
)
from app.adapters.clients import list_profiles as _profiles
from app.config import load_settings
from app.install import ManagedRoots
from app.install.provider import MemoryFileProvider
from app.models import EvidenceLevel, Plugin, PluginKind
from app.runtime import Runtime
from app.services.adapter_service import AdapterService
from app.web import create_app

ALLOWED_HOST = "testserver"
ORIGIN = f"http://{ALLOWED_HOST}"

SKILL_FILES = {
    "SKILL.md": (
        "---\nname: demo-skill\ndescription: 一个演示 Skill\n---\n\n# 用法\n..."
    ).encode("utf-8"),
    "helper.py": b"print('hi')\n",
}
RULES_FILES = {"AGENTS.md": "# 规则\n\n始终使用简体中文。\n".encode("utf-8")}
MCP_FILES = {
    "server.py": b"from mcp.server.mcpserver import MCPServer\nserver = MCPServer('x')\n",
    "requirements.txt": b"mcp==2.3.0\n",
}


def make_plugin(service, kind: PluginKind, *, slug: str = "demo"):
    return service.upsert(
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
# 适配器识别 / 校验
# --------------------------------------------------------------------------- #
def test_skill_detected_and_valid():
    adapter = SkillAdapter()
    assert adapter.detect(SKILL_FILES) is True
    problems, notes = adapter.validate(
        Plugin.new(
            source="github:acme/demo-skill",
            slug="demo-skill",
            name="acme/demo-skill",
            kind=PluginKind.SKILL,
        ),
        SKILL_FILES,
    )
    assert problems == []
    assert any("不执行" in n for n in notes)


def test_skill_missing_frontmatter_is_a_problem():
    adapter = SkillAdapter()
    files = {"SKILL.md": "# 没有 frontmatter\n".encode("utf-8")}
    problems, _ = adapter.validate(
        Plugin.new(
            source="github:acme/x", slug="x", name="x", kind=PluginKind.SKILL
        ),
        files,
    )
    assert problems and "frontmatter" in problems[0]


def test_skill_missing_file_not_detected():
    adapter = SkillAdapter()
    assert adapter.detect({"README.md": b"hi"}) is False
    problems, _ = adapter.validate(
        Plugin.new(
            source="github:acme/x", slug="x", name="x", kind=PluginKind.SKILL
        ),
        {"README.md": b"hi"},
    )
    assert problems


def test_rules_detected_and_empty_rejected():
    adapter = RulesAdapter()
    assert adapter.detect(RULES_FILES) is True
    problems, _ = adapter.validate(
        Plugin.new(
            source="github:acme/r", slug="r", name="r", kind=PluginKind.RULES_INSTRUCTIONS
        ),
        RULES_FILES,
    )
    assert problems == []

    problems2, _ = adapter.validate(
        Plugin.new(
            source="github:acme/r", slug="r", name="r", kind=PluginKind.RULES_INSTRUCTIONS
        ),
        {"AGENTS.md": b"   \n"},
    )
    assert problems2 and "为空" in problems2[0]


def test_rules_multiple_entries_listed_not_auto_chosen():
    adapter = RulesAdapter()
    files = {"AGENTS.md": b"a\n", "CLAUDE.md": b"b\n"}
    problems, notes = adapter.validate(
        Plugin.new(
            source="github:acme/r", slug="r", name="r", kind=PluginKind.RULES_INSTRUCTIONS
        ),
        files,
    )
    assert problems == []
    assert any("多个规则入口文件" in n for n in notes)
    assert any("不替用户决定" in n for n in notes)


def test_mcp_server_detected_and_notes():
    adapter = McpServerAdapter()
    assert adapter.detect(MCP_FILES) is True
    problems, notes = adapter.validate(
        Plugin.new(
            source="github:acme/m", slug="m", name="m", kind=PluginKind.MCP_SERVER
        ),
        MCP_FILES,
    )
    assert problems == []
    assert any("不会安装其依赖" in n for n in notes)


def test_mcp_server_without_entry_rejected():
    adapter = McpServerAdapter()
    files = {"README.md": b"hi\n", "requirements.txt": b"mcp==2.3.0\n"}
    problems, _ = adapter.validate(
        Plugin.new(
            source="github:acme/m", slug="m", name="m", kind=PluginKind.MCP_SERVER
        ),
        files,
    )
    assert problems and "入口文件" in problems[0]


def test_registry_rejects_unsupported_kind():
    assert supported_kinds() == ["skill", "rules_instructions", "mcp_server"]
    with pytest.raises(AdapterError) as exc:
        get_adapter(PluginKind.AGENT_PLUGIN)
    assert "尚无适配器" in str(exc.value)
    with pytest.raises(AdapterError):
        get_adapter("not-a-kind")


def test_detect_kind_returns_all_matches():
    files = dict(SKILL_FILES)
    files["AGENTS.md"] = b"rules\n"
    kinds = detect_kind(files)
    assert "skill" in kinds and "rules_instructions" in kinds


# --------------------------------------------------------------------------- #
# 客户端格式
# --------------------------------------------------------------------------- #
def test_client_profiles_have_evidence_and_sources():
    for profile in list_profiles():
        assert isinstance(profile.evidence, EvidenceLevel)
        if profile.evidence is EvidenceLevel.VERIFIED:
            assert profile.source, f"{profile.key} 标为已验证却无来源文档"
            assert profile.checked_at
        else:
            assert not profile.writable


def test_unverified_profile_refuses_write():
    cursor = get_profile("cursor_workspace")
    assert cursor.writable is False
    with pytest.raises(AdapterError) as exc:
        cursor.assert_writable()
    assert "拒绝写入" in str(exc.value)


def test_client_servers_key_differs_by_client():
    """**不假设不同客户端格式一致**：VS Code 工作区用 servers，其余用 mcpServers。"""
    assert get_profile("vscode_workspace").servers_key == "servers"
    assert get_profile("claude_desktop").servers_key == "mcpServers"
    assert get_profile("vscode_portable").servers_key == "mcpServers"
    keys = {p.key: p.servers_key for p in _profiles()}
    assert keys["vscode_workspace"] != keys["claude_desktop"]
    assert len(verified_profiles()) >= 4


def test_mcp_client_config_matches_each_client_format():
    adapter = McpServerAdapter()
    plugin = Plugin.new(
        source="github:acme/m", slug="m", name="acme/m", kind=PluginKind.MCP_SERVER
    )

    claude = adapter.client_config(
        plugin, get_profile("claude_desktop"), command="npx", args=["-y", "pkg"]
    )
    assert set(claude) == {"mcpServers"}
    assert claude["mcpServers"]["m"]["command"] == "npx"
    assert claude["mcpServers"]["m"]["args"] == ["-y", "pkg"]

    vscode = adapter.client_config(
        plugin, get_profile("vscode_workspace"), command="npx"
    )
    assert set(vscode) == {"servers"}


def test_mcp_client_config_refuses_to_guess_command():
    adapter = McpServerAdapter()
    plugin = Plugin.new(
        source="github:acme/m", slug="m", name="m", kind=PluginKind.MCP_SERVER
    )
    with pytest.raises(AdapterError):
        adapter.client_config(plugin, get_profile("claude_desktop"), command="")


def test_non_mcp_adapter_has_no_client_config():
    adapter = SkillAdapter()
    plugin = Plugin.new(
        source="github:acme/s", slug="s", name="s", kind=PluginKind.SKILL
    )
    with pytest.raises(AdapterError):
        adapter.client_config(plugin, get_profile("claude_desktop"), command="x")


# --------------------------------------------------------------------------- #
# 预览服务（只读）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def adapters_service(runtime):
    def factory(plugin):  # type: ignore[no-untyped-def]
        return MemoryFileProvider(dict(SKILL_FILES))

    return AdapterService(runtime.conn, provider_factory=factory)


def test_preview_is_readonly(adapters_service, service, tmp_path: Path):
    plugin = make_plugin(service, PluginKind.SKILL, slug="demo-skill")
    before = set(tmp_path.rglob("*"))
    payload = adapters_service.preview(plugin, kind="skill")
    after = set(tmp_path.rglob("*"))
    assert payload["detected"] is True
    assert payload["ok"] is True
    assert before == after, "预览不得产生任何文件变更"


def test_preview_includes_client_config_when_command_given(adapters_service, service):
    plugin = make_plugin(service, PluginKind.MCP_SERVER, slug="m")
    payload = adapters_service.preview(
        plugin, kind="mcp_server", profile_key="claude_desktop", command="npx"
    )
    assert payload["client"]["writable"] is True
    assert "mcpServers" in json.loads(payload["client_config_json"])


def test_preview_does_not_invent_command(adapters_service, service):
    plugin = make_plugin(service, PluginKind.MCP_SERVER, slug="m")
    payload = adapters_service.preview(plugin, kind="mcp_server", profile_key="claude_desktop")
    assert payload["client_config"] is None
    assert "不猜测" in payload["client_config_note"]


def test_preview_reports_skipped_files(runtime, service):
    class SkippingProvider(MemoryFileProvider):
        @property
        def skipped(self):  # type: ignore[override]
            return ["big.bin（超过上限）"]

    svc = AdapterService(
        runtime.conn, provider_factory=lambda plugin: SkippingProvider(dict(SKILL_FILES))
    )
    plugin = make_plugin(service, PluginKind.SKILL, slug="demo-skill")
    payload = svc.preview(plugin, kind="skill")
    assert payload["skipped"] == ["big.bin（超过上限）"]


# --------------------------------------------------------------------------- #
# Web 端点（只读）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def web(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MCPM_ALLOWED_HOSTS", ALLOWED_HOST)
    monkeypatch.setenv("MCPM_ALLOWED_ORIGINS", ORIGIN)
    s = load_settings(
        host="127.0.0.1", port=8765, db_path=tmp_path / "web.db", data_dir=tmp_path / "data"
    )
    rt = Runtime.create(s)
    rt.adapters = AdapterService(
        rt.conn, provider_factory=lambda plugin: MemoryFileProvider(dict(SKILL_FILES))
    )
    app = create_app(rt)
    with TestClient(app, base_url=ORIGIN) as c:
        c.runtime = rt  # type: ignore[attr-defined]
        yield c
    rt.close()


def test_web_adapters_endpoint(web: TestClient):
    resp = web.get("/api/adapters")
    assert resp.status_code == 200
    body = resp.json()
    assert [a["kind"] for a in body["adapters"]] == supported_kinds()
    assert any(c["key"] == "claude_desktop" for c in body["clients"])


def test_web_adapt_endpoint_readonly(web: TestClient):
    rt = web.runtime  # type: ignore[attr-defined]
    p = make_plugin(rt.plugins, PluginKind.SKILL, slug="demo-skill")
    resp = web.get(f"/api/plugins/{p.id}/adapt", params={"kind": "skill"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert not ManagedRoots.create(rt.settings.data_dir).plugins_root.joinpath(
        p.id
    ).exists(), "适配预览不得写盘"


def test_web_adapt_unsupported_kind_400(web: TestClient):
    rt = web.runtime  # type: ignore[attr-defined]
    p = make_plugin(rt.plugins, PluginKind.AGENT_PLUGIN, slug="agent-x")
    resp = web.get(f"/api/plugins/{p.id}/adapt")
    assert resp.status_code == 400
    assert resp.json()["error"] == "adapter_rejected"


def test_web_adapt_is_get_only(web: TestClient):
    rt = web.runtime  # type: ignore[attr-defined]
    p = make_plugin(rt.plugins, PluginKind.SKILL, slug="demo-skill")
    resp = web.post(f"/api/plugins/{p.id}/adapt", headers={"Origin": ORIGIN}, json={})
    assert resp.status_code in (404, 405)