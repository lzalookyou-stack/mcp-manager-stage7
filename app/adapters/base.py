"""插件适配器抽象（阶段 6）。

目标：**新增插件类型无需重写核心逻辑**。

三类真实适配：
- ``skill``       文件型 Skill（``SKILL.md`` + 附属文件）
- ``rules``       文件型规则 / 指令（``AGENTS.md``、``*.md``、``*.mdc``）
- ``mcp_server``  MCP Server（生成**客户端配置片段**，不直接改别人的配置文件）

关键纪律：
- **不假设不同 AI 客户端格式一致**：客户端配置由 ``ClientProfile`` 描述，
  每个 profile 记录**来源文档 URL** 与**证据等级**；
- 只有证据等级为【已验证】的 profile 才允许写入真实客户端配置目录；
  其余 profile 只能**输出片段**供用户自行核对（``assert_writable``）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.models import EvidenceLevel, Plugin, PluginKind


class AdapterError(RuntimeError):
    """适配器无法处理该插件 / 该 profile。调用方**不得**降级为猜测。"""


@dataclass(frozen=True)
class ClientProfile:
    """一个 AI 客户端的 MCP 配置格式。

    ``evidence`` 为【已验证】时才允许写入（``writable`` 由本字段派生）。
    """

    key: str
    display_name: str
    config_path_hint: str
    servers_key: str
    evidence: EvidenceLevel
    source: str | None = None
    checked_at: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def writable(self) -> bool:
        """只有【已验证】的格式才允许自动写入。"""
        return self.evidence is EvidenceLevel.VERIFIED

    def assert_writable(self) -> None:
        if not self.writable:
            raise AdapterError(
                f"客户端格式 {self.key!r} 的证据等级为 {self.evidence.value}，"
                "未经验证，拒绝写入其配置文件；只能输出片段供人工核对。"
            )


@dataclass(frozen=True)
class RenderedFile:
    """将要落地的一个文件（``role`` 区分插件文件与客户端配置）。"""

    path: str
    content: str
    role: str = "plugin"


@dataclass(frozen=True)
class AdapterReport:
    """适配器对一次预览 / 安装的结论（如实呈现问题与证据）。"""

    kind: str
    adapter: str
    detected: bool
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    evidence: EvidenceLevel = EvidenceLevel.UNCHECKED
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.detected and not self.problems


class PluginAdapter(ABC):
    """插件类型适配器。"""

    #: 本适配器负责的插件类型
    kind: PluginKind

    #: 供展示的人类可读名称
    label: str = ""

    @abstractmethod
    def detect(self, files: Mapping[str, bytes]) -> bool:
        """根据仓库文件清单判断这是否是本类型的插件。"""

    @abstractmethod
    def validate(
        self, plugin: Plugin, files: Mapping[str, bytes]
    ) -> tuple[list[str], list[str]]:
        """返回 ``(problems, notes)``。``problems`` 非空即拒绝安装。"""

    def client_config(
        self,
        plugin: Plugin,
        profile: ClientProfile,
        *,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """生成**该客户端格式**的配置片段。

        默认实现只对 ``mcp_server`` 有意义；其余类型抛 ``AdapterError``，
        避免用「看起来像」的片段冒充可用配置。
        """
        raise AdapterError(
            f"{self.kind.value} 类型不提供 MCP 客户端配置（该类型不是 MCP Server）。"
        )

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "adapter": type(self).__name__,
            "label": self.label,
        }


def _text(files: Mapping[str, bytes], path: str) -> str | None:
    raw = files.get(path)
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace")


def _basenames(files: Mapping[str, bytes]) -> set[str]:
    return {p.rsplit("/", 1)[-1].lower() for p in files}


def _suffixes(files: Mapping[str, bytes]) -> set[str]:
    out: set[str] = set()
    for path in files:
        name = path.rsplit("/", 1)[-1]
        if "." in name:
            out.add("." + name.rsplit(".", 1)[-1].lower())
    return out