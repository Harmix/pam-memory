"""Unit tests for list_claude_sessions."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from list_claude_sessions import list_sessions, load_cowork_index, summarize_session


def _no_cowork(tmp_path: Path) -> Path:
    """A cowork_root that doesn't exist, for tests that don't care about it."""
    return tmp_path / "no-cowork-index"


def _write_session(path: Path, *, session_id: str, cwd: str, timestamp: str, text: str) -> None:
    lines = [
        {"type": "mode", "mode": "normal", "sessionId": session_id},
        {
            "type": "user",
            "sessionId": session_id,
            "cwd": cwd,
            "gitBranch": "main",
            "version": "2.1.0",
            "timestamp": timestamp,
            "message": {"role": "user", "content": text},
        },
        {
            "type": "assistant",
            "sessionId": session_id,
            "cwd": cwd,
            "timestamp": timestamp,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


class TestSummarizeSession:
    def test_extracts_metadata_without_full_content(self, tmp_path: Path) -> None:
        session_file = tmp_path / "s1.jsonl"
        _write_session(
            session_file,
            session_id="s1",
            cwd="/Users/dev/project-a",
            timestamp="2026-08-01T10:00:00.000Z",
            text="How do I deploy this service?",
        )

        summary = summarize_session(session_file)

        assert summary is not None
        assert summary["session_id"] == "s1"
        assert summary["project_path"] == "/Users/dev/project-a"
        assert summary["message_count"] == 2
        assert summary["title"] == "How do I deploy this service?"
        assert summary["client"] == "claude_code"

    def test_skips_files_with_no_messages(self, tmp_path: Path) -> None:
        session_file = tmp_path / "empty.jsonl"
        session_file.write_text(
            json.dumps({"type": "mode", "mode": "normal", "sessionId": "empty"}),
            encoding="utf-8",
        )
        assert summarize_session(session_file) is None

    def test_truncates_long_titles(self, tmp_path: Path) -> None:
        session_file = tmp_path / "s2.jsonl"
        _write_session(
            session_file,
            session_id="s2",
            cwd="/Users/dev/project-b",
            timestamp="2026-08-01T10:00:00.000Z",
            text="x" * 500,
        )
        summary = summarize_session(session_file)
        assert summary is not None
        assert len(summary["title"]) <= 120
        assert summary["title"].endswith("…")


class TestListSessions:
    def test_lists_sessions_across_projects(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        (root / "-Users-dev-project-a").mkdir(parents=True)
        (root / "-Users-dev-project-b").mkdir(parents=True)
        _write_session(
            root / "-Users-dev-project-a" / "s1.jsonl",
            session_id="s1",
            cwd="/Users/dev/project-a",
            timestamp="2026-08-01T10:00:00.000Z",
            text="First session",
        )
        _write_session(
            root / "-Users-dev-project-b" / "s2.jsonl",
            session_id="s2",
            cwd="/Users/dev/project-b",
            timestamp="2026-08-03T10:00:00.000Z",
            text="Second session",
        )

        result = list_sessions(root=root, cowork_root=_no_cowork(tmp_path))

        assert result["count"] == 2
        assert result["projects"] == ["/Users/dev/project-a", "/Users/dev/project-b"]
        assert result["sessions"][0]["session_id"] == "s2"
        assert result["clients"] == {"claude_code": 2}

    def test_filters_by_since(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        (root / "-Users-dev-project-a").mkdir(parents=True)
        _write_session(
            root / "-Users-dev-project-a" / "old.jsonl",
            session_id="old",
            cwd="/Users/dev/project-a",
            timestamp="2026-01-01T10:00:00.000Z",
            text="Old session",
        )
        _write_session(
            root / "-Users-dev-project-a" / "new.jsonl",
            session_id="new",
            cwd="/Users/dev/project-a",
            timestamp="2026-08-01T10:00:00.000Z",
            text="New session",
        )

        result = list_sessions(root=root, cowork_root=_no_cowork(tmp_path), since=date(2026, 6, 1))

        assert result["count"] == 1
        assert result["sessions"][0]["session_id"] == "new"

    def test_filters_by_project(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        (root / "-Users-dev-project-a").mkdir(parents=True)
        (root / "-Users-dev-project-b").mkdir(parents=True)
        _write_session(
            root / "-Users-dev-project-a" / "s1.jsonl",
            session_id="s1",
            cwd="/Users/dev/project-a",
            timestamp="2026-08-01T10:00:00.000Z",
            text="A",
        )
        _write_session(
            root / "-Users-dev-project-b" / "s2.jsonl",
            session_id="s2",
            cwd="/Users/dev/project-b",
            timestamp="2026-08-01T10:00:00.000Z",
            text="B",
        )

        result = list_sessions(
            root=root, cowork_root=_no_cowork(tmp_path), project_contains="project-b"
        )

        assert result["count"] == 1
        assert result["sessions"][0]["session_id"] == "s2"

    def test_missing_root_returns_empty(self, tmp_path: Path) -> None:
        result = list_sessions(
            root=tmp_path / "does-not-exist", cowork_root=_no_cowork(tmp_path)
        )
        assert result["count"] == 0
        assert result["sessions"] == []


class TestCoworkIntegration:
    def test_tags_client_and_prefers_desktop_title(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        (root / "-Users-dev-project-a").mkdir(parents=True)
        _write_session(
            root / "-Users-dev-project-a" / "s1.jsonl",
            session_id="s1",
            cwd="/Users/dev/project-a",
            timestamp="2026-08-01T10:00:00.000Z",
            text="how do i deploy this",
        )

        cowork_root = tmp_path / "cowork"
        index_dir = cowork_root / "16a8badf" / "f4a5526d"
        index_dir.mkdir(parents=True)
        (index_dir / "local_abc.json").write_text(
            json.dumps({"cliSessionId": "s1", "title": "Deploy walkthrough"}),
            encoding="utf-8",
        )

        result = list_sessions(root=root, cowork_root=cowork_root)

        assert result["clients"] == {"cowork": 1}
        session = result["sessions"][0]
        assert session["client"] == "cowork"
        assert session["title"] == "Deploy walkthrough"

    def test_sessions_without_cowork_entry_stay_claude_code(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        (root / "-Users-dev-project-a").mkdir(parents=True)
        _write_session(
            root / "-Users-dev-project-a" / "s1.jsonl",
            session_id="s1",
            cwd="/Users/dev/project-a",
            timestamp="2026-08-01T10:00:00.000Z",
            text="cli only session",
        )

        cowork_root = tmp_path / "cowork"
        index_dir = cowork_root / "x" / "y"
        index_dir.mkdir(parents=True)
        (index_dir / "local_other.json").write_text(
            json.dumps({"cliSessionId": "some-other-session", "title": "Unrelated"}),
            encoding="utf-8",
        )

        result = list_sessions(root=root, cowork_root=cowork_root)

        assert result["clients"] == {"claude_code": 1}
        assert result["sessions"][0]["title"] == "cli only session"

    def test_missing_cowork_root_is_silently_ignored(self, tmp_path: Path) -> None:
        index = load_cowork_index(tmp_path / "does-not-exist")
        assert index == {}

    def test_malformed_cowork_index_file_is_skipped(self, tmp_path: Path) -> None:
        cowork_root = tmp_path / "cowork"
        index_dir = cowork_root / "x" / "y"
        index_dir.mkdir(parents=True)
        (index_dir / "local_bad.json").write_text("not json", encoding="utf-8")

        index = load_cowork_index(cowork_root)
        assert index == {}
