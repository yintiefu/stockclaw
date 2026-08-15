from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ag_ui.core.types import RunAgentInput, UserMessage
from ag_ui_langgraph import LangGraphAgent
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver

import chat
from agent.models import ModelRef, RunSecrets

# 1A 不宣称跨 resume 的转移数上限（见 test_transition_limit）。
PRODUCT_TRANSITION_LIMIT = None


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
    middleware: tuple[Any, ...]
    graph: Any | None = None
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

    def start_input(self, content: str, run_id: str = "protocol-1") -> RunAgentInput:
        return RunAgentInput(
            thread_id=self.thread_id,
            run_id=run_id,
            state={},
            messages=[UserMessage(id=f"{run_id}-user", content=content)],
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
    ) -> RuntimeHandle:
        model = model_builder(model_ref, secrets)
        saver = checkpointer or MemorySaver()
        graph = create_agent(
            model,
            tools=list(tools),
            system_prompt=chat.SYSTEM_PROMPT.format(context="Agent 工作台"),
            middleware=list(middleware),
            checkpointer=saver,
        )
        return RuntimeHandle(
            thread_id=thread_id,
            model_ref=model_ref,
            checkpointer=saver,
            tools=tuple(tools),
            middleware=tuple(middleware),
            graph=graph,
            model=model,
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
        handle.graph = create_agent(
            model,
            tools=list(handle.tools),
            system_prompt=chat.SYSTEM_PROMPT.format(context="Agent 工作台"),
            middleware=list(handle.middleware),
            checkpointer=handle.checkpointer,
        )
        handle.model = model
