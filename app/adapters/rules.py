"""文件型 Rules / Instructions 适配器（阶段 6）。

覆盖「规则 / 指令」类插件（如 ``AGENTS.md``、``CLAUDE.md``、``*.mdc`` 规则文件）。

**不猜测**：
- 至少需要一个候选规则文件，否则拒绝；
- 空文件拒绝；
- 若同时存在多个入口文件，如实列出并**要求人工选择**（不在代码里替用户决定）。
"""

from __future__ import annotations

from typing import Any, Mapping

from app.adapters.base import PluginAdapter, _text
from app.models import EvidenceLevel, Plugin, PluginKind

# 常见规则入口文件名（小写比较）
RULE_FILENAMES = frozenset(
    {
        "agents.md",
        "claude.md",
        "gemini.md",
        "rules.md",
        "instructions.md",
        "copilot-instructions.md",
        ".cursorrules",
        "cursorrules",
    }
)
RULE_SUFFIXES = (".mdc", ".rules")


class RulesAdapter(PluginAdapter):
    kind = PluginKind.RULES_INSTRUCTIONS
    label = "文件型规则 / 指令"

    def candidate_paths(self, files: Mapping[str, bytes]) -> list[str]:
        found: list[str] = []
        for path in sorted(files):
            name = path.rsplit("/", 1)[-1].lower()
            if name in RULE_FILENAMES or name.endswith(RULE_SUFFIXES):
                found.append(path)
        return found

    def detect(self, files: Mapping[str, bytes]) -> bool:
        return bool(self.candidate_paths(files))

    def validate(
        self, plugin: Plugin, files: Mapping[str, bytes]
    ) -> tuple[list[str], list[str]]:
        problems: list[str] = []
        notes: list[str] = []

        candidates = self.candidate_paths(files)
        if not candidates:
            problems.append(
                "未找到任何规则入口文件"
                f"（候选名：{sorted(RULE_FILENAMES)}；后缀：{list(RULE_SUFFIXES)}）。"
            )
            return problems, notes

        empty = [p for p in candidates if not (_text(files, p) or "").strip()]
        for path in empty:
            problems.append(f"规则文件为空：{path}")

        if len(candidates) > 1:
            notes.append(
                "存在多个规则入口文件：" + "、".join(candidates) + "。"
                "本系统**不替用户决定**生效哪一个，请在客户端侧明确指定。"
            )

        notes.append(
            "规则 / 指令为纯文本插件：安装只落地文件，不执行脚本、不起进程。"
        )
        notes.append(
            "不同 AI 客户端读取的规则文件名不同；本适配器只识别上述候选名，"
            "未识别的命名不会被自动接管。"
        )
        return problems, notes

    def describe(self) -> dict[str, Any]:
        info = super().describe()
        info["evidence"] = EvidenceLevel.INFERRED.value
        info["requires"] = ["至少一个规则入口文件且非空"]
        info["candidates"] = sorted(RULE_FILENAMES)
        info["suffixes"] = list(RULE_SUFFIXES)
        return info