"""Unit tests for ingest_memory_from_chat (real PAM ingest + local-queue fallback)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import ingest_memory_from_chat
from ingest_memory_from_chat import ingest_memory_from_chat as ingest


def _force_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the fallback path deterministically, regardless of the machine's own PAM config."""
    monkeypatch.setattr(
        ingest_memory_from_chat.pam_plugin_config, "resolve_api_key", lambda: ""
    )


class TestLocalFallback:
    """These exercise the fallback queue — forced via a missing API key."""

    def test_queues_valid_item(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_no_api_key(monkeypatch)
        queue_file = tmp_path / "claude_code.jsonl"
        item = {"session_id": "s1", "summary": "Discussed deploy process."}

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
        ingest({"session_id": "s1", "summary": "First pass."}, queue_file=queue_file)
        result = ingest(
            {"session_id": "s1", "summary": "Updated summary."}, queue_file=queue_file
        )

        assert result["status"] == "queued_locally"
        assert result["queue_count"] == 1
        lines = queue_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["summary"] == "Updated summary."

    def test_appends_distinct_sessions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_no_api_key(monkeypatch)
        queue_file = tmp_path / "claude_code.jsonl"
        ingest({"session_id": "s1", "summary": "A"}, queue_file=queue_file)
        result = ingest({"session_id": "s2", "summary": "B"}, queue_file=queue_file)

        assert result["status"] == "queued_locally"
        assert result["queue_count"] == 2


class TestValidation:
    def test_rejects_missing_required_field(self, tmp_path: Path) -> None:
        queue_file = tmp_path / "claude_code.jsonl"
        result = ingest({"session_id": "s1"}, queue_file=queue_file)

        assert result["status"] == "error"
        assert not queue_file.exists()


class TestRealIngest:
    def test_success_sends_to_pam(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        mock_response = MagicMock(ok=True, item_id="item-123")
        mock_client = MagicMock()
        mock_client.memory.ingest.return_value = mock_response
        monkeypatch.setattr(
            ingest_memory_from_chat.PAMClient, "for_plugin", lambda **_: mock_client
        )

        queue_file = tmp_path / "claude_code.jsonl"
        result = ingest(
            {"session_id": "s1", "summary": "Discussed deploy process."},
            queue_file=queue_file,
        )

        assert result == {"status": "queued", "item_id": "item-123"}
        assert not queue_file.exists()
        mock_client.memory.ingest.assert_called_once()

    def test_business_error_falls_back_locally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ingest_memory_from_chat.pam_plugin_config,
            "resolve_api_key",
            lambda: "pam_mkey_test.secret",
        )
        monkeypatch.setattr(
            ingest_memory_from_chat.pam_plugin_config, "resolve_base_url", lambda: "https://x"
        )

        mock_response = MagicMock(ok=False, error_code="quota_exceeded", error_message="limit")
        mock_client = MagicMock()
        mock_client.memory.ingest.return_value = mock_response
        monkeypatch.setattr(
            ingest_memory_from_chat.PAMClient, "for_plugin", lambda **_: mock_client
        )

        queue_file = tmp_path / "claude_code.jsonl"
        result = ingest({"session_id": "s1", "summary": "X"}, queue_file=queue_file)

        assert result["status"] == "queued_locally"
        assert "quota_exceeded" in result["reason"]

    def test_network_error_falls_back_locally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ingest_memory_from_chat.pam_plugin_config,
            "resolve_api_key",
            lambda: "pam_mkey_test.secret",
        )
        monkeypatch.setattr(
            ingest_memory_from_chat.pam_plugin_config, "resolve_base_url", lambda: "https://x"
        )

        mock_client = MagicMock()
        mock_client.memory.ingest.side_effect = RuntimeError("connection reset")
        monkeypatch.setattr(
            ingest_memory_from_chat.PAMClient, "for_plugin", lambda **_: mock_client
        )

        queue_file = tmp_path / "claude_code.jsonl"
        result = ingest({"session_id": "s1", "summary": "X"}, queue_file=queue_file)

        assert result["status"] == "queued_locally"
        assert "connection reset" in result["reason"]
        lines = queue_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

    def test_missing_api_key_falls_back_locally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_no_api_key(monkeypatch)
        queue_file = tmp_path / "claude_code.jsonl"

        result = ingest({"session_id": "s1", "summary": "X"}, queue_file=queue_file)

        assert result["status"] == "queued_locally"
        assert result["reason"] == "no PAM API key configured"
