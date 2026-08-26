from collections import deque
from typing import Any, Iterator, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
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
