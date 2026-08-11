#!/usr/bin/env python3
"""Push one extracted memory item to PAM, with a local-queue fallback.

POSTs to PAM's REST API via curl first (no Python HTTP library needed --
just python3 + curl). If the API key isn't configured, curl isn't on PATH,
or the network call fails or comes back as a business error, falls back to
queuing locally at ~/.pam/sync_queue/claude_code.jsonl (validates, dedupes
by session_id) so no extracted memory item is silently lost. The caller can
tell which path was taken from the returned "status": "queued" (sent to
PAM) vs "queued_locally" (fallback).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pam_plugin_config

INGEST_PATH = "/v1/memory/ingest"
REQUEST_TIMEOUT_SECONDS = 90
CONNECT_TIMEOUT_SECONDS = 5

QUEUE_DIR = Path.home() / ".pam" / "sync_queue"
QUEUE_FILE = QUEUE_DIR / "claude_code.jsonl"
REQUIRED_FIELDS = ("session_id", "turns")
ITEM_FIELDS = (
    "session_id",
    "turns",
    "source",
    "client",
    "project_path",
    "started_at",
    "ended_at",
    "title",
    "part_index",
    "part_count",
    "has_more",
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


def _build_ingest_body(item: dict[str, Any]) -> dict[str, Any]:
    """Mirror IngestMemoryItem.model_dump(exclude_none=True) without pydantic."""
    body: dict[str, Any] = {}
    for field in ITEM_FIELDS:
        value = item.get(field)
        if value is None:
            continue
        if field == "turns":
            value = [
                {k: v for k, v in turn.items() if v is not None} for turn in value
            ]
        body[field] = value
    return body


def _send_to_pam(item: dict[str, Any]) -> dict[str, Any]:
    """POST the item to PAM's REST API via curl. Returns a result dict on
    success, raises on failure.

    Shells out to curl instead of depending on an HTTP library (httpx) so
    this never needs a pip install / venv bootstrap -- only a plain python3
    and curl, both of which are safe to assume on a dev machine.
    """
    api_key = pam_plugin_config.resolve_api_key()
    if not api_key:
        raise RuntimeError("no PAM API key configured")
    base_url = pam_plugin_config.resolve_base_url().rstrip("/")
    url = f"{base_url}{INGEST_PATH}"

    payload = json.dumps(_build_ingest_body(item)).encode("utf-8")

    # Header goes in a curl config file (not argv/env visible to `ps`) so the
    # API key never shows up in the process list.
    fd, cfg_path = tempfile.mkstemp(prefix="pam-curl-", suffix=".cfg")
    try:
        os.chmod(cfg_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as cfg:
            cfg.write(f'header = "Authorization: Bearer {api_key}"\n')
            cfg.write('header = "Content-Type: application/json"\n')

        try:
            result = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "-K",
                    cfg_path,
                    "--max-time",
                    str(REQUEST_TIMEOUT_SECONDS),
                    "--connect-timeout",
                    str(CONNECT_TIMEOUT_SECONDS),
                    "-w",
                    "\n%{http_code}",
                    "--data-binary",
                    "@-",
                    url,
                ],
                input=payload,
                capture_output=True,
                timeout=REQUEST_TIMEOUT_SECONDS + 5,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("curl not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("PAM memory ingest timed out") from exc
    finally:
        os.unlink(cfg_path)

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"curl failed (exit {result.returncode}): {stderr[:200]}")

    stdout = result.stdout.decode("utf-8", errors="replace")
    response_body, _, status_code_str = stdout.rpartition("\n")
    status_code = int(status_code_str) if status_code_str.isdigit() else 0

    if status_code == 401:
        raise RuntimeError("Invalid or missing PAM API key")
    if status_code >= 400:
        raise RuntimeError(f"PAM request failed: {status_code} {response_body[:200]}")

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from PAM server: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("expected JSON object from PAM server")

    item_id = data.get("item_id")
    if data.get("status") != "ok" or not item_id:
        raise RuntimeError(
            f"ingest rejected: {data.get('error_code') or 'unknown'} "
            f"{data.get('error_message') or ''}".strip()
        )

    return {"status": "queued", "item_id": item_id}


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
