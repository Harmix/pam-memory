---
name: sync-claude-memory
description: Reads the user's local Claude Code and Claude Desktop Cowork session transcripts, summarizes each into a structured memory item, and queues them for PAM memory. Always discloses which local files it will read and asks for explicit confirmation before reading any transcript content. Triggers on "/sync-claude-memory", "sync my claude memory into pam", "import my claude chat history".
argument-hint: [--since YYYY-MM-DD] [--project <name>]
allowed-tools: Bash, Read, Agent, AskUserQuestion
---

# sync-claude-memory

Turn the user's local Claude session history — both the Claude Code CLI and
Claude Desktop's Cowork mode — into PAM memory items. Cowork runs the same
local CLI engine under the hood, so its transcripts land in the identical
`~/.claude/projects/**/*.jsonl` format; there's no separate Cowork parser.
Plain Claude Desktop **Chat** (non-Cowork) has no local transcript at all and
is out of scope — there's nothing on disk to read.

This is a two-phase flow: **disclose scope and get explicit confirmation
first**, then read transcript content only for what was confirmed. Never read
transcript message content before the user has confirmed.

Step 3 below sends each memory item to PAM via `ingest_memory_from_chat.py`,
which calls PAM's real ingest API using the configured `pam_mkey_*` key. If
that call fails for any reason (no key configured, network error, PAM
rejects it) the script automatically falls back to queuing the item locally
at `~/.pam/sync_queue/claude_code.jsonl` instead of losing it — the script's
own JSON output says which happened (`"status": "queued"` = sent to PAM,
`"status": "queued_locally"` = fallback). Report whichever actually
happened; don't assume success.

## 1. Discover scope (metadata only, no message content)

Run, forwarding `$ARGUMENTS` as flags if present:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/list_claude_sessions.py" $ARGUMENTS
```

This only reads session metadata (session id, project cwd, timestamps, message
count, and a short snippet of the first user message used as a title) — it
does not extract full transcript content. It also checks Claude Desktop's own
Cowork session index (`~/Library/Application Support/Claude/claude-code-sessions/**/local_*.json`
on macOS — title and session-id linkage only, never transcript content) to
tag which sessions came from Cowork and to prefer Desktop's own session title
over the derived snippet. Parse the JSON result: `count`, `clients` (a
`{"claude_code": N, "cowork": M}` breakdown), `projects`, `date_range`,
`sessions` (each tagged `"client": "claude_code" | "cowork"`).

## 2. Disclose, then confirm

Before doing anything else, tell the user plainly, in your own words:

- This reads local files under `~/.claude/projects/**/*.jsonl` — full session
  transcripts from both Claude Code and Claude Desktop's Cowork mode, which
  can include pasted file contents, tool output, and anything typed in those
  sessions. It also peeks at Desktop's lightweight Cowork session index for
  titles (no transcript content there).
- Report the scope from step 1: how many sessions (broken down by
  Claude Code vs Cowork), across how many projects, and the date range found.
- What happens to the data: each session gets read and condensed into a short
  structured summary (not a verbatim copy), redacted of any secrets/tokens,
  then sent to PAM over your configured API key. If that send fails for any
  reason it's queued locally instead (`~/.pam/sync_queue/claude_code.jsonl`)
  rather than lost — you'll get an accurate count of which happened at the
  end, not just an assumption of success.

Then use `AskUserQuestion` to get explicit confirmation with at least these
options: proceed with everything found, narrow the scope (ask what to narrow
by — date or project — and re-run step 1), or cancel. If the user cancels,
stop here. Do not read any transcript file content beyond what step 1 already
read.

## 3. Batch and extract in parallel

Only after confirmation. Group the confirmed `sessions` list into batches of
roughly 5–10 sessions each (e.g. by calendar week, or straight chunks if that's
simpler) so no single subagent has to hold too many transcripts in context.

Tell the user up front how many batches you're launching (e.g. "Processing
23 sessions in 4 parallel batches…") so they know what "done" will look like.

Batches can freely mix Claude Code and Cowork sessions — they're the same
file format, nothing to branch on when reading.

For each batch, launch an `Agent` (a fresh general-purpose agent, not a fork —
these don't need this conversation's context) in parallel with the others
(one message, multiple Agent calls). Give each agent a self-contained prompt
along these lines:

> You're extracting durable memory items from Claude session transcripts.
> For each of these files: [list of absolute paths, each with its `client`
> tag ("claude_code" or "cowork") from step 1]
>
> 1. Read the file (JSONL — one JSON object per line; message text lives at
>    `.message.content`, which is either a string or a list of content blocks
>    with `.type == "text"`).
> 2. Decide if the session has anything worth remembering long-term: durable
>    facts, decisions, preferences, or context about the user/their projects.
>    Skip sessions that are pure one-off debugging, exploratory dead ends, or
>    have nothing reusable — don't force output.
> 3. For sessions worth keeping, write ONE memory item as JSON matching this
>    schema (see below), save it to a temp file, then run:
>    `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ingest_memory_from_chat.py" --file <tmp-path>`
> 4. Never copy secrets, API keys, tokens, or credentials into the summary or
>    facts, even if they appear verbatim in the transcript — redact them.
> 5. Don't attempt a verbatim transcript reproduction — extract meaning, not
>    text. `ingest_memory_from_chat.py`'s JSON output tells you what actually
>    happened per item: `"status": "queued"` means it reached PAM,
>    `"status": "queued_locally"` means it fell back to a local queue file
>    (report the `"reason"` field for these), `"status": "error"` means it
>    was rejected outright. Report back: how many sessions you processed, how
>    many were sent to PAM vs queued locally vs skipped, and why.

### Memory item schema

```json
{
  "source": "claude_code",
  "client": "claude_code | cowork",
  "session_id": "string, required — from the transcript",
  "project_path": "string — cwd of the session, if known",
  "started_at": "ISO 8601 timestamp",
  "ended_at": "ISO 8601 timestamp",
  "title": "short human-readable title",
  "summary": "string, required — 2-5 sentences on what happened/was decided",
  "facts": ["short standalone fact or decision", "..."],
  "topics": ["short keyword tags"],
  "confidence": "high | medium | low"
}
```

`source` is always `"claude_code"` — it names the pam-jobs data source, shared
by both clients. `client` is which local app actually produced the session;
carry over the tag from step 1's `sessions` list.

Only `session_id` and `summary` are required by `ingest_memory_from_chat.py`;
everything else is best-effort.

## 4. Progress feedback between batches

Batches run concurrently, but their completions arrive one at a time — don't
go silent until every batch is done. As each batch's agent reports back, post
one short line before waiting on the rest, e.g.:

> Batch 2/4 done — 7 sessions read, 5 sent to PAM, 0 queued locally, 2
> skipped (no durable info).

Keep it to one line per batch, no extra commentary. If any items fell back
to `"queued_locally"` or a batch's agent errored outright, say so in that
line instead of silently dropping it (e.g. "Batch 3/4 done — 1 of 6 queued
locally: no PAM API key configured"). Once every batch has reported, move to
step 5 for the final summary.

## 5. Report results

Tally what each batch actually reported — don't re-derive counts by
re-reading files, the batches already told you. Sum across all batches: total
sessions found, how many were sent to PAM (`"queued"`), how many fell back to
the local queue (`"queued_locally"`), and how many were skipped (no durable
info) or errored.

If any items fell back locally, also run this so you can tell the user
exactly where they landed:

```
python3 -c "import json,pathlib; p=pathlib.Path.home()/'.pam'/'sync_queue'/'claude_code.jsonl'; print(sum(1 for _ in p.open()) if p.is_file() else 0)"
```

Tell the user the final tally plainly: how many sessions were found, how many
actually reached PAM, how many are staged locally (and why, and that they'll
need a re-run once the underlying issue — e.g. missing API key — is fixed),
and how many were skipped as not worth keeping. Don't report success for
anything that only made it to the local fallback queue.
