from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class ActionType(str, Enum):
    # Pregame (London mulligan)
    MULLIGAN = "MULLIGAN"
    KEEP_HAND = "KEEP_HAND"
    BOTTOM_CARD = "BOTTOM_CARD"

    # Game actions (future)
    PLAY_LAND = "PLAY_LAND"
    TAP_FOR_MANA = "TAP_FOR_MANA"
    CAST_SPELL = "CAST_SPELL"
    ACTIVATE_ABILITY = "ACTIVATE_ABILITY"
    DECLARE_ATTACKERS = "DECLARE_ATTACKERS"
    DECLARE_BLOCKERS = "DECLARE_BLOCKERS"
    PASS_PRIORITY = "PASS_PRIORITY"
    RESOLVE_DECISION = "RESOLVE_DECISION"
    SCOOP = "SCOOP"
    SKIP_COMBAT = "SKIP_COMBAT"
    SKIP_MAIN2 = "SKIP_MAIN2"


@dataclass(frozen=True)
class Action:
    """
    Canonical action schema.

    - actor_id: the player performing the action
    - object_id: optional primary object (card instance id, permanent id, etc.)
    - targets: optional structured targets payload (list/dict/etc.)
    - payload: optional extra action-specific payload

    This matches how your engine.py currently creates and reads actions.
    """
    type: ActionType
    actor_id: str
    object_id: Optional[str] = None
    targets: Optional[Any] = None
    payload: Optional[Any] = None

    # Back-compat shim (if any old code still expects player_id)
    @property
    def player_id(self) -> str:
        return self.actor_id
