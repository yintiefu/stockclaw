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


def test_handle_never_stores_real_model_key(monkeypatch):
    """真实 ChatOpenAI 路径：handle/coordinator 快照上不保留密钥载体。"""
    from agent.runtime import AgentFactory
    from langchain_core.tools import tool as lc_tool

    monkeypatch.setattr(chat, "_check_base_url", lambda value: None)

    @lc_tool
    def noop(code: str) -> str:
        """noop"""
        return "ok"

    secret = "sk-real-leak-probe"
    handle = AgentFactory().create(
        model_ref=ModelRef(provider="openai", base_url="https://api.openai.com/v1", model="gpt-5-mini"),
        secrets=RunSecrets(model_api_key=secret),
        model_builder=build_chat_model,
        tools=[noop],
        thread_id="thread-leak",
    )
    assert handle.model is None
    rendered = repr(handle) + repr(handle.snapshot) + repr(vars(handle.snapshot))
    assert secret not in rendered
    handle.release_graph()
    assert handle.graph is None and handle.model is None


def test_compose_system_prompt_orders_neutrality_policy_catalog():
    from agent.runtime import compose_system_prompt

    prompt = compose_system_prompt("POLICY-EXPLANATION", "\n\n## 用户已启用的 Skill")
    assert prompt.index("Agent 工作台") < prompt.index("POLICY-EXPLANATION") < prompt.index("用户已启用的 Skill")


def test_create_and_resume_keep_policy_explanation_on_handle(monkeypatch):
    from agent.runtime import AgentFactory

    monkeypatch.setattr(chat, "_check_base_url", lambda value: None)
    ref = ModelRef(provider="openai", base_url="https://api.openai.com/v1", model="gpt-5-mini")
    factory = AgentFactory()
    handle = factory.create(
        model_ref=ref,
        secrets=RunSecrets(model_api_key="k"),
        model_builder=build_chat_model,
        tools=[],
        thread_id="thread-policy",
        policy_explanation="POLICY-EXPLANATION",
    )
    assert handle.policy_explanation == "POLICY-EXPLANATION"
    factory.resume(handle=handle, model_ref=ref, secrets=RunSecrets(model_api_key="k"),
                   model_builder=build_chat_model)
    assert handle.policy_explanation == "POLICY-EXPLANATION"
    handle.release_graph()
