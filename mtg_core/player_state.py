from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from mtg_core.game_state import CardInstance


@dataclass
class ManaPool:
    colored: Dict[str, int] = field(default_factory=dict)  # keys: "BLACK", "RED", etc
    generic: int = 0

    def clear(self) -> None:
        self.colored.clear()
        self.generic = 0

    def validate(self) -> None:
        if self.generic < 0:
            raise ValueError("ManaPool.generic must be >= 0")
        for k, v in self.colored.items():
            if not isinstance(k, str) or not k:
                raise ValueError("ManaPool.colored keys must be non-empty strings")
            if not isinstance(v, int) or v < 0:
                raise ValueError("ManaPool.colored values must be ints >= 0")


@dataclass
class PlayerState:
    player_id: str

    # Control surface
    is_ai: bool = False 

    # Hidden/public zones
    library: List[CardInstance] = field(default_factory=list)
    hand: List[CardInstance] = field(default_factory=list)
    graveyard: List[CardInstance] = field(default_factory=list)

    # Core stats
    life: int = 20
    mana_pool: ManaPool = field(default_factory=ManaPool)

    # Turn-scoped
    lands_played_this_turn: int = 0

    # Pregame mulligan (London)
    mulligans_taken: int = 0
    has_kept_hand: bool = False
    bottoming_required: int = 0
        # Pregame / metadata
    deck_name: Optional[str] = None # Optional name of the deck being used

    def validate(self) -> None:
        if not self.player_id:
            raise ValueError("PlayerState.player_id must be non-empty")
        if not isinstance(self.is_ai, bool):
            raise ValueError("PlayerState.is_ai must be bool")
        if self.life <= 0:
            # phase-1 simplicity: life should be positive at start; game-over later
            raise ValueError("PlayerState.life must be > 0")

        for zone_name, zone in (("library", self.library), ("hand", self.hand), ("graveyard", self.graveyard)):
            for ci in zone:
                if not isinstance(ci, CardInstance):
                    raise ValueError(f"{zone_name} must contain CardInstance")
                ci.validate()
                if ci.owner_id != self.player_id:
                    raise ValueError(f"{zone_name} CardInstance.owner_id mismatch (expected {self.player_id})")

        if self.lands_played_this_turn < 0:
            raise ValueError("lands_played_this_turn must be >= 0")

        if self.mulligans_taken < 0:
            raise ValueError("mulligans_taken must be >= 0")
        if self.bottoming_required < 0:
            raise ValueError("bottoming_required must be >= 0")

        self.mana_pool.validate()
