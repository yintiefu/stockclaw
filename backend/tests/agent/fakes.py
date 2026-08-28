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


FAKE_TOOL_RESULTS: dict[str, object] = {
    "query_quote": {"600519": {"code": "600519", "name": "贵州茅台", "price": 1500.0, "change_pct": 1.2}},
    "query_valuation": {"pe_ttm": 25.0, "pb": 8.1, "agree": "一致预期脚本数据"},
    "query_valuation_percentile": {"pe_percentile": 35.6, "pb_percentile": 48.2},
    "query_financials": {"revenue": 88_800_000_000.0, "net_profit": 41_200_000_000.0, "roe": 32.5},
    "query_kline": [{"date": "2026-08-24", "close": 1495.0}, {"date": "2026-08-25", "close": 1500.0}],
    "query_fund_flow": [{"date": "2026-08-25", "net": -120_000_000.0}],
    "query_margin": [{"date": "2026-08-25", "margin_balance": 21_300_000_000.0}],
    "query_holders": {"holder_count": 82_913, "change": -3211},
    "query_announcements": [{"date": "2026-08-20", "title": "确定性公告标题"}],
    "query_lockup": [{"date": "2026-09-30", "shares": 8_000_000}],
    "query_concepts": {},
    "query_reports": [{"date": "2026-08-18", "title": "确定性研报标题"}],
    "query_news": [{"发布时间": "2026-08-25", "新闻标题": "确定性新闻标题"}],
}


def install_fake_exec_tool(monkeypatch) -> None:
    """离线拦截 tools.exec_tool（tool_executor 运行期按属性查找模块对象）。
    不 fake 会触真实数据源；query_concepts 留空制造真实缺口，其余给足实质
    数据避免 abort_no_data。"""
    import tools as legacy_tools

    monkeypatch.setattr(
        legacy_tools, "exec_tool",
        lambda name, args: FAKE_TOOL_RESULTS.get(name, {"error": f"fixture 未实现工具 {name}"}),
    )
