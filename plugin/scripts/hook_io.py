"""Claude hook stdin/stdout helpers."""

from __future__ import annotations

import json
import sys
from typing import Any


def read_hook_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def emit_additional_context(context: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "additionalContext": context,
        }
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def exit_ok() -> None:
    raise SystemExit(0)
