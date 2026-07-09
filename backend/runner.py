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

import asyncio
import json
import re
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
    # 任何 /vN 结尾都视为已带版本段（覆盖 /v1, /v3, /api/paas/v4, /api/coding/paas/v4 等）
    if not re.search(r"/v\d+$", base):
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
    """运行 agent，吐 NDJSON 事件流。

    Task 4 改造：graph 与 LLM 并发，消除 5s 首字延迟。
    tool_trace 非实时（一次性 dump）；decision_failed 时发 error 事件。
    """
    decision_id = uuid.uuid4().hex
    try:
        graph_state = {
            "messages": req.messages,
            "context_codes": req.context_codes,
            "style": req.style,
            "thread_id": req.thread_id or decision_id,
        }

        # 并发：graph 在后台跑，立刻开始流 LLM
        graph_task = asyncio.create_task(agent_graph.ainvoke(graph_state))

        try:
            # LLM 流式（不依赖 graph 结果——决策卡自己有结构化数字）
            async for text in _stream_llm_text(
                req.llm.model_dump(), SYSTEM_PROMPT_AGENT, req.messages, req.context_codes,
            ):
                yield {"type": "text_delta", "text": text}

            # 等 graph 完成（如果还没好）
            graph_result = await graph_task
        except BaseException:
            # LLM 流挂了 / 用户 abort：取消 graph 防 leak
            # （否则后台工具继续跑：rate-limiter 占用、可能写脏数据、
            # GC 时报 "Task was destroyed but it is pending!"）
            graph_task.cancel()
            try:
                await graph_task
            except (asyncio.CancelledError, Exception):
                pass
            raise

        decision_card = graph_result.get("decision_card")
        graph_intent = graph_result.get("intent")

        # tool_traces 都推（失败/成功都看）——评审 Minor #1 合并两处循环
        for trace in (graph_result.get("tool_traces") or []):
            yield {"type": "tool_trace", **trace}

        # 评审 #1：工具全失败时显式发 error，不静默 done
        if graph_intent == "decision_failed":
            yield {
                "type": "error",
                "message": "未能获取股票数据，无法生成决策卡。请确认代码是否正确（如 600519）或稍后重试。",
            }
            yield {"type": "done", "summary": {"thread_id": req.thread_id or decision_id, "failed": True}}
            return

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
            # 入库 decisions 表（Phase 3 收益追踪用）
            try:
                from persistence import decisions as decisions_dao, threads as threads_dao
                # 评审 #2：req.thread_id 为空时先建 thread 行，避免 FK 违反
                effective_tid = req.thread_id
                if not effective_tid:
                    effective_tid = await threads_dao.create_thread(
                        title=f"决策 · {decision_card.get('code', '')}",
                        model=req.llm.model,
                    )
                else:
                    # 已传 thread_id 但可能不在 threads 表（前端没先建）→ 兜底建
                    if not await threads_dao.get_thread(effective_tid):
                        await threads_dao.create_thread(
                            tid=effective_tid,
                            title=f"决策 · {decision_card.get('code', '')}",
                            model=req.llm.model,
                        )
                await decisions_dao.create_decision(
                    thread_id=effective_tid,
                    code=decision_card.get("code", ""),
                    name=decision_card.get("name"),
                    target_price=decision_card.get("target_price") or 0.0,
                    entry_low=decision_card.get("entry_low") or 0.0,
                    entry_high=decision_card.get("entry_high") or 0.0,
                    stop_loss=decision_card.get("stop_loss") or 0.0,
                    take_profit=decision_card.get("take_profit") or 0.0,
                    cadence=decision_card.get("cadence") or [],
                    basis_type=decision_card.get("basis_type") or "model",
                    model_versions_json=decision_card.get("model_versions_json") or {},
                    assumptions=decision_card.get("assumptions") or [],
                    citations=decision_card.get("citations") or [],
                    raw_artifact=decision_card,
                )
            except Exception as persist_err:
                # 入库失败不阻塞流（用 console 记录）
                print(f"[runner] decision persist failed: {persist_err}", flush=True)

        yield {"type": "done", "summary": {"thread_id": req.thread_id or decision_id}}

    except Exception as e:
        yield {"type": "error", "message": f"agent 运行失败：{e}"}
