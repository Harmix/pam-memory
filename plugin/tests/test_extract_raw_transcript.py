"""Unit tests for extract_raw_transcript (mechanical, no-LLM transcript parsing)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import extract_raw_transcript
from extract_raw_transcript import (
    build_payloads,
    main,
    parse_transcript,
    redact,
    split_into_parts,
)


def _write_transcript(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def _user_line(session_id: str, content, *, ts: str = "2026-01-01T00:00:00Z", cwd: str = "/proj") -> dict:
    return {
        "type": "user",
        "sessionId": session_id,
        "cwd": cwd,
        "timestamp": ts,
        "message": {"role": "user", "content": content},
    }


def _assistant_line(session_id: str, content, *, ts: str = "2026-01-01T00:01:00Z", cwd: str = "/proj") -> dict:
    return {
        "type": "assistant",
        "sessionId": session_id,
        "cwd": cwd,
        "timestamp": ts,
        "message": {"role": "assistant", "content": content},
    }


class TestParseTranscript:
    def test_extracts_text_turns_with_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "s1.jsonl"
        _write_transcript(
            path,
            [
                _user_line("s1", "we use uv for deps", ts="2026-01-01T00:00:00Z"),
                _assistant_line(
                    "s1",
                    [{"type": "text", "text": "got it"}],
                    ts="2026-01-01T00:01:00Z",
                ),
            ],
        )

        result = parse_transcript(path)

        assert result["session_id"] == "s1"
        assert result["project_path"] == "/proj"
        assert result["started_at"] == "2026-01-01T00:00:00Z"
        assert result["ended_at"] == "2026-01-01T00:01:00Z"
        assert result["title"] == "we use uv for deps"
        assert [t["role"] for t in result["turns"]] == ["user", "assistant"]
        assert result["turns"][0]["text"] == "we use uv for deps"
        assert result["turns"][1]["text"] == "got it"

    def test_drops_tool_use_and_tool_result_blocks(self, tmp_path: Path) -> None:
        path = tmp_path / "s1.jsonl"
        _write_transcript(
            path,
            [
                _assistant_line(
                    "s1",
                    [
                        {"type": "text", "text": "Let me check that file."},
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}},
                        {"type": "tool_result", "content": "file contents here"},
                    ],
                ),
            ],
        )

        result = parse_transcript(path)

        assert len(result["turns"]) == 1
        assert result["turns"][0]["text"] == "Let me check that file."

    def test_drops_image_blocks(self, tmp_path: Path) -> None:
        path = tmp_path / "s1.jsonl"
        _write_transcript(
            path,
            [
                _user_line(
                    "s1",
                    [
                        {"type": "text", "text": "look at this"},
                        {"type": "image", "source": {"data": "base64stuff"}},
                    ],
                ),
            ],
        )

        result = parse_transcript(path)

        assert len(result["turns"]) == 1
        assert result["turns"][0]["text"] == "look at this"

    def test_skips_meta_lines_and_blank_content(self, tmp_path: Path) -> None:
        path = tmp_path / "s1.jsonl"
        _write_transcript(
            path,
            [
                {"type": "mode", "mode": "normal", "sessionId": "s1"},
                _user_line("s1", "   "),
                _user_line("s1", "real content"),
            ],
        )

        result = parse_transcript(path)

        assert len(result["turns"]) == 1
        assert result["turns"][0]["text"] == "real content"

    def test_redacts_secrets_in_turn_text(self, tmp_path: Path) -> None:
        path = tmp_path / "s1.jsonl"
        _write_transcript(
            path,
            [_user_line("s1", "my key is pam_mkey_abcXYZ123")],
        )

        result = parse_transcript(path)

        assert "pam_mkey_abcXYZ123" not in result["turns"][0]["text"]
        assert "[REDACTED:pam_mkey]" in result["turns"][0]["text"]

    def test_falls_back_to_filename_when_session_id_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "fallback-id.jsonl"
        path.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
            encoding="utf-8",
        )

        result = parse_transcript(path)

        assert result["session_id"] == "fallback-id"


class TestRedact:
    @pytest.mark.parametrize(
        "text,expected_marker",
        [
            ("pam_mkey_abc123XYZ789", "pam_mkey"),
            ("sk-abcdefghijklmnopqrstuvwxyz123456", "openai_key"),
            ("AKIAABCDEFGHIJKLMNOP", "aws_key"),
            ("gho_abcdefghijklmnopqrstuvwxyz1234567890", "github_token"),
            ("Bearer abcxyz1234567890ABCDEF.ghijkl", "bearer_token"),
            ('api_key="sk_live_abcdefghijklmnop1234"', "generic_secret_assignment"),
        ],
    )
    def test_redacts_known_secret_shapes(self, text: str, expected_marker: str) -> None:
        assert f"[REDACTED:{expected_marker}]" in redact(text)

    def test_leaves_ordinary_text_untouched(self) -> None:
        text = "we deploy via github actions and use docker compose"
        assert redact(text) == text


class TestSplitIntoParts:
    def test_empty_turns_yields_no_parts(self) -> None:
        assert split_into_parts([]) == []

    def test_small_session_is_one_part(self) -> None:
        turns = [{"role": "user", "text": "hi", "timestamp": None}]
        assert split_into_parts(turns, max_chars=1000) == [turns]

    def test_splits_on_turn_boundaries_when_over_budget(self) -> None:
        turns = [{"role": "user", "text": "x" * 100, "timestamp": None} for _ in range(5)]
        parts = split_into_parts(turns, max_chars=250)

        assert len(parts) > 1
        # Every turn appears exactly once across all parts, never split mid-turn.
        flattened = [t for part in parts for t in part]
        assert flattened == turns


class TestBuildPayloads:
    def test_empty_turns_produces_no_payloads(self) -> None:
        parsed = {
            "session_id": "s1",
            "project_path": "",
            "started_at": None,
            "ended_at": None,
            "title": None,
            "turns": [],
        }
        assert build_payloads(parsed, client="claude_code") == []

    def test_single_part_payload_shape(self) -> None:
        parsed = {
            "session_id": "s1",
            "project_path": "/proj",
            "started_at": "2026-01-01T00:00:00Z",
            "ended_at": "2026-01-01T00:01:00Z",
            "title": "hello",
            "turns": [{"role": "user", "text": "hi", "timestamp": "2026-01-01T00:00:00Z"}],
        }

        payloads = build_payloads(parsed, client="cowork")

        assert len(payloads) == 1
        payload = payloads[0]
        assert payload["session_id"] == "s1"
        assert payload["client"] == "cowork"
        assert payload["part_index"] == 0
        assert payload["part_count"] == 1
        assert payload["turns"] == parsed["turns"]

    def test_multi_part_indices_are_ordered_and_turns_split_across_parts(self) -> None:
        # Three turns of 100k chars each exceed the 250k-char default budget,
        # forcing build_payloads to split into more than one part.
        turns = [
            {"role": "user", "text": f"turn-{i}-" + ("x" * 100_000), "timestamp": None}
            for i in range(3)
        ]
        parsed = {
            "session_id": "s1",
            "project_path": "",
            "started_at": None,
            "ended_at": None,
            "title": None,
            "turns": turns,
        }

        payloads = build_payloads(parsed, client="claude_code")

        assert len(payloads) > 1
        assert [p["part_index"] for p in payloads] == list(range(len(payloads)))
        assert all(p["part_count"] == len(payloads) for p in payloads)
        assert all(p["session_id"] == "s1" for p in payloads)
        # Every original turn shows up exactly once, in order, across parts.
        flattened = [t for p in payloads for t in p["turns"]]
        assert flattened == turns


class TestMainIntegration:
    def test_dry_run_prints_payloads_without_ingesting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        path = tmp_path / "s1.jsonl"
        _write_transcript(path, [_user_line("s1", "hello there")])

        mock_ingest = MagicMock()
        monkeypatch.setattr(extract_raw_transcript, "ingest_memory_from_chat", mock_ingest)
        monkeypatch.setattr(
            "sys.argv", ["extract_raw_transcript.py", "--file", str(path), "--dry-run"]
        )

        main()

        mock_ingest.assert_not_called()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "dry_run"
        assert out["payloads"][0]["session_id"] == "s1"

    def test_empty_session_is_skipped_without_ingesting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        path = tmp_path / "s1.jsonl"
        _write_transcript(path, [{"type": "mode", "mode": "normal", "sessionId": "s1"}])

        mock_ingest = MagicMock()
        monkeypatch.setattr(extract_raw_transcript, "ingest_memory_from_chat", mock_ingest)
        monkeypatch.setattr("sys.argv", ["extract_raw_transcript.py", "--file", str(path)])

        main()

        mock_ingest.assert_not_called()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "skipped"

    def test_calls_ingest_once_per_part(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        path = tmp_path / "s1.jsonl"
        _write_transcript(path, [_user_line("s1", "hello there")])

        mock_ingest = MagicMock(return_value={"status": "queued", "item_id": "x"})
        monkeypatch.setattr(extract_raw_transcript, "ingest_memory_from_chat", mock_ingest)
        monkeypatch.setattr("sys.argv", ["extract_raw_transcript.py", "--file", str(path)])

        main()

        mock_ingest.assert_called_once()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "done"
        assert out["part_count"] == 1
        assert out["results"] == [{"status": "queued", "item_id": "x"}]
