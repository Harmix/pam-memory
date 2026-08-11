"""PAM SDK tests — memory.ingest()."""

from __future__ import annotations

import httpx
import pytest
import respx

from pam import IngestMemoryItem, PAMClient
from pam.exceptions import PAMAPIError, PAMAuthError


@respx.mock
def test_ingest_success() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/ingest").mock(
        return_value=httpx.Response(
            200,
            json={"status": "ok", "item_id": "item-123"},
        )
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    result = client.memory.ingest(
        item=IngestMemoryItem(session_id="s1", summary="Discussed deploy process.")
    )

    assert result.ok
    assert result.item_id == "item-123"


@respx.mock
def test_ingest_business_error() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/ingest").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "error",
                "error_code": "quota_exceeded",
                "error_message": "Monthly ingest limit reached.",
            },
        )
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    result = client.memory.ingest(item=IngestMemoryItem(session_id="s1", summary="X"))

    assert not result.ok
    assert result.error_code == "quota_exceeded"


@respx.mock
def test_ingest_auth_raises() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/ingest").mock(
        return_value=httpx.Response(401, json={"detail": "Unauthorized"})
    )

    client = PAMClient(api_key="bad-key")
    with pytest.raises(PAMAuthError):
        client.memory.ingest(item=IngestMemoryItem(session_id="s1", summary="X"))


@respx.mock
def test_ingest_server_error_raises_api_error() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/ingest").mock(
        return_value=httpx.Response(500, json={"detail": "Internal error"})
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    with pytest.raises(PAMAPIError):
        client.memory.ingest(item=IngestMemoryItem(session_id="s1", summary="X"))


@respx.mock
def test_client_ingest_shortcut() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/ingest").mock(
        return_value=httpx.Response(200, json={"status": "ok", "item_id": "item-456"})
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    result = client.ingest(item=IngestMemoryItem(session_id="s1", summary="X"))

    assert result.ok
    assert result.item_id == "item-456"


def test_ingest_item_rejects_unknown_fields() -> None:
    with pytest.raises(Exception):
        IngestMemoryItem(session_id="s1", summary="X", unexpected_field="nope")


def test_ingest_item_requires_session_id_and_summary() -> None:
    with pytest.raises(Exception):
        IngestMemoryItem(summary="X")
