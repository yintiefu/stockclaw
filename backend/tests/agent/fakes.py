from collections import deque
from typing import Any, Iterator, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool


class ScriptedChatModel(BaseChatModel):
    """离线脚本化模型：按顺序吐出预设回复，供协议/运行时测试复用。"""

    replies: deque[AIMessage]

    def __init__(self, replies: Sequence[AIMessage]):
        super().__init__(replies=deque(replies))

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
        return ChatResult(generations=[ChatGeneration(message=self.replies.popleft())])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        # ag-ui-langgraph 只从 on_chat_model_stream 生成 TEXT_MESSAGE_* 事件，
        # 因此脚本化模型必须支持流式输出。
        message = self.replies.popleft()
        if message.content:
            yield ChatGenerationChunk(message=AIMessageChunk(content=message.content))
        else:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_calls=message.tool_calls))
