# mtg_core — v0 spec (phase-1, shippable)

This spec documents the phase-1 Magic: The Gathering rules engine and its control surfaces. The engine is authoritative for in-game state mutation; action enumeration and UIs sit on top of it.

## Scope
- Phase-1, two-player MTG subset with deterministic, engine-owned state.
- In-game only: pregame decisions (mulligan/bottom) are handled by a separate AI surface.
- Action legality is computed from a player-visible snapshot (VisibleState); the engine still validates actions.
- Phase-1 card pool is defined by `data/cards_phase1.json` and `data/decks_phase1.json`; decklists are authoritative.
- Rules are data-driven and implemented only as required by the phase-1 card pool.

## Module map
- `actions.py`: canonical `ActionType` enum and `Action` dataclass (actor/object/targets/payload).
- `cards.py`: card model + rules/effects, JSON (de)serialization, card DB and deck list loaders.
- `game_state.py`: core state types (phases/steps, card instances, permanents, stack, turn state).
- `player_state.py`: per-player zones, life, mana, mulligan bookkeeping.
- `engine.py`: authoritative `MTGEngine` (validate + resolve actions, advance turn/steps).
- `action_surface.py`: legal action enumeration and action schema generation from `VisibleState`.
- `aibase.py`: shared visible-state schema + `AIBase` thin adapter.
- `cli_base.py`: terminal UI for a human player (action list + prompts).
- `tui_base.py`: Textual UI single-turn view + optional reasoning prompt.
- `dpg_ui.py`: Dear PyGui playtest UI (mouse-driven, action schema driven).
- `ai_pregame.py`: LLM mulligan/bottom decision surface (strict JSON I/O + normalization).
- `ai_live.py`: LLM live action chooser using action schema (strict JSON I/O + normalization + validation).
- `ai_broker.py`: background asyncio loop + OpenAI async client wrapper.
- `ai_trace.py`: append-only AI trace logging (`runtime/ai_trace.jsonl`).
- `data/cards_phase1.json`: phase-1 card DB (Scryfall-based schema).
- `data/decks_phase1.json`: phase-1 deck lists.

## Core gameplay loop (in-game)
1. Engine builds a `VisibleState` for a specific player (`engine.get_visible_state(player_id)`).
2. `ActionSurface` computes legal actions or an action schema using only `VisibleState`.
   - If there is a pending decision, only `RESOLVE_DECISION` actions are exposed.
3. A control surface (CLI/TUI/AI) chooses one `Action`.
4. Engine validates and resolves the action, mutating `GameState`.
5. Engine applies state-based actions and win checks.

## Data model

### Card DB + rules
- Card DB uses a Scryfall-based schema with: `card_id`, `name`, `type_line`, `mana_cost`, `colors`, `color_identity`, `oracle_text`, `power`, `toughness`.
- `card_from_dict` parses `oracle_text` into a `RulesBlock`:
  - `keywords` (enforced by the engine)
  - `effects` (spell effects)
  - `static_abilities` (continuous effects)
  - `triggered_abilities`
  - `activated_abilities`
  - `additional_costs`, `alternate_costs`, `flashback_cost`
- Equip abilities are auto-generated for Equipment at load time.
- Alias map resolves `plains/island/swamp/mountain/forest` to canonical `basic_*` IDs.
- Tokens are defined in `cards.py` and can be created by effects.

### GameState
- `GameState` holds: `players`, `turn`, `zones`, `card_db`, RNG, `temporary_effects`, `exile_links`, `damage_dealt_to_players`, `pending_decision`, `extra_turns`, and game-over metadata.
- `PermanentState` includes: tapped, damage_marked, counters, summoning_sick, attached_to, goad metadata, and draw-on-attack metadata.
- Stack items can be `SPELL` or `ABILITY`.

### Zones
- Per-player: `library`, `hand`, `graveyard`.
- Global: `battlefield`, `exile`, `stack`.
- Attachments are represented by `PermanentState.attached_to`, and visible state exposes `attachments` per host.

## Actions
Supported `ActionType` values (phase-1):
- `PLAY_LAND`, `TAP_FOR_MANA`, `CAST_SPELL`, `ACTIVATE_ABILITY`
- `DECLARE_ATTACKERS`, `DECLARE_BLOCKERS`
- `PASS_PRIORITY`, `RESOLVE_DECISION`
- `SKIP_COMBAT`, `SKIP_MAIN2`, `SCOOP`
- Pregame: `MULLIGAN`, `KEEP_HAND`, `BOTTOM_CARD`

### Casting, costs, and timing
- Spells can be cast from hand if they are `INSTANT`, `SORCERY`, `CREATURE`, `ARTIFACT`, or `ENCHANTMENT`.
- Timing:
  - `INSTANT` or `FLASH` can be cast any time you have priority.
  - Others are sorcery speed (active player MAIN1/MAIN2 with empty stack).
- X costs are supported.
- Additional costs are data-driven (discard, sacrifice, pay life, etc.).
- Alternate costs are data-driven (e.g., “control a Forest and pay life”).
- Flashback is supported: cast from graveyard using `flashback_cost`; spell is exiled on resolve.

### Land drops and mana
- One land drop per turn from hand.
- `TAP_FOR_MANA` is available for basic lands (or lands with `land_stats`).

### Targeting
- Targeting is driven by `TargetSpec` selectors (see `cards.py`).
- Hexproof prevents opponents from targeting a permanent.
- Some effects require multi-target groups (e.g., source/target pairs, equipment attach).
- Action schema target shapes:
  - `targets` is a list of target groups, where each group is a list of target dicts.
  - Each target dict includes `type` plus `instance_id` and/or `player_id`.
  - Single-target spells may still present one group with one target.

## AI decision surfaces (normalization)
- Pregame (`ai_pregame.py`):
  - Mulligan/bottom outputs are strict JSON, but bottom selections are normalized.
  - `bottom` may be instance IDs, card IDs (if unambiguous), or indices; entries are deduped.
  - If fewer/more than required, the list is auto-filled/truncated to the exact count before validation.
- Live (`ai_live.py`):
  - Actions are normalized against the action schema before validation.
  - Object IDs are recovered from indices/card_id/name when unambiguous.
  - Targets are matched to canonical schema targets (including grouped targets) and rewritten to the canonical form.
  - Common payload slips (mode indices, x values, single additional cost choice, ability costs) are coerced when safe.

## Abilities and effects

### Triggered abilities
Supported trigger types used by phase-1 cards:
- `ETB`, `DIES`, `ATTACKS`, `ATTACKS_OR_BLOCKS`, `EQUIPPED_CREATURE_ATTACKS`
- `COMBAT_DAMAGE_TO_PLAYER`, `DEALT_DAMAGE`, `BECOMES_TARGET`
- `UPKEEP`, `YOU_LOSE_LIFE`
- `CAST_SPELL`, `CREATURE_ENTERS`, `OTHER_FRIENDLY_DIES`, `OTHER_DIES_DURING_YOUR_TURN`

Trigger conditions supported include:
- `controller` (YOU/OPPONENT)
- `during_opponent_turn`
- `has_keyword`
- `subtype`
- `spell_type`
- `control_subtype_count`

### Activated abilities
- Costs supported: mana, tap, sacrifice (self/creature/other creature), discard, pay life.
- Timing restrictions are enforced (anytime vs sorcery-speed).
- Non-mana abilities are placed on the stack; mana abilities resolve immediately.

### Static/continuous effects
- Static abilities and temporary effects update a derived battlefield state each frame.
- Supported continuous effects include P/T modification, keyword add/remove, subtype add, lords, cost reduction, attack requirements, and damage prevention flags.
- Equipment/Aura “equipped/enchanted only” effects are applied to the attached permanent.

### One-shot effects (examples; data-driven)
- Damage, destroy, exile, return to hand
- Counter/copy spells
- Draw/discard and hand replacement effects
- Gain/lose life
- Add mana
- Create tokens
- Search/scry/reveal/put on bottom
- Extra turns, goad, and combat-specific effects

The engine supports all `EffectType` entries used in `cards_phase1.json` (see `cards.py`).

## Combat
- Attackers: untapped creatures, not summoning sick unless HASTE, and not DEFENDER. Must attack if goaded/required.
- Blockers: each blocker may block one attacker; attackers can have multiple blockers.
- Menace: if blocked, it must be blocked by two or more creatures.
- Flying/Reach rules are enforced for blocking.
- Combat damage resolves automatically after blockers:
  - First strike / double strike split the damage application.
  - Trample, deathtouch, and lifelink are enforced.
  - `assign_damage_as_unblocked` and `prevent_combat_damage` effects are honored.
- No priority window between first-strike and normal damage.

## State-based actions
- Destroy creatures with 0 toughness or lethal damage (unless indestructible).
- Destroy deathtouch-marked creatures.
- Auras are destroyed if not attached to a legal creature.
- Equipment detaches if attached to an illegal creature.

## Visibility contract (engine → surfaces)
- Per-player visibility: player sees own hand; opponent hand is hidden.
- VisibleState includes:
  - turn/phase, priority holder, life totals
  - battlefield (including attachments and derived P/T/keywords)
  - stack, graveyards, exile, library size
  - mana pool and lands played this turn
  - combat declarations, pending decisions, and game-over status

## Current constraints / known gaps
- Exactly two players (validated in `GameState`).
- No planeswalkers or battles.
- No replacement effects or comprehensive layer system; continuous effects are applied in a single derived pass.
- Trigger ordering is engine-defined (no APNAP ordering choices).
- Combat damage assignment to multiple blockers uses engine order, not player-chosen order.
- `Step.DAMAGE` exists as a placeholder but damage resolves at the end of `DECLARE_BLOCKERS`.

## Extension points
- Add new `EffectType` handlers + target specs in `cards.py` and `engine.py`.
- Expand keyword enforcement in `engine.py` as new keywords are introduced.
- Extend decision handling and `ActionSurface` schema for new card mechanics.
- Swap in richer card data, deck lists, and AI prompting logic.
