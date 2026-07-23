# PAM Python SDK

Hand-written client for PAM Memory v1 (`retrieve` only).

Part of the [pam-memory](https://github.com/Harmix/pam-memory) repo (`sdk/`).

## Install

From this package directory:

```bash
pip install -e .
```

From GitHub:

```bash
pip install git+https://github.com/Harmix/pam-memory.git#subdirectory=sdk
```

When published to PyPI: `pip install pam-sdk` (name TBD).

## Quick start

Get your API key from PAM → **For Developers** → generate Memory MCP key. Copy the **full** key as shown (`pam_mkey_<your-key>` — one token, no dot).

```bash
export PAM_API_KEY="pam_mkey_<your-key>"
export PAM_BASE_URL="https://api.pam.harmix.ai"   # optional; default
```

```python
from pam import PAMClient

client = PAMClient()
result = client.memory.retrieve(
    prompt="What is our migration plan?",
    session_id="my-session",
)

if result.ok:
    print(result.content_text)
```

## Try it

Run the included example (requires `PAM_API_KEY`):

```bash
python examples/smoke_retrieve.py
```

See `examples/smoke_retrieve.py --help` for `--plugin-mode` and other options.

## Plugin mode

Use the same timeouts as the Claude Code plugin (5s read):

```python
client = PAMClient.for_plugin(api_key="pam_mkey_...")
result = client.memory.retrieve(prompt="What is our migration plan?")
```

## Developing

```bash
pip install -e ".[dev]"
pytest -q
```

After SDK changes, sync the plugin vendored copy from the repo root:

```bash
../scripts/sync_vendor.sh
```
