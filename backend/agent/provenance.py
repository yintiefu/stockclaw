"""1D 来源与 provenance：URL 规范化、CommonMark 提取、工具来源摘要与准入规划。

只做规范化与记录：绝不进行 DNS、HTTP、redirect、预览、健康检查、评分、排序
或真实性验证（spec §16）。URL 候选的标点剥离与配对括号规则在此实现并与
`create_artifact` 描述符共享同一 Golden 行为。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit, urlunsplit

from agent.models import ModelUrlSource, SourceRecord, ToolExecutionSource

SOURCE_CAPACITY = 200
URL_MAX_CHARS = 2048
SUMMARY_MAX_CHARS = 1000

_SENTENCE_TAIL = ".,;:!?，。；：！？"
_PAIRED_CLOSERS = {")": "(", "]": "[", "}": "{", "）": "（", "】": "【",
                   "｝": "｛", "」": "「", "』": "『"}
# 裸 URL token 终止于空白/尖括号/中日韩字符与全角符号（原始 CJK 不应出现在合法 URL 中）
_BARE_URL_RE = re.compile(r"https?://[^\s<>\u3000-\u9fff\uff00-\uffef]+", re.IGNORECASE)


class SourceUrlInvalid(ValueError):
    """strict 模式下（create_artifact 描述符）的无效 URL。"""


@dataclass(frozen=True)
class NormalizedUrl:
    url: str       # 规范化后的展示 URL
    key: str       # 去重 key（scheme/host 小写、无 fragment/默认端口、空 path 为 /）


def _strip_trailing_punctuation(candidate: str) -> str:
    """迭代移除尾部句末标点与未配对的尾部闭合符，直到稳定。"""
    changed = True
    while changed:
        changed = False
        while candidate and candidate[-1] in _SENTENCE_TAIL:
            candidate = candidate[:-1]
            changed = True
        if candidate and candidate[-1] in _PAIRED_CLOSERS:
            closer = candidate[-1]
            opener = _PAIRED_CLOSERS[closer]
            if candidate.count(opener) < candidate.count(closer):
                candidate = candidate[:-1]
                changed = True
    return candidate


def normalize_source_url(candidate: str, *, strict: bool = False) -> NormalizedUrl | None:
    """规范化单个 URL 候选；无效返回 None（strict 时抛 SourceUrlInvalid）。"""
    if not isinstance(candidate, str):
        if strict:
            raise SourceUrlInvalid("URL 必须是字符串")
        return None
    text = _strip_trailing_punctuation(candidate.strip())
    if not text or len(text) > URL_MAX_CHARS:
        if strict:
            raise SourceUrlInvalid("URL 为空或超过 2048 字符")
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        if strict:
            raise SourceUrlInvalid("URL 解析失败") from None
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        if strict:
            raise SourceUrlInvalid("URL 必须是绝对 HTTP/HTTPS 地址")
        return None
    hostname = (parts.hostname or "").lower()
    if not hostname:
        if strict:
            raise SourceUrlInvalid("URL 缺少 hostname")
        return None
    if parts.username is not None or parts.password is not None:
        if strict:
            raise SourceUrlInvalid("URL 不允许携带 userinfo")
        return None
    try:
        port = parts.port
    except ValueError:
        if strict:
            raise SourceUrlInvalid("URL 端口不合法")
        return None
    default_port = 80 if scheme == "http" else 443
    if port is None or port == default_port:
        netloc = hostname
    else:
        netloc = f"{hostname}:{port}"
    path = parts.path or "/"
    normalized = urlunsplit((scheme, netloc, path, parts.query, ""))
    key = urlunsplit((scheme, netloc, path, parts.query, ""))
    return NormalizedUrl(url=normalized, key=key)


def _eligible_text_node(node) -> bool:
    """文本节点不得处于 link、inline code、fenced/indented code 或 HTML 内。"""
    parent = node.parent
    while parent is not None:
        if parent.t in ("link", "code", "code_block", "html_block", "html_inline"):
            return False
        parent = parent.parent
    return True


def extract_model_urls(markdown: str) -> list[NormalizedUrl]:
    """CommonMark 顺序提取：link/autolink destination + 合格文本节点中的裸 URL。

    commonmark 会把实体边界（如 `&`）拆成相邻 text 节点；先把相邻同级 text
    字面量并回同一 run，再扫描裸 URL，避免查询串被截断。
    """
    import commonmark

    root = commonmark.Parser().parse(markdown or "")
    items: list[tuple[str, Any]] = []  # ("link", destination) | ("run", [literal,...])
    last_text = None
    walker = root.walker()
    event = walker.nxt()
    while event is not None:
        node = event["node"]
        entering = event["entering"]
        if entering and node.t == "link":
            items.append(("link", node.destination or ""))
            last_text = None
        elif entering and node.t == "text" and _eligible_text_node(node):
            if (last_text is not None and node.parent is last_text.parent
                    and last_text.nxt is node and items and items[-1][0] == "run"):
                items[-1][1].append(node.literal or "")
            else:
                items.append(("run", [node.literal or ""]))
            last_text = node
        elif entering and node.t != "text":
            last_text = None
        event = walker.nxt()

    found: list[NormalizedUrl] = []
    seen_keys: set[str] = set()

    def _admit(candidate: str) -> None:
        normalized = normalize_source_url(candidate)
        if normalized is not None and normalized.key not in seen_keys:
            seen_keys.add(normalized.key)
            found.append(normalized)

    for kind, payload in items:
        if kind == "link":
            _admit(payload)
        else:
            for token in _BARE_URL_RE.findall("".join(payload)):
                _admit(token)
    return found


def redact_recursively(value: Any, secrets: Iterable[str]) -> Any:
    """递归脱敏（先脱敏、后截断；密钥池本身绝不持久化）。"""
    active = [secret for secret in secrets if secret]
    if not active:
        return value
    if isinstance(value, str):
        text = value
        for secret in active:
            text = text.replace(secret, "[redacted]")
        return text
    if isinstance(value, dict):
        return {key: redact_recursively(item, active) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_recursively(item, active) for item in value]
    return value


def _canonical_summary(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str)
    if len(encoded) <= SUMMARY_MAX_CHARS:
        return encoded
    return encoded[: SUMMARY_MAX_CHARS - len("...[truncated]")] + "...[truncated]"


def summarize_tool_source(
    call: dict[str, Any],
    secrets: Iterable[str] = (),
) -> ToolExecutionSource:
    """从完成的工具调用构造 tool_execution 来源（摘要已脱敏并限 1,000 字符）。"""
    return ToolExecutionSource(
        id=f"source-{call['tool_call_id']}",
        kind="tool_execution",
        tool_call_id=call["tool_call_id"],
        tool_name=call["tool_name"],
        origin=call["origin"],
        completed_at=call["completed_at"],
        arguments_summary=_canonical_summary(redact_recursively(call.get("args"), secrets)),
        result_summary=_canonical_summary(redact_recursively(call.get("result"), secrets)),
        verification="executed_record",
    )


@dataclass(frozen=True)
class SourcePlan:
    """`create_artifact`/自动收集共用的准入规划结果。"""

    reused_ids: list[str] = field(default_factory=list)
    new_records: list[SourceRecord] = field(default_factory=list)
    next_id: int = 0
    truncated: bool = False
    first_failure: tuple[int, str, int] | None = None  # (descriptor_index, reason, remaining)


def plan_source_admission(
    existing: Sequence[SourceRecord],
    descriptors: Sequence[dict[str, Any]],
    *,
    now: str,
    id_prefix: str = "source",
) -> SourcePlan:
    """按输入顺序解析 URL 描述符：复用已有 key、模拟总量、绝不部分写入。

    返回的 first_failure 携带第一个失败描述符的 (序号, 规范化原因, 剩余容量)。
    """
    by_key: dict[str, SourceRecord] = {}
    by_id: dict[str, SourceRecord] = {}
    for record in existing:
        if record.kind == "model_url":
            normalized = normalize_source_url(record.url)
            if normalized is not None:
                by_key[normalized.key] = record
        by_id[record.id] = record
    reused: list[str] = []
    new_records: list[SourceRecord] = []
    used_keys = set(by_key)
    next_id = len(existing)
    failure: tuple[int, str, int] | None = None
    for index, descriptor in enumerate(descriptors):
        try:
            normalized = normalize_source_url(descriptor.get("url", ""), strict=True)
        except SourceUrlInvalid as exc:
            failure = (index, str(exc), SOURCE_CAPACITY - len(existing) - len(new_records))
            break
        if normalized.key in used_keys and normalized.key in by_key:
            reused.append(by_key[normalized.key].id)
            continue
        if len(existing) + len(new_records) >= SOURCE_CAPACITY:
            failure = (index, "source_capacity_exceeded",
                       SOURCE_CAPACITY - len(existing) - len(new_records))
            break
        record_id = f"{id_prefix}-{next_id}"
        next_id += 1
        record = ModelUrlSource(
            id=record_id,
            kind="model_url",
            url=normalized.url,
            label=descriptor.get("label"),
            created_at=now,
            verification="model_provided_unverified",
        )
        used_keys.add(normalized.key)
        new_records.append(record)
    return SourcePlan(
        reused_ids=reused,
        new_records=new_records,
        next_id=next_id,
        truncated=len(existing) + len(new_records) >= SOURCE_CAPACITY,
        first_failure=failure,
    )


def append_automatic_urls(
    existing: Sequence[SourceRecord],
    candidates: Sequence[NormalizedUrl],
    *,
    now: str,
    id_prefix: str = "source",
) -> tuple[list[SourceRecord], bool]:
    """自动提取：按顺序填满剩余容量，溢出只置 truncated，不改已有顺序。"""
    records = list(existing)
    existing_keys = {
        normalize_source_url(record.url).key
        for record in records if record.kind == "model_url"
    }
    truncated = False
    next_id = len(records)
    for candidate in candidates:
        if candidate.key in existing_keys:
            continue
        if len(records) >= SOURCE_CAPACITY:
            truncated = True
            continue
        records.append(ModelUrlSource(
            id=f"{id_prefix}-{next_id}",
            kind="model_url",
            url=candidate.url,
            created_at=now,
            verification="model_provided_unverified",
        ))
        existing_keys.add(candidate.key)
        next_id += 1
    return records, truncated
