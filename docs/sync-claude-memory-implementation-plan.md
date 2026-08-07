# sync-claude-memory: cross-repo implementation & testing plan

Tracking: PAM-1282. Branch `feat/pam-1282/claude-code-ingest` cut from
`develop` in all four repos (pam-memory, pam-agent-api, pam-jobs,
pam-backend-api).

Turns local Claude Code / Cowork session history into PAM memory. Three repos,
one feature. This plan is the result of reading each repo directly (not
guessing) — file:line references throughout are real, not illustrative.

Architecture recap (see the [visual diagram](https://claude.ai/code/artifact/63f661f2-8974-4df2-a014-219ece851b00)
from earlier review): local agent (Code/Cowork) reads transcripts on disk →
extracts a compressed memory item → crosses the network as one MCP tool call
→ pam-agent-api lands it → pam-jobs' existing pipeline turns it into
knowledge-base content → `retrieve_memory` (already built) serves it back to
any client, including plain Chat.

Status legend: 🟢 built/confirmed · 🟡 planned, investigated · 🔴 open question.

**Update:** all five open questions are closed — four verified directly
against the `harmix-pam-dev` Cloud SQL instance (via
`pam-infrastructure/scripts/connect-to-database.sh dev tunnel`) and live GCP
IAM (`gcloud`), not just repo source; the fifth (quota gate reuse) decided
by the team. See the confirmed details inline in §2/§3 and the closed-out
open-questions list at the bottom. Nothing left to confirm before starting.

---

## 1. pam-memory (this repo)

🟢 **Built today:** `plugin/skills/sync-claude-memory/SKILL.md` (discover →
disclose → confirm → parallel batch-extract → progress-report → final
report), `plugin/scripts/list_claude_sessions.py` (metadata-only scan of
`~/.claude/projects/**/*.jsonl`, plus Cowork session tagging via
`~/Library/Application Support/Claude/claude-code-sessions/**/local_*.json`),
`plugin/scripts/ingest_memory_from_chat.py` (local-only stub: validates,
dedupes by `session_id`, appends to `~/.pam/sync_queue/claude_code.jsonl`,
zero network calls), 15 passing pytest tests.

🟡 **Changes once pam-agent-api ships a real ingest endpoint:**

1. **SDK — add `memory.ingest(...)`.** Mirror `memory.retrieve(...)` exactly:
   - `sdk/src/pam/types/memory.py` (+ vendored copy in
     `plugin/vendor/pam/types/memory.py`, kept in sync via
     `scripts/sync_vendor.sh`): add `IngestMemoryItem` (the memory-item
     schema: `session_id`, `summary` required; `source`, `client`,
     `project_path`, `started_at`, `ended_at`, `title`, `facts`, `topics`,
     `confidence` optional) and `IngestMemoryResponse` (`status`,
     `item_id`, `error_code`, `error_message`, mirroring
     `RetrieveMemoryResponse`'s shape).
   - `sdk/src/pam/resources/memory.py` (+ vendored copy): add
     `MemoryResource.ingest(self, *, item: IngestMemoryItem) -> IngestMemoryResponse`,
     posting to a new `INGEST_PATH` constant in `_constants.py`.
   - **Interface decision:** retrieve already exists as *both* a REST
     endpoint (`POST /v1/memory/retrieve`, what the SDK/plugin actually
     calls) and an MCP tool (`retrieve_memory`, for MCP-native clients that
     talk to PAM's MCP server directly). Recommend the same duality for
     ingest: a `POST /v1/memory/ingest` REST endpoint for the SDK to call
     (simple, no MCP session/handshake needed from a short-lived script),
     backed by the *same* executor as the `ingest_memory_from_chat` MCP
     tool described in §2 — not two separate implementations.

2. **`ingest_memory_from_chat.py` — swap local queue for the real call.**
   Change `ingest_memory_from_chat()` from "append to
   `~/.pam/sync_queue/claude_code.jsonl`" to "call
   `PAMClient.for_plugin(api_key=..., base_url=...).memory.ingest(item=...)`
   using `pam_plugin_config.resolve_api_key()`". Keep the local-queue path
   as an explicit **fallback**, not a silent one: if the API key isn't
   configured or the network call fails, still queue locally and say so in
   the script's JSON output (`{"status": "queued_locally", "reason": ...}`)
   so the orchestrating skill can surface it in the per-batch progress line
   instead of claiming success it didn't have.

3. **`SKILL.md` — drop the "nothing is uploaded" framing** once this ships;
   replace with what actually happens (crosses the network as one MCP/REST
   call per session, redacted client-side first).

4. **Tests:** extend `plugin/tests/test_ingest_memory_from_chat.py` to mock
   `PAMClient.memory.ingest` for success, auth failure, and network-error →
   local-fallback paths (same mocking style already used in
   `test_retrieve_memory_hook.py` for `PAMClient.for_plugin`). Keep the
   existing local-queue tests — they now cover the fallback path, not the
   primary one.

5. **Version + README:** bump `plugin/.claude-plugin/plugin.json` version,
   update `plugin/README.md`'s "Sync your Claude Code chat history
   (experimental)" section to drop "experimental"/"nothing is uploaded" once
   shipped, keep the Cowork-coverage note.

---

## 2. pam-agent-api

🟡 **Existing pattern:** `MemoryMcpService._handle_tools_call`
(`app/services/mcp/memory_mcp_service.py:254-371`) dispatches by
`tool_name`; `retrieve_memory` delegates to `MemoryMcpRetrieveExecutor.execute()`
(`app/services/memory/memory_mcp_retrieve_executor.py:77`). Auth, entitlement,
and quota gating happen once in `handle_request`
(`memory_mcp_service.py:159-167`, `check_mcp_entitlement`) *before*
dispatch — a new tool inherits this for free.

🔴 **Critical finding — there is no DB table for raw memory content.**
`MemoryService` (`app/services/memory/memory_service.py`) manages a
**per-user VM workspace** (shell-based writes via `app.core.utils.system`,
not Postgres). `_init_knowledge_base` (line 240) creates
`.knowledge/sources/{chat,googledrive,notion,gmail,slack,transcript,answers}/`;
`setup_initial_folder_structure` (line 139) creates `unsorted/` — the
landing zone raw source content sits in before pam-jobs' `extract` stage
picks it up. **This is a filesystem write, not a database insert.**

**Plan:**

- New enum value: `MemoryMcpToolName.INGEST_MEMORY_FROM_CHAT =
  "ingest_memory_from_chat"` (`app/core/enums.py:292-299`).
- New tool schema in `memory_tools.py`, appended to `MEMORY_MCP_TOOLS`
  (line 203): `inputSchema` = the memory-item schema (§1) — `session_id` +
  `summary` required, rest optional. Annotations: `readOnlyHint: False`,
  `destructiveHint: False`, `idempotentHint: True` (re-ingesting the same
  `session_id` upserts, matching the plugin's current local-stub dedup
  behavior).
- New dispatch branch in `_handle_tools_call`
  (`memory_mcp_service.py:280`, alongside the `RETRIEVE_MEMORY` check) →
  new `MemoryMcpIngestExecutor` (mirror `MemoryMcpRetrieveExecutor`'s
  shape): validate → resolve `user.workspace_path` → write
  `unsorted/extract/claude_code_items_{run_ts}.json` as `{"items":
  [record]}` → return tool result. **Implemented and verified to match
  §3's confirmed pam-jobs contract exactly** (an earlier draft of this
  bullet said `unsorted/claude_code/{session_id}.json`, which disagreed
  with §3 — that was the stale version; the shape above is what both sides
  actually implement, cross-checked against real code in both repos). No
  new repository class — writes go through the same VM filesystem helpers
  every other source-landing path uses, not `BaseSessionRepository`.
- New parallel REST endpoint `POST /v1/memory/ingest`
  (`app/api/v1/memory/`, mirroring `retrieve_api.py`) calling the same
  executor, for the SDK/plugin to use without an MCP handshake (see §1's
  interface decision).
- **DB migration — 🟢 confirmed owner: `pam-backend-api`.** Verified live
  against the dev DB (`pam.memorysourcetype` currently has 8 values: `gmail`,
  `googledrive`, `outlook`, `notion`, `pam_chats`, `meeting_transcripts`,
  `slack`, `generic`; `pam.alembic_version` = `72cbb599c884`). That revision
  isn't in pam-agent-api or pam-jobs — neither has a migrations directory.
  `pam-backend-api/migrations/versions/` is the real owner, and has added
  enum values this exact way three times already (e.g.
  `2026-02-02-18-37-02_add_pam_chats_to_memorysourcetype_enum_52b7a7f98979_.py`).
  Copy that file's pattern exactly:
  ```python
  def upgrade() -> None:
      op.execute("ALTER TYPE pam.memorysourcetype ADD VALUE IF NOT EXISTS 'claude_code'")
  def downgrade() -> None:
      pass  # Postgres can't drop enum values
  ```
  Set `down_revision` to whatever `pam-backend-api`'s current head is at
  merge time (`72cbb599c884` as of this writing). After a successful
  ingest, call `UserSourceMemoryRepository.upsert_synced_source(user_id,
  MemorySourceType.CLAUDE_CODE)` so readiness/sync-state tracking treats
  `claude_code` like any other source.
- **Trigger the pipeline after landing the file — 🟢 already wired, reuse
  as-is.** pam-agent-api already has `MemoryPipelinePublisher.publish_run_requested(...)`
  (`app/gateways/memory_pipeline_publisher.py:37-83`), DI-registered as
  `services.memory.memory_pipeline_publisher`
  (`app/services/memory/container.py:325`), and already called from
  `integrations_api.py:366` when a user connects a new source — the exact
  same shape this feature needs:
  ```python
  await memory_pipeline_publisher.publish_run_requested(
      user_id=user_id,
      run_type="add_source",
      requested_by="claude_code_sync",
      dedup_key=f"claude_code:{user_id}:{run_ts}",
      source_name="claude_code",
  )
  ```
  **IAM is already granted** — confirmed live on the dev topic
  (`gcloud pubsub topics get-iam-policy pam-dev-memory-pipeline-triggers`):
  `pam-dev-sa-clients-vm@harmix-pam-dev.iam.gserviceaccount.com` (the SA
  pam-agent-api runs as) already has `roles/pubsub.publisher`, granted by
  `agent_api_triggers_publisher` in
  `pam-infrastructure/modules/memory-pipeline/main.tf:509-515`, whose
  comment literally says *"pam-agent-api SA — publisher on triggers
  (connect_source publishes RunRequested)"*. **No new IAM work, no new
  pam-infrastructure changes.**
- **Auth/quota — 🟢 decided: reuse `can_use_mcp_retrieval`.** Reuses the
  same `pam_mkey_*`/OAuth path as retrieve automatically; the quota check
  (`plan_quotas.py:83`) is shared as-is rather than adding a separate
  `can_use_mcp_ingest` gate. Ingest calls count against the same per-plan
  MCP quota as retrieve calls — no new quota dimension to build, track, or
  surface in billing/plan UI. If sync volume ever needs its own limit
  independent of retrieval, that's a later, separate change, not part of
  this feature.

**Tests:** new `tests/test_memory_mcp_ingest.py` next to the existing
`tests/test_memory_mcp.py` (mirror its fixtures/mocking). Cases: happy path
(valid item → 200, file written under `unsorted/claude_code/`, source-memory
row upserted), missing `session_id`/`summary` → `INVALID_ARGUMENTS`, bad/missing
`pam_mkey_*` → 401, quota/entitlement blocked → same shape as the existing
retrieve-blocked test cases, malformed `arguments` → error, re-ingesting the
same `session_id` overwrites rather than duplicates the staged file.

---

## pam-backend-api (schema owner — one migration, nothing else)

Confirmed the sole other repo this feature touches, purely for the enum
value. See §2's DB migration bullet for the exact file to copy and the
`upgrade()`/`downgrade()` body. No application code in this repo changes —
it doesn't run the MCP server, the pipeline, or the plugin. One new file
under `migrations/versions/`, named following this repo's existing
`YYYY-MM-DD-HH-MM-SS_add_x_to_memorysourcetype_enum_<hash>_.py` convention.

**Test:** none needed beyond alembic's own upgrade/downgrade smoke test
(`alembic upgrade head` / `alembic downgrade -1` against a scratch DB) —
this repo has no logic to unit test for a single `ALTER TYPE ADD VALUE`.

---

## 3. pam-jobs

**🟢 Confirmed contract** (no longer an assumption — verified directly in
`pam-jobs/src/storage/file_utils.py:68-70` and cross-checked against every
existing source's ingest call): `extract_staging_dir(workspace_path)` =
`Path(workspace_path) / "unsorted" / "extract"`, and every source writes
`{source}_{noun}_{run_ts}.json` there — `email_emails_raw_{run_ts}.json`
(`ingest.py:93`), `slack_messages_raw_{run_ts}.json` (`ingest.py:584`),
`pam_chats_conversations_{run_ts}.json` (`ingest.py:739`), etc. pam-agent-api's
`ingest_memory_from_chat` tool handler should write to
`unsorted/extract/claude_code_items_{run_ts}.json` — same convention,
nothing new to invent. Because it lands directly in `extract/`, pam-jobs
needs **no download step** for this source, only a normalizer at the
`extract` stage (§3.3).

### 3.1 Register the new source

Two **separately maintained** literal sets both need `"claude_code"` added —
they are not linked today:
- `src/pipeline/ingest.py:27-35` (`ALL_SOURCES`)
- `workflows/memory_pipeline/extract.py:65-73` (`ALL_SOURCES`, a separate copy)

Also add to `SOURCE_TO_KB_DIR` (`extract.py:2233-2242`, e.g.
`"claude_code": "claude_code"`) so `compress_all` writes to
`.knowledge/sources/claude_code/`, and check `SOURCE_SUBDIRS`
(`extract.py:2222-2231`) for other call sites before assuming it's
compress-only.

Unlike the Composio-integration sources (gmail/slack/notion/googledrive/outlook,
gated by `_SLUG_TO_SOURCE` + `get_user_connected_sources`,
`src/storage/db.py:172-210`), `claude_code` has no integration to connect —
it should join `_INTERNAL_SOURCES` (`db.py:183`, currently `{"pam_chats",
"meeting_transcripts"}`) so it's always considered active, same reasoning as
`pam_chats`.

**🔴 Known risk:** three near-duplicate source-name lists were found in this
repo alone (`ingest.py:27`, `extract.py:65`, `extract.py:2222`/`2233`) — more
may exist. A regression test (§3.5) is the proposed stopgap rather than a
refactor, to keep this change additive.

### 3.2 No download step needed

Recommend **not** building an `ingest_claude_code` pull function. Unlike
every other source, `claude_code` is client-push — there's nothing to
download, the data's already at rest in `unsorted/` by the time pam-jobs
runs. If `run_ingest`'s handler dict (`ingest.py:926-934`) requires an entry
per source, map `"claude_code"` to a no-op `StageResult.ok`.

### 3.3 `extract_claude_code` — a normalizer, not a Gemini pass

This is the one new function that matters, and it should be **structurally
different** from every existing `extract_*`. Those all run a Gemini
extract+validate pass over raw content (`_extract_validate_text`, e.g.
`extract.py:1652-1674` in `extract_pam_chats`). Claude-sourced items arrive
**already summarized** — title/summary/facts/topics/confidence, produced by
the client-side subagents in pam-memory's skill — so re-running Gemini
classification here would be redundant cost and could contradict the
client's own extraction.

`extract_claude_code` should: glob `claude_code_items_*.json` from staging
(open with the "no matches → no-op" pattern every extract function uses,
e.g. `extract.py:1588-1591`), map each item into a `ValidationResult`
(`src/models.py:154-166`), and call
`file_utils.save_validation_cache(user.workspace_path, "claude_code",
cache_items)` (same terminal call every extract function makes, e.g.
`extract.py:1697`). No `_run_parallel`/Gemini semaphore needed — this is a
straight mapping, not a Gemini pass.

**🟢 Confirmed `ValidationResult` shape** (`src/models.py:154-166`):
`kept_facts: list[ValidatedFact]`, `dropped_facts`, `conflicts`, `new_facts`
(all empty here — this isn't a validation pass), `summary: str`. Map each
memory item's `facts` entries into `ValidatedFact`
(`src/models.py:111-127`) — `statement` = the fact text, `category`/`level`
picked per item or defaulted, `confidence` = the item's `confidence`
enum mapped to a float (e.g. `high=0.9, medium=0.6, low=0.3`),
`source_turn_id` = the item's `session_id`. Item's `summary` field maps
straight to `ValidationResult.summary`.

Register the glob→dest mapping in `_move_extract_to_sources`'s `simple`
list (`extract.py:2156-2167`): add `("claude_code_items_*.json",
"claude_code")`.

### 3.4 Downstream stages — source-agnostic (needs final confirmation)

`compress_all` (`extract.py:2245+`) iterates `ALL_SOURCES` generically and
reads whatever's in each source's validation cache — nothing there branches
on source name beyond the `SOURCE_TO_KB_DIR` lookup in §3.1.
`deep_analysis.py`, `self_reflection.py`, `graph_generation.py`,
`notify_pull.py` were not read line-by-line in this pass — **recommend the
implementer do one final read of those four files** before merging,
specifically grepping for any hardcoded source-name list separate from
`ALL_SOURCES`, given three were already found in this repo.

### 3.5 On-demand trigger — already exists, reuse it

`source_name` param on `dispatch_run_requested`
(`src/dispatcher/logic.py:399-408`) and `publish_run_requested`
(`src/clients/pubsub.py:61-70`) exists specifically to pin an `add_source`
run to one source — this is exactly what's needed for an on-demand,
single-user, single-source trigger. `run_type="add_source"` is an existing
literal (`src/dispatcher/logic.py:78,114,356,382`). **Nothing new needs
building in pam-jobs.** pam-agent-api's tool handler should, after staging
the file, call:

```python
pubsub.publish_run_requested(
    user_id=user.id,
    run_type="add_source",
    requested_by="claude_code_sync",
    dedup_key=f"claude_code:{user.id}:{run_ts}",
    source_name="claude_code",
)
```

— the same call `_publish_completion_chain` makes
(`workflows/memory_update/run_pipeline_per_user.py:38-52`). This flows
through the existing dispatcher lock/dedup/queue path with zero new
pam-jobs code, **provided** pam-agent-api can reach the same Pub/Sub topic
pam-jobs' dispatcher listens on (🔴 cross-repo question — may need a new IAM
binding, not a pam-jobs code change; see §2).

### 3.6 DB migration

None needed **in pam-jobs** — no `migrations/`/`alembic/` directory exists
in this repo; schema lives elsewhere. `pam.user_source_memories.source` is
read/written as a plain string column here (`db.py:149-167`, `239-258`)
with no local CHECK constraint visible. 🔴 Flag for whoever owns the shared
schema: confirm `source` / `pam.integrations.slug` has no DB-level
enum/CHECK constraint elsewhere that would reject `"claude_code"` — not
verifiable from this repo alone.

### 3.7 Tests

**No `tests/` directory exists in this repo today** — nothing to mirror,
proposing a convention from scratch:
- `tests/pipeline/test_ingest_claude_code.py`: staged file →
  `extract_claude_code` produces the expected `cache_items` shape; empty
  staging → no-op `StageResult.ok`; malformed item → skipped, not fatal.
- `tests/pipeline/test_all_sources_sync.py`: assert `ALL_SOURCES` in
  `ingest.py` and `extract.py` stay equal — a regression guard for the
  duplicate-list problem in §3.1; would have caught drift immediately.
- `tests/dispatcher/test_add_source_claude_code.py`:
  `dispatch_run_requested(source_name="claude_code", run_type="add_source")`
  produces the same outcome shape as existing sources (mock db/pubsub,
  assert nothing source-specific rejects it).
- Regression: run `compress_all` with only `gmail`/`slack` caches populated,
  confirm no `claude_code` KB dir gets created — cheap proof the new source
  is additive, not disruptive.

---

## Open questions — status after verifying against dev DB / GCP

Verified 2026-08-06 against `harmix-pam-dev` (Cloud SQL via
`pam-infrastructure/scripts/connect-to-database.sh dev tunnel`, credentials
from `pam-backend-api/.env`, plus live `gcloud` IAM/Pub/Sub queries — not
just reading source).

1. ~~Which repo owns the `pam.memorysourcetype` Postgres enum migration?~~
   🟢 **Resolved: `pam-backend-api`.** Confirmed via `pam.alembic_version` =
   `72cbb599c884` on the dev DB, traced to
   `pam-backend-api/migrations/versions/`, which has three prior migrations
   adding enum values the same way. (§2)
2. ~~Can pam-agent-api publish to the Pub/Sub topic pam-jobs' dispatcher
   listens on?~~ 🟢 **Resolved: yes, already granted, already used.**
   `pam-dev-sa-clients-vm@...` already has `roles/pubsub.publisher` on
   `pam-dev-memory-pipeline-triggers` (confirmed live via `gcloud pubsub
   topics get-iam-policy`), and `MemoryPipelinePublisher.publish_run_requested`
   already exists in pam-agent-api and is already called for other sources.
   Zero new infra work. (§2, §3.5)
3. ~~Exact file-naming contract between pam-agent-api's write and
   pam-jobs' `extract_claude_code` glob~~ 🟢 **Resolved:**
   `unsorted/extract/claude_code_items_{run_ts}.json`, confirmed against
   `file_utils.extract_staging_dir` and all six existing sources' identical
   naming pattern. (§3, intro)
4. ~~`ValidationResult`'s exact expected fields~~ 🟢 **Resolved:** read
   directly from `pam-jobs/src/models.py:111-166` — see §3.3 for the exact
   field mapping.
5. ~~Reuse `can_use_mcp_retrieval` for the ingest quota gate, or add a
   dedicated `can_use_mcp_ingest`?~~ 🟢 **Decided: reuse
   `can_use_mcp_retrieval` as-is.** (§2)

All five open questions are now closed — nothing left to confirm before
starting.

## Suggested build order

With every open question closed, the two backend repos have no hard
sequencing dependency on each other.

1. **pam-jobs** — `extract_claude_code` + registrations (§3.1-3.4), fully
   testable against a fixture staged file.
2. **pam-agent-api** — enum, tool schema, dispatch, workspace write, REST
   endpoint, and the `publish_run_requested` call (§2) — everything it
   needs (IAM, the publisher gateway) already exists.
3. **pam-backend-api** — the one-file enum migration (§2), can happen any
   time before pam-agent-api's ingest handler ships, independent of 1/2.
4. **pam-memory** — swap the local stub for the real SDK call (§1), ship as
   a plugin version bump.
5. **End-to-end manual QA:** run `/sync-claude-memory` for real, confirm a
   later `retrieve_memory` call in a *different* client (e.g. plain Chat)
   surfaces content synced from Code/Cowork.
