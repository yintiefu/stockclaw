"""保留第三方 OpenAI 兼容上游（智谱 GLM 等）思考输出的 ChatOpenAI 子类。

`ChatOpenAI` 只针对官方 OpenAI 规范：流式 delta 里的非标准字段
`reasoning_content` 会在 `_convert_delta_to_message_chunk` 中被直接丢弃
（见其模块 docstring），因此即使上游开了 thinking，思考文本也到不了前端。

本子类在 chunk→generation 转换之后做最小改写：

- `reasoning_content` 增量 → `thinking` content block。选 `thinking` 而非
  canonical 的 `reasoning` 类型，是因为请求侧 `_format_message_content` 会
  自动剥离 `thinking` 块——含思考的历史消息回传上游时不会触发 400；
  assistant-ui 的转换层对两种类型都能映射成 reasoning 展示。
- text 增量同步转成 `text` block：`merge_content` 对「list + 字符串」会把裸
  字符串追加进列表，前端转换层不认字符串元素（会丢正文），必须全程 block。
- 块带固定 `index`，`merge_lists` 据此把连续增量合并进同一块（字符串拼接），
  否则逐 token 产生几百个小块。
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI


class ReasoningChatOpenAI(ChatOpenAI):
    """启用 thinking 的模型包装：reasoning_content 增量转 thinking 内容块。"""

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
            message.content = [{"type": "thinking", "thinking": reasoning, "index": 0}]
        elif isinstance(message.content, str) and message.content:
            message.content = [{"type": "text", "text": message.content, "index": 1}]
        return generation

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
