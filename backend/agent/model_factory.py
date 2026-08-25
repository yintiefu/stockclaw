"""模型构造工厂。

统一定义 ChatOpenAI / ReasoningChatOpenAI 的唯一构造路径。
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent.reasoning_model import ReasoningChatOpenAI
from agent.settings import AgentSettings


def build_model(settings: AgentSettings) -> BaseChatModel:
    """基于统一设置构造 ChatOpenAI 或 ReasoningChatOpenAI 实例。"""
    model = settings.model
    model_cls = ReasoningChatOpenAI if model.thinking else ChatOpenAI
    extra_kwargs: dict[str, Any] = {}
    if model.thinking:
        extra_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    return model_cls(
        model=model.name,
        base_url=model.base_url.rstrip("/"),
        api_key=SecretStr(model.api_key.get_secret_value()),
        temperature=model.temperature,
        streaming=True,
        parallel_tool_calls=False,
        **extra_kwargs,
    )
