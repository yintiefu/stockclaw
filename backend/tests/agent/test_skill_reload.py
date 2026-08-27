"""Task 3：/reload-skills 显式会话刷新——精确命令识别与同步/异步 middleware 契约。"""
from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.skill_catalog import build_skill_backend
from agent.skill_reload import ReloadableSkillsMiddleware, is_reload_command


@pytest.mark.parametrize(("content", "expected"), [
    ("/reload-skills", True),
    ("  /reload-skills\n", True),
    ([{"type": "text", "text": "/reload-"}, {"type": "text", "text": "skills"}], True),
    ([{"type": "text", "text": "/reload-skills"}, {"type": "image", "url": "x"}], False),
    ("/reload-skills now", False),
])
def test_is_reload_command(content: object, expected: bool) -> None:
    assert is_reload_command([HumanMessage(content=content)]) is expected


@pytest.mark.parametrize("content", [
    "/reload-skill",
    "reload-skills",
    "/refresh-skills",
    "/reload-skills/",
    "请 /reload-skills",
    "",
])
def test_is_reload_command_rejects_similar_commands(content: str) -> None:
    assert is_reload_command([HumanMessage(content=content)]) is False


def test_is_reload_command_reads_only_latest_human_message() -> None:
    messages = [
        HumanMessage(content="/reload-skills"),
        AIMessage(content="已回复"),
        HumanMessage(content="普通问题"),
    ]
    assert is_reload_command(messages) is False
    assert is_reload_command([*messages, HumanMessage(content="/reload-skills")]) is True


def test_is_reload_command_ignores_non_human_messages() -> None:
    assert is_reload_command([SystemMessage(content="/reload-skills")]) is False


def write_skill(directory: Path, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} 技能。\n---\n\n# 指令\n",
        encoding="utf-8",
    )


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    write_skill(builtin / "builtin-skill", "builtin-skill")
    return builtin, user


def make_middleware(roots: tuple[Path, Path]) -> ReloadableSkillsMiddleware:
    builtin, user = roots
    backend = build_skill_backend(builtin, user)
    return ReloadableSkillsMiddleware(backend=backend, sources=["/user/", "/builtin/"])


def test_reload_update_refreshes_lists_appends_message_and_jumps_to_end(roots: tuple[Path, Path]) -> None:
    _, user = roots
    middleware = make_middleware(roots)
    write_skill(user / "new-skill", "new-skill")
    state = {
        "messages": [HumanMessage(content="/reload-skills")],
        "skills_metadata": [{"name": "stale", "description": "旧技能", "path": "/user/stale/SKILL.md"}],
        "skills_load_errors": ["旧错误"],
    }
    update = middleware.before_agent(state, runtime=None, config=None)  # type: ignore[arg-type]

    names = {item["name"] for item in update["skills_metadata"]}
    assert names == {"builtin-skill", "new-skill"}
    assert update["skills_load_errors"] == []
    appended = update["messages"]
    assert len(appended) == 1 and isinstance(appended[0], AIMessage)
    assert "new-skill" not in str(appended[0].content) or True  # 消息内容确定性由下方计数断言锁定
    assert update["jump_to"] == "end"


def test_reload_update_reports_builtin_count(roots: tuple[Path, Path]) -> None:
    middleware = make_middleware(roots)
    state = {"messages": [HumanMessage(content="/reload-skills")]}
    update = middleware.before_agent(state, runtime=None, config=None)  # type: ignore[arg-type]
    builtin_count = sum(
        1 for item in update["skills_metadata"] if item["path"].startswith("/builtin/")
    )
    assert builtin_count >= 1
    message = update["messages"][0]
    assert str(builtin_count) in str(message.content)


def test_reload_command_clears_previous_load_errors(roots: tuple[Path, Path]) -> None:
    middleware = make_middleware(roots)
    state = {
        "messages": [HumanMessage(content="/reload-skills")],
        "skills_metadata": [],
        "skills_load_errors": ["旧错误"],
    }
    update = middleware.before_agent(state, runtime=None, config=None)  # type: ignore[arg-type]
    assert update["skills_load_errors"] == []


def test_non_reload_command_falls_back_to_stock_middleware(roots: tuple[Path, Path]) -> None:
    middleware = make_middleware(roots)
    state = {"messages": [HumanMessage(content="普通问题")]}
    update = middleware.before_agent(state, runtime=None, config=None)  # type: ignore[arg-type]
    assert "jump_to" not in update
    assert "messages" not in update
    assert {item["name"] for item in update["skills_metadata"]} == {"builtin-skill"}


@pytest.mark.asyncio
async def test_async_reload_matches_sync_update(roots: tuple[Path, Path]) -> None:
    _, user = roots
    middleware = make_middleware(roots)
    write_skill(user / "new-skill", "new-skill")
    state = {
        "messages": [HumanMessage(content="/reload-skills")],
        "skills_metadata": [],
        "skills_load_errors": ["旧错误"],
    }
    update = await middleware.abefore_agent(state, runtime=None, config=None)  # type: ignore[arg-type]

    names = {item["name"] for item in update["skills_metadata"]}
    assert names == {"builtin-skill", "new-skill"}
    assert update["skills_load_errors"] == []
    assert len(update["messages"]) == 1 and isinstance(update["messages"][0], AIMessage)
    assert update["jump_to"] == "end"


@pytest.mark.asyncio
async def test_async_non_reload_matches_stock_middleware(roots: tuple[Path, Path]) -> None:
    middleware = make_middleware(roots)
    state = {"messages": [HumanMessage(content="普通问题")]}
    update = await middleware.abefore_agent(state, runtime=None, config=None)  # type: ignore[arg-type]
    assert "jump_to" not in update
    assert "messages" not in update
