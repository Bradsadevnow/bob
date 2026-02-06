# MTG Playtest UI v0 — Rules-Correct Spec

*Last updated: 2026-02-05*

This document defines the **first real playtesting UI** for the MTG engine. It is intentionally minimal, rules-correct, and engineered to surface engine and AI issues quickly.

This is **not** a product UI. It is a *debuggable Magic table*.

---

## 0. Design Invariants (Non-Negotiable)

* **There is exactly ONE battlefield.**
* The engine (`mtg_core`) is the sole authority.
* The UI is a pure renderer + input surface.
* All state shown comes from `VisibleState`.
* All actions go through `ActionSurface`.
* Crashes are preferred over silent recovery.

The UI must never invent zones, rules, or legality.

---

## 1. Correct Mental Model (Lock This In)

Magic has:

* one shared battlefield
* one shared stack
* controller- and owner-relative views

The UI **partitions the view**, not the game.

### Example

```
Battlefield
 ├─ Permanent A (controller = P1)
 ├─ Permanent B (controller = P2)
 ├─ Permanent C (controller = P1)
```

The UI groups permanents **by controller for display only**.

---

## 2. Scope (v0 Only)

### In Scope

* Local, single-machine playtesting UI
* Human vs Bob / Human vs Human / Bob vs Bob
* Mouse-driven interaction
* Card art via runtime fetch ("pull a Forge")
* Hold Priority + discussion with Bob
* Full end-to-end game completion

### Explicitly Out of Scope

* Animations
* Sound
* Deck building
* Online play
* Vision parsing
* Accessibility polish

---

## 3. UI Technology (Locked)

**Dear PyGui**

Rationale:

* Python-native
* Immediate-mode rendering
* Fast iteration
* Excellent mouse support
* Matches `VisibleState -> redraw` architecture

---

## 4. Layout (Rules-Correct)

Single window, vertically structured battlefield, with side panels.

```
┌──────────────────────────────────────────────┐
│ Battlefield — Permanents Opponent Controls   │
├──────────────────────────────────────────────┤
│ Battlefield — Permanents You Control         │
├──────────────────────────────────────────────┤
│ Hand (You)                                   │
├───────────────┬───────────────────┬─────────┤
│ Stack         │ Phase / Priority   │ Bob /   │
│               │ Turn Info          │ Discuss │
└───────────────┴───────────────────┴─────────┘
```

This represents **one battlefield**, rendered in controller-relative regions.

---

## 5. Battlefield Rendering

### Grouping

* Permanents grouped by `controller_id`
* Ownership shown only if relevant (tooltips)

### Visual Encoding

* Tapped: greyed / rotated
* Summoning sickness: marker (**TODO** until engine exposes state)
* Power/Toughness: always visible
* Counters: numeric badges (**TODO** until counters exist on permanents)

### Interaction

* Click permanent -> select
* Selected permanent is usable **only if** it appears in the schema target candidates

---

## 6. Card Art (Forge-Style Runtime Fetch)

Card art is a **UI-only concern**.

### Rules

* Fetched at runtime from Scryfall
* Triggered only when a card appears in a game
* Cached per `game_id`
* Cleared on game end or short TTL
* Never bundled or preloaded

### Failure Mode

* Missing art -> placeholder rectangle
* Gameplay continues uninterrupted

---

## 7. Stack & Phase Panel

### Stack

* Single shared stack
* Rendered top -> bottom
* Each item clickable for inspection

### Phase / Priority

* Current turn
* Step (v0 uses `VisibleState.phase` which is currently a step string)
* Active player
* Priority holder

No UI shortcuts that skip rules.

---

## 8. Hand & Actions

### Hand

* Render only the local player's hand
* Click card -> attempt cast / activate

### Actions

* Pulled directly from `ActionSurface.get_action_schema()`
* UI renders only legal actions
* No speculative or inferred actions
* Target selection is constrained to schema candidates

Submitting an action:

```
UI -> MTGEngine.submit_action()
```

Invalid actions must crash loudly.

---

## 9. Bob Integration (Playtest-Correct)

### Normal Play

* Bob acts only when holding priority
* Uses same `ActionSchema` as human
* All decisions logged to journal

### Hold Priority / Discussion

* UI can pause Bob's action when hold priority is enabled
* User may converse with Bob
* Bob may explain, disagree, or reason
* Bob may NOT act or mutate state

Discussion entries are logged alongside game events.

---

## 10. Data Flow (Invariant)

```
MTGEngine
   ↓
VisibleState
   ↓
UI Render (pure)
   ↓
User Input
   ↓
ActionSurface
   ↓
MTGEngine.submit_action()
   ↓
Journal Log
   ↓
UI Redraw
```

The UI never owns state.

---

## 11. Failure Philosophy

The UI must prefer:

* hard crashes
* explicit tracebacks
* frozen state on error

Over:

* silent fixes
* retries
* coercion

A crash is a signal, not a defect.

---

## 12. Definition of Done (v0)

This UI is complete when:

* A full game can be played mouse-only
* All public information is visible and correct
* Bob can play legally
* Priority can be held and discussed
* Games terminate correctly
* Journals are complete and readable

No polish required.

---

*End of MTG Playtest UI v0 spec.*
