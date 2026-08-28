import re
from collections import deque
from typing import Any, ClassVar, Iterator, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool


class ScriptedChatModel(BaseChatModel):
    """离线脚本化模型：按顺序吐出预设回复，并记录每次收到的消息列表。"""

    replies: deque[AIMessage]
    invocations: list[list[BaseMessage]]

    def __init__(self, replies: Sequence[AIMessage]):
        super().__init__(replies=deque(replies), invocations=[])

    @property
    def _llm_type(self) -> str:
        return "scripted-agent-spike"

    def bind_tools(self, tools: Sequence[BaseTool | dict[str, Any]], **kwargs: Any):
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.invocations.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.replies.popleft())])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        # 流式输出与 _generate 同步记录调用面，供测试断言第二次模型调用内容。
        self.invocations.append(list(messages))
        message = self.replies.popleft()
        if message.content:
            yield ChatGenerationChunk(message=AIMessageChunk(content=message.content))
        else:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_calls=message.tool_calls))


class PausingChatModel(ScriptedChatModel):
    """首个回复前阻塞一段时间（sync sleep 在线程池里跑，不卡事件循环），
    让 http.disconnect 有机会在流式响应中途被处理。"""

    pause_seconds: float = 0.2

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        import time
        time.sleep(self.pause_seconds)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _plain_text(content: object) -> str:
    """把 str 或 content-block 列表规整为纯文本（SystemMessage 常为块列表）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", "")) for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


class SkillsAwareScriptedModel(ScriptedChatModel):
    """在脚本回复之上识别「列出当前技能」：从收到的系统提示提取可见技能名。

    E2E 用它断言 SkillsMiddleware 实际注入 Agent 的视图（导入/启停/reload
    的可见性变化），不需要真实模型。流式与非流式两条路径共用同一回复选择。
    """

    LIST_COMMAND: ClassVar[str] = "列出当前技能"
    APPROVE_COMMAND: ClassVar[str] = "触发工具审批"
    _SKILL_LINE: ClassVar[re.Pattern[str]] = re.compile(
        r"^-\s+\*\*(?P<name>[a-z0-9][a-z0-9-]*)\*\*", re.MULTILINE
    )

    def _pick_reply(self, messages: list[BaseMessage]) -> AIMessage:
        last_user = next(
            (message for message in reversed(messages) if isinstance(message, HumanMessage)),
            None,
        )
        if last_user is not None and _plain_text(last_user.content).strip() == self.APPROVE_COMMAND:
            # 拒绝/批准后图会带着既有 tool_call 再次调模型：只在首次请求该工具调用
            already_called = any(
                isinstance(message, AIMessage)
                and any(call.get("id") == "skills-aware-approval-call" for call in (message.tool_calls or []))
                for message in messages
            )
            if already_called:
                return AIMessage(content="拒绝已记录，工具未执行。")
            return AIMessage(content="", tool_calls=[{
                "id": "skills-aware-approval-call",
                "name": "fixture_echo",
                "args": {"value": "待审批"},
            }])
        if last_user is not None and _plain_text(last_user.content).strip() == self.LIST_COMMAND:
            system_text = "\n".join(
                _plain_text(message.content)
                for message in messages if isinstance(message, SystemMessage)
            )
            names = sorted(set(self._SKILL_LINE.findall(system_text)))
            return AIMessage(content="可见技能：" + (", ".join(names) if names else "无"))
        return self.replies.popleft()

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.invocations.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self._pick_reply(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        self.invocations.append(list(messages))
        message = self._pick_reply(messages)
        if message.content:
            yield ChatGenerationChunk(message=AIMessageChunk(content=message.content))
        else:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_calls=message.tool_calls))
