"""Unit tests for sync_batch (in-process multi-session batch driver)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sync_batch
from sync_batch import main, run_batch


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


class TestRunBatch:
    def test_processes_every_session_and_tallies_by_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queued_path = tmp_path / "queued.jsonl"
        local_path = tmp_path / "local.jsonl"
        empty_path = tmp_path / "empty.jsonl"
        _write_transcript(queued_path, [_user_line("s-queued", "hello there")])
        _write_transcript(local_path, [_user_line("s-local", "hello again")])
        _write_transcript(empty_path, [{"type": "mode", "mode": "normal", "sessionId": "s-empty"}])

        def fake_ingest(payload: dict) -> dict:
            if payload["session_id"] == "s-queued":
                return {"status": "queued", "item_id": "q1"}
            return {"status": "queued_locally", "reason": "no api key", "item_id": "l1"}

        monkeypatch.setattr(sync_batch, "ingest_memory_from_chat", fake_ingest)

        sessions = [
            {"file": str(queued_path), "client": "claude_code"},
            {"file": str(local_path), "client": "claude_code"},
            {"file": str(empty_path), "client": "claude_code"},
        ]

        tally = run_batch(sessions)

        assert tally == {"queued": 1, "queued_locally": 1, "skipped": 1, "errors": 0}

    def test_one_bad_session_does_not_abort_the_rest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        good_path = tmp_path / "good.jsonl"
        _write_transcript(good_path, [_user_line("s-good", "hello there")])

        mock_ingest = MagicMock(return_value={"status": "queued", "item_id": "x"})
        monkeypatch.setattr(sync_batch, "ingest_memory_from_chat", mock_ingest)

        sessions = [
            {"file": str(tmp_path / "does-not-exist.jsonl"), "client": "claude_code"},
            {"file": str(good_path), "client": "claude_code"},
        ]

        tally = run_batch(sessions)

        assert tally["errors"] == 1
        assert tally["queued"] == 1
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert lines[0]["status"] == "error"
        assert lines[0]["index"] == 1
        assert lines[1]["status"] == "done"
        assert lines[1]["index"] == 2

    def test_only_the_last_payload_in_the_whole_batch_has_more_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Three sessions, each producing one payload. Only the very last
        # payload overall (C's) should carry has_more=False -- every other
        # payload must be True so pam-agent-api stages without triggering a
        # pipeline run, and exactly one trigger fires after the batch.
        a_path = tmp_path / "a.jsonl"
        _write_transcript(a_path, [_user_line("s-a", "first")])
        b_path = tmp_path / "b.jsonl"
        _write_transcript(b_path, [_user_line("s-b", "second")])
        c_path = tmp_path / "c.jsonl"
        _write_transcript(c_path, [_user_line("s-c", "third")])

        seen_has_more: list[tuple[str, bool]] = []

        def fake_ingest(payload: dict) -> dict:
            seen_has_more.append((payload["session_id"], payload["has_more"]))
            return {"status": "queued", "item_id": "x"}

        monkeypatch.setattr(sync_batch, "ingest_memory_from_chat", fake_ingest)

        sessions = [
            {"file": str(a_path), "client": "claude_code"},
            {"file": str(b_path), "client": "claude_code"},
            {"file": str(c_path), "client": "claude_code"},
        ]

        run_batch(sessions)

        assert seen_has_more == [("s-a", True), ("s-b", True), ("s-c", False)]

    def test_multi_part_session_only_final_part_has_more_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A single session split into multiple parts (large transcript) --
        # every part but the session's own last part must be True, and since
        # it's also the last session in the batch, that final part is the
        # one payload with has_more=False.
        monkeypatch.setattr(sync_batch, "build_payloads", lambda parsed, *, client: [
            {"session_id": parsed["session_id"], "turns": [], "part_index": 0, "part_count": 3},
            {"session_id": parsed["session_id"], "turns": [], "part_index": 1, "part_count": 3},
            {"session_id": parsed["session_id"], "turns": [], "part_index": 2, "part_count": 3},
        ])

        path = tmp_path / "big.jsonl"
        _write_transcript(path, [_user_line("s-big", "x")])

        seen_has_more: list[bool] = []

        def fake_ingest(payload: dict) -> dict:
            seen_has_more.append(payload["has_more"])
            return {"status": "queued", "item_id": "x"}

        monkeypatch.setattr(sync_batch, "ingest_memory_from_chat", fake_ingest)

        run_batch([{"file": str(path), "client": "claude_code"}])

        assert seen_has_more == [True, True, False]

    def test_last_payload_has_more_false_even_when_final_session_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If the batch's last *session* is mechanically skipped (no turns),
        # the true last payload belongs to an earlier session -- has_more
        # must still land on that one, not silently vanish.
        good_path = tmp_path / "good.jsonl"
        _write_transcript(good_path, [_user_line("s-good", "hello")])
        empty_path = tmp_path / "empty.jsonl"
        _write_transcript(empty_path, [{"type": "mode", "mode": "normal", "sessionId": "s-empty"}])

        seen_has_more: list[bool] = []

        def fake_ingest(payload: dict) -> dict:
            seen_has_more.append(payload["has_more"])
            return {"status": "queued", "item_id": "x"}

        monkeypatch.setattr(sync_batch, "ingest_memory_from_chat", fake_ingest)

        sessions = [
            {"file": str(good_path), "client": "claude_code"},
            {"file": str(empty_path), "client": "claude_code"},
        ]

        run_batch(sessions)

        assert seen_has_more == [False]

    def test_emits_flushed_progress_line_per_session_plus_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        path = tmp_path / "s1.jsonl"
        _write_transcript(path, [_user_line("s1", "hello there")])

        monkeypatch.setattr(
            sync_batch, "ingest_memory_from_chat", lambda payload: {"status": "queued", "item_id": "x"}
        )

        run_batch([{"file": str(path), "client": "claude_code"}])

        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(lines) == 2
        assert lines[0]["status"] == "done"
        assert lines[0]["total"] == 1
        assert lines[1] == {
            "status": "summary",
            "total": 1,
            "queued": 1,
            "queued_locally": 0,
            "skipped": 0,
            "errors": 0,
        }


class TestMainIntegration:
    def test_reads_sessions_from_file_and_runs_batch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        transcript_path = tmp_path / "s1.jsonl"
        _write_transcript(transcript_path, [_user_line("s1", "hello there")])

        sessions_path = tmp_path / "sessions.json"
        sessions_path.write_text(
            json.dumps([{"file": str(transcript_path), "client": "cowork"}]), encoding="utf-8"
        )

        mock_ingest = MagicMock(return_value={"status": "queued", "item_id": "x"})
        monkeypatch.setattr(sync_batch, "ingest_memory_from_chat", mock_ingest)
        monkeypatch.setattr(
            "sys.argv", ["sync_batch.py", "--sessions-file", str(sessions_path)]
        )

        main()

        mock_ingest.assert_called_once()
        assert mock_ingest.call_args[0][0]["client"] == "cowork"
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert lines[-1]["status"] == "summary"
        assert lines[-1]["queued"] == 1

    def test_non_list_sessions_input_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sessions_path = tmp_path / "sessions.json"
        sessions_path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        monkeypatch.setattr(
            "sys.argv", ["sync_batch.py", "--sessions-file", str(sessions_path)]
        )

        with pytest.raises(SystemExit):
            main()
