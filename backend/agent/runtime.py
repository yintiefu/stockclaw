from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ag_ui.core.types import RunAgentInput, UserMessage
from ag_ui_langgraph import LangGraphAgent
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver

import chat


@dataclass
class RuntimeHandle:
    """一次会话线程的运行时句柄：Graph/模型请求级可释放，检查点跨请求复用。"""

    thread_id: str
    graph: Any
    checkpointer: MemorySaver
    model: BaseChatModel
    tools: Sequence[BaseTool]

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

    def new_adapter(self, run_id: str) -> LangGraphAgent:
        # 每个请求都新建 LangGraphAgent —— 不缓存、不克隆。
        return LangGraphAgent(name=f"vibe-research-{run_id}", graph=self.graph)


class AgentFactory:
    def create(
        self,
        *,
        model: BaseChatModel,
        tools: Sequence[BaseTool],
        thread_id: str,
        checkpointer: MemorySaver | None = None,
        middleware: Sequence[Any] = (),
    ) -> RuntimeHandle:
        saver = checkpointer or MemorySaver()
        graph = create_agent(
            model,
            tools=list(tools),
            system_prompt=chat.SYSTEM_PROMPT.format(context="Agent 工作台"),
            middleware=list(middleware),
            checkpointer=saver,
        )
        return RuntimeHandle(thread_id, graph, saver, model, tuple(tools))
