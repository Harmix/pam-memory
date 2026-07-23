"""Resolve plugin config — secrets from Claude, tunables from ~/.pam and repo."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ENV_PLUGIN_API_KEY = "CLAUDE_PLUGIN_OPTION_API_KEY"
ENV_PLUGIN_BASE_URL = "CLAUDE_PLUGIN_OPTION_BASE_URL"
ENV_DEV_API_KEY = "PAM_API_KEY"
ENV_DEV_BASE_URL = "PAM_BASE_URL"

DEFAULT_BASE_URL = "https://api.pam.harmix.ai"
DEFAULT_MIN_PROMPT_CHARS = 10
DEFAULT_MAX_CONTEXT_CHARS = 4000


@dataclass(frozen=True)
class PluginSettings:
    enabled: bool = True
    min_prompt_chars: int = DEFAULT_MIN_PROMPT_CHARS
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS


@dataclass(frozen=True)
class PluginConfig:
    api_key: str
    base_url: str
    settings: PluginSettings


def resolve_api_key() -> str:
    for env_name in (ENV_PLUGIN_API_KEY, ENV_DEV_API_KEY):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return ""


def resolve_base_url() -> str:
    for env_name in (ENV_PLUGIN_BASE_URL, ENV_DEV_BASE_URL):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return DEFAULT_BASE_URL


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_user_settings() -> PluginSettings:
    data = _load_json(Path.home() / ".pam" / "settings.json")
    min_prompt_chars = (
        int(data["min_prompt_chars"])
        if "min_prompt_chars" in data
        else DEFAULT_MIN_PROMPT_CHARS
    )
    max_context_chars = (
        int(data["max_context_chars"])
        if "max_context_chars" in data
        else DEFAULT_MAX_CONTEXT_CHARS
    )
    return PluginSettings(
        enabled=bool(data.get("enabled", True)),
        min_prompt_chars=min_prompt_chars,
        max_context_chars=max_context_chars,
    )


def load_repo_settings(cwd: str | None = None) -> tuple[PluginSettings, dict]:
    root = Path(cwd or os.getcwd())
    for candidate in (
        root / ".pam" / "config.json",
        root / ".claude" / ".pam" / "config.json",
    ):
        data = _load_json(candidate)
        if data:
            min_prompt_chars = (
                int(data["min_prompt_chars"])
                if "min_prompt_chars" in data
                else DEFAULT_MIN_PROMPT_CHARS
            )
            max_context_chars = (
                int(data["max_context_chars"])
                if "max_context_chars" in data
                else DEFAULT_MAX_CONTEXT_CHARS
            )
            return (
                PluginSettings(
                    enabled=bool(data.get("enabled", True)),
                    min_prompt_chars=min_prompt_chars,
                    max_context_chars=max_context_chars,
                ),
                data,
            )
    return PluginSettings(), {}


def load_config(*, cwd: str | None = None) -> PluginConfig:
    user = load_user_settings()
    user_data = _load_json(Path.home() / ".pam" / "settings.json")
    repo, repo_data = load_repo_settings(cwd)
    min_prompt_chars = (
        int(repo_data["min_prompt_chars"])
        if "min_prompt_chars" in repo_data
        else int(user_data["min_prompt_chars"])
        if "min_prompt_chars" in user_data
        else DEFAULT_MIN_PROMPT_CHARS
    )
    settings = PluginSettings(
        enabled=repo.enabled and user.enabled,
        min_prompt_chars=min_prompt_chars,
        max_context_chars=min(repo.max_context_chars, user.max_context_chars),
    )
    return PluginConfig(
        api_key=resolve_api_key(),
        base_url=resolve_base_url(),
        settings=settings,
    )


def derive_session_id(hook_input: dict) -> str:
    session_id = str(hook_input.get("session_id") or "").strip()
    if session_id:
        return session_id
    cwd = str(hook_input.get("cwd") or os.getcwd())
    return f"claude-code:{cwd}"
