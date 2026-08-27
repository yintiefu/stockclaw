"""Task 1/2：技能目录严格契约——frontmatter 解析、目录扫描与过滤 backend。"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.skill_catalog import (
    MAX_SKILL_DESCRIPTION_LENGTH,
    MAX_SKILL_FILE_SIZE,
    MAX_SKILL_NAME_LENGTH,
    SkillValidationError,
    parse_skill_document,
    parse_skill_file,
)

VALID = "---\nname: sample-skill\ndescription: 用于结构化研究。\n---\n\n# 指令\n"


def _document(name: str = "sample-skill", description: str = "x") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# 指令\n"


def test_parse_skill_document_returns_public_metadata() -> None:
    parsed = parse_skill_document(VALID, "sample-skill", "/user/sample-skill/SKILL.md")
    assert parsed.metadata["name"] == "sample-skill"
    assert parsed.metadata["path"] == "/user/sample-skill/SKILL.md"
    assert parsed.instructions == VALID


def test_parse_skill_document_allows_staged_import_without_directory_name() -> None:
    parsed = parse_skill_document(VALID, None, "/user/import/SKILL.md")
    assert parsed.metadata["name"] == "sample-skill"


@pytest.mark.parametrize("document", [
    "---\nname: other\ndescription: x\n---\n",
    "---\nname: Sample\ndescription: x\n---\n",
    "---\nname: sample-skill\ndescription: !!python/object/apply:os.system ['id']\n---\n",
    "---\nname: &n sample-skill\ndescription: *n\n---\n",
])
def test_parse_skill_document_rejects_mismatch_tags_and_aliases(document: str) -> None:
    with pytest.raises(SkillValidationError):
        parse_skill_document(document, "sample-skill", "/user/sample-skill/SKILL.md")


@pytest.mark.parametrize("document", [
    "---\nname: sample-skill\ndescription: x\n",
    "name: sample-skill\ndescription: x\n---\n",
    "",
    "---\ndescription: x\n---\n",
    "---\nname: sample-skill\n---\n",
])
def test_parse_skill_document_rejects_missing_delimiters_and_fields(document: str) -> None:
    with pytest.raises(SkillValidationError):
        parse_skill_document(document, "sample-skill", "/user/sample-skill/SKILL.md")


def test_parse_skill_document_accepts_closing_delimiter_at_end_of_file() -> None:
    parsed = parse_skill_document("---\nname: sample-skill\ndescription: x\n---", None, "/p")
    assert parsed.metadata["name"] == "sample-skill"


def test_parse_skill_document_accepts_crlf_frontmatter() -> None:
    document = "---\r\nname: sample-skill\r\ndescription: x\r\n---\r\n\r\n# 指令\r\n"
    parsed = parse_skill_document(document, "sample-skill", "/user/sample-skill/SKILL.md")
    assert parsed.metadata["name"] == "sample-skill"


def test_parse_skill_document_rejects_non_mapping_yaml() -> None:
    with pytest.raises(SkillValidationError):
        parse_skill_document("---\n- a\n- b\n---\n", "sample-skill", "/user/sample-skill/SKILL.md")


def test_parse_skill_document_enforces_name_length_limit() -> None:
    too_long = "a" * (MAX_SKILL_NAME_LENGTH + 1)
    with pytest.raises(SkillValidationError):
        parse_skill_document(_document(name=too_long), None, "/user/import/SKILL.md")


@pytest.mark.parametrize("name", ["-lead", "trail-", "double--dash", " UPPER", "has.dot", "带中文"])
def test_parse_skill_document_rejects_invalid_name_grammar(name: str) -> None:
    with pytest.raises(SkillValidationError):
        parse_skill_document(_document(name=name), None, "/user/import/SKILL.md")


def test_parse_skill_document_rejects_blank_name_even_without_directory_name() -> None:
    with pytest.raises(SkillValidationError):
        parse_skill_document(_document(name=""), None, "/user/import/SKILL.md")


def test_parse_skill_document_enforces_description_length_limit() -> None:
    too_long = "x" * (MAX_SKILL_DESCRIPTION_LENGTH + 1)
    with pytest.raises(SkillValidationError):
        parse_skill_document(_document(description=too_long), None, "/user/import/SKILL.md")


def test_parse_skill_document_enforces_compatibility_length_limit() -> None:
    document = (
        "---\nname: sample-skill\ndescription: x\n"
        f"compatibility: {'c' * 501}\n---\n"
    )
    with pytest.raises(SkillValidationError):
        parse_skill_document(document, None, "/user/import/SKILL.md")


def test_parse_skill_document_accepts_string_metadata_and_optional_fields() -> None:
    document = (
        "---\nname: sample-skill\ndescription: x\nlicense: MIT\n"
        "compatibility: Python 3.11+\nmetadata:\n  author: tester\n  tier: gold\n"
        "allowed-tools: read_file, ls\n---\n"
    )
    parsed = parse_skill_document(document, None, "/user/import/SKILL.md")
    assert parsed.metadata["license"] == "MIT"
    assert parsed.metadata["compatibility"] == "Python 3.11+"
    assert parsed.metadata["metadata"] == {"author": "tester", "tier": "gold"}
    assert parsed.metadata["allowed_tools"] == ["read_file", "ls"]


def test_parse_skill_document_accepts_allowed_tools_list() -> None:
    document = (
        "---\nname: sample-skill\ndescription: x\n"
        "allowed-tools:\n  - read_file\n  - ls\n---\n"
    )
    parsed = parse_skill_document(document, None, "/user/import/SKILL.md")
    assert parsed.metadata["allowed_tools"] == ["read_file", "ls"]


@pytest.mark.parametrize("tools", ["5", "[1, 2]", "[\"\"]", "[\" \"]", "{}"])
def test_parse_skill_document_rejects_invalid_allowed_tools(tools: str) -> None:
    document = f"---\nname: sample-skill\ndescription: x\nallowed-tools: {tools}\n---\n"
    with pytest.raises(SkillValidationError):
        parse_skill_document(document, None, "/user/import/SKILL.md")


@pytest.mark.parametrize("metadata", ["[]", "5", "{a: 1, b: 2}", "{1: a}"])
def test_parse_skill_document_rejects_non_string_metadata(metadata: str) -> None:
    document = f"---\nname: sample-skill\ndescription: x\nmetadata: {metadata}\n---\n"
    with pytest.raises(SkillValidationError):
        parse_skill_document(document, None, "/user/import/SKILL.md")


def test_parse_skill_document_rejects_non_string_license() -> None:
    document = "---\nname: sample-skill\ndescription: x\nlicense: 5\n---\n"
    with pytest.raises(SkillValidationError):
        parse_skill_document(document, None, "/user/import/SKILL.md")


def test_parse_skill_document_rejects_oversize_document_by_bytes() -> None:
    # 多字节字符：字符串 code points 在限制内但 UTF-8 字节数超限
    filler = "漢" * (MAX_SKILL_FILE_SIZE // 2 + 1)
    document = f"---\nname: sample-skill\ndescription: x\n---\n\n{filler}\n"
    assert len(document) < MAX_SKILL_FILE_SIZE
    with pytest.raises(SkillValidationError):
        parse_skill_document(document, None, "/user/import/SKILL.md")


def test_parse_skill_file_rejects_invalid_utf8(tmp_path: Path) -> None:
    skill = tmp_path / "sample-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(b"---\nname: sample-skill\ndescription: x\n---\n\xff\xfe")
    with pytest.raises(SkillValidationError):
        parse_skill_file(skill / "SKILL.md", "/user/sample-skill/SKILL.md")


def test_parse_skill_file_rejects_oversize_file_before_decode(tmp_path: Path) -> None:
    skill = tmp_path / "sample-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(b"x" * (MAX_SKILL_FILE_SIZE + 1))
    with pytest.raises(SkillValidationError):
        parse_skill_file(skill / "SKILL.md", "/user/sample-skill/SKILL.md")


def test_parse_skill_file_uses_parent_directory_name(tmp_path: Path) -> None:
    skill = tmp_path / "sample-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(VALID, encoding="utf-8")
    parsed = parse_skill_file(skill / "SKILL.md", "/user/sample-skill/SKILL.md")
    assert parsed.metadata["name"] == "sample-skill"
    (skill / "SKILL.md").write_text(_document(name="other"), encoding="utf-8")
    with pytest.raises(SkillValidationError):
        parse_skill_file(skill / "SKILL.md", "/user/sample-skill/SKILL.md")


# ---------------------------------------------------------------------------
# Task 2：过滤只读 backend
# ---------------------------------------------------------------------------

def write_skill(directory: Path, name: str, *, description: str = "测试技能。") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# 指令\n",
        encoding="utf-8",
    )
    return directory


def test_filtered_backend_hides_invalid_and_excluded_skills(tmp_path: Path) -> None:
    from agent.skill_catalog import FilteredSkillBackend

    write_skill(tmp_path / "valid", "valid")
    write_skill(tmp_path / "blocked", "other")
    backend = FilteredSkillBackend(tmp_path, excluded_names={"conflict"})
    assert [entry["path"] for entry in backend.ls("/").entries or []] == ["/valid/"]
    assert backend.read("/blocked/SKILL.md").error is not None
    assert backend.download_files(["/blocked/SKILL.md"])[0].error == "file_not_found"


def test_valid_skill_names_skips_invalid_directories(tmp_path: Path) -> None:
    from agent.skill_catalog import valid_skill_names

    write_skill(tmp_path / "valid", "valid")
    write_skill(tmp_path / "blocked", "other")
    assert valid_skill_names(tmp_path) == frozenset({"valid"})


@pytest.mark.asyncio
async def test_filtered_backend_async_contract_matches_sync(tmp_path: Path) -> None:
    from agent.skill_catalog import FilteredSkillBackend

    write_skill(tmp_path / "valid", "valid")
    backend = FilteredSkillBackend(tmp_path)
    assert (await backend.als("/")).entries == backend.ls("/").entries
    assert (await backend.aread("/valid/SKILL.md")).file_data
    assert (await backend.adownload_files(["/valid/SKILL.md"]))[0].content


def test_filtered_backend_lists_subdirectory_only_when_authorized(tmp_path: Path) -> None:
    from agent.skill_catalog import FilteredSkillBackend

    write_skill(tmp_path / "valid", "valid")
    (tmp_path / "valid" / "references").mkdir()
    (tmp_path / "valid" / "references" / "note.md").write_text("NOTE", encoding="utf-8")
    write_skill(tmp_path / "blocked", "other")
    backend = FilteredSkillBackend(tmp_path)
    listed = backend.ls("/valid/references")
    assert listed.error is None
    assert [entry["path"] for entry in listed.entries or []] == ["/valid/references/note.md"]
    assert backend.ls("/blocked").error == "path_not_found"


@pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="platform lacks symlink")
def test_filtered_backend_blocks_symlink_escape(tmp_path: Path) -> None:
    import os

    from agent.skill_catalog import FilteredSkillBackend

    secret = write_skill(tmp_path / "blocked", "other") / "SKILL.md"
    valid = write_skill(tmp_path / "valid", "valid")
    references = valid / "references"
    references.mkdir()
    os.symlink(secret.parent, references / "escape")
    os.symlink(secret, valid.parent / "valid-link")
    backend = FilteredSkillBackend(tmp_path)
    assert [entry["path"] for entry in backend.ls("/").entries or []] == ["/valid/"]
    assert backend.read("/valid/references/escape/SKILL.md").error == "file_not_found"
    assert backend.download_files(["/valid/references/escape/SKILL.md"])[0].error == "file_not_found"


def test_filtered_backend_rejects_traversal_and_root_paths(tmp_path: Path) -> None:
    from agent.skill_catalog import FilteredSkillBackend

    write_skill(tmp_path / "valid", "valid")
    backend = FilteredSkillBackend(tmp_path)
    assert backend.read("/../etc/passwd").error == "file_not_found"
    assert backend.read("/SKILL.md").error == "file_not_found"
    assert backend.ls("/valid/../blocked").error == "path_not_found"
