from pydantic import SecretStr, ValidationError
import pytest

from agent.models import (
    AgentMessage,
    ModelRef,
    RunDocument,
    RunSecrets,
    RuntimeForwardedProps,
    ThreadDocument,
)


def test_model_ref_uses_frontend_field_names_and_contains_no_secret():
    ref = ModelRef.model_validate({
        "provider": "deepseek",
        "baseURL": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    })
    assert ref.base_url == "https://api.deepseek.com/v1"
    assert set(ref.model_dump()) == {"provider", "base_url", "model"}
    assert "model_api_key" not in ref.model_dump_json()


def test_run_secret_masks_key():
    secrets = RunSecrets(model_api_key=SecretStr("spike-secret"))
    assert "spike-secret" not in repr(secrets)
    assert secrets.model_api_key.get_secret_value() == "spike-secret"


def test_runtime_props_require_thread_revision_after_migration():
    # Task 7 迁移后：缺省 threadRevision 一律拒绝（临时 1A 兼容路径已移除）
    with pytest.raises(ValidationError):
        RuntimeForwardedProps.model_validate({
            "model": {"provider": "openai", "baseURL": "https://api.openai.com/v1", "model": "gpt-5-mini"},
        })


def test_runtime_props_accept_revision_and_retry_of():
    props = RuntimeForwardedProps.model_validate({
        "model": {"provider": "openai", "baseURL": "https://api.openai.com/v1", "model": "gpt-5-mini"},
        "threadRevision": 3,
        "retryOf": "run-old",
    })
    assert props.thread_revision == 3
    assert props.retry_of == "run-old"


def test_runtime_props_reject_negative_revision():
    with pytest.raises(ValidationError):
        RuntimeForwardedProps.model_validate({
            "model": {"provider": "openai", "baseURL": "https://api.openai.com/v1", "model": "gpt-5-mini"},
            "threadRevision": -1,
        })


def test_runtime_props_reject_unknown_runtime_key():
    with pytest.raises(ValidationError):
        RuntimeForwardedProps.model_validate({
            "model": {"provider": "openai", "baseURL": "https://api.openai.com/v1", "model": "gpt-5-mini"},
            "unexpected": "value",
        })


def test_runtime_props_reject_model_key_below_runtime():
    with pytest.raises(ValidationError):
        RuntimeForwardedProps.model_validate({
            "model": {"provider": "openai", "baseURL": "https://api.openai.com/v1", "model": "gpt-5-mini"},
            "apiKey": "sk-leak",
        })


def test_thread_document_tracks_revision_and_message_completeness():
    thread = ThreadDocument.new("thread-1", "新会话", now="2026-08-15T12:00:00Z")
    thread = thread.model_copy(update={
        "revision": 2,
        "messages": [
            AgentMessage(id="user-1", role="user", content="分析现金流", partial=False),
            AgentMessage(id="assistant-1", role="assistant", content="尚未完成", partial=True),
        ],
    })
    assert thread.schema_version == 1
    assert thread.revision == 2
    assert [item.id for item in thread.model_history()] == ["user-1"]


def test_thread_document_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ThreadDocument.model_validate({
            "schema_version": 1,
            "id": "thread-1",
            "title": "新会话",
            "created_at": "2026-08-15T12:00:00Z",
            "updated_at": "2026-08-15T12:00:00Z",
            "revision": 0,
            "surprise": True,
        })


def test_run_document_never_serializes_model_key():
    run = RunDocument.start(
        run_id="run-1",
        thread_id="thread-1",
        protocol_run_id="protocol-1",
        model_ref=ModelRef(provider="openai", baseURL="https://api.openai.com/v1", model="gpt-5-mini"),
        trigger_message_id="user-1",
        history_head_id="user-1",
        now="2026-08-15T12:00:00Z",
    )
    encoded = run.model_dump_json(by_alias=True)
    assert run.status == "running"
    assert run.protocol_run_ids == ["protocol-1"]
    assert "api_key" not in encoded.lower()
    assert "secret" not in encoded.lower()
