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

Run in Claude Code:

```text
/plugin configure pam-memory@pam-memory
```

Enter your `pam_mkey_*` key when prompted. Optionally set a custom base URL (defaults to `https://api.pam.harmix.ai`).

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

## Sync your Claude chat history

`/sync-claude-memory` reads your local session transcripts — both the
Claude Code CLI (`~/.claude/projects/**/*.jsonl`) and Claude Desktop's
Cowork mode (same jsonl format; Desktop's own session index is used to tag
which sessions came from Cowork and to reuse its titles) — and mechanically
parses each session into raw text turns (tool calls, tool output, and images
dropped; secrets/tokens redacted by pattern matching), then sends the raw
turns to PAM using your configured `pam_mkey_*` key. No summarization or
"is this worth keeping" judgment happens on your machine or spends your own
model quota — PAM extracts facts server-side, the same way it processes
every other memory source. Plain Claude Desktop Chat (non-Cowork) has no
local transcript and is out of scope.

It always discloses what it's about to read and asks for confirmation before
reading any transcript content — see `skills/sync-claude-memory/SKILL.md`.

If sending to PAM fails for any reason (missing key, network error, backend
rejects it), the item is queued locally instead at
`~/.pam/sync_queue/claude_code.jsonl` rather than lost — you'll get an
accurate report of what was actually sent vs staged locally, never a false
"success." See
[`docs/sync-claude-memory-implementation-plan.md`](../docs/sync-claude-memory-implementation-plan.md)
for the full cross-repo design (pam-memory, pam-agent-api, pam-jobs, pam-backend-api).

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
