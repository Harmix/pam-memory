"""Plugin unit tests."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from format_context import format_memory_context
from hook_io import read_hook_input
from pam import RetrieveMemoryResponse
from pam_plugin_config import PluginConfig, PluginSettings, derive_session_id, load_config, resolve_api_key


class TestFormatContext:
    def test_formats_answer_and_sources(self) -> None:
        block = format_memory_context(
            content_text="Migration uses Next.js.",
            sources=["docs/roadmap.md"],
        )
        assert "PAM company memory" in block
        assert "Migration uses Next.js." in block
        assert "docs/roadmap.md" in block

    def test_truncates_long_context(self) -> None:
        block = format_memory_context(
            content_text="x" * 5000,
            max_chars=100,
        )
        assert len(block) <= 100
        assert block.endswith("...(truncated)")


class TestHookIo:
    def test_read_hook_input_parses_json(self) -> None:
        payload = {"prompt": "hello world from test", "session_id": "s1"}
        with patch("hook_io.sys.stdin", StringIO(json.dumps(payload))):
            assert read_hook_input() == payload

    def test_read_hook_input_fail_open_on_bad_json(self) -> None:
        with patch("hook_io.sys.stdin", StringIO("not-json")):
            assert read_hook_input() == {}


class TestPluginConfig:
    def test_resolve_api_key_prefers_plugin_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAM_API_KEY", "pam_mkey_dev")
        monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_KEY", "pam_mkey_plugin")
        assert resolve_api_key() == "pam_mkey_plugin"

    def test_derive_session_id_uses_hook_value(self) -> None:
        assert derive_session_id({"session_id": "abc"}) == "abc"

    def test_load_config_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PAM_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_API_KEY", raising=False)
        config = load_config()
        assert config.api_key == ""


class TestRetrieveMemoryHook:
    def test_user_prompt_exits_zero_without_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PAM_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_API_KEY", raising=False)

        import retrieve_memory

        payload = {
            "prompt": "What is our migration plan for the dashboard?",
            "session_id": "test-session",
        }
        with patch("hook_io.sys.stdin", StringIO(json.dumps(payload))):
            with pytest.raises(SystemExit) as exc:
                retrieve_memory.handle_user_prompt()
        assert exc.value.code == 0

    def test_user_prompt_injects_context_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAM_API_KEY", "pam_mkey_test.secret")

        import retrieve_memory

        mock_result = RetrieveMemoryResponse(
            status="ok",
            memory_ready=True,
            content_text="Answer text",
            sources=["doc.md"],
        )
        mock_client = MagicMock()
        mock_client.memory.retrieve.return_value = mock_result

        payload = {
            "prompt": "What is our migration plan for the dashboard?",
            "session_id": "test-session",
        }

        stdout = StringIO()
        with patch("hook_io.sys.stdin", StringIO(json.dumps(payload))):
            with patch("hook_io.sys.stdout", stdout):
                with patch(
                    "retrieve_memory.PAMClient.for_plugin",
                    return_value=mock_client,
                ):
                    with pytest.raises(SystemExit) as exc:
                        retrieve_memory.handle_user_prompt()

        assert exc.value.code == 0
        output = json.loads(stdout.getvalue())
        assert "Answer text" in output["hookSpecificOutput"]["additionalContext"]

    def test_session_start_shows_setup_hint_without_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PAM_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_API_KEY", raising=False)

        import retrieve_memory

        stdout = StringIO()
        with patch("hook_io.sys.stdout", stdout):
            with pytest.raises(SystemExit):
                retrieve_memory.handle_session_start()

        output = json.loads(stdout.getvalue())
        assert "pam_mkey" in output["hookSpecificOutput"]["additionalContext"]

    def test_user_prompt_fail_open_on_unexpected_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAM_API_KEY", "pam_mkey_test.secret")

        import retrieve_memory

        payload = {
            "prompt": "What is our migration plan for the dashboard?",
            "session_id": "test-session",
        }
        with patch("hook_io.sys.stdin", StringIO(json.dumps(payload))):
            with patch(
                "retrieve_memory.PAMClient.for_plugin",
                side_effect=RuntimeError("boom"),
            ):
                with pytest.raises(SystemExit) as exc:
                    retrieve_memory.handle_user_prompt()
        assert exc.value.code == 0

    def test_user_prompt_skips_short_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAM_API_KEY", "pam_mkey_test.secret")

        import retrieve_memory

        payload = {"prompt": "short", "session_id": "test-session"}
        with patch("hook_io.sys.stdin", StringIO(json.dumps(payload))):
            with patch("retrieve_memory.PAMClient.for_plugin") as mock_for_plugin:
                with pytest.raises(SystemExit) as exc:
                    retrieve_memory.handle_user_prompt()
        assert exc.value.code == 0
        mock_for_plugin.assert_not_called()

    def test_user_prompt_skips_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAM_API_KEY", "pam_mkey_test.secret")

        import retrieve_memory

        payload = {
            "prompt": "What is our migration plan for the dashboard?",
            "session_id": "test-session",
        }
        disabled_config = PluginConfig(
            api_key="pam_mkey_test.secret",
            base_url="https://api.pam.harmix.ai",
            settings=PluginSettings(enabled=False),
        )
        with patch("hook_io.sys.stdin", StringIO(json.dumps(payload))):
            with patch("retrieve_memory.load_config", return_value=disabled_config):
                with patch("retrieve_memory.PAMClient.for_plugin") as mock_for_plugin:
                    with pytest.raises(SystemExit) as exc:
                        retrieve_memory.handle_user_prompt()
        assert exc.value.code == 0
        mock_for_plugin.assert_not_called()

    def test_user_prompt_fail_open_on_pam_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pam.exceptions import PAMTimeoutError

        monkeypatch.setenv("PAM_API_KEY", "pam_mkey_test.secret")

        import retrieve_memory

        payload = {
            "prompt": "What is our migration plan for the dashboard?",
            "session_id": "test-session",
        }
        mock_client = MagicMock()
        mock_client.memory.retrieve.side_effect = PAMTimeoutError("timed out")

        with patch("hook_io.sys.stdin", StringIO(json.dumps(payload))):
            with patch(
                "retrieve_memory.PAMClient.for_plugin",
                return_value=mock_client,
            ):
                with pytest.raises(SystemExit) as exc:
                    retrieve_memory.handle_user_prompt()
        assert exc.value.code == 0
