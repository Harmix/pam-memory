#!/usr/bin/env python3
"""Turn one local Claude Code / Cowork transcript into raw ingest payload(s).

Purely mechanical -- no LLM calls, no judgment about what's worth
remembering. This is the token-burn fix for sync-claude-memory: instead of
spawning an Agent subagent to read and summarize each transcript client-side
(spending the user's own model quota), this script:

1. Parses the transcript JSONL into {role, text, timestamp} turns, keeping
   only plain-text message content (tool_use/tool_result/image blocks are
   dropped -- raw prose only, not tool noise).
2. Applies regex-based secret redaction to each turn's text (API keys,
   tokens, bearer/JWT shapes) -- mechanical pattern matching, not an LLM
   judging content.
3. Drops sessions with zero non-empty turns (mechanical, not a "worth
   remembering" judgment).
4. Splits oversized sessions into ordered parts (same session_id,
   incrementing part_index/part_count) so no single ingest call gets too
   big.
5. Calls ingest_memory_from_chat() once per part.

pam-jobs' extract_claude_code stage does the actual summarization/fact
extraction server-side via Gemini, mirroring every other memory source.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_memory_from_chat import ingest_memory_from_chat  # noqa: E402

META_TYPES = {"mode", "permission-mode", "file-history-snapshot"}
TITLE_MAX_CHARS = 120
MAX_CHARS_PER_PART = 250_000

# Mechanical secret redaction -- pattern matching only, never LLM judgment.
_REDACTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pam_mkey", re.compile(r"pam_mkey_[A-Za-z0-9_-]+")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_.]{20,}")),
    (
        "generic_secret_assignment",
        re.compile(
            r"(?i)(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*"
            r"['\"]?[A-Za-z0-9_\-/+=]{16,}['\"]?"
        ),
    ),
]


def redact(text: str) -> str:
    for name, pattern in _REDACTION_PATTERNS:
        text = pattern.sub(f"[REDACTED:{name}]", text)
    return text


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


def parse_transcript(path: Path) -> dict[str, Any]:
    """Mechanically parse one transcript JSONL into turns + metadata.

    No content judgment -- every user/assistant text turn is kept (after
    redaction), nothing is skipped for being "not durable enough". That
    decision now belongs entirely to pam-jobs' server-side extraction.
    """
    session_id = ""
    project_path = ""
    started_at = ""
    ended_at = ""
    first_user_text = ""
    turns: list[dict[str, str]] = []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(f"cannot read transcript: {exc}")

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
        if entry.get("type") in META_TYPES:
            continue

        message = entry.get("message")
        if not isinstance(message, dict):
            continue

        project_path = project_path or str(entry.get("cwd") or "")
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue

        timestamp = str(entry.get("timestamp") or "")
        if timestamp:
            started_at = started_at or timestamp
            ended_at = timestamp

        text = redact(_extract_text(message.get("content")).strip())
        if not text:
            continue

        if not first_user_text and role == "user":
            first_user_text = text

        turns.append({"role": role, "text": text, "timestamp": timestamp or None})

    if not session_id:
        session_id = path.stem

    title = " ".join(first_user_text.split())
    if len(title) > TITLE_MAX_CHARS:
        title = title[: TITLE_MAX_CHARS - 1].rstrip() + "…"

    return {
        "session_id": session_id,
        "project_path": project_path,
        "started_at": started_at or None,
        "ended_at": ended_at or None,
        "title": title or None,
        "turns": turns,
    }


def split_into_parts(
    turns: list[dict[str, str]], *, max_chars: int = MAX_CHARS_PER_PART
) -> list[list[dict[str, str]]]:
    """Split turns into ordered parts, never splitting a single turn.

    Mirrors pam-jobs' _chunk_segments_by_tokens boundary rule (whole units
    only) so the server-side merge-by-session_id sees complete turns.
    """
    if not turns:
        return []
    parts: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    size = 0
    for turn in turns:
        turn_len = len(turn["text"]) + 1
        if current and size + turn_len > max_chars:
            parts.append(current)
            current, size = [], 0
        current.append(turn)
        size += turn_len
    if current:
        parts.append(current)
    return parts


def build_payloads(parsed: dict[str, Any], *, client: str) -> list[dict[str, Any]]:
    parts = split_into_parts(parsed["turns"])
    if not parts:
        return []

    payloads = []
    for index, part_turns in enumerate(parts):
        payloads.append(
            {
                "session_id": parsed["session_id"],
                "turns": part_turns,
                "source": "claude_code",
                "client": client,
                "project_path": parsed["project_path"] or None,
                "started_at": parsed["started_at"],
                "ended_at": parsed["ended_at"],
                "title": parsed["title"],
                "part_index": index,
                "part_count": len(parts),
            }
        )
    return payloads


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Path to a transcript .jsonl file.")
    parser.add_argument(
        "--client",
        default="claude_code",
        choices=("claude_code", "cowork"),
        help="Which local app produced the session.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the built payload(s) without calling ingest_memory_from_chat.",
    )
    args = parser.parse_args()

    parsed = parse_transcript(Path(args.file))
    payloads = build_payloads(parsed, client=args.client)

    if not payloads:
        json.dump(
            {"status": "skipped", "reason": "no non-empty turns", "session_id": parsed["session_id"]},
            sys.stdout,
        )
        sys.stdout.write("\n")
        return

    if args.dry_run:
        json.dump({"status": "dry_run", "payloads": payloads}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    results = [ingest_memory_from_chat(payload) for payload in payloads]
    json.dump(
        {
            "status": "done",
            "session_id": parsed["session_id"],
            "part_count": len(payloads),
            "results": results,
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    if any(r["status"] not in ("queued", "queued_locally") for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
