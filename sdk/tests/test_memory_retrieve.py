"""PAM SDK tests."""

from __future__ import annotations

import httpx
import pytest
import respx

from pam import PAMClient
from pam.exceptions import PAMAPIError, PAMAuthError, PAMTimeoutError


@respx.mock
def test_retrieve_success() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "memory_ready": True,
                "content_text": "Migration uses Next.js 14.",
                "sources": [".knowledge/decisions/migration.md"],
                "request_id": "req-123",
            },
        )
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    result = client.memory.retrieve(prompt="migration plan?")

    assert result.ok
    assert "Next.js" in result.content_text
    assert result.sources[0].endswith("migration.md")
    assert result.request_id == "req-123"


@respx.mock
def test_retrieve_quota_business_error() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "error",
                "memory_ready": True,
                "content_text": "",
                "error_code": "quota_exceeded",
                "error_message": "Monthly retrieve limit reached.",
            },
        )
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    result = client.memory.retrieve(prompt="anything")

    assert not result.ok
    assert result.error_code == "quota_exceeded"
    assert result.is_business_error


@respx.mock
def test_retrieve_auth_raises() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/retrieve").mock(
        return_value=httpx.Response(401, json={"detail": "Unauthorized"})
    )

    client = PAMClient(api_key="bad-key")
    with pytest.raises(PAMAuthError):
        client.memory.retrieve(prompt="test")


@respx.mock
def test_plugin_mode_timeout_raises() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/retrieve").mock(
        side_effect=httpx.ReadTimeout("slow")
    )

    client = PAMClient.for_plugin(api_key="pam_mkey_test.secret")
    with pytest.raises(PAMTimeoutError):
        client.memory.retrieve(prompt="test")


@respx.mock
def test_retrieve_forbidden_raises_api_error() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/retrieve").mock(
        return_value=httpx.Response(403, json={"detail": "Feature disabled"})
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    with pytest.raises(PAMAPIError) as exc_info:
        client.memory.retrieve(prompt="test")

    assert exc_info.value.status_code == 403


@respx.mock
def test_retrieve_not_found_raises_api_error() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/retrieve").mock(
        return_value=httpx.Response(404, json={"detail": "Not Found"})
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    with pytest.raises(PAMAPIError) as exc_info:
        client.memory.retrieve(prompt="test")

    assert exc_info.value.status_code == 404


@respx.mock
def test_retrieve_memory_not_ready_business_error() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "memory_ready": False,
                "content_text": "Memory sync in progress.",
            },
        )
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    result = client.memory.retrieve(prompt="test")

    assert not result.ok
    assert result.is_business_error


@respx.mock
def test_client_retrieve_shortcut() -> None:
    respx.post("https://api.pam.harmix.ai/v1/memory/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "memory_ready": True,
                "content_text": "Answer",
                "sources": [],
            },
        )
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    result = client.retrieve(prompt="test")

    assert result.ok


def test_client_uses_pam_base_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAM_BASE_URL", "https://uat.example.test")
    client = PAMClient(api_key="pam_mkey_test.secret")
    assert client._http._base_url == "https://uat.example.test"


def test_client_requires_api_key() -> None:
    with pytest.raises(ValueError, match="API key required"):
        PAMClient(api_key="")


def test_for_plugin_uses_strict_timeout() -> None:
    client = PAMClient.for_plugin(api_key="pam_mkey_test.secret")
    assert client._http._timeout.connect == 1.5
    assert client._http._timeout.read == 5.0
