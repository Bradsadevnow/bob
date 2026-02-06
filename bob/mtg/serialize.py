from __future__ import annotations

from typing import Any, Dict

from mtg_core.aibase import VisibleState


def serialize_visible_state_minimal(visible: VisibleState) -> Dict[str, Any]:
    """
    Serialize a compact VisibleState payload for LLM prompts and journaling.

    Intentional omissions:
    - card_db (can be large; action_schema already contains card metadata)
    """
    zones = visible.zones

    battlefield = []
    for perm in zones.battlefield:
        battlefield.append(
            {
                "instance_id": getattr(perm, "instance_id", None),
                "card_id": getattr(perm, "card_id", None),
                "name": getattr(perm, "name", None),
                "controller_id": getattr(perm, "controller_id", None),
                "tapped": bool(getattr(perm, "tapped", False)),
                "power": getattr(perm, "power", None),
                "toughness": getattr(perm, "toughness", None),
                "damage_marked": getattr(perm, "damage_marked", None),
            }
        )

    hand = []
    for ci in zones.hand:
        hand.append(
            {
                "instance_id": getattr(ci, "instance_id", None),
                "card_id": getattr(ci, "card_id", None),
                "name": getattr(ci, "name", None),
                "mana_cost": getattr(ci, "mana_cost", None),
                "card_type": getattr(ci, "card_type", None),
                "power": getattr(ci, "power", None),
                "toughness": getattr(ci, "toughness", None),
            }
        )

    stack = []
    for item in zones.stack:
        stack.append(
            {
                "instance_id": getattr(item, "instance_id", None),
                "card_id": getattr(item, "card_id", None),
                "name": getattr(item, "name", None),
                "controller_id": getattr(item, "controller_id", None),
                "targets": getattr(item, "targets", None),
            }
        )

    graveyards: Dict[str, Any] = {}
    if isinstance(zones.graveyards, dict):
        for pid, gy in zones.graveyards.items():
            if isinstance(gy, list):
                graveyards[pid] = [
                    {"instance_id": getattr(ci, "instance_id", None), "card_id": getattr(ci, "card_id", None)}
                    for ci in gy
                ]
            else:
                graveyards[pid] = gy

    exile: Dict[str, Any] = {}
    if isinstance(zones.exile, dict):
        for key, ci in zones.exile.items():
            exile[str(key)] = {
                "instance_id": getattr(ci, "instance_id", None),
                "card_id": getattr(ci, "card_id", None),
                "owner_id": getattr(ci, "owner_id", None),
            }

    return {
        "turn_number": visible.turn_number,
        "active_player_id": visible.active_player_id,
        "phase": visible.phase,
        "priority_holder_id": visible.priority_holder_id,
        "life_totals": dict(visible.life_totals),
        "lands_played_this_turn": int(getattr(visible, "lands_played_this_turn", 0) or 0),
        "available_mana": visible.available_mana,
        "combat_attackers": list(getattr(visible, "combat_attackers", [])),
        "combat_blockers": dict(getattr(visible, "combat_blockers", {})),
        "combat_attackers_declared": bool(getattr(visible, "combat_attackers_declared", False)),
        "combat_blockers_declared": bool(getattr(visible, "combat_blockers_declared", False)),
        "zones": {
            "hand": hand,
            "battlefield": battlefield,
            "stack": stack,
            "graveyards": graveyards,
            "exile": exile,
            "library_size": zones.library_size,
        },
        "game_over": bool(getattr(visible, "game_over", False)),
        "winner_id": getattr(visible, "winner_id", None),
        "end_reason": getattr(visible, "end_reason", None),
    }

