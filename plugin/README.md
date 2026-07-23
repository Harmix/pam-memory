# PAM Memory Claude Plugin

Injects read-only company memory into Claude Code before each user prompt via the PAM `retrieve` API.

Part of the [pam-memory](https://github.com/Harmix/pam-memory) repo (`plugin/`).

## Requirements

- Claude Code (Claude Pro or higher)
- Full `pam_mkey_<key>` from PAM → **For Developers** (copy as-is; no dot in the key)

## Install

Add the Harmix marketplace:

```text
/plugin marketplace add Harmix/pam-memory
/plugin install pam-memory@pam-memory
```

Or in **Settings → Plugins → Add marketplace**, enter `Harmix/pam-memory`, then install **pam-memory**.

## Configure

In Claude Code plugin settings:

1. **API key** — paste your full `pam_mkey_<key>` from For Developers
2. **Base URL** (optional) — defaults to `https://api.pam.harmix.ai`

Optional tunables: `~/.pam/settings.json`

```json
{
  "enabled": true,
  "min_prompt_chars": 10,
  "max_context_chars": 4000
}
```

## Verify

Start a Claude Code session with the plugin enabled and ask a question of at least 10 characters about your company knowledge. On success, relevant memory is injected silently before the model responds.

## Local hook test

From this directory:

```bash
export CLAUDE_PLUGIN_ROOT="$PWD"
export CLAUDE_PLUGIN_OPTION_API_KEY="pam_mkey_<your-key>"
export CLAUDE_PLUGIN_OPTION_BASE_URL="https://api.pam.harmix.ai"

cat examples/hook_input_user_prompt.json | bash scripts/run_hook.sh --event user_prompt
```

## Developing

```bash
uv sync
uv run pytest -q
```

After SDK changes in `../sdk`, run `../scripts/sync_vendor.sh` from the repo root.
