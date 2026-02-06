## Scratchpad (Codex) — Bob Runtime

Last updated: 2026-02-06

### Working framing
- Bob is a grounded MTG homie with strict rules separation (mtg_core is authoritative; LLM never decides rules).
- Memory is explicit, auditable, and approval-gated; no silent writes.
- Agentic behavior is optional and bounded (TURBOTIME tool selection).
- Legacy prototype folders purged; unified runtime lives in `bob/`.

---

## Current Architecture (Bob)

### Core loop (default)
- THINK/RECALL (internal) -> RESPOND (user-facing)
- Logged to `runtime/turns.jsonl`
- Authoritative continuity only in `runtime/state.json` (active_context + open_threads)
- Continuity summary updated each turn (active_context/open_threads)

### TURBOTIME loop (agentic, optional)
- Stage 1: goal framing
- Stage 2: planning + tool requests
- Stage 2b: tool integration
- Stage 3: response
- Tool selection via CLI `/turbotime <tool|off>` or Gradio dropdown
- Same memory approval pipeline as core loop

---

## Memory Model (Current)

### STM (Short-Term Memory)
- Trace-only (intent/resolution/open questions/entities/tools)
- TTL: up to 72 hours
- Max entries: ~200, ~1–3 KB per entry, FIFO eviction
- Fields tracked: created_at, last_accessed, access_count, sessions_seen
- No STM writes during error/invalid states
- Never shown as “memory” to the user

### STM -> LTM Promotion
- Access-pattern gated (access_count + sessions_seen)
- One-shot attempt per STM entry
- Still requires explicit user approval
- Promotion outcomes are recorded back on STM entries

### LTM (Long-Term Memory)
- Approval-gated, append-only via ledger
- Stored in `runtime/ltm.jsonl` (FileLTMStore)
- Schema includes: id, type, tags, confidence, ttl_days, created_at, last_accessed, source, approved_by, why_store, active, superseded_by
- Retrieval is labeled, dated, non-authoritative
- Procedures are informational only (never imperative)

### Approval UI
- CLI: approve/reject/edit JSON
- Gradio: approve/reject + JSON edit-in-place

---

## Tools & Sandbox
- Tool usage is allowlisted and sandbox-gated
- Env:
  - BOB_TOOL_SANDBOX_ENABLED (default false)
  - BOB_TOOL_ROOTS (comma-separated allowlist)
- TURBOTIME tool selection bypasses sandbox gating for tool execution
- TURBOTIME tools: scryfall.lookup, steam.game_lookup, game.knowledge_search, news.headline_search

---

## MTG Integration (Ground Truth)
- `mtg_core` is the rules authority (validate/resolve/visible state)
- LLM never decides legality
- MTG gameplay bypasses TURBOTIME

---

## What’s Implemented (v0)
- Bob runtime with THINK/RESPOND and JSONL logging
- Approval-gated LTM with ledger (`runtime/memory_approvals.jsonl`)
- STM trace store with access metrics and promotion gating
- Gradio memory approval tab with edit-in-place
- TURBOTIME agentic layer (goal framing + planning + tool use) behind tool selection
- TURBOTIME tool registry (scryfall, steam, knowledge, news)
- Practice scan (`/practice`) proposes approval-gated candidates from recent turns
- MTG CLI runner with journals (`runtime/mtg/<game_id>/journal.jsonl`)
- MTG Dear PyGui playtest UI (schema-driven, art-first, hold-priority)
- Gradio “MTG Playtest” tab launches the DPG UI in a terminal

---

## Commands
- Run: `python -m bob`
- Tests: `python -m unittest discover -s tests -p "test_*.py"`
- CLI tools:
  - `/turbotime <tool|off>` (select tool)
  - `/turbotime tools` (list available tools)
  - `/mtg play [tui|plain|dpg]`
  - `/practice`

---

## High-Leverage Next Steps
1) Wire Bob <-> MTG tool bridge (visible state -> action schema -> submit action -> journal)
2) End-of-game lessons extraction -> memory candidates (approval-gated)
3) TURBOTIME skills beyond current tools (deck analysis, scouting, practice plans)
4) Optional learn/practice consolidation job (proposal-only, no LTM writes)

---

## Guardrails (Non-Negotiable)
- No LTM writes without explicit approval
- No MTG rules from memory; engine is truth
- No control-section leakage into RESPOND
- No claims of sentience/consciousness
