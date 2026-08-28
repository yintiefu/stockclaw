"""底稿进度事件契约测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.workflow_events import DossierProgressEvent, emit_dossier_progress


def test_progress_event_validates_fields():
    event = DossierProgressEvent(
        section_id="quote", section_status="completed", completed=1, total=13,
    )
    assert event.type == "dossier.progress"
    with pytest.raises(ValidationError):
        DossierProgressEvent(section_id="", section_status="completed", completed=1, total=13)
    with pytest.raises(ValidationError):
        DossierProgressEvent(section_id="q", section_status="bogus", completed=1, total=13)


def test_emit_uses_stream_writer(monkeypatch):
    seen: list[object] = []
    monkeypatch.setattr("agent.workflow_events.get_stream_writer", lambda: seen.append)
    emit_dossier_progress("quote", "completed", 1, 13)
    assert seen and seen[0]["type"] == "dossier.progress"


def test_emit_is_silent_without_stream_context(monkeypatch):
    def boom():
        raise RuntimeError("no context")
    monkeypatch.setattr("agent.workflow_events.get_stream_writer", boom)
    emit_dossier_progress("quote", "completed", 1, 13)  # 不得抛异常
