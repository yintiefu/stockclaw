from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ag_ui.core.types import (
    AssistantMessage,
    RunAgentInput,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from ag_ui_langgraph import LangGraphAgent
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

import chat
from agent.models import AgentMessage, ModelRef, RunSecrets

# 1A 不宣称跨 resume 的转移数上限（见 test_transition_limit）。
PRODUCT_TRANSITION_LIMIT = None


def build_chat_model(model_ref: ModelRef, secrets: RunSecrets) -> ChatOpenAI:
    """请求级构建 OpenAI 兼容模型；先过 SSRF 校验，密钥绝不落日志/配置。"""
    chat._check_base_url(model_ref.base_url)
    key = secrets.model_api_key.get_secret_value().strip()
    if not key:
        raise ValueError("X-VR-Agent-Model-Key is required")
    return ChatOpenAI(
        model=model_ref.model,
        base_url=model_ref.base_url.rstrip("/"),
        api_key=SecretStr(key),
        temperature=0.2,
        streaming=True,
    )


@dataclass(frozen=True)
class RunSnapshot:
    """脱敏快照 —— 只含模型引用与计数，绝不含密钥。"""

    model_ref: ModelRef
    thread_id: str
    model_calls: int = 0
    tool_calls: int = 0
    transitions: int = 0


class RunConfigMismatch(RuntimeError):
    """resume 请求的模型配置与活动线程不一致。"""

    code = "RUN_CONFIG_MISMATCH"


@dataclass
class RuntimeHandle:
    """一次会话线程的运行时句柄。

    graph/model 是请求级对象（可释放以便跨请求重建等价 Graph），
    checkpointer 与不可变元组跨请求复用，密钥绝不落在本句柄上。
    """

    thread_id: str
    model_ref: ModelRef
    checkpointer: MemorySaver
    tools: tuple[BaseTool, ...]
    # 请求级中间件工厂：resume 时用新 RunSecrets 重建（本句柄绝不保存已构建中间件或密钥）
    middleware_factory: Callable[[RunSecrets], tuple[Any, ...]] | None = None
    system_context: str = ""
    graph: Any | None = None
    # 注意：不保存 model 引用。运行期间 Graph 内部持有模型（spec 允许），
    # 但 handle/coordinator 上不得保留独立的密钥载体（spec：原始 key 不得进入 ActiveRunHandle）。
    model: BaseChatModel | None = None
    model_calls: int = 0
    tool_calls: int = 0
    transitions: int = 0

    @property
    def snapshot(self) -> RunSnapshot:
        return RunSnapshot(
            model_ref=self.model_ref,
            thread_id=self.thread_id,
            model_calls=self.model_calls,
            tool_calls=self.tool_calls,
            transitions=self.transitions,
        )

    def run_input(
        self,
        *,
        protocol_run_id: str,
        messages: Sequence[AgentMessage],
    ) -> RunAgentInput:
        """Graph 输入只来自服务端已清洗历史（partial/pending_interrupt 已排除）。"""
        return RunAgentInput(
            thread_id=self.thread_id,
            run_id=protocol_run_id,
            state={},
            messages=[to_ag_ui_message(message) for message in messages],
            tools=[],
            context=[],
            forwarded_props={},
        )

    def resume_input(self, run_id: str, resume_value: dict[str, Any]) -> RunAgentInput:
        # 纯恢复不携带新消息（messages=[]），避免进入 adapter 的 regenerate 路径。
        return RunAgentInput(
            thread_id=self.thread_id,
            run_id=run_id,
            state={},
            messages=[],
            tools=[],
            context=[],
            forwarded_props={"command": {"resume": resume_value}},
        )

    def new_adapter(self, run_id: str, callbacks: Sequence[Any] = ()) -> LangGraphAgent:
        # 每个请求都新建 LangGraphAgent —— 不缓存、不克隆。
        return LangGraphAgent(
            name=f"vibe-research-{run_id}",
            graph=self.graph,
            config={"callbacks": list(callbacks)},
        )

    def release_graph(self) -> None:
        self.graph = None
        self.model = None


class AgentFactory:
    def create(
        self,
        *,
        model_ref: ModelRef,
        secrets: RunSecrets,
        model_builder: Callable[[ModelRef, RunSecrets], BaseChatModel],
        tools: Sequence[BaseTool],
        thread_id: str,
        checkpointer: MemorySaver | None = None,
        middleware: Sequence[Any] = (),
        system_context: str = "",
        middleware_factory: Callable[[RunSecrets], tuple[Any, ...]] | None = None,
    ) -> RuntimeHandle:
        model = model_builder(model_ref, secrets)
        saver = checkpointer or MemorySaver()
        if middleware_factory is None:
            static_middleware = tuple(middleware)
            middleware_factory = lambda secrets: static_middleware  # noqa: E731
        graph = create_agent(
            model,
            tools=list(tools),
            system_prompt=chat.SYSTEM_PROMPT.format(context="Agent 工作台") + system_context,
            middleware=list(middleware_factory(secrets)),
            checkpointer=saver,
        )
        return RuntimeHandle(
            thread_id=thread_id,
            model_ref=model_ref,
            checkpointer=saver,
            tools=tuple(tools),
            middleware_factory=middleware_factory,
            system_context=system_context,
            graph=graph,
        )

    def resume(
        self,
        *,
        handle: RuntimeHandle,
        model_ref: ModelRef,
        secrets: RunSecrets,
        model_builder: Callable[[ModelRef, RunSecrets], BaseChatModel],
    ) -> None:
        # 先校验配置一致性，再调用 model_builder（密钥只在此时被消费）。
        if handle.model_ref != model_ref:
            raise RunConfigMismatch(
                f"{RunConfigMismatch.code}: resume model config differs from the active thread"
            )
        model = model_builder(model_ref, secrets)
        # 从当前请求的密钥重建请求级中间件（新 guard / HITL 实例，不复用旧对象）
        factory = handle.middleware_factory or (lambda secrets: ())
        handle.graph = create_agent(
            model,
            tools=list(handle.tools),
            system_prompt=chat.SYSTEM_PROMPT.format(context="Agent 工作台") + handle.system_context,
            middleware=list(factory(secrets)),
            checkpointer=handle.checkpointer,
        )


def _content_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def to_ag_ui_message(message: AgentMessage):
    """把已验证的持久化消息转成 AG-UI 消息；不携带 partial/时间戳/run 元数据。"""
    if message.role == "user":
        return UserMessage(id=message.id, content=_content_str(message.content))
    if message.role == "tool":
        return ToolMessage(
            id=message.id,
            content=_content_str(message.content),
            tool_call_id=message.tool_call_id or "",
        )
    tool_calls = [
        ToolCall(
            id=call.get("id", ""),
            function={"name": call.get("name", ""), "arguments": call.get("args", {})},
        )
        for call in message.tool_calls
    ]
    return AssistantMessage(
        id=message.id,
        content=_content_str(message.content),
        tool_calls=tool_calls or None,
    )
