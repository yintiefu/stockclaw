"""LangGraph Server 本地 HTTP/SSE 客户端与流式协议适配器。

连接本地 LangGraph Server (默认 http://127.0.0.1:2024)，负责：
- SSE 规范解析 (messages-tuple, custom, updates)；
- 统一网络异常与超时映射 (LangGraphUnavailableError, LangGraphTimeoutError)；
- 工作流与智能体统一调用接口。
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class LangGraphClientError(Exception):
    """LangGraph 客户端通用错误。"""


class LangGraphUnavailableError(LangGraphClientError):
    """LangGraph 服务不可用（未启动或端口不通）。"""


class LangGraphTimeoutError(LangGraphClientError):
    """LangGraph 请求超时。"""


class LangGraphResponseError(LangGraphClientError):
    """LangGraph 服务端返回 HTTP 4xx/5xx 错误。"""

    def __init__(self, status_code: int, message: str, details: Any = None):
        super().__init__(f"LangGraph 服务响应错误 ({status_code}): {message}")
        self.status_code = status_code
        self.details = details


async def parse_sse_stream(byte_stream: AsyncIterator[bytes]) -> AsyncIterator[tuple[str, Any]]:
    """解析标准 SSE 字节流，逐条产出 (event_type, parsed_data)。"""
    buffer = ""

    async for chunk in byte_stream:
        text = chunk.decode("utf-8", errors="replace")
        buffer += text
        while "\n\n" in buffer or "\r\n\r\n" in buffer:
            sep = "\r\n\r\n" if "\r\n\r\n" in buffer else "\n\n"
            block, buffer = buffer.split(sep, 1)
            if not block.strip():
                continue

            event_type = "message"
            data_lines = []

            for line in block.splitlines():
                line = line.strip("\r")
                if not line or line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())

            if data_lines:
                data_str = "\n".join(data_lines)
                try:
                    data_obj = json.loads(data_str)
                except Exception:
                    data_obj = data_str
                yield event_type, data_obj


class LangGraphClient:
    """本地 LangGraph Server 异步客户端。"""

    def __init__(self, base_url: str = "http://127.0.0.1:2024", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_thread(self, thread_id: str | None = None) -> str:
        """创建或获取会话 thread_id。"""
        url = "/threads"
        payload = {"thread_id": thread_id} if thread_id else {}
        try:
            resp = await self._client.post(url, json=payload)
            if resp.status_code >= 400:
                raise LangGraphResponseError(resp.status_code, resp.text)
            data = resp.json()
            return data.get("thread_id") or thread_id or ""
        except httpx.ConnectError as e:
            raise LangGraphUnavailableError(
                f"LangGraph 服务不可用 ({self.base_url})，请检查本地后台是否正常运行。"
            ) from e
        except httpx.TimeoutException as e:
            raise LangGraphTimeoutError("LangGraph 请求创建线程超时") from e

    async def stream_agent(
        self,
        assistant_id: str,
        thread_id: str,
        input_payload: dict[str, Any],
        *,
        stream_mode: list[str] | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """流式调用指定 Agent 或工作流图。"""
        modes = stream_mode or ["messages-tuple", "custom", "updates"]
        url = f"/threads/{thread_id}/runs/stream"
        payload = {
            "assistant_id": assistant_id,
            "input": input_payload,
            "stream_mode": modes,
            "multitask_strategy": "reject",
        }

        try:
            async with self._client.stream("POST", url, json=payload) as resp:
                if resp.status_code >= 400:
                    err_text = await resp.aread()
                    raise LangGraphResponseError(resp.status_code, err_text.decode("utf-8", errors="replace"))

                async for event_type, data in parse_sse_stream(resp.aiter_bytes()):
                    yield event_type, data
        except httpx.ConnectError as e:
            raise LangGraphUnavailableError(
                f"LangGraph 服务不可用 ({self.base_url})，请检查本地后台是否正常运行。"
            ) from e
        except httpx.TimeoutException as e:
            raise LangGraphTimeoutError("LangGraph 流式请求超时") from e

    async def stream_workflow(
        self,
        workflow_id: str,
        thread_id: str,
        input_data: dict[str, Any],
        variant: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式执行指定工作流，产出强类型自定义事件。"""
        payload: dict[str, Any] = {"input": input_data}
        if variant:
            payload["variant"] = variant

        async for event_type, data in self.stream_agent(
            assistant_id=workflow_id,
            thread_id=thread_id,
            input_payload=payload,
            stream_mode=["custom", "updates"],
        ):
            if event_type == "custom" and isinstance(data, dict):
                yield data
