#!/usr/bin/env python3
"""
mtg_core/aibase.py — v2 Shared schemas + thin player↔engine adapter

Rules:
- This file does NOT define Action or ActionType.
- Action/ActionType are canonical in mtg_core.actions.
- AIBase is a dumb adapter: it delegates to engine methods.
- This file is POST-GAME-START ONLY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from mtg_core.actions import Action, ActionType


# ============================
# Resolution result (Engine → Control Surfaces)
# ============================

class ResolutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"  # illegal action / rejected by validate()
    ERROR = "ERROR"      # engine exception / bug


@dataclass
class ResolutionResult:
    status: ResolutionStatus
    message: Optional[str] = None
    payload: Optional[Any] = None


# ============================
# Visible state schema (Engine → Surfaces)
# ============================

@dataclass
class ZonesView:
    battlefield: List[Any] = field(default_factory=list)
    stack: List[Any] = field(default_factory=list)
    graveyards: Any = field(default_factory=dict)
    exile: Any = field(default_factory=dict)
    hand: List[Any] = field(default_factory=list)
    library_size: int = 0


@dataclass
class VisibleState:
    turn_number: int
    active_player_id: str
    phase: str
    priority_holder_id: str
    life_totals: Dict[str, int]
    zones: ZonesView
    card_db: Dict[str, Any] = field(default_factory=dict)
    available_mana: Optional[Any] = None
    lands_played_this_turn: int = 0
    stack: List[Any] = field(default_factory=list)
    combat_attackers: List[str] = field(default_factory=list)
    combat_blockers: Dict[str, str] = field(default_factory=dict)
    combat_attackers_declared: bool = False
    combat_blockers_declared: bool = False
    pending_decision: Optional[Any] = None
    game_over: bool = False
    winner_id: Optional[str] = None
    end_reason: Optional[str] = None


@dataclass(frozen=True)
class PermanentView:
    instance_id: str
    card_id: str
    name: str
    card_type: str
    mana_cost: Dict[str, Any]
    owner_id: str
    controller_id: str
    card_types: List[str] = field(default_factory=list)
    subtypes: List[str] = field(default_factory=list)
    power: Optional[int] = None
    toughness: Optional[int] = None
    keywords: List[str] = field(default_factory=list)
    tapped: bool = False
    damage_marked: int = 0
    counters: Dict[str, int] = field(default_factory=dict)
    summoning_sick: bool = False
    attached_to: Optional[str] = None
    attachments: List[str] = field(default_factory=list)
    cant_attack_players: List[str] = field(default_factory=list)
    must_attack: bool = False
    must_be_blocked_by_all: bool = False
    prevent_combat_damage: bool = False
    assign_damage_as_unblocked: bool = False
    goaded_by: Optional[str] = None


@dataclass(frozen=True)
class StackItemView:
    kind: str
    instance_id: str
    card_id: str
    name: str
    controller_id: str
    targets: Any = None
    source_instance_id: Optional[str] = None


@dataclass(frozen=True)
class HandCardView:
    instance_id: str
    card_id: str
    name: str
    card_type: str
    mana_cost: Dict[str, Any]
    power: Optional[int] = None
    toughness: Optional[int] = None


# ============================
# AIBase — thin adapter
# ============================

class AIBase:
    """
    Thin Player ↔ Engine adapter (POST-GAME-START ONLY).

    Contains ZERO rules knowledge.

    Expected engine methods:
      - get_visible_state(player_id) -> VisibleState
      - get_legal_actions(player_id) -> List[Action]
      - submit_action(action: Action) -> ResolutionResult
    """

    def __init__(self, engine: Any, player_id: str):
        self.engine = engine
        self.player_id = player_id

    def get_visible_state(self) -> VisibleState:
        return self.engine.get_visible_state(self.player_id)

    def get_legal_actions(self) -> List[Action]:
        return self.engine.get_legal_actions(self.player_id)

    def submit_action(self, action: Action) -> ResolutionResult:
        try:
            result = self.engine.submit_action(action)

            if not isinstance(result, ResolutionResult):
                return ResolutionResult(
                    status=ResolutionStatus.ERROR,
                    message="Engine returned non-ResolutionResult payload",
                    payload=result,
                )

            return result

        except Exception as e:
            return ResolutionResult(
                status=ResolutionStatus.ERROR,
                message=str(e),
                payload=None,
            )
