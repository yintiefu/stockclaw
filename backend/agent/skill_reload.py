"""/reload-skills 显式会话刷新。

已有会话的技能元数据在 state 里缓存（SkillsMiddleware 首轮加载后跳过），
用户在管理页导入/启停技能后，通过精确输入 `/reload-skills` 让当前线程
重新枚举技能源。刷新只做目录扫描与 state 更新，不调用模型、不执行工具；
随后以一条确定性 AI 消息回显可见技能数量并直接结束本轮（jump_to end）。
"""
from __future__ import annotations

from typing import Any

from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware import hook_config
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

RELOAD_COMMAND = "/reload-skills"


def is_reload_command(messages: list[BaseMessage]) -> bool:
    """仅当最后一条用户消息是纯文本的精确 `/reload-skills` 时返回 True。"""
    latest_human = next(
        (message for message in reversed(messages) if isinstance(message, HumanMessage)),
        None,
    )
    if latest_human is None:
        return False
    content = latest_human.content
    if isinstance(content, str):
        return content.strip() == RELOAD_COMMAND
    if isinstance(content, list):
        if not content or any(not isinstance(block, dict) or block.get("type") != "text" for block in content):
            return False
        joined = "".join(str(block.get("text", "")) for block in content)
        return joined.strip() == RELOAD_COMMAND
    return False


class ReloadableSkillsMiddleware(SkillsMiddleware):
    """在标准 Skills 加载之上叠加精确命令的显式刷新。"""

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state, runtime, config):
        if not is_reload_command(state.get("messages", [])):
            return super().before_agent(state, runtime, config)
        fresh_state = dict(state)
        fresh_state.pop("skills_metadata", None)
        fresh_state.pop("skills_load_errors", None)
        loaded = super().before_agent(fresh_state, runtime, config) or {"skills_metadata": []}
        return self._reload_update(loaded)

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state, runtime, config):
        if not is_reload_command(state.get("messages", [])):
            return await super().abefore_agent(state, runtime, config)
        fresh_state = dict(state)
        fresh_state.pop("skills_metadata", None)
        fresh_state.pop("skills_load_errors", None)
        loaded = await super().abefore_agent(fresh_state, runtime, config) or {"skills_metadata": []}
        return self._reload_update(loaded)

    def _reload_update(self, loaded: dict[str, Any]) -> dict[str, Any]:
        metadata = list(loaded.get("skills_metadata", []))
        builtin_count = sum(1 for item in metadata if item.get("path", "").startswith("/builtin/"))
        user_count = len(metadata) - builtin_count
        message = AIMessage(
            content=(
                f"技能已刷新：当前可见 {len(metadata)} 个技能"
                f"（内置 {builtin_count} 个，用户 {user_count} 个）。"
                "新会话将自动使用最新配置。"
            )
        )
        return {
            "skills_metadata": metadata,
            "skills_load_errors": list(loaded.get("skills_load_errors", [])),
            "messages": [message],
            "jump_to": "end",
        }
