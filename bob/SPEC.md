# Bob Runtime — v0 spec (draft)

This spec is implementation-guidance for the clean-slate unified runtime in `bob/`.
Legacy prototype folders have been purged; `bob/` is the only active runtime.

## Naming
- Canonical identifier: `system_id = "bob"` (stable; used for filenames/keys).
- Display name: `display_name = "Bob"` (UI string; can change without fracturing identity).

## Core principles
- **State is authoritative**: `runtime/state.json` holds only `active_context` and `open_threads`.
- **Logs are complete**: `runtime/turns.jsonl` is append-only telemetry.
- **Memory is opt-in**: long-term memory writes require explicit human approval.
- **Tools are gated**: filesystem/code access is enabled per session and always logged.
 - **Continuity is compact**: each turn updates `active_context`/`open_threads` with short, non-transcript summaries.

## Model configuration (runtime)
- **OpenAI-compatible client**: MTG and chat calls use the Chat Completions API at `.../chat/completions`.
- **Base URLs**:
  - `BOB_CHAT_BASE_URL` (fallback: `OPENAI_BASE_URL`, default: `https://api.openai.com/v1`)
  - `BOB_MTG_BASE_URL` (fallback: `OPENAI_BASE_URL`, default: `https://api.openai.com/v1`)
- **API keys**:
  - `BOB_CHAT_API_KEY` (fallback: `OPENAI_API_KEY`)
  - `BOB_MTG_API_KEY` (fallback: `OPENAI_API_KEY`)
- **Model IDs**:
  - Chat: `BOB_CHAT_MODEL` (default: `gpt-4o-mini`).
  - MTG: `BOB_MTG_MODEL` (default: `BOB_CHAT_MODEL`, otherwise `gpt-5`).
- **Routing**: `BOB_ROUTE_MTG_REMOTE=true` routes MTG to the remote model by default.

## Turn pipeline (stages)
1) THINK/RECALL (internal)
   - understand input + constraints
   - decide needed tools / retrieval
   - propose memory candidates (do not commit)
2) Tools (runtime)
   - execute allowlisted tools; capture structured results
3) RESPOND (user-facing)
   - natural response; no mechanics leakage
   - THINK notes passed in are sanitized (tool requests + memory candidates removed)
4) Memory approval (human)
   - approve/edit/reject proposed memory candidates
5) State commit (minimal)
   - update `active_context`/`open_threads` only (no bloat)

## TURBOTIME (agentic layer)
- Optional, toggle-controlled execution path for goal framing, planning, and tool use.
- Runs alongside the chat layer; MTG gameplay bypasses it.
- Uses the same memory approval pipeline and logging.
- Tools are allowlisted; a per-session tool selection controls which tools are enabled.
- When a TURBOTIME tool is selected, sandbox gating is bypassed for tool execution.
- UI exposes a TURBOTIME tool dropdown; Gradio shows a banner indicator.

### TURBOTIME tools (v0)
- `scryfall.lookup` (card reference; cache: `runtime/scryfall_cache/`)
- `steam.game_lookup` (game metadata; cache: `runtime/steam_cache/`)
- `game.knowledge_search` (community/meta search)
- `news.headline_search` (headlines via RSS; cache: `runtime/news_cache/`)

## Memory model
- **STM (Short-Term Memory)**: a bounded continuity substrate for “what’s going on right now”.
  - Purpose: conversational coherence + local task continuity.
  - Shape: *trace, not transcript* (non-authoritative, may contain errors).
  - Persistence: optional. If persisted, keep it as JSONL with condensation, not chat history.
  - TTL: up to 72 hours.
  - Bounds: ~200 entries, ~1–3 KB per entry, FIFO eviction.
  - Contents (suggested):
    - `intent` (1 line)
    - `resolution` (1 line)
    - `open_questions` (0–3)
    - `tools_used` (structured)
    - `entities` (names/ids; e.g., files, decks, cards)
    - `created_at`, `last_accessed`, `access_count`, `sessions_seen`
  - No STM writes during error/invalid states.
  - STM is never presented as “memory” to the user.
  - Note: the UI can still display chat history; STM is for the model, not the interface.

- **Working Context (ephemeral)**: a tiny “current turn” bundle built from:
  - authoritative `state.json`
  - current user input
  - (optional) last N UI messages (not persisted, or persisted separately as raw logs only)
  - tool results from this turn
  - retrieved memory snippets (explicitly marked non-authoritative)
  - tool outputs are tagged with `source=tool_output` and `confidence=verbatim|summarized`

- **LTM (Long-Term Memory)**: curated store for durable, cross-session continuity.
  - Backend: pluggable (start with SQLite/files; optionally Qdrant later).
  - Writes: **explicit human approval only**.
  - Retrieval: returns *snippets* with metadata; never treated as truth without verification.
  - Procedures are informational only (never imperative).
  - Candidate format (suggested):
    - `text`
    - `type`: `preference|fact|procedure|project_decision|mtg_profile|mtg_lesson`
    - `tags`: list of strings
    - `ttl_days`: int or `null`
    - `source`: `user_said|assistant_inferred|tool_output`
    - `why_store`: 1 line justification

### Approval protocol (contract)
1) Model proposes candidates inside THINK output (up to 2).
2) Runtime parses proposals into `MemoryCandidate` objects.
3) Human reviews each candidate and chooses: approve / reject / edit.
4) Runtime appends a decision record to an append-only approval ledger:
   - `runtime/memory_approvals.jsonl` (recommended default path)
5) Runtime commits only approved (possibly edited) candidates to LTM via the `LTMStore` interface.

Non-goals (by design):
- No automatic LTM writes.
- No “silent edits” to LTM without a ledger record.

### STM → LTM promotion (contract)
- STM entries may nominate themselves for LTM only via access patterns (not content alone).
- Eligibility (all required): access_count >= N (suggested 3), sessions_seen >= 2, within TTL, not error-tainted.
- Promotion is explicit and user-approved only; attempted once per STM entry.

### Practice/Learn consolidation (optional)
- A manual/CLI-triggered “practice” job can propose additional candidates from recent turns or journals.
- It never writes LTM directly and uses the same approval ledger.

### LTM store interface (contract)
- `upsert(candidate, extra_payload) -> id`
- `query(query_text, k) -> list[hits]`

Initial backend:
- JSONL file store (append-only) for bring-up; upgrade later to SQLite/Qdrant without changing callers.

## Tool sandbox (contract)
Tools are gated per session to tightly monitor recursive improvement requests.

- Default: tools disabled.
- If enabled: file access is restricted to allowlisted roots.
- TURBOTIME tool selection bypasses the sandbox gate for tool execution (no env toggle required).
- Any tool execution must be logged in the turn record (`tools` field) with:
  - `tool_name`, `args`, `status`, `error` (if any), `result_ref` (if stored separately)
- Env controls: `BOB_TOOL_SANDBOX_ENABLED`, `BOB_TOOL_ROOTS` (comma-separated allowlist).

### MTG game memory (integration-forward)
MTG has two “truth layers” and we should keep them separate:

1) **Authoritative game truth** (engine-owned)
   - `mtg_core` game state (`GameState`) is the source of truth for the current match.
   - Bob should not “remember” game state in LTM; it should query the engine via tools.

2) **Durable MTG-related memory** (Bob-owned, approval-gated)
   - What *can* go to LTM (examples):
     - user preferences: decks they like, tutoring style, pacing, difficulty
     - recurring strategic leaks: “often forgets to play land before combat”
     - card knowledge boosts: “user is learning timing around instants”
     - per-deck playbook snippets: mulligan heuristics, common lines (kept short + falsifiable)
   - What should stay STM or logs-only (examples):
     - exact hand contents, hidden information, in-progress tactical details
     - turn-by-turn transcripts of a specific game (keep in raw logs, not LTM)

### Suggested MTG memory artifacts
- **GameJournal (per game_id, logs-only)**:
  - full action trace: visible states + chosen actions + engine resolution results
  - user questions asked during the game + answers given
  - outcome summary (winner, reason, key turning point)
  - stored as JSONL/JSON under `runtime/mtg/<game_id>/...`
- **MTGLessons (LTM candidates)**:
  - extracted at game end: 1–3 “lessons” + 1–2 “prefs” candidates for approval
  - tagged by deck/archetype and skill area (combat, stack, mana efficiency, etc.)

## MTG grounding
- `mtg_core/` provides:
  - authoritative engine state mutation (`MTGEngine`)
  - legal action enumeration (`ActionSurface`)
  - action contract (`get_action_schema`)
- Models interact with MTG only through the action schema + tool submission.

## Open items (next iteration)
- Gradio memory approval: UX polish.
- Tool protocol standardization (one request format across all tools).
- Routing rules (manual `/mode` + optional automatic classifier).
- LTM backend choice and schema (start with SQLite; optionally Qdrant).
