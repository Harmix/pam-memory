"""Memory resource — client.memory.retrieve(...) and client.memory.ingest(...)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pam._constants import INGEST_PATH, RETRIEVE_PATH
from pam.types.memory import (
    IngestMemoryItem,
    IngestMemoryResponse,
    RetrieveMemoryRequest,
    RetrieveMemoryResponse,
)

if TYPE_CHECKING:
    from pam._client import _HTTPClient


class MemoryResource:
    def __init__(self, http: _HTTPClient) -> None:
        self._http = http

    def retrieve(
        self,
        *,
        prompt: str,
        session_id: str | None = None,
    ) -> RetrieveMemoryResponse:
        body = RetrieveMemoryRequest(prompt=prompt, session_id=session_id)
        data = self._http.post_json(
            RETRIEVE_PATH,
            body.model_dump(exclude_none=True),
        )
        return RetrieveMemoryResponse.model_validate(data)

    def ingest(self, *, item: IngestMemoryItem) -> IngestMemoryResponse:
        data = self._http.post_json(
            INGEST_PATH,
            item.model_dump(exclude_none=True),
        )
        return IngestMemoryResponse.model_validate(data)
