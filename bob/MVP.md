# Bob × MTG — MVP definition (one page)

## What we’re shipping (MVP)
A grounded, stateful “Magic-like” sparring partner + tutor built on a real rules engine and a strict legal-action interface.

**Core promise:** the model can’t cheat or invent moves because it can only act through an allowlisted action schema that is validated by the engine.

## Hard scope (Phase‑1 format)
- **1v1 only**
- **Curated card pool + decks** (use the repo’s Phase‑1 JSON DB / decklists as the initial format boundary)
- **Rules scope is exactly what `mtg_core` implements today**
- **No claim of “full MTG rules / full card pool”**

## User experiences (MVP modes)
1) **Play / Reps mode**
   - Run games quickly, minimal commentary
   - Primary output: actions + optional short rationale

2) **Tutor mode**
   - The system explains decisions and answers rules questions
   - End-of-game: proposes 1–3 “lessons” as memory candidates (approval-gated)

## System architecture constraints (non-negotiable for MVP quality)
- **Ground truth:** engine state is authoritative; the model never stores “game truth” in LTM.
- **Action contract:** model chooses from `get_action_schema()` only; all actions are re-validated before submit.
- **Memory contract:**
  - LTM writes require explicit approval
  - GameJournal is logs-only (per `game_id`)
- **Logging:** append-only JSONL artifacts for turns + game traces

## Artifacts (what gets saved)
- `runtime/turns.jsonl`: Bob chat turn telemetry (think + response + candidates + state snapshots)
- `runtime/memory_approvals.jsonl`: approval ledger (append-only)
- `runtime/ltm.jsonl`: LTM store (append-only backend for bring-up)
- `runtime/mtg/<game_id>/journal.jsonl`: per-action game journal (visible state snapshot → chosen action → engine result)
- `runtime/mtg/<game_id>/summary.json`: outcome + extracted lesson candidates

## MVP success criteria (measurable)
Technical:
- **Game completion rate:** % of games that reach a terminal condition without manual intervention
- **Illegal-action rate:** % of model action attempts rejected by schema validation (should trend down)
- **Tool/contract compliance:** JSON validity and schema adherence (retry rate)

User value:
- **Tutor usefulness:** user-reported “helpfulness” + whether it correctly explains why actions are legal/illegal in the Phase‑1 format
- **Personalization value:** whether approved lessons/prefs improve subsequent games (subjective + tracked prompts)

## MVP milestones (implementation order)
1) **Bob↔MTG tool bridge**
   - expose: visible state, action schema, submit action
2) **GameJournal**
   - per-action logging; link to Bob session/turn ids
3) **Opponent loop**
   - model chooses actions; engine advances; repeat
4) **Tutor loop**
   - add coaching explanations + end-of-game lesson extraction → memory candidates
5) **UI**
   - CLI first (fast iteration), then Gradio

## IP / data posture (high-level)
- Ship the engine + scaffolding; keep “format” constrained.
- Prefer user-provided data packs or a custom starter set for public distribution.
- Treat any external oracle text sources as optional plugins, not bundled core assets.

