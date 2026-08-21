"""1D 端到端（Playwright）后端应用：生产 Agent 路由 + 确定性本地接缝。

只替换三处，其余（治理、审批、存储、Policy、Artifact、REST）全部走生产代码：
1. 模型构建 → E2EChatModel（按最新用户消息关键词路由：工具/审批/产物/版本/慢速/失败）；
2. MCP 会话 → FakeMcpSession（进程内 list_tools/call_tool，零网络）；
3. 本地内置工具 → fetch_quote fixture。

安全边界：必须提供非默认的临时数据根（VR_E2E_DATA_DIR），且解析后不得等于
默认用户数据根；否则拒绝启动。种子数据只经生产 stores 写入。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.stores import utc_now  # noqa: E402


def _resolve_data_root() -> Path:
    raw = os.environ.get("VR_E2E_DATA_DIR") or tempfile.mkdtemp(prefix="vr-agent-e2e-")
    root = Path(raw).resolve()
    # 自然默认根 = 未设置 VR_DATA_DIR 时的用户数据根；e2e 数据根不得等于或包含它
    natural = (Path.home() / ".vibe-research" / "agent").resolve()
    if root == natural or natural.is_relative_to(root) or root.is_relative_to(natural):
        raise RuntimeError("VR_E2E_DATA_DIR 与默认用户数据根重叠，拒绝启动 e2e 应用")
    return root


ROOT = _resolve_data_root()
# 任何间接的默认路径查找也保持在临时根内，绝不触碰用户数据
os.environ.setdefault("VR_DATA_DIR", str(ROOT.parent))

import agent.mcp as mcp_module  # noqa: E402
import agent.router as router_module  # noqa: E402
from agent.mcp import (  # noqa: E402
    McpHealth,
    McpServer,
    McpToolCatalogEntry,
    StreamableHttpTransport,
    _SessionGeneration,
    mcp_alias,
)
from agent.models import (  # noqa: E402
    AgentMessage,
    ModelRef,
    RunDocument,
    RunSummary,
    ThreadDocument,
)
from agent.router import build_services, shutdown_agent_services, startup_agent_services  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from langchain_core.callbacks import CallbackManagerForLLMRun  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import (  # noqa: E402
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult  # noqa: E402
from langchain_core.tools import BaseTool, tool  # noqa: E402
from mcp.types import CallToolResult, TextContent  # noqa: E402
from mcp.types import Tool as McpToolDescriptor  # noqa: E402

MCP_SERVER_ID = "fixture"
MCP_TOOL_NAME = "quote_lookup"
MCP_TOOL_ALIAS = mcp_alias(MCP_SERVER_ID, MCP_TOOL_NAME)
MODEL_REF = ModelRef(provider="fixture", baseURL="https://fixture.invalid/v1", model="fixture-model")

# 进程级一次性状态（单 worker 运行，保证确定性）
_FAIL_ONCE_SEEN = False

HISTORY_THREAD_ID = "th-history"
LIVE_THREAD_ID = "th-live"
HISTORY_RUN_COUNT = 60


@tool
def fetch_quote(code: str) -> str:
    """查询 A 股收盘价与成交量（fixture 客观数据，不访问网络）。"""
    return json.dumps({
        "code": code,
        "name": "贵州茅台",
        "close": 1500.0,
        "volume": 25000,
        "source": "fixture",
    }, ensure_ascii=False)


def _artifact_v2_call(parent_id: str) -> dict[str, Any]:
    return {
        "id": "call-artifact-v2",
        "name": "create_artifact",
        "args": {
            "type": "markdown",
            "title": "客观数据整理（第 2 版）",
            "content": {"markdown": "# 第 2 版\n\n在第一版基础上补充客观记录（fixture）。"},
            "parent_artifact_id": parent_id,
        },
    }


class E2EChatModel(BaseChatModel):
    """离线行为模型：按最新用户消息关键词路由，工具轮 → 收尾轮自动衔接。"""

    @property
    def _llm_type(self) -> str:
        return "e2e-fixture"

    def bind_tools(self, tools: Sequence[BaseTool | dict[str, Any]], **kwargs: Any):
        return self

    def _route(self, messages: list[BaseMessage]) -> AIMessage:
        global _FAIL_ONCE_SEEN
        last = messages[-1] if messages else None
        if isinstance(last, ToolMessage):
            name = last.name or ""
            text = last.content if isinstance(last.content, str) else json.dumps(last.content, ensure_ascii=False)
            if name == "create_artifact":
                if last.tool_call_id == "call-artifact":
                    try:
                        parent = json.loads(text).get("artifact", {}).get("id", "")
                    except Exception:
                        parent = ""
                    if parent:
                        return AIMessage(content="", tool_calls=[_artifact_v2_call(parent)])
                return AIMessage(content="已整理为不可变 Artifact，可在 Inspector 查看版本链。")
            if name.startswith("mcp__"):
                if "reject" in text.lower() or "拒绝" in text:
                    return AIMessage(content="工具调用被拒绝，本轮按拒绝结果结束。")
                return AIMessage(content="审批后的 MCP 工具返回了客观数据。")
            return AIMessage(
                content="工具返回客观数据（收盘价 1500.0，成交量 25000）。"
                        "更多原始记录见 [行情参考](https://example.com/quote/600519)")
        user_text = ""
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                user_text = str(message.content)
                break
        if "审批" in user_text:
            return AIMessage(content="", tool_calls=[{
                "id": "call-approval", "name": MCP_TOOL_ALIAS, "args": {"symbol": "600519"}}])
        if "产物" in user_text or "整理" in user_text:
            return AIMessage(content="", tool_calls=[{
                "id": "call-artifact",
                "name": "create_artifact",
                "args": {
                    "type": "markdown",
                    "title": "贵州茅台客观数据整理",
                    "content": {"markdown": (
                        "# 客观数据（fixture）\n\n"
                        "- 收盘价 1500.0\n- 成交量 25000\n\n"
                        "原始记录：[行情参考](https://example.com/quote/600519)"
                    )},
                    "sources": [{"kind": "url", "url": "https://example.com/quote/600519", "label": "行情参考"}],
                }}])
        if "慢" in user_text:
            time.sleep(3.0)
            return AIMessage(content="慢速回复完成。")
        if "失败" in user_text:
            if not _FAIL_ONCE_SEEN:
                _FAIL_ONCE_SEEN = True
                raise RuntimeError("fixture 模型第一次执行失败（预期内）")
            return AIMessage(content="重试成功：客观数据已返回。")
        if "你好" in user_text:
            return AIMessage(content="你好，这里是 fixture 模型的纯文本回复。")
        return AIMessage(content="", tool_calls=[{
            "id": "call-quote", "name": "fetch_quote", "args": {"code": "600519"}}])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._route(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        # ag-ui-langgraph 只从 on_chat_model_stream 生成文本事件，必须支持流式
        message = self._route(messages)
        if message.content:
            for index in range(0, len(message.content), 8):
                yield ChatGenerationChunk(message=AIMessageChunk(
                    content=message.content[index:index + 8]))
        else:
            yield ChatGenerationChunk(message=AIMessageChunk(
                content="", tool_calls=message.tool_calls))


class _FakeListResult:
    def __init__(self, tools: list[McpToolDescriptor]):
        self.tools = tools
        self.nextCursor = None


class FakeMcpSession:
    """进程内 MCP 会话：与 mcp.types 兼容，list_tools/call_tool 零网络。"""

    async def list_tools(self, cursor: str | None = None) -> _FakeListResult:
        return _FakeListResult([McpToolDescriptor(
            name=MCP_TOOL_NAME,
            description="查询行情（fixture，不访问网络）",
            inputSchema={
                "type": "object",
                "properties": {"symbol": {"type": "string", "description": "股票代码"}},
                "required": ["symbol"],
            },
        )])

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        payload = json.dumps({
            "symbol": (arguments or {}).get("symbol"),
            "close": 1500.0,
            "volume": 25000,
            "source": "fixture-mcp",
        }, ensure_ascii=False)
        return CallToolResult(content=[TextContent(type="text", text=payload)])


async def _fake_build_session(self: "mcp_module.McpRegistry", server: McpServer) -> _SessionGeneration:
    """替换真实 transport：直接返回持 FakeMcpSession 的 accepting 世代。"""
    if self._shutting_down:
        raise mcp_module.McpError("MCP_UNAVAILABLE: Registry 正在关闭")
    if server.is_stdio:
        raise mcp_module.McpError("e2e fixture 仅支持 streamable_http 传输")
    self._counter += 1
    generation = _SessionGeneration(number=self._counter, server_id=server.id)
    generation.client = FakeMcpSession()
    generation.ready.set()
    self._secret_sets.setdefault(server.id, set())
    return generation


def _seed_mcp(services) -> None:
    server = McpServer(
        id=MCP_SERVER_ID,
        display_name="Fixture 行情",
        enabled=True,
        transport=StreamableHttpTransport(
            type="streamable_http", url="https://mcp.fixture.invalid/mcp", headers={}),
        tools=[McpToolCatalogEntry(
            original_name=MCP_TOOL_NAME,
            alias=MCP_TOOL_ALIAS,
            description="查询行情（fixture，不访问网络）",
            input_schema={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
            enabled=True,
            discovered_at=utc_now(),
        )],
        health=McpHealth(state="ok", detail="fixture", checked_at=utc_now()),
    )
    services.registry.store.update(
        0, lambda doc: doc.model_copy(update={"revision": doc.revision + 1, "servers": [server]}))


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _seed_history(services) -> None:
    base = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
    user_message = AgentMessage(
        id="msg-hist-user", role="user", content="历史问题", created_at=_iso(base))
    assistant_message = AgentMessage(
        id="msg-hist-assistant", role="assistant", content="历史答复", created_at=_iso(base))
    last_summary: RunSummary | None = None
    for index in range(HISTORY_RUN_COUNT):
        moment = base + timedelta(minutes=index)
        stamp = _iso(moment)
        status = "failed" if index % 9 == 0 else "completed"
        run = RunDocument(
            id=f"run-hist-{index:03d}",
            thread_id=HISTORY_THREAD_ID,
            protocol_run_ids=[f"proto-hist-{index:03d}"],
            trigger_message_id=user_message.id,
            status=status,
            started_at=stamp,
            updated_at=stamp,
            ended_at=stamp,
            model_ref=MODEL_REF,
            error_code="MODEL_CALL_FAILED" if status == "failed" else None,
            error_message="fixture 历史失败" if status == "failed" else None,
        )
        services.runs.replace(run)
        last_summary = RunSummary(id=run.id, status=run.status, updated_at=stamp, retry_of=None)
    thread = ThreadDocument.new(HISTORY_THREAD_ID, "历史运行", now=_iso(base))
    services.threads.create(thread.model_copy(update={
        "revision": 1,
        "messages": [user_message, assistant_message],
        "last_run": last_summary,
    }))


def _seed_live_thread(services) -> None:
    moment = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
    thread = ThreadDocument.new(LIVE_THREAD_ID, "交互会话", now=_iso(moment))
    services.threads.create(thread)


def _seed(services) -> None:
    # ThreadStore.list_documents 返回 (documents, warnings) 元组
    existing, _warnings = services.threads.list_documents()
    if existing:
        return
    _seed_mcp(services)
    _seed_history(services)
    _seed_live_thread(services)


# ---- 组装：替换三处接缝，其余保持生产组合 ----

_services = build_services(ROOT)
router_module.services = _services
router_module.build_chat_model = lambda ref, secrets: E2EChatModel()
router_module.build_builtin_tools = lambda: [fetch_quote]
mcp_module.McpRegistry._build_session = _fake_build_session


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await startup_agent_services()
    await asyncio.to_thread(_seed, _services)
    yield
    await shutdown_agent_services()


app = FastAPI(title="vibe-research agent e2e", lifespan=lifespan)
app.include_router(router_module.router)
