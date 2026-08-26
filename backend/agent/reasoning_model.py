"""保留第三方 OpenAI 兼容上游（智谱 GLM 等）思考输出的 ChatOpenAI 子类。

`ChatOpenAI` 只针对官方 OpenAI 规范：流式 delta 里的非标准字段
`reasoning_content` 会在 `_convert_delta_to_message_chunk` 中被直接丢弃
（见其模块 docstring），因此即使上游开了 thinking，思考文本也到不了前端。

本子类做两处最小改写：

**输出侧**（`_convert_chunk_to_generation_chunk`）：

- `reasoning_content` 增量 → canonical 的 `reasoning` content block。必须是
  canonical 类型（而非 `thinking`）：LangGraph 的 v3 流式协议（前端后续轮次
  走 `/threads/{id}/commands` 时启用）会把消息内容规范化成 v1 块，非规范
  类型被包成 `non_standard` 且逐块覆盖不拼接，思考会丢失（实测只剩末位
  增量）；canonical `reasoning` 块则按块正确累积。
- text 增量同步转成 `text` block：`merge_content` 对「list + 字符串」会把裸
  字符串追加进列表，前端转换层不认字符串元素（会丢正文），必须全程 block。
- 块带固定 `index`，`merge_lists` 据此把连续增量合并进同一块（字符串拼接）。
  **索引必须用 `lc_` 前缀字符串，不能用整数**：LangGraph 事件流桥
  （`langchain_core` `chunks_to_events`）以块的 `index` 字段作为源标识合并
  增量，而上游 tool_calls 增量带的是从 0 开始的整数索引——reasoning 块若
  占用整数 0，工具调用轮的第一条 tool_call 增量（index 0）会与其同键，
  累积时把 reasoning 块整体覆盖，思考文本就此从 checkpoint 消失（表现为
  历史会话只剩最后一轮无工具调用的思考）。`lc_` 前缀字符串是官方认可的
  源标识形态（`merge_lists` 只认整数或 `lc_` 前缀字符串作合并键）。

**请求侧**（`_get_request_payload`）：历史 AI 消息里的 `reasoning` /
`thinking` 块（含被 v1 规范化包进 `non_standard` 的）在上游请求前剥离——
langchain 只自动剥 `thinking`，而智谱等第三方对未知内容块返回 400（实测）。
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_openai import ChatOpenAI

# reasoning / text 合成块的稳定块索引：lc_ 前缀字符串命名空间，与上游
# tool_calls 增量的整数索引（0,1,2,…）永不相交（见模块 docstring）。
_REASONING_BLOCK_INDEX = "lc_rs_0"
_TEXT_BLOCK_INDEX = "lc_txt_0"


def _is_thinking_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    if block.get("type") in ("reasoning", "thinking"):
        return True
    value = block.get("value")
    return (
        block.get("type") == "non_standard"
        and isinstance(value, dict)
        and value.get("type") in ("reasoning", "thinking")
    )


def _strip_thinking(messages: list[BaseMessage]) -> list[BaseMessage]:
    cleaned: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, AIMessage) and isinstance(message.content, list):
            kept = [b for b in message.content if not _is_thinking_block(b)]
            if len(kept) != len(message.content):
                message = message.model_copy(update={"content": kept})
        cleaned.append(message)
    return cleaned


class ReasoningChatOpenAI(ChatOpenAI):
    """启用 thinking 的模型包装：reasoning_content 增量转 reasoning 内容块。"""

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> Any:
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation is None or not isinstance(generation.message, AIMessageChunk):
            return generation
        message = generation.message
        reasoning = self._extract_reasoning_delta(chunk)
        if reasoning:
            message.content = [
                {"type": "reasoning", "reasoning": reasoning, "index": _REASONING_BLOCK_INDEX}
            ]
        elif isinstance(message.content, str) and message.content:
            message.content = [
                {"type": "text", "text": message.content, "index": _TEXT_BLOCK_INDEX}
            ]
        return generation

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        messages = self._convert_input(input_).to_messages()
        return super()._get_request_payload(_strip_thinking(messages), stop=stop, **kwargs)

    @staticmethod
    def _extract_reasoning_delta(chunk: dict) -> str | None:
        try:
            choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
            if not choices:
                return None
            delta = choices[0].get("delta") or {}
            value = delta.get("reasoning_content")
            if isinstance(value, str) and value:
                return value
        except Exception:
            return None
        return None
