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
def test_ac5_degraded_retrieve_is_still_ok_fail_open() -> None:
    """AC5: a degraded retrieval stays usable for an existing SDK caller.

    C1 invariant 1 keeps ``status == "ok"`` while degraded, so ``.ok`` -- the
    plugin's only branch -- must stay True and the result must not look like a
    business error (invariant 4: ``error_code`` stays None).
    """
    respx.post("https://api.pam.harmix.ai/v1/memory/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "memory_ready": True,
                "content_text": "Partial context from a bounded prompt.",
                "sources": [".knowledge/decisions/migration.md"],
                "request_id": "req-degraded",
                "degraded": True,
                "degraded_reason": "phase1_prompt_too_large",
            },
        )
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    result = client.memory.retrieve(prompt="migration plan?")

    assert result.ok is True
    assert result.degraded is True
    assert not result.is_business_error
    assert result.error_code is None


@respx.mock
def test_ac5_response_without_degraded_fields_still_parses() -> None:
    """AC5: wire compatibility -- an older server omits both new fields."""
    respx.post("https://api.pam.harmix.ai/v1/memory/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "memory_ready": True,
                "content_text": "Migration uses Next.js 14.",
                "sources": [],
            },
        )
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    result = client.memory.retrieve(prompt="migration plan?")

    assert result.ok is True
    assert result.degraded is False
    assert result.degraded_reason is None


@respx.mock
def test_ac4_degraded_is_typed_boolean_not_prose() -> None:
    """AC4: degradation is readable without string-matching prose.

    ``degraded`` parses as a typed bool and ``degraded_reason`` carries a
    machine token from C1's closed vocabulary, never a sentence.
    """
    respx.post("https://api.pam.harmix.ai/v1/memory/retrieve").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "memory_ready": True,
                "content_text": (
                    "No company memory context was retrieved for this prompt."
                ),
                "sources": [],
                "degraded": True,
                "degraded_reason": "phase1_unparseable",
            },
        )
    )

    client = PAMClient(api_key="pam_mkey_test.secret")
    result = client.memory.retrieve(prompt="anything")

    assert isinstance(result.degraded, bool)
    assert result.degraded is True
    assert result.degraded_reason == "phase1_unparseable"
    assert result.status == "ok"


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
    assert client._http._timeout.connect == 5.0
    assert client._http._timeout.read == 90.0
