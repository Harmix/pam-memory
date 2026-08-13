#!/usr/bin/env python3
"""List local Claude Code session transcripts with lightweight metadata only.

Reads no message content beyond the first user turn (used as a title
snippet). Intended for the sync-claude-memory disclosure/consent step, so a
user can see scope (session count, projects, date range) before any
transcript is read in full.

Covers both Claude Code CLI sessions and Claude Desktop "Cowork" sessions.
Cowork runs the same local CLI engine under the hood, so its transcripts
land in the exact same ~/.claude/projects/**/*.jsonl format -- nothing extra
to parse there. Desktop additionally keeps a lightweight per-session index
(title, cliSessionId) under claude-code-sessions/; when a jsonl's session id
matches an entry there, we tag the session client="cowork" and prefer
Desktop's own title over our first-message-snippet heuristic.

Note: "client" here (claude_code vs cowork) is which local app produced the
session. It's unrelated to the memory item's "source" field downstream,
which is always "claude_code" -- that's the pam-jobs pipeline's data-source
name, shared by both clients.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
COWORK_SESSIONS_DIR = (
    Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
)
META_TYPES = {"mode", "permission-mode", "file-history-snapshot"}
TITLE_MAX_CHARS = 120


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return " ".join(p for p in parts if p)
    return ""


def summarize_session(path: Path) -> dict[str, Any] | None:
    session_id = ""
    cwd = ""
    git_branch = ""
    version = ""
    message_count = 0
    first_user_text = ""
    started_at = ""
    ended_at = ""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue

        session_id = session_id or str(entry.get("sessionId") or "")
        entry_type = entry.get("type")
        if entry_type in META_TYPES:
            continue

        message = entry.get("message")
        if isinstance(message, dict):
            cwd = cwd or str(entry.get("cwd") or "")
            git_branch = git_branch or str(entry.get("gitBranch") or "")
            version = version or str(entry.get("version") or "")
            message_count += 1

            timestamp = str(entry.get("timestamp") or "")
            if timestamp:
                started_at = started_at or timestamp
                ended_at = timestamp

            if not first_user_text and entry_type == "user" and message.get("role") == "user":
                first_user_text = _extract_text(message.get("content"))

    if not session_id:
        session_id = path.stem
    if message_count == 0:
        return None

    title = " ".join(first_user_text.split())
    if len(title) > TITLE_MAX_CHARS:
        title = title[: TITLE_MAX_CHARS - 1].rstrip() + "…"

    return {
        "session_id": session_id,
        "file": str(path),
        "project_path": cwd,
        "git_branch": git_branch,
        "version": version,
        "message_count": message_count,
        "started_at": started_at,
        "ended_at": ended_at,
        "title": title,
        "client": "claude_code",
    }


def find_sessions(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*.jsonl"))


def load_cowork_index(root: Path) -> dict[str, dict[str, str]]:
    """Map cliSessionId -> {title, client} from Desktop's Cowork session index.

    Cowork's own index files are tiny (session metadata only, no transcript
    content) -- this only reads that metadata, never a transcript.
    """
    index: dict[str, dict[str, str]] = {}
    if not root.is_dir():
        return index
    for path in root.glob("**/local_*.json"):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(entry, dict):
            continue
        cli_session_id = str(entry.get("cliSessionId") or "")
        if not cli_session_id:
            continue
        index[cli_session_id] = {
            "title": str(entry.get("title") or ""),
            "client": "cowork",
        }
    return index


def list_sessions(
    *,
    root: Path = CLAUDE_PROJECTS_DIR,
    cowork_root: Path = COWORK_SESSIONS_DIR,
    since: date | None = None,
    project_contains: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    cowork_index = load_cowork_index(cowork_root)

    sessions: list[dict[str, Any]] = []
    for path in find_sessions(root):
        summary = summarize_session(path)
        if summary is None:
            continue
        cowork_entry = cowork_index.get(summary["session_id"])
        if cowork_entry:
            summary["client"] = cowork_entry["client"]
            if cowork_entry["title"]:
                summary["title"] = cowork_entry["title"]
        if project_contains and project_contains.lower() not in summary["project_path"].lower():
            continue
        if since and summary["ended_at"]:
            try:
                ended_date = datetime.fromisoformat(
                    summary["ended_at"].replace("Z", "+00:00")
                ).date()
            except ValueError:
                ended_date = None
            if ended_date and ended_date < since:
                continue
        sessions.append(summary)

    sessions.sort(key=lambda s: s["ended_at"], reverse=True)
    if limit is not None:
        sessions = sessions[:limit]

    projects = sorted({s["project_path"] for s in sessions if s["project_path"]})
    dated = [s["ended_at"] for s in sessions if s["ended_at"]]
    clients: dict[str, int] = {}
    for s in sessions:
        clients[s["client"]] = clients.get(s["client"], 0) + 1

    return {
        "root": str(root),
        "cowork_root": str(cowork_root),
        "count": len(sessions),
        "clients": clients,
        "projects": projects,
        "date_range": {
            "from": min(dated) if dated else None,
            "to": max(dated) if dated else None,
        },
        "sessions": sessions,
    }


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=_parse_date, default=None, help="YYYY-MM-DD")
    parser.add_argument("--project", dest="project_contains", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    result = list_sessions(
        since=args.since,
        project_contains=args.project_contains,
        limit=args.limit,
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
