from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
import random
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mtg_core.player_state import PlayerState

# ============================
# Enums
# ============================

class Phase(str, Enum):
    BEGINNING = "BEGINNING"
    MAIN = "MAIN"
    COMBAT = "COMBAT"
    ENDING = "ENDING"


class Step(str, Enum):
    UNTAP = "UNTAP"
    DRAW = "DRAW"
    MAIN1 = "MAIN1"
    DECLARE_ATTACKERS = "DECLARE_ATTACKERS"
    DECLARE_BLOCKERS = "DECLARE_BLOCKERS"
    DAMAGE = "DAMAGE"
    MAIN2 = "MAIN2"
    END = "END"


# ============================
# Card / Permanent Instances
# ============================

@dataclass
class CardInstance:
    """
    A physical card in a game.
    Exists in exactly one zone at a time.
    """
    instance_id: str
    card_id: str
    owner_id: str
    is_token: bool = False

    def validate(self) -> None:
        if not self.instance_id:
            raise ValueError("CardInstance.instance_id must be non-empty")
        if not self.card_id:
            raise ValueError("CardInstance.card_id must be non-empty")
        if not self.owner_id:
            raise ValueError("CardInstance.owner_id must be non-empty")


@dataclass
class PermanentState:
    tapped: bool = False
    damage_marked: int = 0
    counters: Dict[str, int] = field(default_factory=lambda: {"+1/+1": 0, "-1/-1": 0})
    summoning_sick: bool = True
    attached_to: Optional[str] = None  # for auras/equipment
    goaded_by: Optional[str] = None
    goaded_until_turn: Optional[int] = None
    draw_on_attack_by: Optional[str] = None
    draw_on_attack_until_turn: Optional[int] = None

    def validate(self) -> None:
        if self.damage_marked < 0:
            raise ValueError("PermanentState.damage_marked must be >= 0")
        for k in ("+1/+1", "-1/-1"):
            if k not in self.counters:
                raise ValueError(f"PermanentState.counters missing key {k!r}")
            if self.counters[k] < 0:
                raise ValueError(f"PermanentState.counters[{k!r}] must be >= 0")


@dataclass
class Permanent:
    """
    A card on the battlefield with mutable state.
    """
    instance: CardInstance
    controller_id: str
    state: PermanentState = field(default_factory=PermanentState)

    def validate(self) -> None:
        self.instance.validate()
        if not self.controller_id:
            raise ValueError("Permanent.controller_id must be non-empty")
        self.state.validate()


@dataclass
class StackItemKind(str, Enum):
    SPELL = "SPELL"
    ABILITY = "ABILITY"


@dataclass
class StackItem:
    kind: StackItemKind
    controller_id: str
    instance: Optional[CardInstance] = None  # for spells
    source_instance_id: Optional[str] = None  # for abilities
    effects: Optional[List[Any]] = None
    targets: Optional[Any] = None
    meta: Optional[Dict[str, Any]] = None

    def validate(self) -> None:
        if not self.controller_id:
            raise ValueError("StackItem.controller_id must be non-empty")
        if self.kind == StackItemKind.SPELL:
            if self.instance is None:
                raise ValueError("StackItem.instance required for SPELL")
            self.instance.validate()


# ============================
# Turn State
# ============================

@dataclass
class TurnState:
    active_player_id: str
    turn_number: int
    phase: Phase
    step: Step
    attackers: List[str] = field(default_factory=list)
    blockers: Dict[str, List[str]] = field(default_factory=dict)
    attackers_declared: bool = False
    blockers_declared: bool = False

    def validate(self) -> None:
        if not self.active_player_id:
            raise ValueError("TurnState.active_player_id must be non-empty")
        if self.turn_number < 1:
            raise ValueError("TurnState.turn_number must be >= 1")
        if not isinstance(self.phase, Phase):
            raise ValueError("TurnState.phase must be Phase")
        if not isinstance(self.step, Step):
            raise ValueError("TurnState.step must be Step")
        if not isinstance(self.attackers, list):
            raise ValueError("TurnState.attackers must be a list")
        for aid in self.attackers:
            if not isinstance(aid, str) or not aid:
                raise ValueError("TurnState.attackers must contain non-empty strings")
        if not isinstance(self.blockers, dict):
            raise ValueError("TurnState.blockers must be a dict")
        for atk, blk in self.blockers.items():
            if not isinstance(atk, str) or not atk:
                raise ValueError("TurnState.blockers keys must be non-empty strings")
            if not isinstance(blk, list):
                raise ValueError("TurnState.blockers values must be lists")
            for bid in blk:
                if not isinstance(bid, str) or not bid:
                    raise ValueError("TurnState.blockers entries must be non-empty strings")
        if not isinstance(self.attackers_declared, bool):
            raise ValueError("TurnState.attackers_declared must be bool")
        if not isinstance(self.blockers_declared, bool):
            raise ValueError("TurnState.blockers_declared must be bool")


# ============================
# RNG State
# ============================

@dataclass
class RandomState:
    seed: int
    rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    def validate(self) -> None:
        # Nothing fancy yet, but this gives us a hook later
        pass


# ============================
# Global Zones
# ============================

@dataclass
class GlobalZones:
    battlefield: Dict[str, Permanent] = field(default_factory=dict)
    exile: Dict[str, CardInstance] = field(default_factory=dict)
    stack: List[StackItem] = field(default_factory=list)

    def validate(self) -> None:
        for pid, perm in self.battlefield.items():
            if pid != perm.instance.instance_id:
                raise ValueError("Battlefield key must match Permanent.instance_id")
            perm.validate()

        for cid, card in self.exile.items():
            if cid != card.instance_id:
                raise ValueError("Exile key must match CardInstance.instance_id")
            card.validate()

        for item in self.stack:
            if not isinstance(item, StackItem):
                raise ValueError("Stack must contain StackItem objects")
            item.validate()


# ============================
# Game Metadata
# ============================

@dataclass
class GameMetadata:
    history: List[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        self.history.append(message)


# ============================
# Temporary Effects / Decisions
# ============================


@dataclass
class TemporaryEffect:
    effect: Any
    source_instance_id: Optional[str]
    controller_id: Optional[str]
    expires_turn: int
    expires_step: Optional[Step] = None


@dataclass
class PendingDecision:
    player_id: str
    kind: str
    options: Any
    context: Dict[str, Any] = field(default_factory=dict)


# ============================
# GameState
# ============================

@dataclass
class GameState:
    game_id: str

    players: Dict[str, "PlayerState"]
    card_db: Dict[str, Any]
    starting_player_id: str

    turn: TurnState
    zones: GlobalZones
    rng: RandomState
    metadata: GameMetadata = field(default_factory=GameMetadata)
    temporary_effects: List[TemporaryEffect] = field(default_factory=list)
    exile_links: Dict[str, str] = field(default_factory=dict)  # exiled_instance_id -> source_instance_id
    damage_dealt_to_players: Dict[str, int] = field(default_factory=dict)
    pending_decision: Optional[PendingDecision] = None
    extra_turns: List[str] = field(default_factory=list)
    game_over: bool = False
    winner_id: Optional[str] = None
    reason: Optional[str] = None

    def validate(self) -> None:
        if not self.game_id:
            raise ValueError("GameState.game_id must be non-empty")

        if len(self.players) != 2:
            raise ValueError("Phase-1 GameState must have exactly 2 players")

        for pid, player in self.players.items():
            if pid != player.player_id:
                raise ValueError("PlayerState.player_id mismatch")
            player.validate()

        if not isinstance(self.card_db, dict):
            raise ValueError("GameState.card_db must be a dict")

        self.turn.validate()
        self.zones.validate()
        self.rng.validate()

        if self.turn.active_player_id not in self.players:
            raise ValueError("Active player must exist in players")
        if self.starting_player_id not in self.players:
            raise ValueError("Starting player must exist in players")

        if self.game_over:
            if self.winner_id is not None and self.winner_id not in self.players:
                raise ValueError("Winner must be a valid player id")

        # Invariant: all battlefield permanents must belong to a known player
        for perm in self.zones.battlefield.values():
            if perm.controller_id not in self.players:
                raise ValueError("Permanent.controller_id must be a valid player")

        for pid in self.damage_dealt_to_players.keys():
            if pid not in self.players:
                raise ValueError("damage_dealt_to_players must reference valid players")

        if self.pending_decision is not None:
            if self.pending_decision.player_id not in self.players:
                raise ValueError("pending_decision.player_id must be valid")

        for pid in self.extra_turns:
            if pid not in self.players:
                raise ValueError("extra_turns must reference valid players")

    # ============================
    # Factory
    # ============================

    @staticmethod
    def new_game(players: Dict[str, "PlayerState"], seed: Optional[int] = None) -> "GameState":
        if seed is None:
            seed = random.randrange(1 << 30)

        starting_player_id = list(players.keys())[0]

        game = GameState(
            game_id=str(uuid.uuid4()),
            players=players,
            card_db={},
            starting_player_id=starting_player_id,
            turn=TurnState(
                active_player_id=starting_player_id,
                turn_number=1,
                phase=Phase.BEGINNING,
                step=Step.UNTAP,
            ),
            zones=GlobalZones(),
            rng=RandomState(seed=seed),
        )

        game.validate()
        return game
