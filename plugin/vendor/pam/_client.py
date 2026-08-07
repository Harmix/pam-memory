"""Internal HTTP transport."""

from __future__ import annotations

import os
from typing import Any

import httpx

from pam._constants import (
    DEFAULT_BASE_URL,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    ENV_API_KEY,
    ENV_BASE_URL,
    PLUGIN_CONNECT_TIMEOUT_SECONDS,
    PLUGIN_TIMEOUT_SECONDS,
    RETRIEVE_PATH,
)
from pam.exceptions import PAMAPIError, PAMAuthError, PAMTimeoutError
from pam.resources.memory import MemoryResource
from pam.types.memory import IngestMemoryItem, IngestMemoryResponse, RetrieveMemoryResponse


class _HTTPClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        connect_timeout: float,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout, connect=connect_timeout)

    def post_json(self, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(url, headers=headers, json=json_body)
        except httpx.TimeoutException as exc:
            raise PAMTimeoutError("PAM memory retrieve timed out") from exc
        except httpx.HTTPError as exc:
            raise PAMAPIError(f"HTTP error: {exc}") from exc

        if response.status_code == 401:
            raise PAMAuthError("Invalid or missing PAM API key", status_code=401)

        if response.status_code >= 400:
            raise PAMAPIError(
                "PAM request failed",
                status_code=response.status_code,
                body=response.text,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise PAMAPIError(
                "Invalid JSON from PAM server",
                status_code=response.status_code,
                body=response.text,
            ) from exc

        if not isinstance(data, dict):
            raise PAMAPIError("Expected JSON object from PAM server", body=data)

        return data


class PAMClient:
    """Sync PAM SDK client — exposes memory.retrieve and memory.ingest."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        connect_timeout: float | None = None,
    ) -> None:
        resolved_key = (api_key or os.environ.get(ENV_API_KEY, "")).strip()
        if not resolved_key:
            raise ValueError(
                f"API key required: pass api_key= or set {ENV_API_KEY} env var"
            )

        resolved_base = (
            base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL
        ).strip()

        http = _HTTPClient(
            api_key=resolved_key,
            base_url=resolved_base,
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS,
            connect_timeout=(
                connect_timeout
                if connect_timeout is not None
                else DEFAULT_CONNECT_TIMEOUT_SECONDS
            ),
        )
        self._http = http
        self.memory = MemoryResource(http)

    @classmethod
    def for_plugin(
        cls,
        *,
        api_key: str,
        base_url: str | None = None,
    ) -> PAMClient:
        """Strict timeouts for Claude hooks — target under 5s total."""
        return cls(
            api_key=api_key,
            base_url=base_url,
            timeout=PLUGIN_TIMEOUT_SECONDS,
            connect_timeout=PLUGIN_CONNECT_TIMEOUT_SECONDS,
        )

    def retrieve(
        self,
        *,
        prompt: str,
        session_id: str | None = None,
    ) -> RetrieveMemoryResponse:
        """Shortcut for client.memory.retrieve(...)."""
        return self.memory.retrieve(prompt=prompt, session_id=session_id)

    def ingest(self, *, item: IngestMemoryItem) -> IngestMemoryResponse:
        """Shortcut for client.memory.ingest(...)."""
        return self.memory.ingest(item=item)
