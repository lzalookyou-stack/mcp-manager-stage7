"""MCP Server（对 Agent 暴露）。

阶段 2/4：只读工具。
阶段 7：扩展为完整的发现 → 检查 → 对比 → 计划 → 状态 链路，
**与网页控制台共用同一 service 层**（禁止两套逻辑）。

安全立场（不可违反）：
- Agent **不能**通过 MCP 执行任何写操作，也**不能**确认任何操作；
  它只能"提交操作申请"（生成**待用户确认**的计划）；
  授权必须来自用户在受信任网页上的明确确认。
- 因此本服务器**刻意不提供** confirm / execute 工具。
- 工具返回值中**不得**包含令牌、凭据或敏感绝对路径。
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from app import __version__
from app.adapters import AdapterError, get_adapter
from app.models import InstallStatus, PluginKind
from app.runtime import Runtime
from app.security import sanitize_for_log
from app.services import PluginNotFound, SearchUnavailable, dump_plugin

# 单次返回上限，避免 Agent 侧上下文被撑爆
_MAX_LIMIT = 50

_INSTRUCTIONS = (
    "本地优先的 MCP / Agent 插件管理器。"
    "本服务器提供发现（搜索 / 检查 / 对比）与**操作申请**能力；"
    "安装、回滚、卸载都必须由用户在受信任网页上确认后才会执行，"
    "Agent 无法确认或执行，也不会收到任何确认令牌。"
)


def _err(error: str, detail: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": error}
    if detail:
        payload["detail"] = detail
    return payload


def build_server(runtime: Runtime) -> MCPServer:
    """构造 MCPServer 实例（``mcp 2.x``：``mcp.server.mcpserver.MCPServer``）。"""
    service = runtime.plugins
    installs = runtime.installs

    server = MCPServer(
        name="mcp-manager",
        version=__version__,
        instructions=_INSTRUCTIONS,
    )

    # ------------------------------------------------------------------ #
    # 读取：库内条目
    # ------------------------------------------------------------------ #
    @server.tool(description="列出已登记的插件条目（只读）。")
    def list_plugins(kind: str | None = None, limit: int = 20) -> dict[str, Any]:
        parsed_kind = None
        if kind:
            try:
                parsed_kind = PluginKind(kind)
            except ValueError:
                return _err(
                    "unknown_kind",
                    f"kind 必须是 {[k.value for k in PluginKind]} 之一",
                )
        limit = max(1, min(int(limit), _MAX_LIMIT))
        items = service.list(kind=parsed_kind, limit=limit)
        return {
            "ok": True,
            "count": len(items),
            "items": [
                {
                    "id": p.id,
                    "name": p.name,
                    "kind": p.kind.value,
                    "risk_level": p.risk_level.value,
                    "review_status": p.review_status.value,
                    "install_status": p.install_status.value,
                    "score_total": p.score.total(),
                }
                for p in items
            ],
        }

    @server.tool(description="按 ID 查询单个插件条目的完整信息（只读）。")
    def get_plugin(plugin_id: str) -> dict[str, Any]:
        try:
            plugin = service.get(plugin_id)
        except PluginNotFound:
            return _err("not_found", plugin_id)
        return {"ok": True, "plugin": dump_plugin(plugin)}

    @server.tool(description="获取插件库统计信息（只读）。")
    def get_stats() -> dict[str, Any]:
        return {"ok": True, "stats": service.stats()}

    @server.tool(description="列出**已安装成功**的插件（只读）。")
    def list_installed(limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(int(limit), _MAX_LIMIT))
        items = service.list(install_status=InstallStatus.SUCCEEDED, limit=limit)
        return {
            "ok": True,
            "count": len(items),
            "items": [
                {
                    "id": p.id,
                    "name": p.name,
                    "kind": p.kind.value,
                    "install_status": p.install_status.value,
                    "pinned_ref": p.pinned_ref,
                    "rollback_available": p.rollback_available,
                }
                for p in items
            ],
        }

    # ------------------------------------------------------------------ #
    # 发现：搜索 / 检查 / 对比
    # ------------------------------------------------------------------ #
    @server.tool(
        description=(
            "在 GitHub 上搜索候选项目并落库为**待审查**条目（不安装）。"
            "未配置 GitHub 令牌时显式返回 search_unavailable，不返回空列表。"
        )
    )
    def search_projects(
        query: str,
        limit: int = 10,
        language: str | None = None,
        min_stars: int | None = None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if language:
            filters["language"] = language
        if min_stars is not None:
            filters["min_stars"] = int(min_stars)
        try:
            if ":" in query and " " not in query.split(":", 1)[0]:
                items = service.search_remote(query, limit=limit, actor="agent")
                expr = query.strip()
            else:
                parsed = service.build_query(query, **filters)
                items = service.search_remote(
                    parsed.github_query, limit=limit, actor="agent"
                )
                expr = parsed.github_query
        except SearchUnavailable as exc:
            return _err("search_unavailable", sanitize_for_log(exc))
        except Exception as exc:  # noqa: BLE001 - 上游失败如实回报
            return _err("search_failed", sanitize_for_log(exc))
        return {
            "ok": True,
            "query": expr,
            "count": len(items),
            "items": [{"id": p.id, "name": p.name, "stars": p.stars} for p in items],
            "note": "这些条目**尚未**做安全审查，risk_level 为 none 表示「未评估」。",
        }

    @server.tool(
        description=(
            "补全单个候选的深度信息：固定 commit、README/测试/CI/依赖探测（只读）。"
            "任何子项失败都会如实记入 missing_fields。"
        )
    )
    def inspect_project(plugin_id: str) -> dict[str, Any]:
        try:
            plugin = service.inspect_remote(plugin_id, actor="agent")
        except PluginNotFound:
            return _err("not_found", plugin_id)
        except SearchUnavailable as exc:
            return _err("search_unavailable", sanitize_for_log(exc))
        except Exception as exc:  # noqa: BLE001
            return _err("inspect_failed", sanitize_for_log(exc))
        return {"ok": True, "plugin": dump_plugin(plugin)}

    @server.tool(description="对比多个候选（只呈现已核实字段与证据缺口）。")
    def compare_projects(plugin_ids: list[str]) -> dict[str, Any]:
        if not plugin_ids:
            return _err("invalid_request", "plugin_ids 不能为空")
        try:
            return {"ok": True, "comparison": service.compare(list(plugin_ids))}
        except PluginNotFound as exc:
            return _err("not_found", sanitize_for_log(exc))
        except Exception as exc:  # noqa: BLE001
            return _err("compare_failed", sanitize_for_log(exc))

    @server.tool(
        description=(
            "查看该条目适用的插件适配器与目标客户端格式（只读）。"
            "未支持的类型会返回 adapter_rejected。"
        )
    )
    def inspect_plugin(plugin_id: str) -> dict[str, Any]:
        try:
            plugin = service.get(plugin_id)
        except PluginNotFound:
            return _err("not_found", plugin_id)
        try:
            adapter = get_adapter(plugin.kind)
        except AdapterError as exc:
            return _err("adapter_rejected", sanitize_for_log(exc))
        payload: dict[str, Any] = {
            "ok": True,
            "plugin_id": plugin.id,
            "kind": plugin.kind.value,
            "adapter": adapter.describe(),
        }
        if runtime.adapters is not None:
            payload["clients"] = runtime.adapters.describe()["clients"]
        return payload

    # ------------------------------------------------------------------ #
    # 操作申请（只生成计划，绝不执行）
    # ------------------------------------------------------------------ #
    def _create_plan(plugin_id: str, action: str, reason: str) -> dict[str, Any]:
        if installs is None:  # pragma: no cover - 装配缺失
            return _err("install_unavailable", "安装服务未装配")
        try:
            plugin = service.get(plugin_id)
        except PluginNotFound:
            service.audit(
                actor="agent",
                action=f"plugin.request_{action}",
                target=plugin_id,
                outcome="denied",
                detail="插件条目不存在",
            )
            return _err("not_found", plugin_id)
        try:
            op = installs.create_plan(plugin, action=action, actor="agent")
        except Exception as exc:  # noqa: BLE001 - 如实回报，绝不伪成功
            service.audit(
                actor="agent",
                action=f"plugin.request_{action}",
                target=plugin_id,
                outcome="denied",
                detail=sanitize_for_log(exc),
            )
            return _err("plan_rejected", sanitize_for_log(exc))
        service.audit(
            actor="agent",
            action=f"plugin.request_{action}",
            target=plugin_id,
            outcome="ok",
            detail=f"已生成待确认计划 operation_id={op.id}",
        )
        return {
            "ok": True,
            "operation_id": op.id,
            "action": action,
            "status": op.status,
            "reason": reason,
            "detail": (
                "计划已生成，等待用户在网页控制台确认；"
                "Agent 无法确认或执行，也不会收到确认令牌。"
            ),
        }

    @server.tool(
        description=(
            "申请生成**安装计划**（不会立即安装）。"
            "确认与执行必须由用户在受信任网页上完成。"
        )
    )
    def request_install(plugin_id: str, reason: str) -> dict[str, Any]:
        return _create_plan(plugin_id, "install", reason)

    @server.tool(
        description=(
            "申请一次操作（install / rollback / uninstall）：只生成**待确认**计划，"
            "绝不执行。回滚与卸载仅处理本系统登记并拥有的变更。"
        )
    )
    def request_operation(
        plugin_id: str, action: str, reason: str = ""
    ) -> dict[str, Any]:
        if action not in ("install", "rollback", "uninstall"):
            return _err(
                "invalid_request",
                "action 必须是 install / rollback / uninstall 之一",
            )
        return _create_plan(plugin_id, action, reason)

    @server.tool(
        description=(
            "获取某个操作的**安装计划全文**（只读）：来源与固定版本、文件清单、"
            "命令、权限、审查摘要、回滚与失败处理方案。"
        )
    )
    def get_install_plan(operation_id: str) -> dict[str, Any]:
        if installs is None:  # pragma: no cover
            return _err("install_unavailable", "安装服务未装配")
        try:
            op = installs.get(operation_id)
        except Exception as exc:  # noqa: BLE001
            return _err("not_found", sanitize_for_log(exc))
        if op.plan is None:
            return _err("no_plan", operation_id)
        return {
            "ok": True,
            "operation_id": op.id,
            "status": op.status,
            "plan": op.plan.model_dump(mode="json"),
            "plan_digest": op.plan_digest,
            "note": "计划已生成但**尚未执行**；执行必须由用户在网页确认。",
        }

    @server.tool(description="查询某个操作的当前状态与步骤日志（只读）。")
    def get_operation_status(operation_id: str) -> dict[str, Any]:
        if installs is None:  # pragma: no cover
            return _err("install_unavailable", "安装服务未装配")
        try:
            op = installs.get(operation_id)
        except Exception as exc:  # noqa: BLE001 - 不存在即如实回报
            return _err("not_found", sanitize_for_log(exc))
        payload = op.to_dict()
        payload["ok"] = True
        payload["confirmation"] = installs.confirmation(operation_id)
        payload["logs"] = [log.__dict__ for log in installs.logs(operation_id)]
        # 绝不回传任何令牌
        payload.pop("confirmation_token", None)
        return payload

    @server.tool(description="列出操作历史（只读；可按插件过滤）。")
    def list_operation_history(
        plugin_id: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        if installs is None:  # pragma: no cover
            return _err("install_unavailable", "安装服务未装配")
        limit = max(1, min(int(limit), _MAX_LIMIT))
        ops = installs.list(plugin_id=plugin_id, limit=limit)
        return {
            "ok": True,
            "count": len(ops),
            "items": [
                {
                    "id": op.id,
                    "plugin_id": op.plugin_id,
                    "action": op.action,
                    "status": op.status,
                    "created_at": op.created_at,
                    "updated_at": op.updated_at,
                }
                for op in ops
            ],
        }

    return server


def main() -> None:  # pragma: no cover - 入口，手工运行
    """以 stdio 传输启动（默认，供 MCP 客户端拉起）。"""
    runtime = Runtime.create()
    build_server(runtime).run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
