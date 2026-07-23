# PAM Memory

Public integrations for [PAM](https://pam.harmix.ai) Memory v1 — read-only company knowledge retrieval for Claude Code and Python.

| Package | Path | Purpose |
|---------|------|---------|
| Claude plugin | [`plugin/`](./plugin/) | Auto-inject memory on every prompt |
| Python SDK | [`sdk/`](./sdk/) | `PAMClient.memory.retrieve()` |

---

## Claude Code plugin

### Requirements

- Claude Code (Claude Pro or higher)
- Network access to `https://api.pam.harmix.ai` (or your custom base URL)
- On Windows: [Git Bash](https://git-scm.com/downloads) or WSL (hooks run via `bash`)
- Full `pam_mkey_<key>` from PAM (see below)

### Get your API key

1. Sign in to [PAM](https://pam.harmix.ai)
2. Open **For Developers**
3. Generate a Memory MCP / developer key
4. Copy the **full** key as shown: `pam_mkey_<your-key>` (one token, **no dot**)

Your company sources (email, Drive, Slack, etc.) must be connected and synced in PAM before retrieval returns useful context.

### Install

**From GitHub (recommended):**

In Claude Code or **Settings → Plugins → Add marketplace**, use:

```text
Harmix/pam-memory
```

Then install:

```text
/plugin install pam-memory@pam-memory
```

### Configure

Run in Claude Code:

```text
/plugin configure pam-memory@pam-memory
```

| Setting | Value |
|---------|-------|
| **PAM Memory API Key** | Your full `pam_mkey_<key>` from PAM → For Developers |
| **PAM Agent API URL** | Optional. Default: `https://api.pam.harmix.ai` |

### Verify

1. Start a **new** Claude Code session (hooks load on session start).
2. Ask a question of at least **10 characters** about your company knowledge, e.g. *“What is our deployment process for production?”*
3. On success, Claude’s reply should reflect company-specific details (names, processes, docs) that were not in your prompt alone.
4. Memory is injected as context before the model responds — you may not see a separate “memory” UI panel.

If nothing company-specific appears, see [Troubleshooting](#troubleshooting). Claude still works normally either way: the plugin **fails open** (no hard errors on missing key, quota, timeout, or empty results).

See [`plugin/README.md`](./plugin/README.md) for hook details and local testing.

---

## Python SDK

```bash
pip install git+https://github.com/Harmix/pam-memory.git#subdirectory=sdk
```

```bash
export PAM_API_KEY="pam_mkey_<your-key>"
export PAM_BASE_URL="https://api.pam.harmix.ai"   # optional
```

```python
from pam import PAMClient

client = PAMClient()
result = client.memory.retrieve(
    prompt="What is our deployment process?",
    session_id="my-session",
)

if result.ok:
    print(result.content_text)
else:
    print(result.error_code, result.error_message)
```

See [`sdk/README.md`](./sdk/README.md) and [`sdk/examples/smoke_retrieve.py`](./sdk/examples/smoke_retrieve.py).

---

## API

Both packages call:

```http
POST /v1/memory/retrieve
Authorization: Bearer pam_mkey_<your-key>
```

Default base URL: `https://api.pam.harmix.ai`

---

## Troubleshooting

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| No company context in replies | Missing/invalid key, prompt under 10 chars, or memory not synced | Re-run `/plugin configure pam-memory@pam-memory`; ask a longer question; confirm sources are synced in PAM |
| Claude works with no errors and no memory | Key not configured | Run `/plugin configure pam-memory@pam-memory` and enter your key |
| Marketplace sync fails | Repo private, wrong URL, or missing marketplace file | Use `Harmix/pam-memory`; repo must be public with `.claude-plugin/marketplace.json` |
| Hook / bash errors on Windows | No bash on PATH | Install Git Bash or use WSL |
| `quota_exceeded` / empty retrieve | Plan limit or memory not ready | Check **For Developers** usage; wait for initial sync to finish |
| Works in SDK, not in Claude | Key not configured for plugin | Run `/plugin configure pam-memory@pam-memory` and enter your `pam_mkey_*` key |

---

## Maintainers

After changing the SDK, sync the vendored copy used by the plugin:

```bash
./scripts/sync_vendor.sh
# Windows: .\scripts\sync_vendor.ps1
```

Run tests (use `python -m` so pip/pytest match the same interpreter):

```bash
cd sdk && python -m pip install -e ".[dev]" && python -m pytest -q
cd ../plugin && uv sync && uv run pytest -q
```

---

## License & support

MIT — see [LICENSE](./LICENSE).

Questions or issues: [support@harmix.ai](mailto:support@harmix.ai) · [pam.harmix.ai](https://pam.harmix.ai)
