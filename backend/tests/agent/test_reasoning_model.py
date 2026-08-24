"""ReasoningChatOpenAI 契约：reasoning_content 增量转 canonical reasoning 块、
text 增量转 text 块、块级合并、以及历史回传上游前思考块被剥离（含被 v1
规范化包成 non_standard 的形态）。全部离线。"""
from __future__ import annotations

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_openai.chat_models.base import _convert_message_to_dict

from agent.reasoning_model import ReasoningChatOpenAI, _strip_thinking


def make_model() -> ReasoningChatOpenAI:
    return ReasoningChatOpenAI(
        model="test-model",
        base_url="https://example.invalid/v1",
        api_key="sk-test",
        temperature=0.2,
        extra_body={"thinking": {"type": "enabled"}},
    )


def convert(model: ReasoningChatOpenAI, delta: dict):
    chunk = {"choices": [{"delta": delta, "index": 0}]}
    generation = model._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert generation is not None
    return generation.message


def test_reasoning_delta_becomes_canonical_block():
    message = convert(make_model(), {"reasoning_content": "先分析"})
    assert message.content == [{"type": "reasoning", "reasoning": "先分析", "index": 0}]


def test_text_delta_becomes_text_block():
    message = convert(make_model(), {"content": "结论"})
    assert message.content == [{"type": "text", "text": "结论", "index": 1}]


def test_empty_content_delta_stays_string():
    # 工具调用增量的 content 为空串，保持原样（merge_content 对 list+"" 为 no-op）
    message = convert(make_model(), {"content": "", "tool_calls": [{
        "index": 0, "id": "call-1", "function": {"name": "t", "arguments": "{}"},
    }]})
    assert message.content == ""
    assert message.tool_call_chunks[0]["id"] == "call-1"


def test_consecutive_deltas_merge_into_single_blocks():
    first = convert(make_model(), {"reasoning_content": "思考A"})
    second = convert(make_model(), {"reasoning_content": "思考B"})
    third = convert(make_model(), {"content": "正文"})
    merged = first + second + third
    assert merged.content == [
        {"type": "reasoning", "reasoning": "思考A思考B", "index": 0},
        {"type": "text", "text": "正文", "index": 1},
    ]


def test_request_payload_strips_all_thinking_shapes():
    # v3 流式协议会把非规范块包成 non_standard（线上已出现的形态），三种都要剥
    model = make_model()
    history = [
        HumanMessage(content="hi"),
        AIMessage(content=[
            {"type": "reasoning", "reasoning": "秘密思考1", "index": 0},
            {"type": "thinking", "thinking": "秘密思考2", "index": 0},
            {"type": "non_standard", "value": {"type": "thinking", "thinking": "."}, "index": "lc_ns_0"},
            {"type": "text", "text": "可见正文", "index": 1},
        ]),
    ]
    payload = model._get_request_payload(history)
    assistant_payload = next(m for m in payload["messages"] if m["role"] == "assistant")
    assert assistant_payload["content"] == [{"type": "text", "text": "可见正文"}]
    assert "秘密思考" not in str(payload)

    # 纯文本消息不受影响
    plain = _strip_thinking([HumanMessage(content="普通问题"), AIMessage(content="普通回答")])
    assert plain[1].content == "普通回答"
