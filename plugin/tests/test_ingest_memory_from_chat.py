"""Unit tests for ingest_memory_from_chat (real PAM ingest + local-queue fallback)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import ingest_memory_from_chat
from ingest_memory_from_chat import ingest_memory_from_chat as ingest


def _force_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the fallback path deterministically, regardless of the machine's own PAM config."""
    monkeypatch.setattr(
        ingest_memory_from_chat.pam_plugin_config, "resolve_api_key", lambda: ""
    )


def _mock_curl(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    body: str = "",
    status_code: int | None = 200,
    stderr: bytes = b"",
) -> None:
    """Stand in for the curl subprocess ingest_memory_from_chat shells out to."""
    stdout = body.encode("utf-8")
    if status_code is not None:
        stdout += f"\n{status_code}".encode("utf-8")

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(ingest_memory_from_chat.subprocess, "run", fake_run)


class TestLocalFallback:
    """These exercise the fallback queue — forced via a missing API key."""

    def test_queues_valid_item(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_no_api_key(monkeypatch)
        queue_file = tmp_path / "claude_code.jsonl"
        item = {"session_id": "s1", "turns": [{"role": "user", "text": "Discussed deploy process."}]}

        result = ingest(item, queue_file=queue_file)

        assert result["status"] == "queued_locally"
        assert result["reason"] == "no PAM API key configured"
        assert result["queue_count"] == 1
        lines = queue_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["session_id"] == "s1"
        assert record["source"] == "claude_code"
        assert "item_id" in record
        assert "ingested_at" in record

    def test_dedupes_by_session_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_no_api_key(monkeypatch)
        queue_file = tmp_path / "claude_code.jsonl"
        ingest(
            {"session_id": "s1", "turns": [{"role": "user", "text": "First pass."}]},
            queue_file=queue_file,
        )
        result = ingest(
            {"session_id": "s1", "turns": [{"role": "user", "text": "Updated pass."}]},
            queue_file=queue_file,
        )

        assert result["status"] == "queued_locally"
        assert result["queue_count"] == 1
        lines = queue_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["turns"][0]["text"] == "Updated pass."

    def test_appends_distinct_sessions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_no_api_key(monkeypatch)
        queue_file = tmp_path / "claude_code.jsonl"
        ingest(
            {"session_id": "s1", "turns": [{"role": "user", "text": "A"}]},
            queue_file=queue_file,
        )
        result = ingest(
            {"session_id": "s2", "turns": [{"role": "user", "text": "B"}]},
            queue_file=queue_file,
        )

        assert result["status"] == "queued_locally"
        assert result["queue_count"] == 2


class TestValidation:
    def test_rejects_missing_required_field(self, tmp_path: Path) -> None:
        queue_file = tmp_path / "claude_code.jsonl"
        result = ingest({"session_id": "s1"}, queue_file=queue_file)

        assert result["status"] == "error"
        assert not queue_file.exists()


class TestRealIngest:
    """Exercise _send_to_pam's curl subprocess call, mocked at the subprocess.run boundary."""

    def _set_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ingest_memory_from_chat.pam_plugin_config,
            "resolve_api_key",
            lambda: "pam_mkey_test.secret",
        )
        monkeypatch.setattr(
            ingest_memory_from_chat.pam_plugin_config,
            "resolve_base_url",
            lambda: "https://api.pam.harmix.ai",
        )

    def test_success_sends_to_pam(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set_key(monkeypatch)
        _mock_curl(
            monkeypatch,
            body=json.dumps({"status": "ok", "item_id": "item-123"}),
            status_code=200,
        )

        queue_file = tmp_path / "claude_code.jsonl"
        result = ingest(
            {"session_id": "s1", "turns": [{"role": "user", "text": "Discussed deploy process."}]},
            queue_file=queue_file,
        )

        assert result == {"status": "queued", "item_id": "item-123"}
        assert not queue_file.exists()

    def test_business_error_falls_back_locally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set_key(monkeypatch)
        _mock_curl(
            monkeypatch,
            body=json.dumps(
                {"status": "error", "error_code": "quota_exceeded", "error_message": "limit"}
            ),
            status_code=200,
        )

        queue_file = tmp_path / "claude_code.jsonl"
        result = ingest(
            {"session_id": "s1", "turns": [{"role": "user", "text": "X"}]},
            queue_file=queue_file,
        )

        assert result["status"] == "queued_locally"
        assert "quota_exceeded" in result["reason"]

    def test_network_error_falls_back_locally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set_key(monkeypatch)
        _mock_curl(
            monkeypatch,
            returncode=7,
            body="",
            status_code=None,
            stderr=b"curl: (7) Failed to connect: connection reset by peer",
        )

        queue_file = tmp_path / "claude_code.jsonl"
        result = ingest(
            {"session_id": "s1", "turns": [{"role": "user", "text": "X"}]},
            queue_file=queue_file,
        )

        assert result["status"] == "queued_locally"
        assert "connection reset" in result["reason"]
        lines = queue_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

    def test_invalid_api_key_falls_back_locally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set_key(monkeypatch)
        _mock_curl(monkeypatch, body="", status_code=401)

        queue_file = tmp_path / "claude_code.jsonl"
        result = ingest(
            {"session_id": "s1", "turns": [{"role": "user", "text": "X"}]},
            queue_file=queue_file,
        )

        assert result["status"] == "queued_locally"
        assert "Invalid or missing PAM API key" in result["reason"]

    def test_missing_api_key_falls_back_locally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_no_api_key(monkeypatch)
        queue_file = tmp_path / "claude_code.jsonl"

        result = ingest(
            {"session_id": "s1", "turns": [{"role": "user", "text": "X"}]},
            queue_file=queue_file,
        )

        assert result["status"] == "queued_locally"
        assert result["reason"] == "no PAM API key configured"
