"""文件型 Skill 适配器（阶段 6）。

Skill 的约定（文件型）：
- 根目录或任意一级目录下存在 ``SKILL.md``；
- ``SKILL.md`` 以 YAML frontmatter 开头，且包含 ``name`` 与 ``description``；
- 其余文件为随附资源。

**不猜测**：缺少 ``SKILL.md`` 或 frontmatter 缺字段一律记为问题并拒绝安装。
"""

from __future__ import annotations

from typing import Any, Mapping

from app.adapters.base import AdapterError, ClientProfile, PluginAdapter, _text
from app.models import EvidenceLevel, Plugin, PluginKind

SKILL_FILENAME = "skill.md"


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    """解析极简 YAML frontmatter（仅 ``key: value`` 顶层标量）。

    故意不引入 YAML 依赖：只需要 name / description 两个字段。
    """
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    data: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return data
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip().lower()] = value.strip().strip("'\"")
    return None  # 未闭合的 frontmatter


class SkillAdapter(PluginAdapter):
    kind = PluginKind.SKILL
    label = "文件型 Skill"

    def detect(self, files: Mapping[str, bytes]) -> bool:
        return any(p.rsplit("/", 1)[-1].lower() == SKILL_FILENAME for p in files)

    def skill_path(self, files: Mapping[str, bytes]) -> str | None:
        for path in sorted(files):
            if path.rsplit("/", 1)[-1].lower() == SKILL_FILENAME:
                return path
        return None

    def validate(
        self, plugin: Plugin, files: Mapping[str, bytes]
    ) -> tuple[list[str], list[str]]:
        problems: list[str] = []
        notes: list[str] = []

        path = self.skill_path(files)
        if path is None:
            problems.append("缺少 SKILL.md：无法确认为 Skill 类型插件。")
            return problems, notes

        text = _text(files, path) or ""
        meta = _parse_frontmatter(text)
        if meta is None:
            problems.append(
                f"{path} 缺少合法的 YAML frontmatter（应以 --- 开头并以 --- 结束）。"
            )
        else:
            for field in ("name", "description"):
                if not meta.get(field):
                    problems.append(f"{path} 的 frontmatter 缺少 {field}。")
            if meta.get("name") and meta["name"] != plugin.slug:
                notes.append(
                    f"frontmatter 的 name={meta['name']!r} 与条目标识 {plugin.slug!r} 不同，"
                    "已如实保留，不做自动改写。"
                )

        notes.append(
            "Skill 为纯文件型插件：安装即落地文件，**不执行**任何脚本、不起进程。"
        )
        notes.append("证据等级：Skill 目录约定为【有依据的推断】，以 SKILL.md 存在为准。")
        return problems, notes

    def describe(self) -> dict[str, Any]:
        info = super().describe()
        info["evidence"] = EvidenceLevel.INFERRED.value
        info["requires"] = ["SKILL.md（含 name / description frontmatter）"]
        return info
