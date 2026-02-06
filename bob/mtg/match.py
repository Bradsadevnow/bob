from __future__ import annotations

import os
import random
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from bob.mtg.decider import MtgActionDecider
from bob.mtg.journal import GameJournal, write_game_summary
from bob.mtg.serialize import serialize_visible_state_minimal

from mtg_core.action_surface import ActionSurface
from mtg_core.actions import Action, ActionType
from mtg_core.cards import DeckList, load_card_db, load_decks
from mtg_core.engine import MTGEngine
from mtg_core.game_state import CardInstance, GameMetadata, GameState, GlobalZones, Phase, RandomState, Step, TurnState
from mtg_core.player_state import PlayerState


CARD_DB_PATH = "mtg_core/data/cards_phase1.json"
DECKS_PATH = "mtg_core/data/decks_phase1.json"


@dataclass
class MatchConfig:
    seed: Optional[int] = None
    user_on_play: bool = True
    user_player_id: str = "P1"
    bob_player_id: str = "P2"
    ui: str = "tui"  # "tui" | "plain"


class LocalMtgMatch:
    """
    Local interactive match: user plays one seat, Bob plays the other.

    Tutor mode is intentionally not implemented yet (#TODO).
    """

    def __init__(
        self,
        *,
        runtime_dir: str,
        user_deck: DeckList,
        bob_deck: DeckList,
        cfg: MatchConfig,
        bob_chat_client: Any,
    ) -> None:
        self.cfg = cfg
        self.runtime_dir = runtime_dir

        seed = int(cfg.seed) if cfg.seed is not None else random.randrange(1 << 30)
        self.seed = seed
        rng = random.Random(seed)

        card_db = load_card_db(CARD_DB_PATH)

        players: Dict[str, PlayerState] = {
            cfg.user_player_id: _build_player_from_deck(cfg.user_player_id, user_deck, is_ai=False),
            cfg.bob_player_id: _build_player_from_deck(cfg.bob_player_id, bob_deck, is_ai=True),
        }

        starting_player_id = cfg.user_player_id if cfg.user_on_play else cfg.bob_player_id

        # Shuffle and draw 7 (no mulligans yet).
        for ps in players.values():
            library = list(ps.library)
            rng.shuffle(library)
            ps.hand = library[:7]
            ps.library = library[7:]

        game_id = str(uuid.uuid4())
        game = GameState(
            game_id=game_id,
            players=players,
            card_db=card_db,
            starting_player_id=starting_player_id,
            turn=TurnState(
                active_player_id=starting_player_id,
                turn_number=1,
                phase=Phase.BEGINNING,
                step=Step.UNTAP,
            ),
            zones=GlobalZones(),
            rng=RandomState(seed=seed),
            metadata=GameMetadata(),
            game_over=False,
            winner_id=None,
            reason=None,
        )
        game.validate()

        self.engine = MTGEngine(game)
        self.surface = ActionSurface()
        self.decider = MtgActionDecider(bob_chat_client)

        game_dir = os.path.join(runtime_dir, "mtg", game_id)
        self.journal = GameJournal(os.path.join(game_dir, "journal.jsonl"))
        self.summary_path = os.path.join(game_dir, "summary.json")

        self.user_deck = user_deck
        self.bob_deck = bob_deck
        self.game_id = game_id

    def run(self) -> None:
        print(f"[mtg] game_id={self.game_id} seed={self.seed} user_on_play={self.cfg.user_on_play}")
        print(f"[mtg] you={self.cfg.user_player_id} deck='{self.user_deck.name}' vs bob={self.cfg.bob_player_id} deck='{self.bob_deck.name}'")

        while True:
            v_user = self.engine.get_visible_state(self.cfg.user_player_id)
            if v_user.game_over:
                break

            priority = v_user.priority_holder_id
            if priority == self.cfg.user_player_id:
                self._user_step()
                continue

            if priority == self.cfg.bob_player_id:
                self._bob_step()
                continue

            raise RuntimeError(f"Unexpected priority holder: {priority}")

        v_end = self.engine.get_visible_state(self.cfg.user_player_id)
        print(f"[mtg] game over. winner={v_end.winner_id} reason={v_end.end_reason}")

        write_game_summary(
            self.summary_path,
            summary={
                "game_id": self.game_id,
                "seed": self.seed,
                "user_player_id": self.cfg.user_player_id,
                "bob_player_id": self.cfg.bob_player_id,
                "user_deck": self.user_deck.name,
                "bob_deck": self.bob_deck.name,
                "winner_id": v_end.winner_id,
                "reason": v_end.end_reason,
                # TODO: key turning point + lesson extraction in tutor mode
            },
        )

    def _user_step(self) -> None:
        visible = self.engine.get_visible_state(self.cfg.user_player_id)
        actions = self.surface.get_legal_actions(visible, self.cfg.user_player_id)
        if not actions:
            raise RuntimeError("User has priority but no legal actions")

        idx: Optional[int] = None
        if self.cfg.ui == "tui":
            try:
                from mtg_core.tui_base import choose_action_index_tui

                idx = choose_action_index_tui(visible, actions, self.cfg.user_player_id)
            except Exception as e:
                print(f"[mtg] TUI unavailable ({e}); falling back to plain UI.")
                idx = None

        if idx is None:
            _render_for_user(visible, actions)
            idx = _prompt_index(len(actions))

        chosen = actions[idx]

        result = self.engine.submit_action(chosen)
        self.journal.append(
            {
                "game_id": self.game_id,
                "actor": "user",
                "player_id": self.cfg.user_player_id,
                "visible": serialize_visible_state_minimal(visible),
                "action": _serialize_action(chosen),
                "result": _serialize_result(result),
            }
        )

    def _bob_step(self) -> None:
        visible = self.engine.get_visible_state(self.cfg.bob_player_id)
        schema = self.surface.get_action_schema(visible, self.cfg.bob_player_id)
        if not schema.get("allowed_actions"):
            raise RuntimeError("Bob has priority but schema has no allowed actions")

        decision = self.decider.decide(visible=visible, action_schema=schema, player_id=self.cfg.bob_player_id)

        # Safety: enforce engine.validate. If invalid, fall back to PASS_PRIORITY/first legal.
        if not self.engine.validate(decision.action):
            legal = self.surface.get_legal_actions(visible, self.cfg.bob_player_id)
            if legal:
                decision_action = legal[-1]  # PASS_PRIORITY tends to be last in ActionSurface.get_legal_actions
            else:
                decision_action = decision.action
        else:
            decision_action = decision.action

        result = self.engine.submit_action(decision_action)
        print(f"[bob] {decision_action.type.value} ({decision.reasoning})" if decision.reasoning else f"[bob] {decision_action.type.value}")

        self.journal.append(
            {
                "game_id": self.game_id,
                "actor": "bob",
                "player_id": self.cfg.bob_player_id,
                "visible": serialize_visible_state_minimal(visible),
                "action_schema": schema,
                "decision": {
                    "action": _serialize_action(decision_action),
                    "reasoning": decision.reasoning,
                    "raw_response": decision.raw_response,
                    "error": decision.error,
                },
                "result": _serialize_result(result),
            }
        )


def _build_player_from_deck(player_id: str, deck: DeckList, *, is_ai: bool) -> PlayerState:
    library = []
    for card_id, count in deck.cards:
        for _ in range(int(count)):
            library.append(CardInstance(instance_id=str(uuid.uuid4()), card_id=card_id, owner_id=player_id))
    return PlayerState(player_id=player_id, is_ai=is_ai, library=library, deck_name=deck.name)


def pick_decks_interactive() -> Tuple[DeckList, DeckList]:
    decks = list(load_decks(DECKS_PATH).values())
    if not decks:
        raise RuntimeError("No decks found")

    print("\nAvailable decks:")
    for i, d in enumerate(decks):
        print(f"  [{i}] {d.name} ({d.total_cards()} cards)")

    user_idx = _prompt_index(len(decks), prompt="Choose your deck index: ")
    bob_idx = _prompt_index(len(decks), prompt="Choose Bob deck index: ")
    return decks[user_idx], decks[bob_idx]


def _prompt_index(n: int, prompt: str = "Choose action index: ") -> int:
    while True:
        raw = input(prompt).strip()
        if raw.isdigit():
            idx = int(raw)
            if 0 <= idx < n:
                return idx
        print("Choice out of range.")


def _render_for_user(visible: Any, actions: list[Action]) -> None:
    print("\n" + "=" * 80)
    print(f"Turn {visible.turn_number} | Phase {visible.phase} | Priority {visible.priority_holder_id}")
    print(f"Life: {visible.life_totals}")
    print(f"Mana: {visible.available_mana}")
    print(f"Hand: {[getattr(ci, 'name', getattr(ci, 'card_id', '?')) for ci in visible.zones.hand]}")
    print(f"Battlefield: {[getattr(p, 'name', getattr(p, 'card_id', '?')) for p in visible.zones.battlefield]}")
    print(f"Stack size: {len(visible.zones.stack)}")
    if visible.combat_attackers or visible.combat_blockers:
        print(f"Combat attackers: {visible.combat_attackers}")
        print(f"Combat blockers: {visible.combat_blockers}")
    print("-" * 80)
    print("Legal actions:")
    for i, a in enumerate(actions):
        print(f"  [{i}] {_format_action(a)}")


def _format_action(action: Action) -> str:
    t = action.type
    if t in (ActionType.PASS_PRIORITY, ActionType.SCOOP, ActionType.SKIP_COMBAT, ActionType.SKIP_MAIN2):
        return t.value
    if t in (ActionType.PLAY_LAND, ActionType.TAP_FOR_MANA, ActionType.CAST_SPELL):
        return f"{t.value} object_id={action.object_id}"
    if t in (ActionType.DECLARE_ATTACKERS, ActionType.DECLARE_BLOCKERS):
        return f"{t.value} targets={action.targets}"
    return f"{t.value} object_id={action.object_id} targets={action.targets}"


def _serialize_action(action: Action) -> Dict[str, Any]:
    return {
        "type": action.type.value,
        "actor_id": action.actor_id,
        "object_id": action.object_id,
        "targets": action.targets,
        "payload": action.payload,
    }


def _serialize_result(result: Any) -> Dict[str, Any]:
    return {
        "status": getattr(result.status, "value", str(result.status)),
        "message": getattr(result, "message", None),
        "payload": getattr(result, "payload", None),
    }
