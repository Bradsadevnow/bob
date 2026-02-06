# Bob Runtime + MTG Phase-1 Engine

This repo contains two aligned systems:
- `bob/`: a clean-slate unified runtime (chat + memory + tools).
- `mtg_core/`: a phase-1 Magic: The Gathering rules engine used by Bob for grounded gameplay.

## Status (v0)

### Bob runtime (`bob/`)
- **State is authoritative**: `runtime/state.json` only stores `active_context` and `open_threads`.
- **Logs are complete**: `runtime/turns.jsonl` is append-only telemetry.
- **Memory is opt-in**:
  - STM is bounded, trace-only, TTL-based, and never authoritative.
  - LTM writes require explicit human approval and are ledgered.
- **Tools are gated**: per-session allowlist, sandboxed roots, and execution logging.
- **Turn pipeline**: THINK/RECALL → tools → RESPOND → memory approval → state commit.
- **TURBOTIME** (optional agentic layer): goal framing/planning/tool use, toggle-controlled; MTG gameplay bypasses it.
  - v0 tools: `scryfall.lookup`, `steam.game_lookup`, `game.knowledge_search`, `news.headline_search`.

### MTG engine (`mtg_core/`)
- **Phase-1 rules engine** with deterministic, engine-owned state and data-driven card logic.
- **Authoritative state mutation** via `MTGEngine`; legal action enumeration via `ActionSurface`.
- **Action schema + pending decisions** for CLI/TUI/AI control surfaces.
- **Card DB and decklists** are authoritative (`data/cards_phase1.json`, `data/decks_phase1.json`).
- **Supported systems (as required by phase-1 decklists)**:
  - Keyword enforcement (e.g., flying, vigilance, double strike, first strike, haste, lifelink, deathtouch, trample, reach, flash; plus deck-required defender/hexproof/menace).
  - Triggered abilities (ETB, dies, attacks, damage, upkeep, cast, etc.).
  - Activated abilities with costs (mana, tap, discard, sacrifice, pay life).
  - Continuous effects (PT modifiers, lords, keyword add/remove, equipment-only effects).
  - Equipment + attachments; auras attach to targets on resolution.
  - Tokens.
  - State-based actions (lethal damage/0 toughness, deathtouch-marked, aura legality, equipment detach).
  - Stack items for spells and abilities.
- **Combat** supports first/double strike, trample, deathtouch, lifelink, flying/reach, menace, and attack/block constraints.
- **Constraints/gaps** (by design):
  - Exactly two players.
  - No replacement effects; no full layer system.
  - Trigger ordering is engine-defined (no APNAP ordering choices).
  - Combat damage assignment to multiple blockers is engine-defined.
  - `Step.DAMAGE` is a placeholder; damage resolves after `DECLARE_BLOCKERS`.

## Specs
- `bob/SPEC.md`
- `mtg_core/spec.md`

## Run (when deps installed)
Install Python deps (example):
- `pip install -e .`

Start CLI and Gradio:
- `python -m bob` (default)

Toggle TURBOTIME (CLI):
- `/turbotime on` or `/turbotime off`

Practice scan (CLI):
- `/practice` (generates approval-gated candidates from recent turns)

Standalone practice job:
- `python -m bob.practice`

Play MTG (CLI):
- In the Bob CLI, run `/mtg play` (defaults to TUI; falls back to plain).
- Or run the standalone runner: `python run_mtg.py` (add `--cli` to force plain UI).

## Tests (no network / no extra deps)
- `python -m unittest discover -s tests -p "test_*.py"`

## Open items (from specs)
- Gradio memory approval UX polish.
- Tool protocol standardization (single request format).
- Routing rules (manual `/mode` + optional classifier).
- LTM backend choice and schema (start JSONL; upgrade to SQLite/Qdrant).

## Configuration (env vars)
- `BOB_LOCAL_BASE_URL` (default `http://localhost:1234/v1`)
- `BOB_LOCAL_API_KEY` (default `lm-studio`)
- `BOB_LOCAL_MODEL` (default `mistralai/ministral-3-14b-reasoning`; must match LM Studio model string)
- `BOB_CHAT_BASE_URL` (default `OPENAI_BASE_URL` or `https://api.openai.com/v1`)
- `BOB_CHAT_API_KEY` (default `OPENAI_API_KEY`)
- `BOB_CHAT_MODEL` (default `gpt-4o-mini`)
- `BOB_MTG_BASE_URL` (default `OPENAI_BASE_URL` or `https://api.openai.com/v1`)
- `BOB_MTG_API_KEY` (default `OPENAI_API_KEY`)
- `BOB_MTG_MODEL` (default `mistralai/ministral-3-14b-reasoning`)
- `BOB_ROUTE_MTG_REMOTE` (default `true`)
- `BOB_RUNTIME_DIR` (default `./runtime`)
- `BOB_TURN_LOG` (default `./runtime/turns.jsonl`)
- `BOB_STATE_FILE` (default `./runtime/state.json`)
- `BOB_APPROVAL_LEDGER` (default `./runtime/memory_approvals.jsonl`)
- `BOB_LTM_FILE` (default `./runtime/ltm.jsonl`)
- `BOB_STM_ENABLED` (default `true`)
- `BOB_STM_DIR` (default `./runtime/stm_chroma`)
- `BOB_STM_COLLECTION` (default `stm`)
- `BOB_STM_TTL_HOURS` (default `72`)
- `BOB_STM_INJECT_REFRESH_HOURS` (default `24`)
- `BOB_STM_TOP_K` (default `6`)
- `BOB_STM_EMBED_DIM` (default `256`)
- `BOB_STM_MAX_ENTRIES` (default `200`)
- `BOB_STM_MAX_ENTRY_CHARS` (default `3072`)
- `BOB_STM_PROMOTION_ACCESS_MIN` (default `3`)
- `BOB_STM_PROMOTION_SESSIONS_MIN` (default `2`)
- `BOB_STM_PROMOTION_MAX_PER_TURN` (default `2`)
- `BOB_TOOL_SANDBOX_ENABLED` (default `false`)
- `BOB_TOOL_ROOTS` (default empty; comma-separated allowlist)
- `BOB_PRACTICE_CANDIDATES` (default `./runtime/practice_candidates.jsonl`)
