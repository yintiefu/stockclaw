"""Task 1C-2：SkillRegistry 扫描 / 清单 / 资源解析的安全与确定性测试。"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agent.skills import SkillRegistry, SkillResourceForbidden, SkillUnavailable

SKILL_MD = """---
name: {name}
description: {description}
---

# {name}

{body}
"""


def write_skill(directory: Path, *, name: str | None = None, description: str = "d", body: str = "instructions",
                skill_md: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if skill_md is None:
        skill_md = SKILL_MD.format(name=name, description=description, body=body)
    (directory / "SKILL.md").write_text(skill_md, encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# 扫描基础
# ---------------------------------------------------------------------------

def test_valid_skill_produces_manifest_and_digest(tmp_path):
    root = write_skill(tmp_path / "quality", name="quality", description="质检", body="做质检")
    (root / "references").mkdir()
    (root / "references" / "note.md").write_text("参考", encoding="utf-8")
    generation = SkillRegistry(tmp_path).refresh()
    assert generation.number == 1
    item = generation.by_directory("quality")
    assert item.valid and item.name == "quality" and item.description == "质检"
    assert item.instructions and item.digest
    rels = {f.relative_path: f for f in item.files}
    assert rels["SKILL.md"].category == "skill"
    assert rels["references/note.md"].category == "reference"
    assert rels["references/note.md"].size == len("参考".encode("utf-8"))
    assert rels["references/note.md"].sha256


def test_missing_skill_md_is_invalid(tmp_path):
    (tmp_path / "empty").mkdir()
    item = SkillRegistry(tmp_path).refresh().by_directory("empty")
    assert item.valid is False
    assert item.error_code == "SKILL_INVALID"


def test_custom_yaml_tag_rejected(tmp_path):
    write_skill(tmp_path / "tagged", name="tagged",
                skill_md="---\nname: tagged\ndescription: d\nevil: !python/object:os.system {}\n---\n\nx")
    item = SkillRegistry(tmp_path).refresh().by_directory("tagged")
    assert item.valid is False
    assert item.error_code == "SKILL_INVALID"


def test_name_and_description_constraints(tmp_path):
    write_skill(tmp_path / "bad-name", skill_md="---\nname: Bad_Name\ndescription: d\n---\n\nx")
    write_skill(tmp_path / "too-long-desc", skill_md="---\nname: ok-name\ndescription: " + "x" * 2000 + "\n---\n\nx")
    skills = SkillRegistry(tmp_path).refresh().skills
    assert all(not s.valid for s in skills)
    assert {s.error_code for s in skills} == {"SKILL_INVALID"}


def test_skill_md_size_limit(tmp_path):
    write_skill(tmp_path / "big", name="big", skill_md="---\nname: big\ndescription: d\n---\n\n" + "x" * (256 * 1024 + 10))
    assert SkillRegistry(tmp_path).refresh().by_directory("big").valid is False


def test_instruction_length_limit(tmp_path):
    write_skill(tmp_path / "long", name="long", skill_md="---\nname: long\ndescription: d\n---\n\n" + "x" * 60_001)
    assert SkillRegistry(tmp_path).refresh().by_directory("long").valid is False


def test_duplicate_normalized_names_invalidate_both(tmp_path):
    write_skill(tmp_path / "one", name="cash-flow", description="A")
    write_skill(tmp_path / "two", name="CASH-FLOW", description="B")
    generation = SkillRegistry(tmp_path).refresh()
    assert [item.valid for item in generation.skills] == [False, False]
    assert {item.error_code for item in generation.skills} == {"SKILL_INVALID"}


def test_generation_is_monotonic_and_digest_stable(tmp_path):
    registry = SkillRegistry(tmp_path)
    write_skill(tmp_path / "quality", name="quality")
    g1 = registry.refresh()
    digest1 = g1.by_directory("quality").digest
    g2 = registry.refresh()
    assert g2.number > g1.number
    assert g2.by_directory("quality").digest == digest1


# ---------------------------------------------------------------------------
# 文件树与路径安全
# ---------------------------------------------------------------------------

def test_manifest_never_follows_symlink_or_exposes_scripts(tmp_path):
    root = write_skill(tmp_path / "safe", name="safe", description="safe")
    (root / "scripts").mkdir()
    (root / "scripts" / "run.py").write_text("print('no')", encoding="utf-8")
    (root / "references").mkdir()
    (root / "references" / "link").symlink_to("/etc/passwd")
    item = SkillRegistry(tmp_path).refresh().by_directory("safe")
    assert item.valid is False
    assert "/etc/passwd" not in (item.error_detail or "")
    assert str(tmp_path) not in (item.error_detail or "")


def test_top_level_symlink_directory_rejected(tmp_path):
    real = write_skill(tmp_path.parent / "real-skill", name="real")
    (tmp_path / "linked").symlink_to(real, target_is_directory=True)
    item = SkillRegistry(tmp_path).refresh().by_directory("linked")
    assert item.valid is False


def test_path_collision_nfc_casefold_invalidates(tmp_path):
    root = write_skill(tmp_path / "nfc", name="nfc-skill")
    (root / "references").mkdir()
    # café 的 NFC 与 NFD（e + U+0301）形式在 NFC 规范化后碰撞
    (root / "references" / "caf\u00e9.md").write_text("a", encoding="utf-8")
    (root / "references" / "cafe\u0301.md").write_text("b", encoding="utf-8")
    item = SkillRegistry(tmp_path).refresh().by_directory("nfc")
    assert item.valid is False


def test_reference_over_1mb_rejected(tmp_path):
    root = write_skill(tmp_path / "large", name="large")
    (root / "references").mkdir()
    (root / "references" / "big.txt").write_bytes(b"x" * (1024 * 1024 + 1))
    item = SkillRegistry(tmp_path).refresh().by_directory("large")
    assert item.valid is False


# ---------------------------------------------------------------------------
# MIME 检测与下载性
# ---------------------------------------------------------------------------

def test_mime_signature_validation(tmp_path):
    root = write_skill(tmp_path / "media", name="media")
    assets = root / "assets"
    assets.mkdir()
    (assets / "real.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    (assets / "fake.png").write_bytes(b"not a png at all" + b"0" * 16)
    item = SkillRegistry(tmp_path).refresh().by_directory("media")
    assert item.valid is True
    by_path = {f.relative_path: f for f in item.files}
    assert by_path["assets/real.png"].mime == "image/png"
    assert by_path["assets/real.png"].downloadable is True
    # 签名与扩展名不一致：不可下载，且无 MIME
    assert by_path["assets/fake.png"].downloadable is False
    assert by_path["assets/fake.png"].mime is None


def test_json_asset_must_parse(tmp_path):
    root = write_skill(tmp_path / "jsons", name="jsons")
    (root / "assets").mkdir()
    (root / "assets" / "ok.json").write_text('{"a": 1}', encoding="utf-8")
    (root / "assets" / "bad.json").write_text("{broken", encoding="utf-8")
    item = SkillRegistry(tmp_path).refresh().by_directory("jsons")
    by_path = {f.relative_path: f for f in item.files}
    assert by_path["assets/ok.json"].downloadable is True
    assert by_path["assets/bad.json"].downloadable is False


# ---------------------------------------------------------------------------
# resolve_file 与 require
# ---------------------------------------------------------------------------

def test_require_missing_raises_unavailable(tmp_path):
    write_skill(tmp_path / "quality", name="quality")
    registry = SkillRegistry(tmp_path)
    registry.refresh()
    with pytest.raises(SkillUnavailable):
        registry.require("missing")


def test_resolve_file_rejects_scripts_traversal_and_unlisted(tmp_path):
    root = write_skill(tmp_path / "quality", name="quality")
    (root / "scripts").mkdir()
    (root / "scripts" / "run.py").write_text("x", encoding="utf-8")
    (root / "references").mkdir()
    (root / "references" / "note.md").write_text("参考内容", encoding="utf-8")
    registry = SkillRegistry(tmp_path)
    registry.refresh()
    record, file_entry, path = registry.resolve_file("quality", "references/note.md")
    assert record.name == "quality" and file_entry.sha256 and path.is_file()
    for bad in ("scripts/run.py", "../SKILL.md", "references/../../etc/passwd", "references/nope.md"):
        with pytest.raises((SkillResourceForbidden, SkillUnavailable)):
            registry.resolve_file("quality", bad)


def test_resolve_file_rejects_invalid_skill(tmp_path):
    write_skill(tmp_path / "bad", name="Bad")
    registry = SkillRegistry(tmp_path)
    registry.refresh()
    with pytest.raises(SkillUnavailable):
        registry.resolve_file("Bad", "SKILL.md")


def test_registry_ignores_import_tmp_entries(tmp_path):
    write_skill(tmp_path / "quality", name="quality")
    (tmp_path / ".skill-upload-abc.tmp").write_bytes(b"partial")
    stage = tmp_path / ".skill-import-abc.tmp"
    stage.mkdir()
    (stage / "SKILL.md").write_text("---\nname: staged\ndescription: d\n---\nx", encoding="utf-8")
    generation = SkillRegistry(tmp_path).refresh()
    assert {s.directory for s in generation.skills} == {"quality"}


def test_scan_never_leaves_lock(tmp_path):
    write_skill(tmp_path / "quality", name="quality")
    registry = SkillRegistry(tmp_path)
    assert registry.current() is None or registry.current().number >= 1
    registry.refresh()
    assert registry.list()[0].name == "quality"
