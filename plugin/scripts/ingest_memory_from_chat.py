#!/usr/bin/env python3
"""Push one extracted memory item to PAM, with a local-queue fallback.

Tries PAMClient.memory.ingest(...) first. If the API key isn't configured,
the SDK isn't importable, or the network call fails or comes back as a
business error, falls back to queuing locally at
~/.pam/sync_queue/claude_code.jsonl (validates, dedupes by session_id) so no
extracted memory item is silently lost. The caller can tell which path was
taken from the returned "status": "queued" (sent to PAM) vs
"queued_locally" (fallback).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

try:
    from pam import IngestMemoryItem, PAMClient
except ImportError:
    IngestMemoryItem = None  # type: ignore[assignment,misc]
    PAMClient = None  # type: ignore[assignment,misc]

import pam_plugin_config

QUEUE_DIR = Path.home() / ".pam" / "sync_queue"
QUEUE_FILE = QUEUE_DIR / "claude_code.jsonl"
REQUIRED_FIELDS = ("session_id", "summary")
ITEM_FIELDS = (
    "session_id",
    "summary",
    "source",
    "client",
    "project_path",
    "started_at",
    "ended_at",
    "title",
    "facts",
    "topics",
    "confidence",
)


def _load_item(args: argparse.Namespace) -> dict[str, Any]:
    if args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    return json.loads(raw)


def _validate(item: dict[str, Any]) -> str | None:
    if not isinstance(item, dict):
        return "memory item must be a JSON object"
    for field in REQUIRED_FIELDS:
        if not str(item.get(field) or "").strip():
            return f"missing required field: {field}"
    return None


def _load_existing(queue_file: Path) -> list[dict[str, Any]]:
    if not queue_file.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in queue_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _queue_locally(
    item: dict[str, Any], *, queue_file: Path, reason: str
) -> dict[str, Any]:
    record = dict(item)
    record.setdefault("source", "claude_code")
    record["item_id"] = str(uuid.uuid4())
    record["ingested_at"] = datetime.now(timezone.utc).isoformat()

    session_id = record["session_id"]
    existing = [r for r in _load_existing(queue_file) if r.get("session_id") != session_id]
    existing.append(record)

    queue_file.parent.mkdir(parents=True, exist_ok=True)
    with queue_file.open("w", encoding="utf-8") as f:
        for r in existing:
            f.write(json.dumps(r, ensure_ascii=False))
            f.write("\n")

    return {
        "status": "queued_locally",
        "reason": reason,
        "item_id": record["item_id"],
        "queue_path": str(queue_file),
        "queue_count": len(existing),
    }


def _send_to_pam(item: dict[str, Any]) -> dict[str, Any]:
    """Try the real ingest call. Returns a result dict on success, raises on failure."""
    if PAMClient is None or IngestMemoryItem is None:
        raise RuntimeError("pam SDK unavailable")

    api_key = pam_plugin_config.resolve_api_key()
    if not api_key:
        raise RuntimeError("no PAM API key configured")
    base_url = pam_plugin_config.resolve_base_url()

    ingest_item = IngestMemoryItem(**{k: item[k] for k in ITEM_FIELDS if k in item})
    client = PAMClient.for_plugin(api_key=api_key, base_url=base_url)
    response = client.memory.ingest(item=ingest_item)

    if not response.ok:
        raise RuntimeError(
            f"ingest rejected: {response.error_code or 'unknown'} "
            f"{response.error_message or ''}".strip()
        )

    return {"status": "queued", "item_id": response.item_id}


def ingest_memory_from_chat(
    item: dict[str, Any], *, queue_file: Path = QUEUE_FILE
) -> dict[str, Any]:
    error = _validate(item)
    if error:
        return {"status": "error", "error": error}

    try:
        return _send_to_pam(item)
    except Exception as exc:  # noqa: BLE001 - any failure here falls back, never raises
        return _queue_locally(item, queue_file=queue_file, reason=str(exc))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        default=None,
        help="Path to a JSON file with the memory item. Reads stdin if omitted.",
    )
    args = parser.parse_args()

    try:
        item = _load_item(args)
    except (OSError, json.JSONDecodeError) as exc:
        json.dump({"status": "error", "error": f"invalid input: {exc}"}, sys.stdout)
        sys.stdout.write("\n")
        raise SystemExit(1)

    result = ingest_memory_from_chat(item)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    if result["status"] not in ("queued", "queued_locally"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
