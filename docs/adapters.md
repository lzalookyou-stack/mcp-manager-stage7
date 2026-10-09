# 插件适配器与客户端配置格式（阶段 6）

> 本文件记录**实际核查**结果。所有「已验证」条目均抓取官方文档正文核对，
> 并保留来源 URL 与核查时间。**不假设不同客户端格式一致。**

---

## 1. 已支持的插件类型

| 类型（`PluginKind`） | 适配器 | 识别依据 | 证据等级 |
|---|---|---|---|
| `skill` | `SkillAdapter` | 存在 `SKILL.md`，且含 `name` / `description` frontmatter | 【有依据的推断】 |
| `rules_instructions` | `RulesAdapter` | 存在规则入口文件（`AGENTS.md` / `CLAUDE.md` / `*.mdc` 等）且非空 | 【有依据的推断】 |
| `mcp_server` | `McpServerAdapter` | 存在入口文件（`server.py` / `index.js` 等）+ 依赖清单或 MCP 依赖声明 | 【有依据的推断】 |

**未支持的类型**（`agent_plugin` / `command` / `hook` / `adapter_extension`）：
`get_adapter()` **显式抛 `AdapterError`**，不提供降级实现。
测试：`tests/test_stage6_adapters.py::test_registry_rejects_unsupported_kind`。

---

## 2. AI 客户端 MCP 配置格式（核查记录）

| key | 客户端 | 配置文件位置 | 顶层键名 | 证据等级 | 来源 |
|---|---|---|---|---|---|
| `claude_desktop` | Claude Desktop | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`；Windows `%APPDATA%\Claude\claude_desktop_config.json` | `mcpServers` | 【已验证】 | https://modelcontextprotocol.io/quickstart/user |
| `vscode_workspace` | VS Code（工作区，VS Code 格式） | `<项目>/.vscode/mcp.json` | **`servers`** | 【已验证】 | https://code.visualstudio.com/docs/agent-customization/mcp-servers |
| `vscode_portable` | VS Code / Copilot（可移植格式） | `<项目>/.mcp.json` | `mcpServers` | 【已验证】 | 同上 |
| `copilot_user` | Copilot 用户级配置 | `$COPILOT_HOME/mcp-config.json`（默认 `~/.copilot/mcp-config.json`） | `mcpServers` | 【已验证】 | 同上 |
| `cursor_workspace` | Cursor（工作区） | `<项目>/.cursor/mcp.json` | `mcpServers` | **【未检查】** | — |

核查时间：`2026-10-09T15:59Z`（UTC）。

### 核查到的原文要点

**Claude Desktop**（modelcontextprotocol.io/quickstart/user 正文）：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/username/Desktop"]
    }
  }
}
```

- 该页明确：配置写入 `claude_desktop_config.json`，键为 `mcpServers`；
- 修改后需**完全退出并重启**客户端才会加载；
- 服务器以当前用户权限运行，可执行该用户能执行的所有文件操作。

**VS Code**（code.visualstudio.com/docs/agent-customization/mcp-servers 正文）：

- 「Workspace, VS Code format: create or open `.vscode/mcp.json` in your project.
  This format defines servers in a **top-level `servers` object**.」
- 「Workspace, portable format: create `.mcp.json` at the root of your project.
  This format defines servers in a **top-level `mcpServers` object** and works
  across compatible tools.」
- 「User, portable format: create `$COPILOT_HOME/mcp-config.json`, or
  `~/.copilot/mcp-config.json` when `COPILOT_HOME` is not set. This format
  defines servers in a top-level `mcpServers` object.」
- 该文档同时指出 `.vscode/mcp.json` 已被列为 **deprecated**，推荐使用可移植格式。

> 这直接证明：**不同客户端的顶层键名确实不同**（`servers` vs `mcpServers`）。
> 代码中的 `ClientProfile.servers_key` 即由此而来，测试
> `test_client_servers_key_differs_by_client` 固化该差异。

### Cursor：为什么是【未检查】

未取得官方文档正文核对，因此 `evidence=UNCHECKED`、`writable=False`。
`ClientProfile.assert_writable()` 会抛异常，**禁止**写入该位置；
只能输出片段供用户自行核对后再手工配置。
测试：`test_unverified_profile_refuses_write`。

---

## 3. 安全立场

1. **只有【已验证】的 profile 允许写入**：`ClientProfile.writable` 由证据等级派生，
   未验证的 profile 调用 `assert_writable()` 立即抛 `AdapterError`。
2. **不猜启动命令**：`McpServerAdapter.client_config()` 要求显式传入 `command`，
   否则抛 `AdapterError`；适配预览未提供命令时只返回说明，**不生成**片段。
3. **不写盘**：适配器服务（`AdapterService`）只做只读预览；
   客户端配置的实际写入必须走阶段 5 的安装闭环（计划 → 用户确认 → 执行），
   复用同一套授权、快照与归属台账。
4. **不执行**：MCP Server 的依赖安装与进程启动**都不由本系统执行**；
   生成的片段只是配置文本。
5. **凭据不外泄**：片段中的 `env` 只包含调用方显式传入的键值，
   本系统不注入任何令牌。

---

## 4. 已知限制

- 三类适配器的识别规则均为**启发式**（文件名 / 关键词），
  **必然存在漏识别与误识别**；报告只陈述命中的依据，不给出「这是/不是」的绝对结论。
- 规则文件的生效优先级由**客户端**决定，本系统不替用户选择（多入口时如实列出）。
- `cursor_workspace` 等未验证格式需要人工核对官方文档后补全证据等级，
  在此之前不会自动写入。
- 本阶段未实现 `agent_plugin` / `command` / `hook` / `adapter_extension` 的适配。