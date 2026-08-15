import pytest
from langchain_openai import ChatOpenAI

import chat
from agent.models import ModelRef, RunSecrets
from agent.runtime import build_chat_model


@pytest.mark.parametrize("provider,base_url,model", [
    ("openai", "https://api.openai.com/v1", "gpt-5-mini"),
    ("deepseek", "https://api.deepseek.com/v1", "deepseek-chat"),
])
def test_builds_openai_compatible_model_after_ssrf_check(monkeypatch, provider, base_url, model):
    checked = []
    monkeypatch.setattr(chat, "_check_base_url", checked.append)
    built = build_chat_model(
        ModelRef(provider=provider, base_url=base_url, model=model),
        RunSecrets(model_api_key="request-key"),
    )
    assert isinstance(built, ChatOpenAI)
    assert checked == [base_url]
    assert built.model_name == model
    assert str(built.openai_api_base).rstrip("/") == base_url.rstrip("/")
    assert "request-key" not in repr(built)


def test_blank_key_is_rejected_before_model_construction(monkeypatch):
    monkeypatch.setattr(chat, "_check_base_url", lambda value: None)
    ref = ModelRef(provider="openai", base_url="https://api.openai.com/v1", model="gpt-5-mini")
    with pytest.raises(ValueError, match="X-VR-Agent-Model-Key"):
        build_chat_model(ref, RunSecrets(model_api_key="   "))
