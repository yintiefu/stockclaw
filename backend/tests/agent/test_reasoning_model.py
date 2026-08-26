"""ReasoningChatOpenAI 契约：reasoning_content 增量转 canonical reasoning 块、
text 增量转 text 块、块级合并、工具调用轮思考块不被 tool_call 增量按 index
覆盖（LangGraph 事件流桥真实路径回归）、以及历史回传上游前思考块被剥离
（含被 v1 规范化包成 non_standard 的形态）。全部离线。"""
from __future__ import annotations

from langchain_core.language_models._compat_bridge import chunks_to_events
from langchain_core.language_models.chat_model_stream import ChatModelStream
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
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
    assert message.content == [
        {"type": "reasoning", "reasoning": "先分析", "index": "lc_rs_0"}
    ]


def test_reasoning_block_index_never_collides_with_tool_call_indices():
    # 块索引必须是非整数命名空间：事件流桥按块的 index 字段合并增量，
    # 上游 tool_calls 增量带整数索引（0 起），整数 0 会被同键覆盖
    message = convert(make_model(), {"reasoning_content": "先分析"})
    index = message.content[0]["index"]
    assert not isinstance(index, int)


def test_text_delta_becomes_text_block():
    message = convert(make_model(), {"content": "结论"})
    assert message.content == [
        {"type": "text", "text": "结论", "index": "lc_txt_0"}
    ]


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
        {"type": "reasoning", "reasoning": "思考A思考B", "index": "lc_rs_0"},
        {"type": "text", "text": "正文", "index": "lc_txt_0"},
    ]


def test_tool_call_turn_reasoning_survives_v2_event_bridge():
    """回归：工具调用轮的思考不能在事件流桥中被覆盖。

    真实复现线上路径：LangGraph 事件流（`chunks_to_events` 桥）以块的
    `index` 字段为源标识合并增量。reasoning 块曾占整数 0，与第一条
    tool_call 增量（整数 0）同键，`_accumulate` 异类型同键直接替换，
    checkpoint 里思考文本就此消失——历史会话只剩最后一轮（无工具调用）
    的思考，之前的思考块重开会话后全部丢失。
    """
    model = make_model()

    def gen():
        for delta in (
            {"role": "assistant", "content": ""},
            {"reasoning_content": "思考A"},
            {"reasoning_content": "，思考B"},
            {"content": "我先查数据。"},
            {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "query_quote", "arguments": "{}"}}]},
            {"tool_calls": [{"index": 1, "id": "call-2", "function": {"name": "query_news", "arguments": "{}"}}]},
        ):
            yield ChatGenerationChunk(message=convert(model, delta))

    stream = ChatModelStream()
    for event in chunks_to_events(gen(), message_id="run-test"):
        stream.dispatch(event)
    message = stream.output_message
    assert message is not None

    reasoning_blocks = [
        b for b in message.content
        if isinstance(b, dict) and b.get("type") == "reasoning"
    ]
    assert reasoning_blocks, f"reasoning 块被 tool_call 增量覆盖: {message.content}"
    assert reasoning_blocks[0].get("reasoning") == "思考A，思考B"
    text_blocks = [
        b for b in message.content
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    assert text_blocks and text_blocks[0].get("text") == "我先查数据。"
    assert {tc["id"] for tc in message.tool_calls} == {"call-1", "call-2"}


def test_request_payload_strips_all_thinking_shapes():
    # v3 流式协议会把非规范块包成 non_standard（线上已出现的形态），三种都要剥
    model = make_model()
    history = [
        HumanMessage(content="hi"),
        AIMessage(content=[
            {"type": "reasoning", "reasoning": "秘密思考1", "index": "lc_rs_0"},
            {"type": "thinking", "thinking": "秘密思考2", "index": 0},
            {"type": "non_standard", "value": {"type": "thinking", "thinking": "."}, "index": "lc_ns_0"},
            {"type": "text", "text": "可见正文", "index": "lc_txt_0"},
        ]),
    ]
    payload = model._get_request_payload(history)
    assistant_payload = next(m for m in payload["messages"] if m["role"] == "assistant")
    assert assistant_payload["content"] == [{"type": "text", "text": "可见正文"}]
    assert "秘密思考" not in str(payload)

    # 纯文本消息不受影响
    plain = _strip_thinking([HumanMessage(content="普通问题"), AIMessage(content="普通回答")])
    assert plain[1].content == "普通回答"
