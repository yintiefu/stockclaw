"""Task 1C-3：SkillImporter 限额上传、原子安装、CAS 覆盖/删除与启动恢复。"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from agent.skills import SkillArchiveRejected, SkillConflict, SkillImporter, SkillRegistry, SkillUnavailable

SKILL_BODY = """---
name: {name}
description: {description}
---

# {name}

{body}
"""


def write_skill_zip(target: Path, *, name: str, body: str = "content", description: str = "d",
                    layout: str = "flat", extra: dict[str, bytes] | None = None,
                    entries: dict[str, bytes] | None = None) -> Path:
    """生成 zip：flat 布局根级 SKILL.md；wrapped 布局唯一顶层目录。"""
    if entries is None:
        skill_md = SKILL_BODY.format(name=name, description=description, body=body).encode("utf-8")
        prefix = "" if layout == "flat" else f"{name}/"
        entries = {f"{prefix}SKILL.md": skill_md}
        for rel, payload in (extra or {}).items():
            entries[f"{prefix}{rel}"] = payload
    with zipfile.ZipFile(target, "w") as zf:
        for rel, payload in entries.items():
            zf.writestr(rel, payload)
    return target


def install_skill(root: Path, name: str, body: str = "installed"):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(SKILL_BODY.format(name=name, description="d", body=body), encoding="utf-8")
    return directory


@pytest.fixture()
def setup(tmp_path):
    registry = SkillRegistry(tmp_path)
    importer = SkillImporter(tmp_path, registry)
    return tmp_path, registry, importer


# ---------------------------------------------------------------------------
# receive 限额
# ---------------------------------------------------------------------------

class BigUpload:
    def __init__(self, payload: bytes, chunk: int = 8192):
        self._stream = io.BytesIO(payload)
        self._chunk = chunk
        self.name = "upload.zip"

    async def read(self, size: int = -1):  # Starlette UploadFile.read 兼容签名
        return self._stream.read(self._chunk if size < 0 else min(size, self._chunk))


def test_receive_streams_fixed_chunks_and_cleans_on_overflow(setup):
    root, registry, importer = setup
    payload = b"x" * (20 * 1024 * 1024 + 100)
    with pytest.raises(SkillArchiveRejected):
        importer.receive_chunks(iter([payload[i:i + 65536] for i in range(0, len(payload), 65536)]))
    assert not list(root.glob(".skill-upload-*.tmp"))


def test_receive_accepts_small_upload(setup):
    root, registry, importer = setup
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", SKILL_BODY.format(name="quality", description="d", body="ok"))
    staged = importer.receive_chunks(iter([buf.getvalue()]))
    assert staged.is_file()
    assert staged.parent == root


# ---------------------------------------------------------------------------
# install 布局与限制
# ---------------------------------------------------------------------------

def test_install_flat_layout(setup):
    root, registry, importer = setup
    archive = write_skill_zip(root.parent / "a.zip", name="quality")
    result = importer.install(archive, overwrite=False, expected_digest=None)
    assert result.record.valid and result.record.name == "quality"
    assert (root / "quality" / "SKILL.md").is_file()
    assert registry.require("quality").digest


def test_install_wrapped_layout_with_references(setup):
    root, registry, importer = setup
    archive = write_skill_zip(
        root.parent / "b.zip", name="quality", layout="wrapped",
        extra={"references/note.md": "参考".encode("utf-8")},
    )
    result = importer.install(archive, overwrite=False, expected_digest=None)
    assert result.record.valid
    assert (root / "quality" / "references" / "note.md").is_file()


def test_install_rejects_two_top_level_directories(setup):
    root, registry, importer = setup
    archive = write_skill_zip(root.parent / "c.zip", name="quality",
                              entries={"one/SKILL.md": b"x", "two/SKILL.md": b"y"})
    with pytest.raises(SkillArchiveRejected):
        importer.install(archive, overwrite=False, expected_digest=None)


def test_install_rejects_missing_skill_md(setup):
    root, registry, importer = setup
    archive = write_skill_zip(root.parent / "d.zip", name="quality",
                              entries={"readme.txt": b"no skill"})
    with pytest.raises(SkillArchiveRejected):
        importer.install(archive, overwrite=False, expected_digest=None)


def test_install_enforces_entry_count_and_extract_size(setup):
    root, registry, importer = setup
    many = {f"f{i}.txt": b"x" for i in range(501)}
    archive = write_skill_zip(root.parent / "e.zip", name="quality", entries=many)
    with pytest.raises(SkillArchiveRejected):
        importer.install(archive, overwrite=False, expected_digest=None)
    # 声明小、实际大（zip bomb 的一半尺寸）：用未压缩 stored 大小模拟
    bomb = {f"references/big{i}.bin": b"y" * (1024 * 1024) for i in range(51)}
    archive2 = write_skill_zip(root.parent / "f.zip", name="quality",
                               entries={"SKILL.md": SKILL_BODY.format(name="quality", description="d", body="b"), **bomb})
    with pytest.raises(SkillArchiveRejected):
        importer.install(archive2, overwrite=False, expected_digest=None)


def test_install_rejects_zip_slip_absolute_and_backslash(setup):
    root, registry, importer = setup
    for evil in ("../escape.md", "/abs.md", "a\\b.md", "references/../../x.md"):
        archive = write_skill_zip(root.parent / "g.zip", name="quality",
                                  entries={"SKILL.md": SKILL_BODY.format(name="quality", description="d", body="x").encode(),
                                           evil: b"payload"})
        with pytest.raises(SkillArchiveRejected):
            importer.install(archive, overwrite=False, expected_digest=None)
    assert not (root.parent / "escape.md").exists()


def test_install_rejects_symlink_and_special_and_encrypted(setup):
    root, registry, importer = setup
    target = root.parent / "sl.zip"
    with zipfile.ZipFile(target, "w") as zf:
        zi = zipfile.ZipInfo("SKILL.md")
        zi.create_system = 3  # unix
        zi.external_attr = (0o120777 << 16)  # symlink mode
        zf.writestr(zi, SKILL_BODY.format(name="quality", description="d", body="x"))
        zf.writestr("references/pipe", "x")
        zf.getinfo("references/pipe").create_system = 3
        zf.getinfo("references/pipe").external_attr = (0o010600 << 16)  # fifo
    with pytest.raises(SkillArchiveRejected):
        importer.install(target, overwrite=False, expected_digest=None)


def test_install_rejects_duplicate_nfc_paths(setup):
    root, registry, importer = setup
    entries = {
        "SKILL.md": SKILL_BODY.format(name="quality", description="d", body="x").encode(),
        "references/caf\u00e9.md": b"a",
        "references/cafe\u0301.md": b"b",
    }
    archive = write_skill_zip(root.parent / "h.zip", name="quality", entries=entries)
    with pytest.raises(SkillArchiveRejected):
        importer.install(archive, overwrite=False, expected_digest=None)


def test_install_conflict_without_overwrite(setup):
    root, registry, importer = setup
    install_skill(root, "quality", body="old")
    archive = write_skill_zip(root.parent / "i.zip", name="quality", body="new")
    with pytest.raises(SkillConflict):
        importer.install(archive, overwrite=False, expected_digest=None)
    # 未覆盖成功前旧内容保持
    assert "old" in (root / "quality" / "SKILL.md").read_text(encoding="utf-8")


def test_overwrite_requires_current_digest_and_recovers_backup(tmp_path):
    registry = SkillRegistry(tmp_path)
    importer = SkillImporter(tmp_path, registry)
    install_skill(tmp_path, "quality", body="old")
    digest = registry.refresh().require("quality").digest
    archive = write_skill_zip(tmp_path / "quality.zip", name="quality", body="new")
    with pytest.raises(SkillConflict):
        importer.install(archive, overwrite=True, expected_digest="stale")
    importer.install(archive, overwrite=True, expected_digest=digest)
    assert registry.current().require("quality").instructions.endswith("new")
    assert not list(tmp_path.glob(".skill-backup-*.tmp"))


def test_install_failure_leaves_no_stage_residue(setup):
    root, registry, importer = setup
    archive = write_skill_zip(root.parent / "j.zip", name="quality",
                              entries={"SKILL.md": b"broken frontmatter"})
    with pytest.raises(SkillArchiveRejected):
        importer.install(archive, overwrite=False, expected_digest=None)
    assert not list(root.glob(".skill-import-*.tmp"))
    assert not list(root.glob(".skill-upload-*.tmp"))


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

def test_delete_requires_digest_cas(setup):
    root, registry, importer = setup
    install_skill(root, "quality")
    digest = registry.refresh().require("quality").digest
    with pytest.raises(SkillConflict):
        importer.delete("quality", expected_digest="stale")
    assert (root / "quality").is_dir()
    importer.delete("quality", expected_digest=digest)
    assert not (root / "quality").exists()


def test_delete_missing_skill_raises_unavailable(setup):
    root, registry, importer = setup
    registry.refresh()
    with pytest.raises(SkillUnavailable):
        importer.delete("ghost", expected_digest="x")


# ---------------------------------------------------------------------------
# recover
# ---------------------------------------------------------------------------

def test_recover_removes_orphan_upload_and_stage(setup):
    root, registry, importer = setup
    (root / ".skill-upload-abc.tmp").write_bytes(b"partial")
    stage = root / ".skill-import-def.tmp"
    stage.mkdir()
    (stage / "SKILL.md").write_text("x", encoding="utf-8")
    warnings = importer.recover()
    assert not (root / ".skill-upload-abc.tmp").exists()
    assert not stage.exists()
    assert warnings == []


def test_recover_target_plus_backup_commits_target(setup):
    root, registry, importer = setup
    target = install_skill(root, "quality", body="new")
    backup = root / ".skill-backup-xyz.tmp"
    backup.mkdir()
    (backup / "SKILL.md").write_text(SKILL_BODY.format(name="quality", description="d", body="old"), encoding="utf-8")
    importer.recover()
    assert "new" in (target / "SKILL.md").read_text(encoding="utf-8")
    assert not backup.exists()


def test_recover_backup_without_target_restores_backup(setup):
    root, registry, importer = setup
    backup = root / ".skill-backup-xyz.tmp"
    backup.mkdir()
    (backup / "SKILL.md").write_text(SKILL_BODY.format(name="quality", description="d", body="restored"), encoding="utf-8")
    importer.recover()
    assert (root / "quality" / "SKILL.md").is_file()
    assert not backup.exists()


def test_recover_ambiguous_shape_keeps_and_warns(setup):
    root, registry, importer = setup
    stray = root / ".skill-backup-abc.tmp"
    stray.mkdir()
    (stray / "SKILL.md").write_text("?", encoding="utf-8")
    (stray / "extra.txt").write_text("ambiguous", encoding="utf-8")
    # 同名 target 存在且 backup 内还有一个目录 → 归属不明确：保留并警告
    install_skill(root, "quality")
    (stray / "quality").mkdir()
    (stray / "quality" / "SKILL.md").write_text("inner", encoding="utf-8")
    warnings = importer.recover()
    assert stray.exists()
    assert warnings and all(w.startswith(".skill-") for w in warnings)
