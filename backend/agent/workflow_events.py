"""底稿分节进度自定义事件：唯一保留的 custom 事件。

进度是可丢弃的提示（丢了不影响正确性），事实一律住在 checkpoint（values 通道）；
因此不再需要序号、run 绑定与终态事件——阶段状态机由 values 快照驱动，
阶段正文流由 messages 通道驱动（useStream 原生归并）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from langgraph.config import get_stream_writer
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated


def utc_now() -> str:
    """生成 UTC ISO-8601 时间戳。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
DossierSectionStatus = Literal["completed", "no_record", "gap", "failed"]


class DossierProgressEvent(BaseModel):
    """底稿单节抓取进度。"""
    model_config = ConfigDict(extra="forbid")

    type: Literal["dossier.progress"] = "dossier.progress"
    section_id: NonEmptyString
    section_status: DossierSectionStatus
    completed: int = Field(ge=0)
    total: int = Field(ge=0)


def emit_dossier_progress(
    section_id: str,
    section_status: str,
    completed: int,
    total: int,
) -> None:
    """经当前流上下文发射底稿进度；无流上下文（如单测直调）时静默跳过。"""
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    event = DossierProgressEvent.model_validate({
        "section_id": section_id,
        "section_status": section_status,
        "completed": completed,
        "total": total,
    })
    writer(event.model_dump(mode="json"))
