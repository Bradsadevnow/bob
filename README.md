Requires Python 3.10+ and an OpenAI-compatible API endpoint.

## How It Works

1. **Engine generates legal moves** — `mtg_core` produces a player-scoped action schema from current game state
2. **AI chooses from schema** — Bob selects one legal action via LLM reasoning
3. **Engine validates and resolves** — State mutation is deterministic and engine-owned
4. **Outcomes are logged** — All decisions, triggers, and resolutions are traceable

Bob can also explain plays in natural language, propose strategic learnings (subject to human approval), and operate tools within a sandboxed environment.

## Architecture Highlights

- **No hallucinations**: AI cannot make illegal plays (schema-constrained, not rules-interpreting)
- **Hidden information enforced**: Opponent hand/library are never visible to AI
- **Human-in-the-loop learning**: Long-term memory writes require explicit approval with audit ledger
- **Deterministic replay**: Every game is reproducible from append-only logs

## Why This Architecture Matters

Traditional AI game opponents either:
1. Use hardcoded behavior trees (predictable, cannot learn or explain)
2. Use pure ML models (expensive to train, opaque, prone to illegal moves)

Bob combines the best of both approaches:
- **Deterministic rules engine** ensures correctness (no invalid plays, no cheating)
- **LLM decision-making** enables strategic variety and natural language explanation
- **Governed memory system** allows learning without silent drift or hallucinated beliefs
- **Player-scoped visibility** enforces information hiding (AI only sees what a real player would see)

This architecture is suitable for: AI opponents that improve over time, tutoring and teaching systems, internal design tooling, balance simulation, and any game system where trust and auditability matter.

---

## System Details

### Bob Runtime (`bob/`)

**Core principles:**
- **State is authoritative**: `runtime/state.json` stores only `active_context` and `open_threads`
- **Logs are complete**: `runtime/turns.jsonl` is append-only telemetry for all interactions
- **Memory is opt-in**:
  - **STM** (Short-Term Memory): Bounded, trace-only, TTL-based (72hr default), never authoritative
  - **LTM** (Long-Term Memory): Requires explicit human approval, permanent audit ledger
- **Tools are gated**: Per-session allowlist, sandboxed filesystem roots, execution logging
- **Turn pipeline**: THINK/RECALL → tools → RESPOND → memory approval → state commit

**TURBOTIME** (optional agentic layer):
- Goal framing, planning, and multi-step tool use
- Toggle-controlled (`/turbotime on|off` in CLI)
- MTG gameplay bypasses TURBOTIME and uses direct action schema selection
- v0 tools: `scryfall.lookup`, `steam.game_lookup`, `game.knowledge_search`, `news.headline_search`

### MTG Engine (`mtg_core/`)

**What it does:**
- Enforces phase-1 Magic: The Gathering rules deterministically
- Generates legal action schemas from player-scoped visible state
- Supports full gameplay loop: priority, stack resolution, combat, triggered/activated abilities, state-based actions
- Implements 45+ card effects with data-driven rules (no hardcoding card behavior)
- Enables AI/CLI/TUI control surfaces without embedded rules logic

**Implemented systems** (as required by phase-1 card pool):
- **Keywords**: flying, vigilance, double strike, first strike, haste, lifelink, deathtouch, trample, reach, flash, defender, hexproof, menace
- **Triggered abilities**: ETB, dies, attacks, attacks-or-blocks, equipped-creature-attacks, combat-damage-to-player, dealt-damage, becomes-target, upkeep, you-lose-life, cast-spell, creature-enters, other-friendly-dies, other-dies-during-your-turn
- **Activated abilities**: Mana costs, tap, discard, sacrifice (self/creature/other), pay life; timing restrictions (anytime vs sorcery-speed)
- **Continuous effects**: P/T modifiers, lords, keyword add/remove, subtype add, cost reduction, attack requirements, damage prevention, equipment/aura-only effects
- **Spell systems**: Instant/sorcery timing, X costs, alternate costs, additional costs, flashback
- **Attachments**: Equipment with equip abilities, auras that attach on resolution
- **Tokens**: Data-driven token creation
- **Combat**: First strike/double strike damage split, trample, deathtouch, lifelink, menace, flying/reach blocking rules
- **State-based actions**: Lethal damage, 0 toughness, deathtouch-marked creatures, aura/equipment legality checks

**Known constraints** (by design for phase-1):
- Exactly two players (validated on startup)
- No replacement effects; no comprehensive layer system (continuous effects applied in single derived pass)
- Trigger ordering is engine-defined (no APNAP player ordering choices)
- Combat damage assignment to multiple blockers uses engine-defined order
- `Step.DAMAGE` exists as placeholder; damage currently resolves at end of `DECLARE_BLOCKERS`

**Authoritative data:**
- Card database: `data/cards_phase1.json` (Scryfall-derived schema)
- Decklists: `data/decks_phase1.json`

---

## Usage

### Play MTG

**Textual UI (default):**
```bash