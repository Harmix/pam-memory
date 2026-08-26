"""Typed request/response models for memory.retrieve() and memory.ingest()."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RetrieveMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str
    session_id: str | None = None


class RetrieveMemoryResponse(BaseModel):
    status: Literal["ok", "error"]
    memory_ready: bool = True
    content_text: str = ""
    sources: list[str] = Field(default_factory=list)
    request_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    degraded: bool = False
    degraded_reason: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return (
            self.status == "ok"
            and self.memory_ready
            and bool(self.content_text.strip())
        )

    @property
    def is_business_error(self) -> bool:
        return self.status == "error" or (
            self.status == "ok" and not self.memory_ready
        )


class IngestMemoryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    text: str
    timestamp: str | None = None


class IngestMemoryItem(BaseModel):
    """Raw transcript turns pushed to PAM for server-side extraction.

    Turns are mechanically parsed and secret-redacted client-side only -- no
    client-side LLM summarization. PAM's pam-jobs pipeline runs the actual
    Gemini extract+validate pass server-side, so ingesting never spends the
    caller's own model quota.

    Large sessions may be split into ordered parts (same session_id,
    incrementing part_index/part_count) across multiple ingest() calls.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    turns: list[IngestMemoryTurn]
    source: str = "claude_code"
    client: str | None = None
    project_path: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    title: str | None = None
    part_index: int = 0
    part_count: int = 1


class IngestMemoryResponse(BaseModel):
    status: Literal["ok", "error"]
    item_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.item_id)
