#!/usr/bin/env python3
"""Batch-drive extract_raw_transcript's logic over many sessions, in one process.

Earlier versions of the sync-claude-memory skill shelled out to
extract_raw_transcript.py once per session from a bash `while read` loop.
That's fragile in two ways: (1) it forks a fresh python3 interpreter per
session for no benefit, since the parsing/ingest logic is pure Python with no
side effects between sessions, and (2) that shell loop, when run as a
backgrounded multi-line compound command, was observed to hang indefinitely
before ever spawning its first child process -- almost certainly an artifact
of how the harness wires stdin for backgrounded shell invocations, not a bug
in the extraction logic itself (the same extract_raw_transcript.py call
completes in ~1s when run directly/synchronously).

This script sidesteps both problems: it's a single long-running process that
imports parse_transcript/build_payloads/ingest_memory_from_chat directly and
loops over sessions in-process. It also prints one flushed JSON line per
session as it finishes, so a caller running this via a backgrounded Bash
call can attach the Monitor tool and relay live progress to the user instead
of going silent for minutes.

Every ingest call used to trigger its own pam-jobs pipeline run (pam-agent-api
published a RunRequested per call, unconditionally), so a batch of N sessions
could fire up to N separate workflow executions. Payloads now carry a
`has_more` flag: every payload except the very last one in the whole batch is
sent with `has_more=True` so pam-agent-api stages it without triggering a
run; only the final payload overall is sent with `has_more=False`, firing
exactly one pipeline run after everything has been staged. Determining which
payload is "last" requires building every session's payloads up front (a
first pass over all sessions) before any ingest calls go out, rather than
streaming parse-then-ingest one session at a time as before.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_raw_transcript import build_payloads, parse_transcript  # noqa: E402
from ingest_memory_from_chat import ingest_memory_from_chat  # noqa: E402


def _load_sessions(args: argparse.Namespace) -> list[dict[str, Any]]:
    raw = Path(args.sessions_file).read_text(encoding="utf-8") if args.sessions_file else sys.stdin.read()
    sessions = json.loads(raw)
    if not isinstance(sessions, list):
        raise SystemExit("sessions input must be a JSON array of {file, client} objects")
    return sessions


def _emit(obj: dict[str, Any]) -> None:
    json.dump(obj, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _parse_all(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """First pass: parse every session and build its ingest payloads.

    Done up front (rather than streamed session-by-session) so the caller
    can find the single last payload across the *whole* batch before any
    ingest calls happen -- that's the only one that should trigger the
    pipeline run.
    """
    entries: list[dict[str, Any]] = []
    for index, session in enumerate(sessions, start=1):
        file_path = session.get("file")
        client = session.get("client", "claude_code")
        try:
            parsed = parse_transcript(Path(file_path))
            payloads = build_payloads(parsed, client=client)
            entries.append(
                {
                    "index": index,
                    "file_path": file_path,
                    "parsed": parsed,
                    "payloads": payloads,
                    "error": None,
                }
            )
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - one bad session must not kill the batch
            # parse_transcript raises SystemExit (not Exception) on unreadable
            # files, so that must be caught here too -- otherwise a single
            # missing/unreadable transcript would abort the whole batch,
            # which is strictly worse than the old one-subprocess-per-session
            # approach this script replaced.
            entries.append(
                {
                    "index": index,
                    "file_path": file_path,
                    "parsed": None,
                    "payloads": None,
                    "error": str(exc),
                }
            )
    return entries


def run_batch(sessions: list[dict[str, Any]]) -> dict[str, int]:
    tally = {"queued": 0, "queued_locally": 0, "skipped": 0, "errors": 0}
    total = len(sessions)

    entries = _parse_all(sessions)

    last_payload_key: tuple[int, int] | None = None
    for entry in entries:
        if entry["payloads"]:
            last_payload_key = (entry["index"], len(entry["payloads"]) - 1)

    for entry in entries:
        index = entry["index"]

        if entry["error"] is not None:
            tally["errors"] += 1
            _emit(
                {
                    "index": index,
                    "total": total,
                    "status": "error",
                    "file": entry["file_path"],
                    "error": entry["error"],
                }
            )
            continue

        parsed = entry["parsed"]
        payloads = entry["payloads"]
        if not payloads:
            tally["skipped"] += 1
            _emit(
                {
                    "index": index,
                    "total": total,
                    "status": "skipped",
                    "reason": "no non-empty turns",
                    "session_id": parsed["session_id"],
                }
            )
            continue

        results = []
        for part_index, payload in enumerate(payloads):
            payload["has_more"] = (index, part_index) != last_payload_key
            results.append(ingest_memory_from_chat(payload))
        for r in results:
            if r["status"] == "queued":
                tally["queued"] += 1
            elif r["status"] == "queued_locally":
                tally["queued_locally"] += 1
            else:
                tally["errors"] += 1

        _emit(
            {
                "index": index,
                "total": total,
                "status": "done",
                "session_id": parsed["session_id"],
                "part_count": len(payloads),
                "results": results,
            }
        )

    _emit({"status": "summary", "total": total, **tally})
    return tally


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions-file",
        default=None,
        help="Path to a JSON file containing an array of {file, client} objects. Reads stdin if omitted.",
    )
    args = parser.parse_args()

    sessions = _load_sessions(args)
    run_batch(sessions)


if __name__ == "__main__":
    main()