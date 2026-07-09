"""Agent 运行入口——FastAPI /api/agent/chat 调它。

输出 NDJSON 事件流（spec §7 协议）：
- text_delta: 助手回答文本增量
- tool_trace: 工具调用记录（status: running/ok/error）
- decision_artifact: Decision Node 生成的决策卡
- citations: 数据出处批量上报
- done: 流正常结束
- error: 异常上报，流提前终止
"""
from __future__ import annotations

import json
import uuid
from typing import Any, AsyncGenerator

import httpx
from pydantic import BaseModel

from agents.graph import agent_graph
from agents.prompts import SYSTEM_PROMPT_AGENT


class LLMConfig(BaseModel):
    provider: str = ""
    baseURL: str = ""
    apiKey: str = ""
    model: str


class AgentChatReq(BaseModel):
    """spec §7 NDJSON 协议请求体。"""
    thread_id: str | None = None
    messages: list[dict]
    context_codes: list[str] = []
    llm: LLMConfig
    style: str = "balanced"


async def _stream_llm_text(cfg: dict, system_prompt: str, user_messages: list[dict],
                           context_codes: list[str]) -> AsyncGenerator[str, None]:
    """调上游 OpenAI 兼容端点的流式接口，逐 chunk yield 文本 delta。

    用 httpx.AsyncClient 真正异步流式——不能退化到 requests.post(stream=True)，
    那会阻塞 FastAPI event loop。
    """
    base = cfg["baseURL"].rstrip("/")
    if not base.endswith(("/v1", "/v3", "/api/v3", "/api/paas/v4")):
        base = base + "/v1"
    context_str = "；".join(context_codes) if context_codes else "（无）"
    messages = [{"role": "system", "content": system_prompt.format(context=context_str)}]
    messages.extend(user_messages)

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['apiKey']}", "Content-Type": "application/json"},
            json={"model": cfg["model"], "messages": messages, "temperature": 0.3, "stream": True},
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(f"模型接口 HTTP {resp.status_code}: {body.decode('utf-8', errors='replace')[:300]}")

            # SSE 流式解析：按行读，data: 前缀 + [DONE] 结束
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    j = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = j.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield text


async def run_agent(req: AgentChatReq) -> AsyncGenerator[dict, None]:
    """主入口：跑 graph + 流式输出 NDJSON 事件。"""
    decision_id = uuid.uuid4().hex
    try:
        # Step 1：调 graph 拿 decision_card
        graph_state = {
            "messages": req.messages,
            "context_codes": req.context_codes,
            "style": req.style,
            "thread_id": req.thread_id or decision_id,
        }
        graph_result = await agent_graph.ainvoke(graph_state)
        decision_card = graph_result.get("decision_card")

        # 流式 LLM 文本（决策卡作 context 加强）
        if decision_card:
            summary = (
                f"基于工具结果：目标价 {decision_card.get('target_price')}，"
                f"止损 {decision_card.get('stop_loss')}，止盈 {decision_card.get('take_profit')}，"
                f"依据 {decision_card.get('basis_type')}。"
            )
            # 注：用 user 角色 + 显式前缀，避免伪造 assistant turn 让 LLM 误以为是自己刚说的
            enhanced_messages = list(req.messages) + [{
                "role": "user", "content": f"[系统注入·工具结果摘要] {summary}"
            }]
        else:
            enhanced_messages = req.messages

        async for text in _stream_llm_text(
            req.llm.model_dump(), SYSTEM_PROMPT_AGENT, enhanced_messages, req.context_codes,
        ):
            yield {"type": "text_delta", "text": text}

        # 推决策卡 artifact
        if decision_card:
            yield {
                "type": "decision_artifact",
                "decision_id": decision_id,
                "data": decision_card,
            }
            yield {
                "type": "citations",
                "items": decision_card.get("citations") or [],
            }

        yield {"type": "done", "summary": {"thread_id": req.thread_id or decision_id}}

    except Exception as e:
        yield {"type": "error", "message": f"agent 运行失败：{e}"}
