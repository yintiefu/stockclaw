from pydantic import SecretStr, ValidationError
import pytest

from agent.models import ModelRef, RunSecrets, RuntimeForwardedProps


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


def test_runtime_props_require_model_and_reject_retry_in_1a():
    with pytest.raises(ValidationError):
        RuntimeForwardedProps.model_validate({
            "model": {"provider": "openai", "baseURL": "https://api.openai.com/v1", "model": "gpt-5-mini"},
            "retryOf": "run-old",
        })
