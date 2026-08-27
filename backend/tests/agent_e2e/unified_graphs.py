"""Task 13 浏览器 E2E：确定性统一工作流图（脚本化模型 + 假工具，零真实网络）。

- `tools.exec_tool` 在本模块导入时被替换为固定结果（tool_executor 运行期按
  属性查找同一模块对象，辩论底稿 / 嵌入式工具调用全部确定性化）；
- 辩论模型按系统提示中的角色标记输出对应文本（顺序无关，重试也稳定），
  并带小延迟让「中止 / 重试 / 恢复」可被浏览器触发；
- 反思 / 复盘 / 资讯 / 嵌入式使用循环脚本模型，耗尽后回落默认回复。
"""
from __future__ import annotations

import asyncio
import time

import tools as legacy_tools
from langchain_core.messages import AIMessage
from tests.agent.fakes import ScriptedChatModel

# --------------------------------------------------------------------------
# 1) 假工具：13 项辩论底稿 + 嵌入式常用查询全部返回确定性数据
# --------------------------------------------------------------------------
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
    "query_concepts": {},  # 制造一个真实数据缺口：页面应显示「未取到」
    "query_reports": [{"date": "2026-08-18", "title": "确定性研报标题"}],
    "query_news": [{"发布时间": "2026-08-25", "新闻标题": "确定性新闻标题"}],
}


def _fake_exec_tool(name: str, args: dict) -> object:
    result = FAKE_TOOL_RESULTS.get(name)
    if result is None:
        return {"error": f"fixture 未实现工具 {name}"}
    return result


legacy_tools.exec_tool = _fake_exec_tool  # noqa: F811 — 故意替换给 tool_executor 用


# --------------------------------------------------------------------------
# 2) 脚本化模型
# --------------------------------------------------------------------------
class CyclingScriptedModel(ScriptedChatModel):
    """回复耗尽后回落默认文本：多轮 / 重试场景绝不 IndexError、绝不联网。"""

    default_reply: str = "脚本化确定性回复。"

    def __init__(self, replies=(), default_reply: str = "脚本化确定性回复。"):
        super().__init__(replies)
        self.default_reply = default_reply

    def _pick_reply(self, messages: list) -> AIMessage:
        if self.replies:
            return self.replies.popleft()
        return AIMessage(content=self.default_reply)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.invocations.append(list(messages))
        from langchain_core.outputs import ChatGeneration, ChatResult
        return ChatResult(generations=[ChatGeneration(message=self._pick_reply(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.invocations.append(list(messages))
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk
        message = self._pick_reply(messages)
        yield ChatGenerationChunk(message=AIMessageChunk(content=message.content))


class StageAwareDebateModel(CyclingScriptedModel):
    """辩论阶段感知：按系统提示中的角色标记输出，顺序无关、重试稳定。

    每个阶段流式前 sleep，保证浏览器有窗口点「中止」。延迟必须显著大于 v2
    SSE 的首事件/心跳窗口（~5s）：新线程上 SDK 的 values 订阅要等首个可 flush
    事件/心跳才能就绪——run 若短于该窗口，中间态只能 run 结束后整段回放，
    「生成中/中止」场景将永远抓不到运行中 UI。
    """

    stage_delay_seconds: float = 4.0

    def _pick_reply(self, messages: list) -> AIMessage:
        system_text = "".join(
            str(m.content) for m in messages if getattr(m, "type", "") == "system"
        )
        if "中立主持人" in system_text:
            return AIMessage(content="中立主持脚本归纳：双方分歧点与待核验清单，不裁决多空。")
        if "上面是空方的质疑" in system_text:
            return AIMessage(content="多方反驳脚本：承认部分质疑并给出数据回应。")
        if "上面是多方的论述" in system_text:
            return AIMessage(content="空方反驳脚本：承认部分论点并质疑数据口径。")
        if "空方研究员" in system_text:
            return AIMessage(content="空方脚本观点：估值与资金面风险证据。")
        return AIMessage(content="多方脚本观点：业绩与估值分位支撑证据。")

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        time.sleep(self.stage_delay_seconds)
        yield from super()._stream(messages, stop, run_manager, **kwargs)


# --------------------------------------------------------------------------
# 3) 组装五个非 workspace 图（agent 图沿用 graph.py 的 MCP 审批脚本）
# --------------------------------------------------------------------------
from agent.embedded_graph import build_embedded_graph  # noqa: E402
from agent.skill_backends import BUILTIN_SKILLS_DIR  # noqa: E402
from agent.workflow_builder import build_workflow_graph  # noqa: E402
from agent.workflow_loader import load_all_production_workflows  # noqa: E402
from agent.workflows_graph import WORKFLOWS_DIR  # noqa: E402

_CONFIGS = load_all_production_workflows(WORKFLOWS_DIR, BUILTIN_SKILLS_DIR)

embedded_graph = asyncio.run(build_embedded_graph(
    model=CyclingScriptedModel([
        AIMessage(content="嵌入式脚本回答：基于页面快照的客观说明。"),
        AIMessage(content="嵌入式脚本回答：基于页面快照的客观说明。"),
        AIMessage(content="嵌入式脚本回答：基于页面快照的客观说明。"),
        AIMessage(content="嵌入式脚本回答：基于页面快照的客观说明。"),
    ], default_reply="嵌入式脚本回答：基于页面快照的客观说明。"),
    builtin_skills_root=BUILTIN_SKILLS_DIR,
))

debate_graph = build_workflow_graph(
    _CONFIGS["debate"], model=StageAwareDebateModel([]), builtin_skills_root=BUILTIN_SKILLS_DIR,
)
reflection_graph = build_workflow_graph(
    _CONFIGS["reflection"],
    model=CyclingScriptedModel([AIMessage(content="反思脚本审计：两处推理缺数据支撑，一处口径不一致。")]),
    builtin_skills_root=BUILTIN_SKILLS_DIR,
)
daily_review_graph = build_workflow_graph(
    _CONFIGS["daily_review"],
    model=CyclingScriptedModel([AIMessage(content="复盘脚本结论：缩量整理，量能与情绪指标列举。")]),
    builtin_skills_root=BUILTIN_SKILLS_DIR,
)
news_digest_graph = build_workflow_graph(
    _CONFIGS["news_digest"],
    model=CyclingScriptedModel([AIMessage(content="资讯脚本要点：三条确定性要点归纳。")]),
    builtin_skills_root=BUILTIN_SKILLS_DIR,
)
