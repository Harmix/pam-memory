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


class IngestMemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    summary: str
    source: str = "claude_code"
    client: str | None = None
    project_path: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    title: str | None = None
    facts: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] | None = None


class IngestMemoryResponse(BaseModel):
    status: Literal["ok", "error"]
    item_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.item_id)
