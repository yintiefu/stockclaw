"""1D Policy store 契约：默认值、范围、CAS、非破坏性损坏与显式恢复。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agent.models import PolicySnapshot
from agent.policy import (
    POLICY_DEFAULTS,
    PolicyCorrupt,
    PolicyInvalid,
    PolicyPatch,
    PolicyReset,
    PolicyRevisionConflict,
    PolicyStore,
)

LOWER_EDGES = {
    "max_model_calls": 1,
    "max_tool_calls": 1,
    "tool_timeout_seconds": 5,
    "max_active_seconds": 30,
    "max_context_chars": 16000,
}
UPPER_EDGES = {
    "max_model_calls": 32,
    "max_tool_calls": 64,
    "tool_timeout_seconds": 120,
    "max_active_seconds": 1800,
    "max_context_chars": 500000,
}


def write_policy(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_missing_policy_returns_defaults_without_write(tmp_path):
    path = tmp_path / "policy.json"
    store = PolicyStore(path)
    view = store.get()
    assert view.persisted is False
    assert view.revision == 0
    assert {key: getattr(view, key) for key in POLICY_DEFAULTS} == POLICY_DEFAULTS
    assert not path.exists()  # GET 绝不落盘
    snapshot = store.snapshot()
    assert isinstance(snapshot, PolicySnapshot)
    assert snapshot.policy_revision == 0
    assert snapshot.max_model_calls == POLICY_DEFAULTS["max_model_calls"]


def test_every_inclusive_range_edge_round_trips(tmp_path):
    store = PolicyStore(tmp_path / "policy.json")
    lower = store.patch(PolicyPatch(revision=0, **LOWER_EDGES))
    assert lower.revision == 1 and lower.persisted is True
    upper = store.patch(PolicyPatch(revision=1, **UPPER_EDGES))
    assert upper.revision == 2
    reloaded = store.get()
    assert reloaded.persisted is True
    assert {key: getattr(reloaded, key) for key in UPPER_EDGES} == UPPER_EDGES
    assert store.snapshot().policy_revision == 2


def test_patch_model_rejects_unknown_fields_and_requires_one_change():
    with pytest.raises(ValidationError):
        PolicyPatch.model_validate({"revision": 0, "bogus": 1})
    with pytest.raises(ValidationError):
        PolicyPatch.model_validate({"revision": 0})
    with pytest.raises(ValidationError):
        PolicyPatch.model_validate({"revision": 0, "max_model_calls": 33})
    with pytest.raises(ValidationError):
        PolicyPatch.model_validate({"revision": -1, "max_model_calls": 8})


def test_partial_patch_merges_with_current_values(tmp_path):
    path = tmp_path / "policy.json"
    store = PolicyStore(path)
    store.patch(PolicyPatch(revision=0, max_model_calls=10, max_tool_calls=20))
    view = store.patch(PolicyPatch(revision=1, max_tool_calls=5))
    assert view.revision == 2
    assert view.max_model_calls == 10  # 未提交字段保持
    assert view.max_tool_calls == 5
    assert view.tool_timeout_seconds == POLICY_DEFAULTS["tool_timeout_seconds"]
    assert json.loads(path.read_text(encoding="utf-8"))["revision"] == 2


def test_stale_revision_conflict_reports_current(tmp_path):
    store = PolicyStore(tmp_path / "policy.json")
    store.patch(PolicyPatch(revision=0, max_model_calls=10))
    with pytest.raises(PolicyRevisionConflict) as raised:
        store.patch(PolicyPatch(revision=0, max_model_calls=11))
    assert raised.value.code == "POLICY_REVISION_CONFLICT"
    assert raised.value.current_revision == 1


def test_normal_reset_writes_defaults_and_bumps_revision(tmp_path):
    path = tmp_path / "policy.json"
    store = PolicyStore(path)
    store.patch(PolicyPatch(revision=0, max_model_calls=10))
    view = store.reset(PolicyReset(revision=1))
    assert view.revision == 2
    assert view.max_model_calls == POLICY_DEFAULTS["max_model_calls"]
    assert json.loads(path.read_text(encoding="utf-8"))["max_model_calls"] == 8


@pytest.mark.parametrize("content", [
    '{"schema_version":1,"max_model_calls":0}',      # 数值越界
    '{"schema_version":1,"revision":"x"}',           # 类型不合法
    '{"schema_version":1,"surprise":true}',          # 未知字段
    'not-json-at-all',                                # JSON 解析失败
])
def test_corrupt_read_fails_closed_non_destructively(tmp_path, content):
    path = tmp_path / "policy.json"
    write_policy(path, content)
    original = path.read_bytes()
    store = PolicyStore(path)
    for _ in range(2):  # 重复 GET 仍是损坏，绝不回落默认值
        with pytest.raises(PolicyCorrupt) as raised:
            store.get()
        assert raised.value.code == "POLICY_CORRUPT"
        assert path.read_bytes() == original  # 非破坏性：文件原样保留
    with pytest.raises(PolicyCorrupt):
        store.patch(PolicyPatch(revision=1, max_model_calls=9))
    with pytest.raises(PolicyCorrupt):
        store.reset(PolicyReset(revision=1))
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("policy.json.corrupt-*"))


def test_corrupt_reason_has_no_absolute_path_or_file_content(tmp_path):
    path = tmp_path / "policy.json"
    write_policy(path, '{"schema_version":1,"max_model_calls":0,"note":"SECRET-CONTENT"}')
    store = PolicyStore(path)
    with pytest.raises(PolicyCorrupt) as raised:
        store.get()
    message = str(raised.value)
    assert str(tmp_path) not in message
    assert "SECRET-CONTENT" not in message
    assert path.name in message  # 只报文件名


def test_corrupt_get_is_non_destructive_until_confirmed_reset(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"schema_version":1,"max_model_calls":0}', encoding="utf-8")
    store = PolicyStore(path)
    for _ in range(2):
        with pytest.raises(PolicyCorrupt) as raised:
            store.get()
        assert raised.value.code == "POLICY_CORRUPT"
        assert path.exists()
    reset = store.reset(PolicyReset(confirm_corrupt=True))
    assert reset.revision == 1
    assert list(tmp_path.glob("policy.json.corrupt-*"))
    healthy = store.get()
    assert healthy.persisted is True and healthy.revision == 1
    assert healthy.max_model_calls == POLICY_DEFAULTS["max_model_calls"]
    assert store.snapshot().policy_revision == 1


def test_reset_bodies_are_mutually_exclusive():
    with pytest.raises(ValidationError):
        PolicyReset.model_validate({})
    with pytest.raises(ValidationError):
        PolicyReset.model_validate({"revision": 1, "confirm_corrupt": True})
    with pytest.raises(ValidationError):
        PolicyReset.model_validate({"confirm_corrupt": False})
    with pytest.raises(ValidationError):
        PolicyReset.model_validate({"revision": 1, "bogus": 2})


def test_confirm_corrupt_reset_on_healthy_policy_is_rejected(tmp_path):
    path = tmp_path / "policy.json"
    store = PolicyStore(path)
    store.patch(PolicyPatch(revision=0, max_model_calls=10))
    with pytest.raises(PolicyInvalid) as raised:
        store.reset(PolicyReset(confirm_corrupt=True))
    assert raised.value.code == "POLICY_INVALID"
    # 健康文件保持原样，也不产生隔离副本
    assert json.loads(path.read_text(encoding="utf-8"))["max_model_calls"] == 10
    assert not list(tmp_path.glob("policy.json.corrupt-*"))
