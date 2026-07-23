#!/usr/bin/env python3
"""Claude Code hook entrypoint for PAM memory retrieval."""

from __future__ import annotations

import argparse
import sys


def handle_session_start() -> None:
    from hook_io import emit_additional_context, exit_ok
    from pam_plugin_config import load_config

    config = load_config()
    if config.api_key:
        exit_ok()
    emit_additional_context(
        "PAM memory: API key not found. Configure it with:\n"
        "  /plugin configure pam-memory@pam-memory\n"
        "Or add to ~/.claude/settings.json:\n"
        '  "env": { "PAM_API_KEY": "pam_mkey_<your-key>" }',
        hook_event_name="SessionStart",
    )
    exit_ok()


def handle_user_prompt() -> None:
    from hook_io import exit_ok

    try:
        _handle_user_prompt_impl()
    except Exception:
        exit_ok()


def _handle_user_prompt_impl() -> None:
    from pam import PAMClient
    from pam.exceptions import PAMError

    from format_context import format_memory_context
    from hook_io import emit_additional_context, exit_ok, read_hook_input
    from pam_plugin_config import derive_session_id, load_config

    hook_input = read_hook_input()
    prompt = str(hook_input.get("prompt") or "").strip()
    cwd = str(hook_input.get("cwd") or "")

    config = load_config(cwd=cwd or None)

    if not config.settings.enabled:
        exit_ok()

    if not config.api_key:
        exit_ok()

    if len(prompt) < config.settings.min_prompt_chars:
        exit_ok()

    session_id = derive_session_id(hook_input)

    try:
        client = PAMClient.for_plugin(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        result = client.memory.retrieve(prompt=prompt, session_id=session_id)
    except PAMError:
        exit_ok()

    if not result.ok:
        exit_ok()

    context = format_memory_context(
        content_text=result.content_text,
        sources=result.sources,
        max_chars=config.settings.max_context_chars,
    )
    if not context:
        exit_ok()

    emit_additional_context(context)
    exit_ok()


def main() -> None:
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--event",
            required=True,
            choices=("session_start", "user_prompt"),
        )
        args = parser.parse_args()

        if args.event == "session_start":
            handle_session_start()
        else:
            handle_user_prompt()
    except Exception:
        raise SystemExit(0)


if __name__ == "__main__":
    main()
