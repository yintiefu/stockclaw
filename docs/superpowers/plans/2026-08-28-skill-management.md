# Skill Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Vibe-Research 增加本地技能管理闭环：区分只读内置技能与用户导入技能，支持安全导入、启停、详情、删除，并让已有 `α-mind` 会话通过 `/reload-skills` 显式刷新。

**Architecture:** FastAPI 只负责本地磁盘管理 API，LangGraph 只负责严格只读技能视图与会话内刷新，两者共享 `agent/skill_catalog.py` 的公开校验合同但不互相调用。用户启停由活动根和同级停用根的位置表达；运行时通过过滤 backend 阻止无效、冲突和符号链接穿透，内置技能以 later-wins 顺序保持最终优先。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic 2、PyYAML 6.0.2、Deep Agents 0.7.7、LangChain/LangGraph、pytest；React 19、TypeScript strict、React Router、Tailwind CSS v4、Base UI Dialog、ReactMarkdown、Sonner、Vitest、Playwright。

---

## Success Criteria

- 管理 API 只操作隔离用户目录；导入默认停用，同名不覆盖，PATCH 同状态幂等。
- 内置、无效、冲突、停用技能均不能从错误命名空间被 Agent 列出或读取；同步与异步 backend 路径行为一致。
- 新会话读取当前活动技能；旧会话保持缓存，精确 `/reload-skills` 只刷新当前线程且不调用模型/工具。
- `/skills`、`/skills/user/:name`、`/skills/builtin/:name` 完整覆盖加载、空、错误、导入、启停、删除和移动端状态。
- HITL 待审批时输入框与发送按钮不可用，审批控件仍可恢复原 run。
- 后端离线测试、前端单元测试、TypeScript 构建和隔离 Playwright 全部通过，测试不接触真实用户目录。

## File Map

### Backend production

- Create `backend/agent/skill_catalog.py`: Agent Skills 严格 frontmatter 解析、目录扫描、过滤只读 backend、自定义 Skills prompt。
- Create `backend/agent/skill_reload.py`: `/reload-skills` 文本提取与同步/异步 `ReloadableSkillsMiddleware`。
- Create `backend/skillmgr.py`: 用户根快照、枚举、导入、启停、删除、锁、原子文件操作和安全领域错误。
- Modify `backend/agent/settings.py`: 缺失技能根以 `0700` 创建，拒绝文件系统根，公开一致的路径解析函数。
- Modify `backend/agent/skill_backends.py`: `/builtin/` 与 `/user/` 都挂过滤 backend，用户排除内置同名。
- Modify `backend/agent/graph.py`: later-wins source 顺序、自定义 prompt、刷新 middleware。
- Modify `backend/agent/embedded_graph.py`: 仍只挂 `/builtin/`，但改用同一严格过滤 backend 和 prompt。
- Modify `backend/agent/workflow_loader.py`: 移除 Deep Agents 下划线私有 import，复用公开严格解析器。
- Modify `backend/app.py`: 请求模型、流式 body 上限、五个 `/api/skills` 路由和错误映射。

### Backend tests

- Create `backend/tests/agent/test_skill_catalog.py`.
- Create `backend/tests/agent/test_skill_reload.py`.
- Create `backend/tests/test_skillmgr.py`.
- Create `backend/tests/test_skills_api.py`.
- Modify `backend/tests/agent/test_settings.py`, `test_workflow_loader.py`, `test_graph.py`, `test_embedded_graph.py`.

### Frontend production

- Create `frontend/src/pages/Skills.tsx`: 双分区列表、加载/空/错误状态、启停动作与导入入口。
- Create `frontend/src/pages/SkillDetail.tsx`: Markdown 详情、虚拟路径、启停和删除确认。
- Create `frontend/src/components/skills/SkillImportDialog.tsx`: 文件夹/ZIP 分段导入及本地隐私说明。
- Create `frontend/src/components/skills/SkillToggle.tsx`: 稳定尺寸、可访问的技能状态开关。
- Modify `frontend/src/lib/api.ts`: 技能类型、集中 API 方法、`PATCH` method。
- Modify `frontend/src/router.tsx`: 三条技能路由。
- Modify `frontend/src/components/layout/Layout.tsx`: `alpha-mind` 下方一级“技能管理”入口。
- Modify `frontend/src/pages/Agent.tsx` and `frontend/src/components/agent/AgentThread.tsx`: HITL interrupt 存在时禁用 Composer。

### Frontend tests and acceptance

- Create `frontend/src/pages/Skills.test.tsx`, `frontend/src/pages/SkillDetail.test.tsx`, `frontend/src/components/skills/SkillImportDialog.test.tsx`.
- Modify `frontend/src/components/layout/Layout.test.tsx`, `frontend/src/components/agent/AgentThread.test.tsx`, `frontend/src/pages/Agent.test.tsx`.
- Modify `backend/tests/agent_e2e/server_graph.py`, `backend/tests/agent_e2e/start_langgraph.py`, `frontend/playwright.config.ts`, `frontend/e2e/agent-workspace.spec.ts`.
- Create `frontend/e2e/skill-management.spec.ts`.
- Modify `README.md` and `CHANGELOG.md`.

## UI Direction

- Audience: self-hosted 投研用户；single job: 快速判断技能来源与实际加载状态，并安全完成管理操作。
- Palette/type: 不新增颜色或字体，严格复用 `background` 深蓝黑、`primary` 暖橙、`success`、`warning`、`destructive` 运行时 token，以及 Inter / JetBrains Mono。
- Layout: 无 hero、无嵌套卡片。页头下先“用户技能”操作带，再“内置技能”只读带；两区用标题、计数和细分隔线编码来源。
- Signature: 每张技能卡左侧固定 3px 来源条；用户技能用状态色，内置技能用低对比边框。它表达真实来源，不添加装饰图形。
- Responsive: `grid-cols-1 lg:grid-cols-2`；卡片最小高度固定，名称/描述可换行，开关和删除图标不挤压正文。

```text
Desktop
+--------------------------------------------------------------+
| 技能管理                              [导入技能]             |
| 用户技能  2 个 / 已加载 1 个                                |
| | user card                       | user card                 |
|--------------------------------------------------------------|
| 内置技能  5 个                         始终启用              |
| | builtin card                    | builtin card              |
+--------------------------------------------------------------+

Mobile
+--------------------------+
| 技能管理       [导入]    |
| 用户技能 2 / 1           |
| user card                |
| user card                |
| 内置技能 5               |
| builtin card             |
+--------------------------+
```

## Spec Coverage Map

| Design requirement | Implementation task |
|---|---|
| Builtin/user distinction, routes, list/detail UX | Tasks 7-9 |
| Folder/ZIP import, limits, default disabled | Tasks 5-6, 8 |
| Directory-position enable/disable/delete semantics | Task 4 |
| Strict YAML contract and no private helper import | Task 1 |
| Tool-level invalid/conflict/symlink isolation | Task 2 |
| Builtin later-wins and custom no-execute prompt | Tasks 2-3 |
| Explicit current-session `/reload-skills` | Task 3 |
| Process snapshot and `skills.path` drift conflict | Tasks 1, 4 |
| Missing config still lists builtins | Tasks 4, 6 |
| HITL Composer blocking | Task 10 |
| Shared isolated FastAPI/LangGraph acceptance | Task 11 |
| Privacy, neutral product boundary, final gates | Tasks 11-12 |

## Task 1: Strict Skill Contract And Settings Path

**Files:**
- Create: `backend/agent/skill_catalog.py`
- Modify: `backend/agent/settings.py`
- Modify: `backend/agent/workflow_loader.py`
- Test: `backend/tests/agent/test_skill_catalog.py`
- Test: `backend/tests/agent/test_settings.py`
- Test: `backend/tests/agent/test_workflow_loader.py`

- [ ] **Step 1: Write failing strict-parser tests**

Create helpers and exact cases in `test_skill_catalog.py`:

```python
from pathlib import Path
import pytest

from agent.skill_catalog import SkillValidationError, parse_skill_document

VALID = "---\nname: sample-skill\ndescription: 用于结构化研究。\n---\n\n# 指令\n"

def test_parse_skill_document_returns_public_metadata() -> None:
    parsed = parse_skill_document(VALID, "sample-skill", "/user/sample-skill/SKILL.md")
    assert parsed.metadata["name"] == "sample-skill"
    assert parsed.metadata["path"] == "/user/sample-skill/SKILL.md"
    assert parsed.instructions == VALID

@pytest.mark.parametrize("document", [
    "---\nname: other\ndescription: x\n---\n",
    "---\nname: Sample\ndescription: x\n---\n",
    "---\nname: sample-skill\ndescription: !!python/object/apply:os.system ['id']\n---\n",
    "---\nname: &n sample-skill\ndescription: *n\n---\n",
])
def test_parse_skill_document_rejects_mismatch_tags_and_aliases(document: str) -> None:
    with pytest.raises(SkillValidationError):
        parse_skill_document(document, "sample-skill", "/user/sample-skill/SKILL.md")
```

Add explicit tests for 64/1024/500 character limits, `metadata: dict[str, str]`, `allowed-tools` string/list, missing delimiters, non-mapping YAML, invalid UTF-8 at file boundary, and 10 MiB maximum.

- [ ] **Step 2: Run parser tests and confirm RED**

Run: `cd backend && .venv/bin/pytest tests/agent/test_skill_catalog.py -q`

Expected: collection fails because `agent.skill_catalog` does not exist.

- [ ] **Step 3: Implement the public parser without private Deep Agents imports**

Use these public shapes and constants in `skill_catalog.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import re
import yaml
from yaml.tokens import AliasToken, AnchorToken
from deepagents.middleware.skills import SkillMetadata

MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024
MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024
MAX_SKILL_COMPATIBILITY_LENGTH = 500

class SkillValidationError(ValueError):
    """只包含稳定中文原因，不包含物理路径或原始内容。"""

@dataclass(frozen=True, slots=True)
class ParsedSkill:
    metadata: SkillMetadata
    instructions: str

def _skill_name_valid(name: str, directory_name: str) -> bool:
    return (
        0 < len(name) <= MAX_SKILL_NAME_LENGTH
        and name == directory_name
        and not name.startswith("-")
        and not name.endswith("-")
        and "--" not in name
        and all(char == "-" or char.isdigit() or (char.isalpha() and char.islower()) for char in name)
    )

def parse_skill_document(content: str, directory_name: str, virtual_path: str) -> ParsedSkill:
    if len(content.encode("utf-8")) > MAX_SKILL_FILE_SIZE:
        raise SkillValidationError("SKILL.md 超过 10 MiB")
    match = re.match(r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", content, re.DOTALL)
    if match is None:
        raise SkillValidationError("SKILL.md 缺少合法 YAML frontmatter")
    try:
        tokens = yaml.scan(match.group(1))
        if any(isinstance(token, (AnchorToken, AliasToken)) for token in tokens):
            raise SkillValidationError("YAML frontmatter 不允许 anchor 或 alias")
        raw = yaml.safe_load(match.group(1))
    except SkillValidationError:
        raise
    except yaml.YAMLError:
        raise SkillValidationError("YAML frontmatter 解析失败") from None
    if not isinstance(raw, dict):
        raise SkillValidationError("YAML frontmatter 必须是对象")
    name = raw.get("name")
    description = raw.get("description")
    if not isinstance(name, str) or not _skill_name_valid(name, directory_name):
        raise SkillValidationError("技能 name 格式无效或与目录名不一致")
    if not isinstance(description, str) or not 0 < len(description.strip()) <= MAX_SKILL_DESCRIPTION_LENGTH:
        raise SkillValidationError("技能 description 缺失或超过 1024 字符")
    license_value = raw.get("license")
    if license_value is not None and not isinstance(license_value, str):
        raise SkillValidationError("技能 license 必须是字符串")
    compatibility = raw.get("compatibility")
    if compatibility is not None and (
        not isinstance(compatibility, str)
        or not 0 < len(compatibility.strip()) <= MAX_SKILL_COMPATIBILITY_LENGTH
    ):
        raise SkillValidationError("技能 compatibility 必须是 1-500 字符的字符串")
    metadata_raw = raw.get("metadata", {})
    if not isinstance(metadata_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in metadata_raw.items()
    ):
        raise SkillValidationError("技能 metadata 必须是字符串键值对象")
    tools_raw = raw.get("allowed-tools", [])
    if isinstance(tools_raw, str):
        allowed_tools = [tool for tool in re.split(r"[\s,]+", tools_raw) if tool]
    elif isinstance(tools_raw, list) and all(isinstance(tool, str) and tool.strip() for tool in tools_raw):
        allowed_tools = [tool.strip() for tool in tools_raw]
    else:
        raise SkillValidationError("技能 allowed-tools 必须是字符串或字符串数组")
    metadata = SkillMetadata(
        name=name, description=description.strip(), path=virtual_path,
        license=license_value.strip() or None if license_value is not None else None,
        compatibility=compatibility.strip() if compatibility is not None else None,
        metadata=dict(metadata_raw), allowed_tools=allowed_tools,
    )
    return ParsedSkill(metadata=metadata, instructions=content)

def parse_skill_file(path: Path, virtual_path: str) -> ParsedSkill:
    raw = path.read_bytes()
    if len(raw) > MAX_SKILL_FILE_SIZE:
        raise SkillValidationError("SKILL.md 超过 10 MiB")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SkillValidationError("SKILL.md 必须是 UTF-8 文本") from None
    return parse_skill_document(content, path.parent.name, virtual_path)
```

Do not coerce metadata fields with `str()`. Parenthesize the optional license expression in production code so formatter/type-checker interpretation is unambiguous.

- [ ] **Step 4: Lock settings path creation and root rejection with tests**

Add to `test_settings.py`:

```python
def test_load_settings_creates_missing_skill_root_with_0700(tmp_path: Path) -> None:
    root = tmp_path / "new" / "skills"
    settings_path = write_valid_settings(tmp_path, root)
    loaded = load_agent_settings(settings_path)
    assert loaded.skills.path == root.resolve()
    assert root.stat().st_mode & 0o777 == 0o700

def test_load_settings_rejects_filesystem_root(tmp_path: Path) -> None:
    settings_path = write_valid_settings(tmp_path, Path(tmp_path.anchor))
    with pytest.raises(AgentSettingsError, match="文件系统根"):
        load_agent_settings(settings_path)
```

Expose and use one path function:

```python
def resolve_skills_path(value: Path, *, create: bool) -> Path:
    resolved = value.expanduser().resolve()
    if resolved.parent == resolved:
        raise AgentSettingsError("Agent Skills 路径不能是文件系统根")
    if create and not resolved.exists():
        resolved.mkdir(parents=True, mode=0o700)
        resolved.chmod(0o700)
    if not resolved.is_dir() or not os.access(resolved, os.R_OK | os.W_OK):
        raise AgentSettingsError("Agent Skills 目录不存在、不可读写或不是目录")
    return resolved
```

- [ ] **Step 5: Migrate workflow validation to the public parser**

In `_verify_instruction`, replace both private calls with:

```python
try:
    parse_skill_file(skill_file, virtual_path=f"/builtin/{skill_root.name}/SKILL.md")
except (OSError, UnicodeDecodeError, SkillValidationError):
    raise _config_error(field, "Skill 根 SKILL.md frontmatter 元数据无效") from None
```

Add a static regression assertion:

```python
def test_workflow_loader_does_not_import_private_skill_helpers() -> None:
    source = Path(workflow_loader.__file__).read_text(encoding="utf-8")
    assert "_parse_skill_metadata" not in source
    assert "_validate_skill_name" not in source
```

- [ ] **Step 6: Run focused tests and commit**

Run: `cd backend && .venv/bin/pytest tests/agent/test_skill_catalog.py tests/agent/test_settings.py tests/agent/test_workflow_loader.py -q`

Expected: PASS.

Commit:

```bash
git add backend/agent/skill_catalog.py backend/agent/settings.py backend/agent/workflow_loader.py backend/tests/agent
git commit -m "feat: add strict skill catalog validation"
```

## Task 2: Filtered Read-Only Skill Backend

**Files:**
- Modify: `backend/agent/skill_catalog.py`
- Modify: `backend/agent/skill_backends.py`
- Modify: `backend/agent/embedded_graph.py`
- Test: `backend/tests/agent/test_skill_catalog.py`
- Test: `backend/tests/agent/test_graph.py`
- Test: `backend/tests/agent/test_embedded_graph.py`

- [ ] **Step 1: Write failing backend isolation tests**

Create valid, invalid, conflicting and symlinked trees, then assert both sync and async calls:

```python
def test_filtered_backend_hides_invalid_and_excluded_skills(tmp_path: Path) -> None:
    write_skill(tmp_path / "valid", "valid")
    write_skill(tmp_path / "blocked", "other")
    backend = FilteredSkillBackend(tmp_path, excluded_names={"conflict"})
    assert [entry["path"] for entry in backend.ls("/").entries or []] == ["/valid/"]
    assert backend.read("/blocked/SKILL.md").error is not None
    assert backend.download_files(["/blocked/SKILL.md"])[0].error == FILE_NOT_FOUND

@pytest.mark.asyncio
async def test_filtered_backend_async_contract_matches_sync(tmp_path: Path) -> None:
    write_skill(tmp_path / "valid", "valid")
    backend = FilteredSkillBackend(tmp_path)
    assert (await backend.als("/")).entries == backend.ls("/").entries
    assert (await backend.aread("/valid/SKILL.md")).file_data
    assert (await backend.adownload_files(["/valid/SKILL.md"]))[0].content
```

On platforms supporting symlinks, create `valid/references/escape -> ../../blocked/secret.md` and assert `read`/`download_files` return not found.

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd backend && .venv/bin/pytest tests/agent/test_skill_catalog.py -q`

Expected: FAIL because `FilteredSkillBackend` is absent.

- [ ] **Step 3: Implement authorization at every backend operation**

Add this concrete interface to `skill_catalog.py`:

```python
class FilteredSkillBackend(BackendProtocol):
    def __init__(self, root: Path | str, *, excluded_names: set[str] | frozenset[str] = frozenset()):
        self.root = Path(root).resolve()
        self._excluded = frozenset(excluded_names)
        self._delegate = FilesystemBackend(root_dir=self.root, virtual_mode=True)

    def _authorized(self, virtual_path: str) -> bool:
        normalized = PurePosixPath("/" + virtual_path.lstrip("/"))
        if ".." in normalized.parts or len(normalized.parts) < 2:
            return False
        name = normalized.parts[1]
        skill_root = self.root / name
        if name in self._excluded or skill_root.is_symlink():
            return False
        try:
            parse_skill_file(skill_root / "SKILL.md", f"/{name}/SKILL.md")
            (self.root / str(normalized).lstrip("/")).resolve().relative_to(skill_root.resolve())
        except (OSError, UnicodeDecodeError, ValueError, SkillValidationError):
            return False
        return True

    def ls(self, path: str) -> LsResult:
        if path.rstrip("/") in {"", "."}:
            result = self._delegate.ls("/")
            entries = [entry for entry in result.entries or [] if self._authorized(entry["path"])]
            return LsResult(error=result.error, entries=entries)
        return self._delegate.ls(path) if self._authorized(path) else LsResult(error="path_not_found")

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._delegate.read(file_path, offset, limit) if self._authorized(file_path) else ReadResult(error="file_not_found")

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            self._delegate.download_files([path])[0]
            if self._authorized(path)
            else FileDownloadResponse(path=path, content=None, error=FILE_NOT_FOUND)
            for path in paths
        ]
```

Keep inherited `als`/`aread`/`adownload_files`, which dispatch through these guarded sync methods. Normalize root listing correctly for both `"/"` and empty route-stripped paths; tests must lock the actual `CompositeBackend` behavior.

- [ ] **Step 4: Build strict composite backends and prompt**

Define:

```python
SKILLS_SYSTEM_PROMPT = """## Skills
{skills_locations}{skills_load_warnings}

可用技能：
{skills_list}

先按名称与描述判断是否适用，再用 `read_file` 读取列出的 SKILL.md；附属文件和脚本仅作只读参考，不能执行。
"""

def build_skill_backend(builtin_root: Path | str, user_root: Path | str) -> CompositeBackend:
    builtin_names = valid_skill_names(Path(builtin_root))
    return CompositeBackend(default=StateBackend(), routes={
        "/builtin/": FilteredSkillBackend(builtin_root),
        "/user/": FilteredSkillBackend(user_root, excluded_names=builtin_names),
    })

def build_builtin_skill_backend(builtin_root: Path | str) -> CompositeBackend:
    return CompositeBackend(default=StateBackend(), routes={
        "/builtin/": FilteredSkillBackend(builtin_root),
    })
```

Update `embedded_graph.py` to use `build_builtin_skill_backend` and pass `system_prompt_template=SKILLS_SYSTEM_PROMPT`. It must still expose no `/user/` route.

- [ ] **Step 5: Update graph construction assertions and commit**

Assert workspace sources will be `['/user/', '/builtin/']`, embedded sources remain `['/builtin/']`, prompt is custom, and raw `FilesystemBackend` is absent from routed skill backends.

Run: `cd backend && .venv/bin/pytest tests/agent/test_skill_catalog.py tests/agent/test_graph.py tests/agent/test_embedded_graph.py -q`

Expected: PASS.

Commit:

```bash
git add backend/agent/skill_catalog.py backend/agent/skill_backends.py backend/agent/embedded_graph.py backend/tests/agent
git commit -m "feat: isolate strict skill filesystem views"
```

## Task 3: Explicit Session Skill Reload

**Files:**
- Create: `backend/agent/skill_reload.py`
- Modify: `backend/agent/graph.py`
- Test: `backend/tests/agent/test_skill_reload.py`
- Test: `backend/tests/agent/test_graph.py`

- [ ] **Step 1: Write failing command and middleware tests**

Cover exact string, whitespace, pure text blocks, mixed blocks, similar commands, old error clearing, sync/async parity, no model call, `after_agent` trace and ephemeral `jump_to`:

```python
@pytest.mark.parametrize((content, expected), [
    ("/reload-skills", True),
    ("  /reload-skills\n", True),
    ([{"type": "text", "text": "/reload-"}, {"type": "text", "text": "skills"}], True),
    ([{"type": "text", "text": "/reload-skills"}, {"type": "image", "url": "x"}], False),
    ("/reload-skills now", False),
])
def test_is_reload_command(content: object, expected: bool) -> None:
    assert is_reload_command([HumanMessage(content=content)]) is expected
```

For middleware, seed state with old `skills_metadata` and `skills_load_errors`, change the temp skill root, invoke both hooks, and assert the update contains complete new lists, one deterministic AI message and `jump_to == 'end'`.

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd backend && .venv/bin/pytest tests/agent/test_skill_reload.py -q`

Expected: collection fails because `agent.skill_reload` does not exist.

- [ ] **Step 3: Implement paired public hooks**

```python
class ReloadableSkillsMiddleware(SkillsMiddleware):
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
```

`_reload_update` must count `metadata['path'].startswith('/builtin/')`, explicitly set `skills_load_errors` to `loaded.get("skills_load_errors", [])`, append exactly one `AIMessage`, and set `jump_to: 'end'`. Do not import `_list_skills`, `_alist_skills` or `_with_errors` variants.

- [ ] **Step 4: Replace workspace Skills middleware**

In `graph.py` instantiate:

```python
ReloadableSkillsMiddleware(
    backend=backend,
    sources=["/user/", "/builtin/"],
    system_prompt_template=SKILLS_SYSTEM_PROMPT,
)
```

Keep it after `SessionTraceMiddleware`, before `FilesystemMiddleware`, and only in the workspace graph.

- [ ] **Step 5: Run focused and compiled-graph tests, then commit**

Run: `cd backend && .venv/bin/pytest tests/agent/test_skill_reload.py tests/agent/test_graph.py -q`

Expected: PASS; the reload integration test reports zero scripted-model invocations and the next run state contains no `jump_to`.

Commit:

```bash
git add backend/agent/skill_reload.py backend/agent/graph.py backend/tests/agent
git commit -m "feat: add explicit skill reload command"
```

## Task 4: Skill Manager Enumeration And Mutations

**Files:**
- Create: `backend/skillmgr.py`
- Create: `backend/tests/test_skillmgr.py`

- [ ] **Step 1: Write failing storage-model tests**

Test active/disabled enumeration, invalid and builtin collision states, stable sorting, virtual details, idempotent moves, simultaneous-name conflict, delete, root snapshot drift, and safe `OSError` mapping:

```python
def test_list_marks_active_builtin_collision_blocked(manager: SkillManager) -> None:
    write_skill(manager.active_root / "stock-analysis", "stock-analysis")
    result = manager.list_skills()
    item = next(item for item in result["user"] if item["name"] == "stock-analysis")
    assert {key: item[key] for key in ("enabled", "valid", "effective")} == {
        "enabled": True, "valid": True, "effective": False,
    }
    assert item["error"] == "与内置技能同名，已阻止加载"

def test_set_enabled_is_idempotent(manager: SkillManager) -> None:
    write_skill(manager.disabled_root / "sample", "sample")
    first = manager.set_enabled("sample", True)
    second = manager.set_enabled("sample", True)
    assert first == second
    assert (manager.active_root / "sample").is_dir()
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd backend && .venv/bin/pytest tests/test_skillmgr.py -q`

Expected: collection fails because `skillmgr` does not exist.

- [ ] **Step 3: Implement manager contracts and process snapshot**

Use these shapes:

```python
class SkillManagerError(RuntimeError):
    def __init__(self, kind: Literal["bad_request", "not_found", "conflict", "too_large", "unavailable", "internal"], message: str):
        super().__init__(message)
        self.kind = kind

@dataclass(frozen=True, slots=True)
class SkillRoots:
    settings_path: Path
    active: Path
    disabled: Path

```

The concrete public methods are `list_skills() -> dict[str, object]`, `get_skill(source, name) -> dict[str, object]`, `set_enabled(name, enabled) -> dict[str, object]`, `delete(name) -> dict[str, bool]`, `import_folder(files) -> dict[str, object]`, and `import_zip(filename, content_b64) -> dict[str, object]`. Keep their names and argument order unchanged in API and tests.

Expose read-only `active_root` and `disabled_root` properties for filesystem operations and tests. Add one cached factory:

```python
@lru_cache(maxsize=1)
def get_skill_manager() -> SkillManager:
    settings_path = agent_settings_path().expanduser().resolve()
    try:
        settings = load_agent_settings(settings_path)
    except AgentSettingsError:
        return SkillManager(BUILTIN_SKILLS_DIR, roots=None, user_error="Agent 设置缺失或无效")
    active = settings.skills.path
    disabled = active.parent / f"{active.name}.disabled"
    return SkillManager(
        BUILTIN_SKILLS_DIR,
        roots=SkillRoots(settings_path=settings_path, active=active, disabled=disabled),
    )
```

Tests call `get_skill_manager.cache_clear()` around any case that changes the settings fixture; production keeps the first snapshot for the process lifetime.

Derive `disabled = active.parent / f'{active.name}.disabled'`, create it with `0700`, and keep one module-level `threading.Lock`. Before every mutation, while holding the lock, parse only `skills.path` from the current JSON and compare its resolved path to the snapshot. A missing/invalid/different path raises conflict without touching either root; unrelated settings changes do not.

Map filesystem errors by `errno`: `EEXIST/ENOTEMPTY -> conflict`, `EACCES/EPERM/EROFS -> unavailable`, all other `OSError -> internal`. Messages are fixed Chinese strings and never include `str(exc)` or a physical path.

- [ ] **Step 4: Implement summaries and physical-state rules**

Every summary must contain:

```python
{
    "name": name,
    "description": description_or_none,
    "source": "builtin" | "user",
    "enabled": True | False,
    "valid": True | False,
    "effective": True | False,
    "error": safe_error_or_none,
}
```

Details add only virtual `path` and `instructions`; invalid details set `instructions=None`. Ignore non-directory entries and directory names that fail the name grammar. If both active and disabled copies exist, mutation raises conflict and enumeration reports one safe blocked diagnostic rather than choosing a copy.

- [ ] **Step 5: Run tests and commit**

Run: `cd backend && .venv/bin/pytest tests/test_skillmgr.py -q`

Expected: PASS.

Commit:

```bash
git add backend/skillmgr.py backend/tests/test_skillmgr.py
git commit -m "feat: add local skill manager"
```

## Task 5: Bounded Folder And ZIP Import

**Files:**
- Modify: `backend/skillmgr.py`
- Modify: `backend/tests/test_skillmgr.py`

- [ ] **Step 1: Add failing import matrix**

Add exact fixtures for root `SKILL.md`, one wrapper directory, multiple roots, 256/257 files, 25 MiB boundary, pure/data-URI base64, backslash paths, traversal, duplicate normalized paths, `__MACOSX`, `.DS_Store`, symlink/encrypted/corrupt/unsupported ZIP, high-compression streamed overflow, and cleanup after failure.

```python
def test_import_folder_preserves_bytes_and_defaults_disabled(manager: SkillManager) -> None:
    payload = [
        {"path": "sample/SKILL.md", "content_b64": b64(VALID_SKILL)},
        {"path": "sample/assets/raw.bin", "content_b64": b64(b"\x00\xff")},
    ]
    item = manager.import_folder(payload)
    assert item["enabled"] is False
    assert (manager.disabled_root / "sample/assets/raw.bin").read_bytes() == b"\x00\xff"
```

- [ ] **Step 2: Run import tests and confirm RED**

Run: `cd backend && .venv/bin/pytest tests/test_skillmgr.py -k import -q`

Expected: FAIL because import methods are absent.

- [ ] **Step 3: Implement shared bounded staging pipeline**

Add constants and helpers:

```python
MAX_BODY_BYTES = 36 * 1024 * 1024
MAX_EXTRACTED_BYTES = 25 * 1024 * 1024
MAX_FILES = 256

def decode_base64(value: str) -> bytes:
    encoded = value
    if value.startswith("data:"):
        header, separator, encoded = value.partition(",")
        if not separator or not re.fullmatch(r"data:[^;,]+;base64", header):
            raise SkillManagerError("bad_request", "data URI 必须包含非空 MIME 与 base64 标记")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise SkillManagerError("bad_request", "base64 内容无效") from None

def normalize_member_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    raw_parts = normalized.split("/")
    if (
        not normalized or "\x00" in normalized or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise SkillManagerError("bad_request", "导入路径无效")
    return PurePosixPath(*raw_parts)

def identify_skill_root(paths: set[PurePosixPath]) -> PurePosixPath:
    candidates = [path.parent for path in paths if path.name == "SKILL.md"]
    if len(candidates) != 1 or len(candidates[0].parts) > 1:
        raise SkillManagerError("bad_request", "导入包必须有且仅有一个技能根")
    return candidates[0]
```

The caller maintains a `seen_paths` set and rejects duplicate normalized paths before staging. It also verifies `(temp_root / path).resolve().is_relative_to(temp_root.resolve())` before the first write. The staging function performs these operations in order: create `tempfile.mkdtemp(dir=disabled.parent)`, determine the unique root from the complete normalized name set, stream each file in 64 KiB chunks, count bytes before deciding whether to discard macOS noise, parse the staged root `SKILL.md`, check all three collision sets under the manager lock, then `os.replace(staged_skill_root, disabled/name)`. Its `finally` removes the remaining temporary wrapper and logs only a fixed warning if cleanup fails.

- [ ] **Step 4: Implement ZIP streaming without `extractall`**

For each `ZipInfo`, reject encryption (`flag_bits & 0x1`), symlink mode (`external_attr >> 16`), and unsupported compression. Open each member and copy in 64 KiB chunks while incrementing the actual global byte count; never trust `file_size`. Convert `BadZipFile`, CRC and decompression errors into `bad_request`, always remove the temp tree in `finally`, and never execute staged scripts.

- [ ] **Step 5: Run import tests and commit**

Run: `cd backend && .venv/bin/pytest tests/test_skillmgr.py -q`

Expected: PASS and no temp directory remains after every parametrized failure.

Commit:

```bash
git add backend/skillmgr.py backend/tests/test_skillmgr.py
git commit -m "feat: add bounded local skill import"
```

## Task 6: FastAPI Skill Management API

**Files:**
- Modify: `backend/app.py`
- Create: `backend/tests/test_skills_api.py`

- [ ] **Step 1: Write failing API contract tests**

Use `TestClient`, monkeypatch `app_module.get_skill_manager` to a temp manager, and test all five routes, auth, exact response shapes, invalid detail null body, PATCH idempotency, error codes, 36 MiB body streaming, and builtins when user config is unavailable.

```python
def test_skill_list_keeps_builtins_when_user_unavailable(client, unavailable_manager) -> None:
    response = client.get("/api/skills")
    assert response.status_code == 200
    body = response.json()
    assert body["builtin"]
    assert body["user_available"] is False

def test_skill_patch_is_idempotent(client, manager) -> None:
    first = client.patch("/api/skills/user/sample", json={"enabled": True})
    second = client.patch("/api/skills/user/sample", json={"enabled": True})
    assert first.status_code == second.status_code == 200
```

- [ ] **Step 2: Run API tests and confirm RED**

Run: `cd backend && .venv/bin/pytest tests/test_skills_api.py -q`

Expected: routes return 404.

- [ ] **Step 3: Add request models and bounded JSON reader**

Define `FolderFileIn`, `FolderImportIn`, `ZipImportIn`, discriminated `SkillImportIn`, and `SkillEnabledIn`. The import route must call:

```python
async def _bounded_json_body(request: Request) -> bytes:
    raw_length = request.headers.get("content-length")
    if raw_length and raw_length.isdigit() and int(raw_length) > skillmgr.MAX_BODY_BYTES:
        raise HTTPException(413, "导入请求超过 36 MiB")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > skillmgr.MAX_BODY_BYTES:
            raise HTTPException(413, "导入请求超过 36 MiB")
        chunks.append(chunk)
    return b"".join(chunks)
```

Only after this returns may `TypeAdapter(SkillImportIn).validate_json(body)` run.

- [ ] **Step 4: Add routes and stable error mapping**

```python
_SKILL_ERROR_STATUS = {
    "bad_request": 400,
    "not_found": 404,
    "conflict": 409,
    "too_large": 413,
    "unavailable": 503,
    "internal": 500,
}

def _skill_api_call(operation, *args):
    try:
        return operation(*args)
    except SkillManagerError as exc:
        raise HTTPException(_SKILL_ERROR_STATUS[exc.kind], str(exc)) from None

@app.get("/api/skills")
def skills_list(): return get_skill_manager().list_skills()

@app.get("/api/skills/{source}/{name}")
def skill_detail(source: Literal["builtin", "user"], name: str):
    return _skill_api_call(get_skill_manager().get_skill, source, name)

@app.post("/api/skills/import")
async def skill_import(request: Request):
    body = await _bounded_json_body(request)
    try:
        payload = SKILL_IMPORT_ADAPTER.validate_json(body)
    except ValidationError:
        raise HTTPException(400, "导入请求格式无效") from None
    manager = get_skill_manager()
    if isinstance(payload, FolderImportIn):
        return _skill_api_call(manager.import_folder, [item.model_dump() for item in payload.files])
    return _skill_api_call(manager.import_zip, payload.filename, payload.content_b64)

@app.patch("/api/skills/user/{name}")
def skill_toggle(name: str, payload: SkillEnabledIn):
    return _skill_api_call(get_skill_manager().set_enabled, name, payload.enabled)

@app.delete("/api/skills/user/{name}")
def skill_delete(name: str):
    return _skill_api_call(get_skill_manager().delete, name)
```

Map manager kinds to `400/404/409/413/503/500`; raise `HTTPException` with only `exc.args[0]`. Keep these routes as disk/business operations: no LangGraph SDK import, no model call, no thread mutation.

- [ ] **Step 5: Run API and offline backend suites, then commit**

Run: `cd backend && .venv/bin/pytest tests/test_skills_api.py tests/test_skillmgr.py tests/agent -q`

Expected: PASS.

Commit:

```bash
git add backend/app.py backend/tests/test_skills_api.py
git commit -m "feat: expose local skill management api"
```

## Task 7: Frontend API, Routes, Navigation And Toggle

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/router.tsx`
- Modify: `frontend/src/components/layout/Layout.tsx`
- Modify: `frontend/src/components/layout/Layout.test.tsx`
- Create: `frontend/src/components/skills/SkillToggle.tsx`

- [ ] **Step 1: Add failing navigation and API source tests**

Update layout tests to render `/skills/user/sample`, assert “技能管理” is the only active new link, and assert it immediately follows `alpha-mind` in DOM order. Add a Node source contract that `request` includes `PATCH` and pages do not call raw `fetch`.

- [ ] **Step 2: Add strict API types and methods**

```typescript
export type SkillSource = "builtin" | "user";
export interface SkillSummary {
  name: string;
  description: string | null;
  source: SkillSource;
  enabled: boolean;
  valid: boolean;
  effective: boolean;
  error: string | null;
}
export interface SkillDetail extends SkillSummary {
  path: string;
  instructions: string | null;
}
export interface SkillsResponse {
  builtin: SkillSummary[];
  user: SkillSummary[];
  user_available: boolean;
  user_error?: string;
}
export type SkillImportPayload =
  | { kind: "folder"; files: Array<{ path: string; content_b64: string }> }
  | { kind: "zip"; filename: string; content_b64: string };
```

Change method union to `"GET" | "POST" | "PATCH" | "DELETE"` and add `skills`, `skillDetail`, `importSkill`, `setSkillEnabled`, `deleteSkill` methods with `encodeURIComponent(name)`.

- [ ] **Step 3: Add routes, nav item and accessible switch**

Add `Sparkles` to lucide imports and put `{ to: '/skills', icon: Sparkles, label: '技能管理' }` immediately after `/agent`. Add all three routes. Implement `SkillToggle` as a fixed `h-6 w-10` iconless binary control with `role="switch"`, `aria-checked`, an external visible label, pending disable, and no layout shift.

- [ ] **Step 4: Run tests and build, then commit**

Run: `cd frontend && npm test && npm run test:unit -- src/components/layout/Layout.test.tsx && npm run build`

Expected: PASS.

Commit:

```bash
git add frontend/src/lib/api.ts frontend/src/router.tsx frontend/src/components/layout frontend/src/components/skills
git commit -m "feat: add skill management frontend contracts"
```

## Task 8: Skills List And Import Dialog

**Files:**
- Create: `frontend/src/pages/Skills.tsx`
- Create: `frontend/src/pages/Skills.test.tsx`
- Create: `frontend/src/components/skills/SkillImportDialog.tsx`
- Create: `frontend/src/components/skills/SkillImportDialog.test.tsx`

- [ ] **Step 1: Write failing list-page tests**

Mock `api.skills`, render with `MemoryRouter`, and assert user section precedes builtins, counts use `effective`, invalid active rows show “已阻止” plus disable/delete affordances, card click navigates, switch click does not, failed PATCH keeps prior state, and unavailable user config leaves builtins visible.

- [ ] **Step 2: Implement list loading and mutation flow**

Use component-local state with `useEffect`, because this page has no cross-route shared cache. Render one unframed section per source and one card per skill; `onClick={() => navigate(skillRoute)}` on card plus `event.stopPropagation()` on controls. On successful mutation refetch then call:

```typescript
toast.success("技能状态已更新。新会话将自动使用最新配置；已有会话请执行 /reload-skills。");
```

On failure, do not mutate state and call `toast.error(error instanceof Error ? error.message : "技能操作失败")`.

- [ ] **Step 3: Write failing import tests**

Test folder `webkitRelativePath`, ZIP filename/data URL, segmented mode, pending disable, successful close/refetch, and failure retaining selection. Use fake `FileReader` and exact payload assertions.

- [ ] **Step 4: Implement import dialog**

Use existing Base UI `Dialog`, `DialogContent`, `DialogHeader`, `DialogDescription`, `DialogFooter`; mode uses a two-button segmented control, not text pills. Define a typed directory input prop for `webkitdirectory`. Convert files with `FileReader.readAsDataURL`, preserve every relative path, and submit one `SkillImportPayload` through `api.importSkill`.

The dialog must state: files stay local on import; after enable, metadata enters local checkpoints and read instructions are sent to the configured model. It does not offer Git URL, editor, marketplace or execution controls.

- [ ] **Step 5: Run unit tests and commit**

Run: `cd frontend && npm run test:unit -- src/pages/Skills.test.tsx src/components/skills/SkillImportDialog.test.tsx && npm run build`

Expected: PASS.

Commit:

```bash
git add frontend/src/pages/Skills.tsx frontend/src/pages/Skills.test.tsx frontend/src/components/skills
git commit -m "feat: add skill list and local import ui"
```

## Task 9: Skill Detail, Markdown And Delete

**Files:**
- Create: `frontend/src/pages/SkillDetail.tsx`
- Create: `frontend/src/pages/SkillDetail.test.tsx`

- [ ] **Step 1: Write failing detail tests**

Test builtin read-only state, valid user toggle/delete, invalid `instructions: null` diagnostic, virtual path only, Markdown/GFM rendering, back navigation, 404 state, request disable, destructive confirmation, and failed delete retaining the page.

- [ ] **Step 2: Implement route-driven detail**

Read `source` and `name` from `useParams`, reject any source outside `builtin/user`, fetch through `api.skillDetail`, and render:

```tsx
<div className="prose prose-sm dark:prose-invert max-w-none wrap-break-word text-foreground">
  <ReactMarkdown remarkPlugins={[remarkGfm]}>{skill.instructions}</ReactMarkdown>
</div>
```

Show `skill.path` in a wrapping mono row. Never render or infer a host path. For invalid skill, render the safe `error` and no Markdown body.

- [ ] **Step 3: Implement controls and confirmation**

Builtins show “内置 / 始终启用” only. Valid users show `SkillToggle`; active invalid users show a “停用” command but no enabled switch. Delete is a `Trash2` icon button with tooltip and an existing Base UI confirmation dialog; confirm text explicitly says the managed local copy is permanently deleted. On success navigate to `/skills` and show the standard refresh toast.

- [ ] **Step 4: Run tests and commit**

Run: `cd frontend && npm run test:unit -- src/pages/SkillDetail.test.tsx && npm run build`

Expected: PASS.

Commit:

```bash
git add frontend/src/pages/SkillDetail.tsx frontend/src/pages/SkillDetail.test.tsx
git commit -m "feat: add skill detail and deletion ui"
```

## Task 10: Block Composer During HITL Approval

**Files:**
- Modify: `frontend/src/pages/Agent.tsx`
- Modify: `frontend/src/components/agent/AgentThread.tsx`
- Modify: `frontend/src/components/agent/AgentThread.test.tsx`
- Modify: `frontend/src/pages/Agent.test.tsx`

- [ ] **Step 1: Write failing pending-interrupt tests**

Mock `useLangChainInterrupts` with one valid interrupt and assert the “Agent 消息” input and send button are disabled, “请先处理待审批工具调用” is visible, and ApprovalPanel radio/submit controls remain enabled. With no interrupt, existing composer behavior stays unchanged.

- [ ] **Step 2: Lift pending state inside runtime boundary**

Create `AgentContent` beneath `AgentRuntimeProvider`:

```tsx
function AgentContent({ desktop }: { desktop: boolean }) {
  const approvalPending = useLangChainInterrupts().length > 0;
  return <AgentWorkspace
    desktop={desktop}
    threads={<AgentThreadList />}
    approval={<ApprovalPanel disabled={false} />}
    chat={<AgentThread approvalPending={approvalPending} />}
  />;
}
```

Change `WorkspaceComposer` and `AgentThread` to accept `approvalPending`; disable input and send when `isRunning || approvalPending`. Do not create or submit `Command(resume=...)` from the composer.

- [ ] **Step 3: Run focused tests and commit**

Run: `cd frontend && npm run test:unit -- src/components/agent/AgentThread.test.tsx src/pages/Agent.test.tsx && npm run build`

Expected: PASS.

Commit:

```bash
git add frontend/src/pages/Agent.tsx frontend/src/pages/Agent.test.tsx frontend/src/components/agent/AgentThread.tsx frontend/src/components/agent/AgentThread.test.tsx
git commit -m "fix: block agent composer during tool approval"
```

## Task 11: Isolated End-To-End Skill Lifecycle

**Files:**
- Modify: `backend/tests/agent_e2e/server_graph.py`
- Modify: `backend/tests/agent_e2e/start_langgraph.py`
- Modify: `frontend/playwright.config.ts`
- Modify: `frontend/e2e/agent-workspace.spec.ts`
- Create: `frontend/e2e/skill-management.spec.ts`

- [ ] **Step 1: Make both services share one isolated settings file**

Export `E2E_SETTINGS_PATH = path.join(langGraphRoot, 'settings.json')` and set FastAPI `VR_AGENT_SETTINGS` to it. `start_langgraph.py` remains the single writer, creates active skills and disabled sibling in `VR_E2E_ROOT`, and writes `0600` settings before exec. Because skill manager initialization is lazy, no FastAPI module reload is needed.

- [ ] **Step 2: Give fixture workspace the production skill stack**

In `server_graph.py`, build the strict composite backend and include:

```python
ReloadableSkillsMiddleware(
    backend=backend,
    sources=["/user/", "/builtin/"],
    system_prompt_template=SKILLS_SYSTEM_PROMPT,
),
FilesystemMiddleware(backend=backend, tools=["ls", "read_file"]),
```

Keep existing MCP/HITL/trace middleware. Extend the scripted model only for a message “列出当前技能”：inspect the received system prompt and return a deterministic comma-separated list of visible skill names; preserve all existing approval replies.

- [ ] **Step 3: Add browser lifecycle scenario**

The new serial spec must:

1. Open `/skills`, import a folder skill with `SKILL.md` plus an asset, and observe disabled state.
2. Open `/agent`, create thread A while the skill is disabled, send “列出当前技能”, and assert `e2e-skill` is absent.
3. Return to detail, verify Markdown and `/user/e2e-skill/SKILL.md`, enable it, and verify the standard refresh toast.
4. Create thread B after enable and assert `e2e-skill` is visible while thread A still has its old cache.
5. Return to thread A, send exact `/reload-skills`, assert deterministic count message and no approval/model tool UI; then “列出当前技能” includes `e2e-skill`.
6. Trigger MCP interrupt, assert composer cannot send `/reload-skills`, reject via ApprovalPanel, then composer re-enables.
7. Return to skills, disable and permanently delete the user skill.

Export the isolated data root from `playwright.config.ts`, create `e2e-upload/e2e-skill/SKILL.md` and an asset beneath it with Node `fs`, and pass that directory path to Playwright `setInputFiles` on the directory picker so Chromium supplies real `webkitRelativePath` values. Remove the fixture directory in test cleanup; it never leaves `VR_E2E_DATA_ROOT`.

- [ ] **Step 4: Add desktop/mobile visual and overlap checks**

At `1440x900` and `390x844`, screenshot list and detail, assert no horizontal document overflow, cards are two columns only on desktop, all card text is within bounding boxes, dialog controls do not overlap, and the imported skill source/status remains a first-screen signal.

- [ ] **Step 5: Run E2E and commit**

Run: `cd frontend && npm run test:e2e -- e2e/skill-management.spec.ts e2e/agent-workspace.spec.ts`

Expected: PASS; no file appears under `~/.vibe-research` or the repository.

Commit:

```bash
git add backend/tests/agent_e2e frontend/playwright.config.ts frontend/e2e
git commit -m "test: cover skill management lifecycle"
```

## Task 12: Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Document the shipped user contract**

In README's `alpha-mind` section, replace the old generic Skills bullet with: builtins always read-only; user skills are managed at `/skills`; imports default disabled; active/disabled roots; existing sessions require `/reload-skills`; user skills never enter Ask-AI/fixed workflows; enabled instructions may be sent to the configured model. Add an unreleased changelog entry with the same boundaries, without implementation marketing.

- [ ] **Step 2: Run the complete verification matrix**

```bash
cd backend
.venv/bin/pytest -m "not live"

cd ../frontend
npm test
npm run test:unit
npm run build
npm run test:e2e -- e2e/skill-management.spec.ts e2e/agent-workspace.spec.ts e2e/unified-ai-workflows.spec.ts
```

Expected: every command exits 0.

- [ ] **Step 3: Run security/static checks**

```bash
rg -n "from deepagents\.middleware\.skills import .*_" backend --glob '*.py'
rg -n "extractall|yaml\.load\(" backend/skillmgr.py backend/agent/skill_catalog.py
git diff --check
git status --short
```

Expected: first two searches return no production matches; `git diff --check` is silent; status contains only the intended README/CHANGELOG changes before the final commit.

- [ ] **Step 4: Commit documentation and final gate**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: document skill management workflow"
git status --short
```

Expected: clean worktree.

## Final Review Checklist

- [ ] Every design-spec requirement maps to a task above; no editor, Git import, marketplace, scoring, recommendation or trading behavior was added.
- [ ] FastAPI routes perform disk operations only and import no LangGraph client/runtime.
- [ ] `embedded_agent` and four fixed workflows never see `/user/`.
- [ ] Tests prove invalid/conflicting paths fail at both metadata enumeration and tool read/download layers.
- [ ] Tests prove ZIP limits use actual streamed bytes and failures leave no staged or target directory.
- [ ] Tests prove reload sync/async parity, old error clearing, zero model/tool calls, `after_agent` trace and ephemeral jump cleanup.
- [ ] Frontend uses centralized API and storage wrappers, no raw page `fetch` or raw `localStorage`.
- [ ] Browser screenshots pass desktop/mobile overflow and overlap checks in both themes where practical.
- [ ] `git status` confirms no private user data, E2E artifacts or generated LangGraph state is staged.
