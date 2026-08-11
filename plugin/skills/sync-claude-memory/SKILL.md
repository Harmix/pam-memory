---
name: sync-claude-memory
description: Reads the user's local Claude Code and Claude Desktop Cowork session transcripts, mechanically parses and redacts each into raw turns, and sends them to PAM for server-side extraction. Always discloses which local files it will read and asks for explicit confirmation before reading any transcript content. Triggers on "/sync-claude-memory", "sync my claude memory into pam", "import my claude chat history".
argument-hint: [--since YYYY-MM-DD] [--project <name>]
allowed-tools: Bash, AskUserQuestion
---

# sync-claude-memory

Turn the user's local Claude session history — both the Claude Code CLI and
Claude Desktop's Cowork mode — into PAM memory. Cowork runs the same
local CLI engine under the hood, so its transcripts land in the identical
`~/.claude/projects/**/*.jsonl` format; there's no separate Cowork parser.
Plain Claude Desktop **Chat** (non-Cowork) has no local transcript at all and
is out of scope — there's nothing on disk to read.

This is a two-phase flow: **disclose scope and get explicit confirmation
first**, then read transcript content only for what was confirmed. Never read
transcript message content before the user has confirmed.

**No LLM judgment happens client-side.** Step 3 below runs a purely
mechanical script (`extract_raw_transcript.py`) — no `Agent` subagents, no
model calls of any kind — to parse each transcript into raw `{role, text,
timestamp}` turns and apply regex-based secret redaction, then pushes those
turns to PAM via `ingest_memory_from_chat.py`. Deciding what's durable,
summarizing, and extracting facts all happen server-side in PAM's pam-jobs
pipeline (the same Gemini extract+validate pass every other memory source
goes through) — this command never spends the user's own model quota on
PAM's ingest work.

If that push fails for any reason (no key configured, network error, PAM
rejects it) `ingest_memory_from_chat.py` automatically falls back to queuing
the item locally at `~/.pam/sync_queue/claude_code.jsonl` instead of losing
it — its JSON output says which happened (`"status": "queued"` = sent to
PAM, `"status": "queued_locally"` = fallback). Report whichever actually
happened; don't assume success.

## 1. Discover scope (metadata only, no message content)

Run, forwarding `$ARGUMENTS` as flags if present:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/list_claude_sessions.py" $ARGUMENTS
```

This step, and every script in this skill, only needs a plain `python3` —
no pip install or venv bootstrap. Sending data to PAM (step 3) shells out to
`curl` rather than a Python HTTP library, so there's no third-party
dependency to install on the user's machine.

This only reads session metadata (session id, project cwd, timestamps, message
count, and a short snippet of the first user message used as a title) — it
does not extract full transcript content. It also checks Claude Desktop's own
Cowork session index (`~/Library/Application Support/Claude/claude-code-sessions/**/local_*.json`
on macOS — title and session-id linkage only, never transcript content) to
tag which sessions came from Cowork and to prefer Desktop's own session title
over the derived snippet. Parse the JSON result: `count`, `clients` (a
`{"claude_code": N, "cowork": M}` breakdown), `projects`, `date_range`,
`sessions` (each tagged `"client": "claude_code" | "cowork"`, with a `"file"`
absolute path).

## 2. Disclose, then confirm

Before doing anything else, tell the user plainly, in your own words:

- This reads local files under `~/.claude/projects/**/*.jsonl` — full session
  transcripts from both Claude Code and Claude Desktop's Cowork mode, which
  can include pasted file contents, tool output, and anything typed in those
  sessions. It also peeks at Desktop's lightweight Cowork session index for
  titles (no transcript content there).
- Report the scope from step 1: how many sessions (broken down by
  Claude Code vs Cowork), across how many projects, and the date range found.
- What happens to the data: each session's user/assistant text turns are
  parsed out mechanically (tool calls, tool output, and images are dropped)
  and run through regex-based secret redaction — no summarization or "is
  this worth keeping" judgment happens on your machine. The raw, redacted
  turns are sent to PAM over your configured API key, and PAM extracts facts
  from them server-side. If that send fails for any reason it's queued
  locally instead (`~/.pam/sync_queue/claude_code.jsonl`) rather than lost —
  you'll get an accurate count of which happened at the end, not just an
  assumption of success.

Then use `AskUserQuestion` to get explicit confirmation with at least these
options: proceed with everything found, narrow the scope (ask what to narrow
by — date or project — and re-run step 1), or cancel. If the user cancels,
stop here. Do not read any transcript file content beyond what step 1 already
read.

## 3. Parse and push — mechanical, no subagents

Only after confirmation. For every confirmed session, run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/extract_raw_transcript.py" --file <path> --client <client>
```

using the `"file"` and `"client"` values from that session's entry in step
1's `sessions` list. This is a single Python process per session with zero
model calls — do not spawn an `Agent` for this, and do not read transcript
content yourself. Batch the calls into one Bash invocation (a shell loop
over the confirmed sessions) rather than one tool call per session, to keep
round-trips down for large scopes.

The user's default shell may be bash or zsh (check the environment info you
were given). Bash-only array syntax (`declare -a`, `"${!FILES[@]}"`) is
**not** portable to zsh — under zsh it fails silently (arrays end up empty,
the loop body never runs, and `wc -l` reports 0) rather than erroring, which
looks like success while actually syncing nothing. Use a heredoc fed into a
`while read` loop instead — this works identically in bash and zsh:

```bash
while IFS='|' read -r f c; do
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/extract_raw_transcript.py" \
    --file "$f" --client "$c"
done <<'EOF'
<file1>|<client1>
<file2>|<client2>
EOF
```

Each line of output is one JSON object:

- `{"status": "skipped", "reason": "no non-empty turns", "session_id": ...}`
  — mechanical skip, not a durability judgment (e.g. a session with only
  tool calls and no prose).
- `{"status": "done", "session_id": ..., "part_count": N, "results": [...]}`
  — one `ingest_memory_from_chat.py` result per part; each part's own
  `"status"` is `"queued"` (reached PAM) or `"queued_locally"` (fallback,
  check `"reason"`).

Large sessions are split into multiple parts automatically (same
`session_id`, ordered `part_index`) — PAM merges parts by `session_id`
server-side before extraction, so this is transparent; just tally every
part's status.

## 4. Report results

Tally the JSON lines from step 3 — don't re-read transcripts to re-derive
counts, the script output already has everything. Sum: total sessions
processed, how many were sent to PAM (`"queued"`), how many fell back to the
local queue (`"queued_locally"`), and how many were mechanically skipped (no
text content).

If any items fell back locally, also run this so you can tell the user
exactly where they landed:

```
python3 -c "import json,pathlib; p=pathlib.Path.home()/'.pam'/'sync_queue'/'claude_code.jsonl'; print(sum(1 for _ in p.open()) if p.is_file() else 0)"
```

Tell the user the final tally plainly: how many sessions were found, how many
actually reached PAM, how many are staged locally (and why, and that they'll
need a re-run once the underlying issue — e.g. missing API key — is fixed),
and how many were skipped as having no text content. Don't report success for
anything that only made it to the local fallback queue. Since extraction now
happens server-side, don't claim any particular fact or summary was produced
— that's PAM's pipeline's job, running asynchronously after this command
finishes.
