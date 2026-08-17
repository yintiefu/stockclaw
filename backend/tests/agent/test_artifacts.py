"""1D Artifact 校验与存储：四种 schema、canonical 尺寸、路径与不可变链。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.artifacts import (
    ArtifactInvalid,
    ArtifactNotFound,
    ArtifactStore,
    StagedArtifact,
    encode_artifact,
)
from agent.models import (
    ArtifactDocument,
    ArtifactMetadata,
    JsonContent,
    MarkdownContent,
    SourcesContent,
    SourcesContentItem,
    TableContent,
    TableColumn,
)
from agent.stores import AgentPaths, DocumentCorrupt


def artifact(**overrides) -> ArtifactDocument:
    values = dict(
        id="artifact-1", thread_id="thread-a", run_id="run-1", type="markdown",
        title="研究摘录", created_at="2026-08-16T12:00:00Z",
        parent_artifact_id=None, content=MarkdownContent(markdown="# 标题"),
        source_ids=[],
    )
    values.update(overrides)
    return ArtifactDocument(**values)


def artifact_with_exact_serialized_size(target: int) -> ArtifactDocument:
    """构造 canonical 序列化恰好 target 字节的 markdown Artifact。"""
    base = artifact(content=MarkdownContent(markdown=""))
    base_bytes = encode_artifact(base)
    pad = target - len(base_bytes)
    assert pad >= 0
    return artifact(content=MarkdownContent(markdown="x" * pad))


def test_all_four_content_types_validate():
    assert artifact(type="markdown", content=MarkdownContent(markdown="文本"))
    assert artifact(type="table", content=TableContent(
        columns=[TableColumn(key="symbol", label="代码")],
        rows=[{"symbol": "600519"}, {"symbol": None}]))
    assert artifact(type="json", content=JsonContent(value={"a": [1, 2, {"b": True}]}))
    declared = ["source-1", "source-2"]
    assert artifact(type="sources", source_ids=declared, content=SourcesContent(items=[
        SourcesContentItem(source_id="source-2", note="核对口径")]))


def test_title_trim_and_bounds():
    assert artifact(title="  合法标题  ").title == "合法标题"
    with pytest.raises(ValidationError):
        artifact(title="   ")
    with pytest.raises(ValidationError):
        artifact(title="长" * 201)


def test_table_rules():
    with pytest.raises(ValidationError):
        TableContent(columns=[], rows=[])
    with pytest.raises(ValidationError):
        TableContent(columns=[TableColumn(key="a", label="A")] * 2, rows=[])
    with pytest.raises(ValidationError):
        TableContent(columns=[TableColumn(key="a", label="A")], rows=[{"b": 1}])
    with pytest.raises(ValidationError):
        TableContent(columns=[TableColumn(key="a", label="A")], rows=[{"a": [1]}])
    with pytest.raises(ValidationError):
        TableContent(columns=[TableColumn(key="a", label="A")],
                     rows=[{"a": float("nan")}])
    deep = TableContent(columns=[TableColumn(key=f"col_{i}", label=str(i)) for i in range(50)],
                        rows=[])
    assert len(deep.columns) == 50
    with pytest.raises(ValidationError):
        TableContent(
            columns=[TableColumn(key=f"c{i}", label=str(i)) for i in range(51)], rows=[])


def test_json_depth_and_node_limits():
    value: dict = {}
    cursor = value
    for _ in range(40):
        cursor["n"] = {}
        cursor = cursor["n"]
    with pytest.raises(ValidationError):
        JsonContent(value=value)
    wide = {"k": list(range(50_001))}
    with pytest.raises(ValidationError):
        JsonContent(value=wide)
    with pytest.raises(ValidationError):
        JsonContent(value=float("inf"))


def test_sources_content_subset_rule():
    with pytest.raises(ValidationError):
        artifact(type="sources", source_ids=["source-1"], content=SourcesContent(items=[
            SourcesContentItem(source_id="source-9")]))


def test_canonical_size_counts_exact_staged_bytes(tmp_path):
    artifact_exact = artifact_with_exact_serialized_size(1_048_576)
    encoded = encode_artifact(artifact_exact)
    assert len(encoded) == 1_048_576
    store = ArtifactStore(tmp_path)
    staged = store.stage(artifact_exact)
    assert staged.path.read_bytes() == encoded
    with pytest.raises(ArtifactInvalid):
        encode_artifact(artifact_with_exact_serialized_size(1_048_577))


def test_store_rejects_traversal_and_symlinks(tmp_path):
    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactInvalid):
        store._path("thread-a", "../../etc/passwd")
    with pytest.raises(ArtifactInvalid):
        store.stage(artifact(thread_id="thread-a/../b"))
    with pytest.raises(ArtifactInvalid):
        store.stage(artifact(id="artifact.with.dot"))
    assert not (tmp_path / "artifacts" / "thread-a").exists()
    # symlink 最终文件不跟随、不读取
    directory = tmp_path / "artifacts" / "thread-a"
    directory.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (directory / "artifact-9.json").symlink_to(outside)
    with pytest.raises(ArtifactNotFound):
        store.get("thread-a", "artifact-9")


def test_publish_is_immutable_and_list_reads_back(tmp_path):
    store = ArtifactStore(tmp_path)
    staged = store.stage(artifact())
    store.publish(staged)
    reloaded = store.get("thread-a", "artifact-1")
    assert reloaded == artifact()
    # 不可变：同一 id 再次 publish 失败
    again = store.stage(artifact())
    with pytest.raises(ArtifactInvalid):
        store.publish(again)
    assert not again.path.exists()  # staging 已被清理
    metadata = store.metadata_for_thread("thread-a")
    assert metadata == [ArtifactMetadata.from_artifact(artifact())]


def test_chain_same_type_leaf_only_and_fork_detection(tmp_path):
    store = ArtifactStore(tmp_path)
    first = artifact(id="artifact-a")
    child = artifact(id="artifact-b", parent_artifact_id="artifact-a")
    store.publish(store.stage(first))
    store.publish(store.stage(child))
    assert store.children_map("thread-a") == {"artifact-a": ["artifact-b"]}
    issues = store.detect_chain_issues("thread-a")
    assert issues == {}

    # 跨类型 parent
    wrong = artifact(id="artifact-c", type="table", parent_artifact_id="artifact-a",
                     content=TableContent(columns=[TableColumn(key="k", label="K")], rows=[]))
    store.publish(store.stage(wrong))
    assert store.detect_chain_issues("thread-a")["artifact-c"] == "parent_type_mismatch"

    # fork：同一 parent 第二个子版本
    fork = artifact(id="artifact-d", parent_artifact_id="artifact-a")
    store.publish(store.stage(fork))
    issues = store.detect_chain_issues("thread-a")
    assert issues.get("artifact-a") == "fork"  # fork 归因于分叉的 parent

    # cycle（手工落盘构造，绕过 create 路径校验）
    cyc_a = artifact(id="artifact-x", parent_artifact_id="artifact-y")
    cyc_b = artifact(id="artifact-y", parent_artifact_id="artifact-x")
    store.publish(store.stage(cyc_a))
    store.publish(store.stage(cyc_b))
    issues = store.detect_chain_issues("thread-a")
    assert "cycle" in (issues.get("artifact-x"), issues.get("artifact-y"))


def test_corrupt_artifact_read_quarantines_identity(tmp_path):
    store = ArtifactStore(tmp_path)
    store.publish(store.stage(artifact()))
    final = tmp_path / "artifacts" / "thread-a" / "artifact-1.json"
    final.write_text('{"schema_version":1}', encoding="utf-8")
    with pytest.raises(DocumentCorrupt):
        store.get("thread-a", "artifact-1")
