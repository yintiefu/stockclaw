"""工作流自定义事件模式与发射器契约测试。

验证：
- 9 类自定义事件契约符合 discriminated union；
- docs/contracts/workflow-custom-events.json 的所有示例均通过校验；
- WorkflowEventEmitter 序号单调递增且连续；
- 缺少必填字段时拒绝生成。
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from agent.workflow_events import (
    WorkflowEventEmitter,
    validate_workflow_event,
)

CONTRACT_FILE = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "workflow-custom-events.json"


def test_validate_all_contract_fixture_events() -> None:
    assert CONTRACT_FILE.exists(), f"Contract file {CONTRACT_FILE} must exist"
    events = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    assert len(events) == 9

    types_seen = set()
    for raw in events:
        event = validate_workflow_event(raw)
        assert event.type == raw["type"]
        assert event.seq == raw["seq"]
        assert event.run_id == raw["run_id"]
        assert event.workflow_id == raw["workflow_id"]
        types_seen.add(event.type)

    assert types_seen == {
        "workflow_started",
        "dossier_progress",
        "dossier_completed",
        "stage_started",
        "stage_delta",
        "stage_completed",
        "stage_failed",
        "workflow_completed",
        "workflow_failed",
    }


def test_reject_missing_required_event_fields() -> None:
    with pytest.raises(ValidationError):
        validate_workflow_event({
            "type": "stage_delta",
            "workflow_id": "debate",
            "run_id": "r1",
            "seq": 1,
            # missing emitted_at, stage_id, delta
        })


@pytest.mark.asyncio
async def test_event_emitter_monotonic_sequence() -> None:
    dispatched = []

    class FakeConfig:
        def get(self, key, default=None):
            if key == "configurable":
                return {"run_id": "run-test-123"}
            return default

    emitter = WorkflowEventEmitter.from_config("debate", starting_seq=10, config=FakeConfig())  # type: ignore
    assert emitter.last_seq == 10

    # Monkeypatch adispatch_custom_event in emitter
    emitter._dispatch_fn = lambda name, data, config: dispatched.append((name, data))  # type: ignore

    await emitter.emit("stage_started", stage_id="bull")
    assert emitter.last_seq == 11
    assert dispatched[-1][1]["seq"] == 11
    assert dispatched[-1][1]["stage_id"] == "bull"
    assert dispatched[-1][1]["run_id"] == "run-test-123"

    await emitter.emit("stage_delta", stage_id="bull", delta="text chunk")
    assert emitter.last_seq == 12
    assert dispatched[-1][1]["seq"] == 12
    assert dispatched[-1][1]["delta"] == "text chunk"


def test_event_emitter_top_level_run_id() -> None:
    import uuid
    real_uuid = uuid.uuid4()
    cfg = {"run_id": real_uuid}
    emitter = WorkflowEventEmitter.from_config("debate", starting_seq=0, config=cfg)  # type: ignore
    assert emitter.run_id == str(real_uuid)
