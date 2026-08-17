"""1D provenance：CommonMark 提取、URL golden corpus、脱敏摘要与容量。"""

from __future__ import annotations

import pytest

from agent.models import ModelUrlSource, ToolExecutionSource
from agent.provenance import (
    SOURCE_CAPACITY,
    append_automatic_urls,
    extract_model_urls,
    normalize_source_url,
    plan_source_admission,
    redact_recursively,
    summarize_tool_source,
)

SECRET = "sk-mcp-secret-1"


URL_GOLDEN_CORPUS = [
    # (markdown, expected normalized URLs)
    ("参见 [年报](https://Example.com/REPORT#a) 结尾。", ["https://example.com/REPORT"]),
    ("自动链接 <https://example.com/docs?v=2&b=1>", ["https://example.com/docs?v=2&b=1"]),
    ("裸地址 https://Example.com/a?x=1。后续", ["https://example.com/a?x=1"]),
    ("代码内忽略 `https://ignored.example/code`", []),
    (" fenced 忽略\n```\nhttps://ignored.example/fenced\n```\n", []),
    ("缩进代码忽略\n\n    https://ignored.example/indented\n", []),
    ("[链接文字里的 https://ignored.example/label 不提取](https://kept.example/x)",
     ["https://kept.example/x"]),
    ("括号收尾 (https://example.com/paren) 保留内部", ["https://example.com/paren"]),
    ("未配对闭合 https://example.com/unmatched) 剥离", ["https://example.com/unmatched"]),
    ("默认端口 https://example.com:443/ 和 http://example.com:80/", ["https://example.com/", "http://example.com/"]),
    ("空路径 https://example.com 规范为 /", ["https://example.com/"]),
    ("fragment 去除 https://example.com/p#frag", ["https://example.com/p"]),
    ("userinfo 拒绝 https://user:pw@example.com/u", []),
    ("非 http 拒绝 ftp://example.com/f", []),
    ("句读剥离 https://example.com/tail，。；？", ["https://example.com/tail"]),
    ("CJK 括号 https://example.com/cjk）", ["https://example.com/cjk"]),
]


@pytest.mark.parametrize(("markdown", "expected"), URL_GOLDEN_CORPUS)
def test_commonmark_url_golden_corpus(markdown, expected):
    assert [item.url for item in extract_model_urls(markdown)] == expected


def test_dedup_by_normalized_key_keeps_first():
    found = extract_model_urls(
        "先 https://example.com/a 再 [同](HTTPS://EXAMPLE.COM/a#x) 后 https://other.example/b")
    assert [item.url for item in found] == [
        "https://example.com/a", "https://other.example/b"]


def test_query_order_and_percent_encoding_unchanged():
    found = extract_model_urls("https://example.com/p?b=2&a=%E4%B8%AD")
    assert found[0].url == "https://example.com/p?b=2&a=%E4%B8%AD"


def test_normalize_rejects_overlong_candidate():
    long_url = "https://example.com/" + "x" * 2100
    assert normalize_source_url(long_url) is None
    assert normalize_source_url(long_url[:100]) is not None


def test_tool_summaries_are_redacted_before_truncation():
    source = summarize_tool_source({
        "tool_call_id": "call-1", "tool_name": "query_quote", "origin": "builtin",
        "completed_at": "2026-08-16T12:00:00Z",
        "args": {"token": SECRET + "x" * 2000}, "result": {"data": SECRET},
    }, secrets={SECRET})
    assert SECRET not in source.arguments_summary
    assert SECRET not in source.result_summary
    assert len(source.arguments_summary) <= 1000
    assert source.verification == "executed_record"


def test_source_record_verification_is_fixed_by_kind():
    tool = ToolExecutionSource.model_validate({
        "id": "source-1", "kind": "tool_execution", "tool_call_id": "c1",
        "tool_name": "t", "origin": "builtin", "completed_at": "now",
        "verification": "executed_record"})
    url = ModelUrlSource.model_validate({
        "id": "source-2", "kind": "model_url", "url": "https://example.com/",
        "created_at": "now", "verification": "model_provided_unverified"})
    assert tool.verification == "executed_record"
    assert url.verification == "model_provided_unverified"


def test_automatic_fill_to_capacity_sets_truncated_without_reordering():
    existing = [ModelUrlSource(id=f"source-{i}", kind="model_url",
                               url=f"https://example.com/seed/{i}",
                               created_at="now") for i in range(199)]
    candidates = extract_model_urls(
        "https://example.com/new/1 https://example.com/new/2")
    records, truncated = append_automatic_urls(
        existing, candidates, now="now-2")
    assert len(records) == SOURCE_CAPACITY
    assert truncated is True
    assert records[0].url == "https://example.com/seed/0"  # 既有顺序不变
    assert records[-1].url == "https://example.com/new/1"  # 只放得下第一个新 key


def test_plan_source_admission_capacity_failure_is_atomic():
    existing = [ModelUrlSource(id=f"source-{i}", kind="model_url",
                               url=f"https://example.com/seed/{i}",
                               created_at="now") for i in range(197)]
    plan = plan_source_admission(existing, [
        {"url": "https://example.com/d0"},
        {"url": "https://example.com/d1"},
        {"url": "https://example.com/d2"},
        {"url": "https://example.com/d3"},
    ], now="now")
    assert plan.first_failure is not None
    index, reason, remaining = plan.first_failure
    assert (index, reason, remaining) == (3, "source_capacity_exceeded", 0)
    assert len(plan.new_records) == 3
    assert len(existing) == 197  # 零部分写入


def test_plan_source_admission_reuses_existing_key():
    existing = [ModelUrlSource(id="source-9", kind="model_url",
                               url="https://example.com/a", created_at="now",
                               label="原始标签")]
    plan = plan_source_admission(existing, [{"url": "https://example.com/a#frag"}], now="n")
    assert plan.reused_ids == ["source-9"]
    assert plan.new_records == []
