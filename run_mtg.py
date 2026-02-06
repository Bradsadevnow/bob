from __future__ import annotations

import json
import argparse
import os
import random
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional

from mtg_core.engine import MTGEngine
from mtg_core.game_state import (
    GameState,
    CardInstance,
    TurnState,
    Phase,
    Step,
    GlobalZones,
    RandomState,
    GameMetadata,
)
from mtg_core.player_state import PlayerState
from mtg_core.cli_base import CLIPlayer
from mtg_core.actions import Action
from mtg_core.cards import load_card_db

from mtg_core.ai_pregame import (
    AIPregameDecider,
    MulliganContext,
    BottomContext,
    CardView,
)
from mtg_core.action_surface import ActionSurface
from mtg_core.ai_live import LiveAIDecider
from mtg_core.ai_trace import log_ai_event
from bob.config import load_config
from bob.models.openai_client import ChatModel, OpenAICompatClient
from bob.mtg.journal import GameJournal
from bob.mtg.serialize import serialize_visible_state_minimal

# ============================
# CONFIG
# ============================

DECKS_PATH = "mtg_core/data/decks_phase1.json"
CARD_DB_PATH = "mtg_core/data/cards_phase1.json"

# ============================
# Minimal AI Controller (LIVE GAME ONLY for now)
# ============================

class AIPlayer:
    def __init__(
        self,
        engine: MTGEngine,
        actions: ActionSurface,
        player_id: str,
        *,
        decider: LiveAIDecider,
        journal: Optional[GameJournal],
        game_id: str,
        actor_label: str,
        hold_gate: Optional["HoldPriorityGate"] = None,
    ):
        self.engine = engine
        self.actions = actions
        self.player_id = player_id
        self.decider = decider
        self.journal = journal
        self.game_id = game_id
        self.actor_label = actor_label
        self.hold_gate = hold_gate

    def loop(self) -> None:
        if self.hold_gate is not None and self.hold_gate.active:
            return
        visible = self.engine.get_visible_state(self.player_id)
        schema = self.actions.get_action_schema(visible, self.player_id)
        if not schema.get("allowed_actions"):
            return

        decision = self.decider.decide_action(visible, schema, self.player_id)
        result = self.engine.submit_action(decision.action)

        log_ai_event(
            "live_action",
            {
                "player_id": self.player_id,
                "prompt": decision.prompt,
                "raw_response": decision.raw_response,
                "attempts": decision.attempts,
                "decision": {
                    "reasoning": decision.reasoning,
                },
                "action": {
                    "type": getattr(decision.action.type, "value", str(decision.action.type)),
                    "object_id": decision.action.object_id,
                    "targets": decision.action.targets,
                    "payload": decision.action.payload,
                },
                "result": {
                    "status": getattr(result.status, "value", str(result.status)),
                    "message": result.message,
                    "payload": result.payload,
                },
            },
        )

        if self.journal is not None:
            self.journal.append(
                {
                    "game_id": self.game_id,
                    "actor": self.actor_label,
                    "player_id": self.player_id,
                    "visible": serialize_visible_state_minimal(visible),
                    "action_schema": schema,
                    "decision": {
                        "reasoning": decision.reasoning,
                        "raw_response": decision.raw_response,
                        "error": decision.attempts[-1].get("error") if decision.attempts else None,
                    },
                    "action": _serialize_action(decision.action),
                    "result": _serialize_result(result),
                }
            )

# ============================
# Deck Loading
# ============================

def load_decks(path: str = DECKS_PATH) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    decks = data.get("decks")
    if not isinstance(decks, list):
        raise ValueError("Invalid decks file: expected key 'decks' to be a list")

    for d in decks:
        if "name" not in d or "cards" not in d:
            raise ValueError("Invalid deck entry: expected keys 'name' and 'cards'")

    return decks


def prompt_for_deck(player_label: str, decks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Deck selection is ALWAYS done by the human running the program.
    Even if the player will be AI-controlled, we do not ask the AI to pick decks.
    """
    print(f"\nSelect deck for {player_label}:")
    for i, deck in enumerate(decks):
        print(f"  [{i}] {deck['name']}")

    while True:
        raw = input("Choose deck: ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 0 <= idx < len(decks):
                return decks[idx]
        print("Choice out of range.")


def build_player_from_deck(pid: str, deck: Dict[str, Any], is_ai: bool) -> PlayerState:
    cards: List[CardInstance] = []

    for entry in deck["cards"]:
        # Support legacy "id" and current "card_id" deck schemas.
        if "id" in entry:
            cid = entry["id"]
        elif "card_id" in entry:
            cid = entry["card_id"]
        else:
            raise KeyError(f"Deck card entry missing 'id' or 'card_id' in deck '{deck.get('name', 'unknown')}'")

        count = int(entry["count"])

        for _ in range(count):
            cards.append(
                CardInstance(
                    instance_id=str(uuid.uuid4()),
                    card_id=cid,
                    owner_id=pid,
                )
            )

    return PlayerState(
        player_id=pid,
        library=cards,
        deck_name=deck["name"],  # 👈 THIS LINE
        is_ai=is_ai,
    )


# ============================
# Pregame Setup (Runner-owned for now)
# ============================

@dataclass
class MatchSetup:
    players: Dict[str, PlayerState]
    seed: int

    libraries: Dict[str, List[CardInstance]] = field(default_factory=dict)
    hands: Dict[str, List[CardInstance]] = field(default_factory=dict)
    mulligan_counts: Dict[str, int] = field(default_factory=dict)

    starting_player_id: Optional[str] = None


def prompt_for_play_or_draw(roll_winner: str, other: str) -> str:
    """
    Returns the starting player id.
    """
    while True:
        raw = input(f"{roll_winner} won the roll. Play or draw? (p/d): ").strip().lower()
        if raw == "p":
            return roll_winner
        if raw == "d":
            return other
        print("Please enter p or d.")


def run_match_setup(
    players: Dict[str, PlayerState],
    *,
    controls: Dict[str, str],
    pregame_deciders: Dict[str, AIPregameDecider],
    journal: Optional[GameJournal],
    game_id: str,
) -> MatchSetup:
    """
    Runner-owned pregame for now:
      - roll for play/draw
      - shuffle
      - draw opening 7
      - mulligan + London bottom (humans via CLI; AI via decider)

    This is intentionally temporary until we move mulligans into an action-driven pregame manager.
    """
    seed = random.randrange(1 << 30)
    rng = random.Random(seed)

    setup = MatchSetup(players=players, seed=seed)
    player_ids = list(players.keys())
    if len(player_ids) != 2:
        raise ValueError("Phase-1 runner expects exactly 2 players")

    p1, p2 = player_ids[0], player_ids[1]

    # ----------------------------
    # 1) Roll for starting player
    # ----------------------------
    roll_winner = rng.choice([p1, p2])
    other = p2 if roll_winner == p1 else p1
    starting = prompt_for_play_or_draw(roll_winner, other)
    setup.starting_player_id = starting
    print(f"[Pregame] Starting player: {setup.starting_player_id}")

    # ----------------------------
    # 2) Shuffle libraries
    # ----------------------------
    for pid, ps in players.items():
        cards = list(ps.library)
        rng.shuffle(cards)
        setup.libraries[pid] = cards
        setup.hands[pid] = []
        setup.mulligan_counts[pid] = 0

    # ----------------------------
    # 3) Draw opening 7
    # ----------------------------
    for pid in players:
        setup.hands[pid] = setup.libraries[pid][:7]
        setup.libraries[pid] = setup.libraries[pid][7:]

    # ----------------------------
    # 4) Mulligan loop (London)
    # ----------------------------
    for pid, ps in players.items():
        control = controls.get(pid, "human")

        # ============================
        # AI-controlled player
        # ============================
        if ps.is_ai:
            decider = pregame_deciders.get(control)
            if decider is None:
                raise RuntimeError(f"No pregame decider configured for control '{control}'")
            while True:
                # Safety guard: force keep at 1 card
                if setup.mulligan_counts[pid] >= 6:
                    break

                ctx = MulliganContext(
                    player_id=pid,
                    deck_name=ps.deck_name,
                    on_play=(pid == setup.starting_player_id),
                    mulligans_taken=setup.mulligan_counts[pid],
                    hand=[
                        CardView(ci.instance_id, ci.card_id)
                        for ci in setup.hands[pid]
                    ],
                )

                decision = decider.decide_mulligan(ctx)
                if journal is not None:
                    journal.append(
                        {
                            "game_id": game_id,
                            "actor": control,
                            "player_id": pid,
                            "event": "mulligan_decision",
                            "deck_name": ps.deck_name,
                            "on_play": (pid == setup.starting_player_id),
                            "mulligans_taken": setup.mulligan_counts[pid],
                            "hand": [{"instance_id": c.instance_id, "card_id": c.card_id} for c in setup.hands[pid]],
                            "decision": decision.decision,
                            "reasoning": decision.reasoning,
                        }
                    )
                if decision.decision == "KEEP":
                    break

                # Take mulligan
                setup.mulligan_counts[pid] += 1
                setup.libraries[pid].extend(setup.hands[pid])
                rng.shuffle(setup.libraries[pid])
                setup.hands[pid] = setup.libraries[pid][:7]
                setup.libraries[pid] = setup.libraries[pid][7:]

            # London bottom (AI)
            to_bottom = setup.mulligan_counts[pid]
            if to_bottom > 0:
                bottom_ctx = BottomContext(
                    player_id=pid,
                    deck_name=ps.deck_name,
                    bottoming_required=to_bottom,
                    hand=[
                        CardView(ci.instance_id, ci.card_id)
                        for ci in setup.hands[pid]
                    ],
                )

                bottom_decision = decider.decide_bottom(bottom_ctx)
                if journal is not None:
                    journal.append(
                        {
                            "game_id": game_id,
                            "actor": control,
                            "player_id": pid,
                            "event": "bottom_decision",
                            "deck_name": ps.deck_name,
                            "bottoming_required": to_bottom,
                            "hand": [{"instance_id": c.instance_id, "card_id": c.card_id} for c in setup.hands[pid]],
                            "bottom": list(bottom_decision.bottom),
                            "reasoning": bottom_decision.reasoning,
                        }
                    )

                for instance_id in bottom_decision.bottom:
                    for i, ci in enumerate(setup.hands[pid]):
                        if ci.instance_id == instance_id:
                            setup.libraries[pid].append(setup.hands[pid].pop(i))
                            break

        # ============================
        # Human-controlled player
        # ============================
        else:
            while True:
                print(f"\n{pid} opening hand:")
                for i, c in enumerate(setup.hands[pid]):
                    print(f"  [{i}] {c.card_id}")

                raw = input("Keep? (y/n): ").strip().lower()

                if raw == "y":
                    if journal is not None:
                        journal.append(
                            {
                                "game_id": game_id,
                                "actor": "user",
                                "player_id": pid,
                                "event": "mulligan_decision",
                                "deck_name": ps.deck_name,
                                "on_play": (pid == setup.starting_player_id),
                                "mulligans_taken": setup.mulligan_counts[pid],
                                "hand": [{"instance_id": c.instance_id, "card_id": c.card_id} for c in setup.hands[pid]],
                                "decision": "KEEP",
                                "reasoning": None,
                            }
                        )
                    break
                if raw != "n":
                    print("Please enter y or n.")
                    continue

                mulligans_taken = setup.mulligan_counts[pid]
                if journal is not None:
                    journal.append(
                        {
                            "game_id": game_id,
                            "actor": "user",
                            "player_id": pid,
                            "event": "mulligan_decision",
                            "deck_name": ps.deck_name,
                            "on_play": (pid == setup.starting_player_id),
                            "mulligans_taken": mulligans_taken,
                            "hand": [{"instance_id": c.instance_id, "card_id": c.card_id} for c in setup.hands[pid]],
                            "decision": "MULLIGAN",
                            "reasoning": None,
                        }
                    )
                setup.mulligan_counts[pid] += 1
                setup.libraries[pid].extend(setup.hands[pid])
                rng.shuffle(setup.libraries[pid])
                setup.hands[pid] = setup.libraries[pid][:7]
                setup.libraries[pid] = setup.libraries[pid][7:]

            # London bottom (Human)
            to_bottom = setup.mulligan_counts[pid]
            if to_bottom > 0:
                for _ in range(to_bottom):
                    print("\nChoose card to bottom:")
                    for i, c in enumerate(setup.hands[pid]):
                        print(f"  [{i}] {c.card_id}")

                    while True:
                        raw = input("Index: ").strip()
                        if raw.isdigit():
                            idx = int(raw)
                            if 0 <= idx < len(setup.hands[pid]):
                                break
                        print("Choice out of range.")

                    card = setup.hands[pid].pop(idx)
                    setup.libraries[pid].append(card)

                if journal is not None:
                    journal.append(
                        {
                            "game_id": game_id,
                            "actor": "user",
                            "player_id": pid,
                            "event": "bottom_decision",
                            "deck_name": ps.deck_name,
                            "bottoming_required": to_bottom,
                            "bottom": [ci.instance_id for ci in setup.libraries[pid][-to_bottom:]],
                            "reasoning": None,
                        }
                    )
                print(f"[London] {pid} bottoms {to_bottom} card(s).")

    # ----------------------------
    # 6) Commit hands + libraries into PlayerState
    # ----------------------------
    for pid, ps in players.items():
        ps.hand = list(setup.hands[pid])
        ps.library = list(setup.libraries[pid])

        ps.mulligans_taken = setup.mulligan_counts[pid]
        ps.has_kept_hand = True
        ps.bottoming_required = 0

    return setup

# ============================
# Main
# ============================

def prompt_for_control(player_id: str) -> str:
    print(f"\nControl type for {player_id}:")
    print("  [0] Human (DPG/TUI)")
    print("  [1] Bob (local)")
    print("  [2] GPT (remote)")

    while True:
        raw = input("Choose control: ").strip()
        if not raw.isdigit():
            print("Please enter a number.")
            continue

        idx = int(raw)
        if idx == 0:
            return "human"
        if idx == 1:
            return "bob"
        if idx == 2:
            return "gpt"

        print("Choice out of range.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MTG Engine Runner")
    parser.add_argument(
        "--ui",
        choices=["tui", "plain", "dpg"],
        default="dpg",
        help="UI for human players (tui, plain, dpg). Default: dpg",
    )
    parser.add_argument("--cli", action="store_true", help="Use basic CLI for human players")
    return parser.parse_args()


def _prompt_reasoning() -> Optional[str]:
    raw = input("Reasoning (optional, blank to skip): ").strip()
    return raw or None


def _prompt_discuss() -> bool:
    raw = input("Discuss with Bob now? (y/n): ").strip().lower()
    return raw in {"y", "yes"}


def _run_discussion_loop(
    *,
    chat_client: Any,
    journal: GameJournal,
    game_id: str,
    player_id: str,
    visible: Any,
    action: Action,
    result: Any,
    reasoning: str,
) -> None:
    context = {
        "player_id": player_id,
        "visible": serialize_visible_state_minimal(visible),
        "action": _serialize_action(action),
        "result": _serialize_result(result),
        "reasoning": reasoning,
    }

    journal.append(
        {
            "game_id": game_id,
            "event": "discussion_context",
            "player_id": player_id,
            "context": context,
        }
    )

    system = (
        "You are Bob, a Magic: The Gathering coach.\n"
        "Discuss the player's reasoning and the current game state.\n"
        "Be concise and tactical. Ask at most one question if needed."
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(context)},
    ]

    resp = chat_client.chat_text(
        messages=messages,
        temperature=0.4,
        max_tokens=400,
        timeout_s=120,
    ).strip()

    if resp:
        print(f"Bob: {resp}")
        journal.append(
            {
                "game_id": game_id,
                "event": "discussion_turn",
                "player_id": player_id,
                "role": "assistant",
                "text": resp,
            }
        )
        messages.append({"role": "assistant", "content": resp})

    while True:
        user_msg = input("You (discuss, blank to end): ").strip()
        if not user_msg:
            break
        journal.append(
            {
                "game_id": game_id,
                "event": "discussion_turn",
                "player_id": player_id,
                "role": "user",
                "text": user_msg,
            }
        )
        messages.append({"role": "user", "content": user_msg})
        resp = chat_client.chat_text(
            messages=messages,
            temperature=0.4,
            max_tokens=400,
            timeout_s=120,
        ).strip()
        if resp:
            print(f"Bob: {resp}")
            journal.append(
                {
                    "game_id": game_id,
                    "event": "discussion_turn",
                    "player_id": player_id,
                    "role": "assistant",
                    "text": resp,
                }
            )
            messages.append({"role": "assistant", "content": resp})


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


def _make_human_logger(
    *,
    journal: Optional[GameJournal],
    game_id: str,
    actor_label: str,
    chat_client: Optional[Any],
    enable_reasoning: bool,
):
    def _log(visible, action, result, reasoning: Optional[str]) -> None:
        if journal is None:
            return
        if reasoning is None and enable_reasoning:
            reasoning = _prompt_reasoning()
        journal.append(
            {
                "game_id": game_id,
                "actor": actor_label,
                "player_id": getattr(action, "actor_id", None),
                "visible": serialize_visible_state_minimal(visible),
                "action": _serialize_action(action),
                "result": _serialize_result(result),
                "decision": {"reasoning": reasoning},
            }
        )
        if reasoning and chat_client is not None:
            if _prompt_discuss():
                _run_discussion_loop(
                    chat_client=chat_client,
                    journal=journal,
                    game_id=game_id,
                    player_id=getattr(action, "actor_id", None),
                    visible=visible,
                    action=action,
                    result=result,
                    reasoning=reasoning,
                )

    return _log


class DiscussionManager:
    def __init__(self, *, chat_client: Optional[Any], journal: Optional[GameJournal], game_id: str, player_id: str) -> None:
        self.chat_client = chat_client
        self.journal = journal
        self.game_id = game_id
        self.player_id = player_id
        self._context: Optional[Dict[str, Any]] = None
        self._messages: List[Dict[str, str]] = []

    def set_context(self, *, visible: Any, action: Action, result: Any, reasoning: Optional[str]) -> None:
        self._context = {
            "player_id": self.player_id,
            "visible": serialize_visible_state_minimal(visible),
            "action": _serialize_action(action),
            "result": _serialize_result(result),
            "reasoning": reasoning,
        }
        self._messages = []
        if self.journal is not None:
            self.journal.append(
                {
                    "game_id": self.game_id,
                    "event": "discussion_context",
                    "player_id": self.player_id,
                    "context": self._context,
                }
            )

    def start(self) -> Optional[str]:
        if self.chat_client is None or self._context is None:
            return None
        system = (
            "You are Bob, a Magic: The Gathering coach.\n"
            "Discuss the player's reasoning and the current game state.\n"
            "Be concise and tactical. Ask at most one question if needed."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(self._context)},
        ]
        resp = self.chat_client.chat_text(
            messages=messages,
            temperature=0.4,
            max_tokens=400,
            timeout_s=120,
        ).strip()
        self._messages = list(messages)
        if resp:
            self._messages.append({"role": "assistant", "content": resp})
            if self.journal is not None:
                self.journal.append(
                    {
                        "game_id": self.game_id,
                        "event": "discussion_turn",
                        "player_id": self.player_id,
                        "role": "assistant",
                        "text": resp,
                    }
                )
            return resp
        return None

    def send(self, user_msg: str) -> Optional[str]:
        if self.chat_client is None or self._context is None:
            return None
        if not self._messages:
            self.start()
        self._messages.append({"role": "user", "content": user_msg})
        if self.journal is not None:
            self.journal.append(
                {
                    "game_id": self.game_id,
                    "event": "discussion_turn",
                    "player_id": self.player_id,
                    "role": "user",
                    "text": user_msg,
                }
            )
        resp = self.chat_client.chat_text(
            messages=self._messages,
            temperature=0.4,
            max_tokens=400,
            timeout_s=120,
        ).strip()
        if resp:
            self._messages.append({"role": "assistant", "content": resp})
            if self.journal is not None:
                self.journal.append(
                    {
                        "game_id": self.game_id,
                        "event": "discussion_turn",
                        "player_id": self.player_id,
                        "role": "assistant",
                        "text": resp,
                    }
                )
            return resp
        return None


def run_interactive(*, ui: str) -> None:
    cfg = load_config()
    card_db = load_card_db(CARD_DB_PATH)
    decks = load_decks()

    p1_control = prompt_for_control("P1")
    p2_control = prompt_for_control("P2")

    # Deck selection is always done by the human runner.
    p1_deck = prompt_for_deck("P1", decks)
    p2_deck = prompt_for_deck("P2", decks)

    p1 = build_player_from_deck("P1", p1_deck, is_ai=(p1_control != "human"))
    p2 = build_player_from_deck("P2", p2_deck, is_ai=(p2_control != "human"))

    players = {"P1": p1, "P2": p2}
    controls = {"P1": p1_control, "P2": p2_control}

    game_id = str(uuid.uuid4())
    journal = GameJournal(os.path.join(cfg.runtime_dir, "mtg", game_id, "journal.jsonl"))

    journal.append(
        {
            "game_id": game_id,
            "event": "match_start",
            "players": {
                "P1": {"control": p1_control, "deck": p1_deck["name"]},
                "P2": {"control": p2_control, "deck": p2_deck["name"]},
            },
        }
    )

    local_client = OpenAICompatClient(ChatModel(cfg.local.base_url, cfg.local.api_key, cfg.local.model))
    remote_client = OpenAICompatClient(ChatModel(cfg.mtg_remote.base_url, cfg.mtg_remote.api_key, cfg.mtg_remote.model))

    pregame_deciders = {
        "bob": AIPregameDecider(chat_client=local_client),
        "gpt": AIPregameDecider(chat_client=remote_client),
    }
    live_deciders = {
        "bob": LiveAIDecider(chat_client=local_client),
        "gpt": LiveAIDecider(chat_client=remote_client),
    }

    discussion_client: Optional[Any] = None
    if any(c == "human" for c in controls.values()):
        discussion_client = local_client

    setup = run_match_setup(
        players,
        controls=controls,
        pregame_deciders=pregame_deciders,
        journal=journal,
        game_id=game_id,
    )
    journal.append(
        {
            "game_id": game_id,
            "event": "pregame_complete",
            "seed": setup.seed,
            "starting_player_id": setup.starting_player_id,
            "mulligans_taken": dict(setup.mulligan_counts),
        }
    )

    gs = GameState(
        game_id=game_id,
        players=players,
        card_db=card_db,
        starting_player_id=setup.starting_player_id or "P1",
        turn=TurnState(
            active_player_id=setup.starting_player_id or "P1",
            turn_number=1,
            phase=Phase.BEGINNING,
            step=Step.UNTAP,
        ),
        zones=GlobalZones(),
        rng=RandomState(seed=setup.seed),
        metadata=GameMetadata(),
    )

    engine = MTGEngine(gs)
    actions_ai = ActionSurface(allow_scoop=False)

    controllers = {}
    ui = (ui or "dpg").strip().lower()
    use_cli_ui = ui == "plain"
    use_dpg_ui = ui == "dpg"
    tui_available = ui == "tui"
    if tui_available:
        try:
            from mtg_core.tui_base import TUIPlayer
        except Exception as e:
            print(
                f"[mtg] TUI unavailable ({e}); falling back to CLI."
            )
            tui_available = False
            use_cli_ui = True
    if use_dpg_ui:
        try:
            from mtg_core.dpg_ui import DPGPlayer, HoldPriorityGate
        except Exception as e:
            raise RuntimeError(f"DPG UI unavailable: {e}") from e

        hold_gate = HoldPriorityGate()
    else:
        DPGPlayer = None  # type: ignore[assignment]
        hold_gate = None
    for pid, ps in players.items():
        control = controls.get(pid, "human")
        if ps.is_ai:
            decider = live_deciders.get(control)
            if decider is None:
                raise RuntimeError(f"No live decider configured for control '{control}'")
            controllers[pid] = AIPlayer(
                engine,
                actions_ai,
                pid,
                decider=decider,
                journal=journal,
                game_id=game_id,
                actor_label=control,
                hold_gate=hold_gate,
            )
        else:
            discussion_mgr = DiscussionManager(
                chat_client=discussion_client,
                journal=journal,
                game_id=game_id,
                player_id=pid,
            )
            log_action = _make_human_logger(
                journal=journal,
                game_id=game_id,
                actor_label="user",
                chat_client=discussion_client,
                enable_reasoning=use_cli_ui,
            )
            def _wrapped_log(visible, action, result, reasoning: Optional[str]) -> None:
                log_action(visible, action, result, reasoning)
                discussion_mgr.set_context(
                    visible=visible,
                    action=action,
                    result=result,
                    reasoning=reasoning,
                )
            if use_dpg_ui and DPGPlayer is not None:
                controllers[pid] = DPGPlayer(
                    engine,
                    pid,
                    runtime_dir=cfg.runtime_dir,
                    game_id=game_id,
                    hold_gate=hold_gate,
                    discussion_start=discussion_mgr.start,
                    discussion_send=discussion_mgr.send,
                    on_action=_wrapped_log,
                )
            elif tui_available:
                controllers[pid] = TUIPlayer(engine, pid, on_action=_wrapped_log, collect_reasoning=True)
            else:
                controllers[pid] = CLIPlayer(engine, pid, on_action=_wrapped_log)


    # Simple turn driver: call both controllers; only the priority holder will have legal actions.
    while True:
        if gs.game_over:
            print(f"\nGame over. Winner: {gs.winner_id}. Reason: {gs.reason}.")
            break
        for pid in ("P1", "P2"):
            controllers[pid].loop()

    for controller in controllers.values():
        if hasattr(controller, "shutdown"):
            try:
                controller.shutdown()
            except Exception:
                pass


def main() -> None:
    args = parse_args()
    if args.cli:
        run_interactive(ui="plain")
        return
    run_interactive(ui=args.ui)


if __name__ == "__main__":
    main()
