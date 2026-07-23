#!/usr/bin/env python3
"""Example: retrieve company memory via the PAM Python SDK.

Usage:
  # Install SDK first:
  pip install -e .

  export PAM_API_KEY="pam_mkey_<your-key>"   # full key from For Developers — no dot
  export PAM_BASE_URL="https://api.pam.harmix.ai"   # optional; this is the default
  python examples/smoke_retrieve.py

  # Plugin-mode timeouts (same as Claude hook):
  python examples/smoke_retrieve.py --plugin-mode
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from pam import PAMClient
from pam.exceptions import PAMError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Example: PAM memory.retrieve")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("PAM_API_KEY", "").strip(),
        help="pam_mkey_* (or set PAM_API_KEY)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("PAM_BASE_URL", "http://localhost:8001").strip(),
        help="API base URL (or set PAM_BASE_URL)",
    )
    parser.add_argument(
        "--prompt",
        default="What decisions did we make about the dashboard migration?",
        help="Retrieve prompt (min ~10 chars for plugin; SDK has no min)",
    )
    parser.add_argument(
        "--session-id",
        default="smoke-test-1",
        help="Optional session id for audit/triage",
    )
    parser.add_argument(
        "--plugin-mode",
        action="store_true",
        help="Use PAMClient.for_plugin() strict timeouts (5s read)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not args.api_key:
        print(
            "Error: missing API key. Pass --api-key or set PAM_API_KEY (pam_mkey_*).\n"
            "Get one from PAM dashboard → For Developers.",
            file=sys.stderr,
        )
        return 1

    print(f"Base URL:   {args.base_url}")
    print(f"Prompt:     {args.prompt[:80]}{'...' if len(args.prompt) > 80 else ''}")
    print(f"Session:    {args.session_id}")
    print(f"Mode:       {'plugin (5s timeout)' if args.plugin_mode else 'default (30s)'}")
    print()

    try:
        if args.plugin_mode:
            client = PAMClient.for_plugin(
                api_key=args.api_key,
                base_url=args.base_url,
            )
        else:
            client = PAMClient(api_key=args.api_key, base_url=args.base_url)

        result = client.memory.retrieve(
            prompt=args.prompt,
            session_id=args.session_id,
        )
    except PAMError as exc:
        print(f"Transport error: {exc}", file=sys.stderr)
        if getattr(exc, "status_code", None):
            print(f"HTTP status: {exc.status_code}", file=sys.stderr)
        return 2

    payload = {
        "ok": result.ok,
        "status": result.status,
        "memory_ready": result.memory_ready,
        "request_id": result.request_id,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "content_text": result.content_text,
        "sources": result.sources,
    }
    print(json.dumps(payload, indent=2))

    if result.ok:
        print("\nSuccess — memory retrieved.")
        return 0

    print("\nBusiness error (HTTP 200) — no injection in plugin.", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
