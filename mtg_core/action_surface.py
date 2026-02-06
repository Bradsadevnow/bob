from __future__ import annotations

import itertools
from typing import List, Optional, Dict, Any

from mtg_core.actions import Action, ActionType
from mtg_core.aibase import VisibleState
from mtg_core.cards import (
    CardType,
    EffectType,
    Selector,
    Zone,
    Keyword,
    CostType,
    TimingRestriction,
    TargetSpec,
)


class ActionSurface:
    """
    Computes legal actions for a player, using only VisibleState.

    IMPORTANT:
    - ActionSurface does NOT resolve actions.
    - ActionSurface does NOT mutate game state.
    - It only decides "what is legal right now?"
    """

    def __init__(self, *, allow_scoop: bool = True):
        self.allow_scoop = allow_scoop

    def get_legal_actions(self, visible: VisibleState, player_id: str) -> List[Action]:
        if getattr(visible, "pending_decision", None) is not None:
            pending = visible.pending_decision
            if getattr(pending, "player_id", None) != player_id:
                return []
            return self._decision_actions(pending, player_id)

        # Priority enforcement: only priority holder may act
        if player_id != visible.priority_holder_id:
            return []

        actions: List[Action] = []

        actions.extend(self._spell_actions(visible, player_id))
        actions.extend(self._ability_actions(visible, player_id))
        actions.extend(self._combat_actions(visible, player_id))
        actions.extend(self._skip_phase_actions(visible, player_id))

        for perm in visible.zones.battlefield:
            if self._can_tap_for_mana(perm, player_id):
                produces = self._basic_land_produces(getattr(perm, "card_id", ""))
                actions.append(
                    Action(
                        ActionType.TAP_FOR_MANA,
                        actor_id=player_id,
                        object_id=getattr(perm, "instance_id", None),
                        payload={"card_id": getattr(perm, "card_id", None), "produces": produces},
                    )
                )

        if self._can_play_land(visible, player_id):
            for ci in visible.zones.hand:
                if self._is_land(ci):
                    actions.append(
                        Action(
                            ActionType.PLAY_LAND,
                            actor_id=player_id,
                            object_id=ci.instance_id,
                            payload={"card_id": ci.card_id},
                        )
                    )

        if not self._pass_blocked_by_combat_declaration(visible, player_id):
            actions.append(Action(ActionType.PASS_PRIORITY, actor_id=player_id))
        if self.allow_scoop:
            actions.append(Action(ActionType.SCOOP, actor_id=player_id))
        return actions

    def get_action_schema(self, visible: VisibleState, player_id: str) -> Dict[str, Any]:
        if getattr(visible, "pending_decision", None) is not None:
            pending = visible.pending_decision
            if getattr(pending, "player_id", None) != player_id:
                return {"allowed_actions": []}
            return self._decision_schema(pending)

        if player_id != visible.priority_holder_id:
            return {"allowed_actions": []}

        schema: Dict[str, Any] = {"allowed_actions": []}

        # Play land
        if self._can_play_land(visible, player_id):
            schema["allowed_actions"].append(ActionType.PLAY_LAND.value)
            schema["play_land"] = {
                "choices": [
                    {
                        "instance_id": getattr(ci, "instance_id", None),
                        "card_id": getattr(ci, "card_id", None),
                        "name": getattr(ci, "name", None),
                        "mana_cost": getattr(ci, "mana_cost", None),
                    }
                    for ci in visible.zones.hand
                    if self._is_land(ci)
                ]
            }

        # Tap for mana
        tap_choices = []
        for perm in visible.zones.battlefield:
            if self._can_tap_for_mana(perm, player_id):
                tap_choices.append(
                    {
                        "instance_id": getattr(perm, "instance_id", None),
                        "card_id": getattr(perm, "card_id", None),
                        "name": getattr(perm, "name", None),
                        "produces": self._basic_land_produces(getattr(perm, "card_id", "")),
                    }
                )
        if tap_choices:
            schema["allowed_actions"].append(ActionType.TAP_FOR_MANA.value)
            schema["tap_for_mana"] = {"choices": tap_choices}

        # Cast spells
        cast_choices = []
        card_db = getattr(visible, "card_db", {}) or {}

        def add_cast_choice(ci: Any, card: Any, payload_base: Dict[str, Any], allow_x: bool, additional_costs: List[Dict[str, Any]]) -> None:
            for mode_payload, effects in self._expand_modal_effects(card):
                targets = self._enumerate_targets_for_effects(
                    effects,
                    visible,
                    player_id,
                    source_perm=None,
                )
                x_values = self._enumerate_x_values(card, visible, player_id) if allow_x else [None]
                cast_choices.append(
                    {
                        "instance_id": getattr(ci, "instance_id", None),
                        "card_id": getattr(ci, "card_id", None),
                        "name": getattr(ci, "name", None),
                        "mana_cost": getattr(ci, "mana_cost", None),
                        "mode_payload": mode_payload,
                        "x_values": x_values,
                        "targets": targets,
                        "additional_costs": additional_costs,
                        **payload_base,
                    }
                )

        for ci in visible.zones.hand:
            card_id = getattr(ci, "card_id", "")
            card = card_db.get(card_id)
            if card is None:
                continue
            if CardType.LAND in card.card_types:
                continue
            if not self._timing_allows_cast(card, visible, player_id):
                continue

            additional_costs = self._enumerate_additional_costs(
                card,
                visible,
                player_id,
                exclude_instance_id=getattr(ci, "instance_id", None),
            )
            if not additional_costs:
                continue

            if self._can_cast(card, visible, player_id):
                add_cast_choice(ci, card, {}, True, additional_costs)

            for alt in self._alternate_cost_options(card, visible, player_id):
                if self._can_pay_alternate_cost(alt, visible, player_id):
                    add_cast_choice(ci, card, {"alternate_cost": alt}, False, additional_costs)

        # Flashback from graveyard
        graveyard = (visible.zones.graveyards or {}).get(player_id, [])
        for ci in graveyard:
            card_id = getattr(ci, "card_id", "")
            card = card_db.get(card_id)
            if card is None:
                continue
            if CardType.LAND in card.card_types:
                continue
            if not getattr(card.rules, "flashback_cost", None):
                continue
            if not self._timing_allows_cast(card, visible, player_id):
                continue
            if not self._has_mana_cost(card.rules.flashback_cost, visible, player_id, card):
                continue
            additional_costs = self._enumerate_additional_costs(
                card,
                visible,
                player_id,
                exclude_instance_id=None,
            )
            if not additional_costs:
                continue
            add_cast_choice(ci, card, {"flashback": True}, False, additional_costs)

        if cast_choices:
            schema["allowed_actions"].append(ActionType.CAST_SPELL.value)
            schema["cast_spell"] = {"choices": cast_choices}

        # Activated abilities
        ability_choices = []
        for perm in visible.zones.battlefield:
            if getattr(perm, "controller_id", None) != player_id:
                continue
            card_id = getattr(perm, "card_id", "")
            card = card_db.get(card_id)
            if card is None:
                continue
            for idx, ability in enumerate(card.rules.activated_abilities):
                if not self._can_activate_ability(ability, perm, visible, player_id):
                    continue
                cost_choices = self._enumerate_cost_choices(ability.costs, perm, visible, player_id)
                if not cost_choices:
                    continue
                targets = self._enumerate_targets_for_effects(
                    ability.effects,
                    visible,
                    player_id,
                    source_perm=perm,
                )
                ability_choices.append(
                    {
                        "instance_id": getattr(perm, "instance_id", None),
                        "card_id": card_id,
                        "name": getattr(perm, "name", None),
                        "ability_index": idx,
                        "cost_choices": cost_choices,
                        "targets": targets,
                    }
                )
        if ability_choices:
            schema["allowed_actions"].append(ActionType.ACTIVATE_ABILITY.value)
            schema["activate_ability"] = {"choices": ability_choices}

        # Combat declarations
        if visible.phase == "DECLARE_ATTACKERS":
            if (
                visible.active_player_id == player_id
                and not visible.combat_attackers_declared
                and not visible.stack
            ):
                defender_id = self._other_player_id(visible, player_id)
                attackers = [
                    perm for perm in visible.zones.battlefield
                    if self._can_attack(perm, visible, player_id, defender_id)
                ]
                schema["allowed_actions"].append(ActionType.DECLARE_ATTACKERS.value)
                schema["declare_attackers"] = {
                    "attackers": [
                        {
                            "instance_id": getattr(p, "instance_id", None),
                            "card_id": getattr(p, "card_id", None),
                            "name": getattr(p, "name", None),
                        }
                        for p in attackers
                    ]
                }

        if visible.phase == "DECLARE_BLOCKERS":
            if (
                visible.active_player_id != player_id
                and not visible.combat_blockers_declared
                and not visible.stack
            ):
                blockers = [
                    perm for perm in visible.zones.battlefield
                    if getattr(perm, "controller_id", None) == player_id
                    and self._perm_is_creature(perm, visible)
                ]
                schema["allowed_actions"].append(ActionType.DECLARE_BLOCKERS.value)
                schema["declare_blockers"] = {
                    "attackers": list(visible.combat_attackers),
                    "blockers": [
                        {
                            "instance_id": getattr(p, "instance_id", None),
                            "card_id": getattr(p, "card_id", None),
                            "name": getattr(p, "name", None),
                        }
                        for p in blockers
                    ],
                }

        # Skip actions
        if (
            visible.active_player_id == player_id
            and not visible.stack
            and visible.phase == "MAIN1"
        ):
            schema["allowed_actions"].append(ActionType.SKIP_COMBAT.value)

        if (
            visible.active_player_id == player_id
            and not visible.stack
            and visible.phase == "MAIN2"
        ):
            schema["allowed_actions"].append(ActionType.SKIP_MAIN2.value)

        if not self._pass_blocked_by_combat_declaration(visible, player_id):
            schema["allowed_actions"].append(ActionType.PASS_PRIORITY.value)

        if self.allow_scoop:
            schema["allowed_actions"].append(ActionType.SCOOP.value)

        return schema

    def _can_play_land(self, visible: VisibleState, player_id: str) -> bool:
        if visible.active_player_id != player_id:
            return False

        if visible.phase not in ("MAIN1", "MAIN2"):
            return False

        if visible.lands_played_this_turn >= 1:
            return False

        return any(self._is_land(ci) for ci in visible.zones.hand)

    def _is_land(self, ci: object) -> bool:
        card_type = getattr(ci, "card_type", None)
        if isinstance(card_type, str) and card_type.upper() == CardType.LAND.value:
            return True
        card_id = getattr(ci, "card_id", "")
        return isinstance(card_id, str) and card_id.startswith("basic_")

    def _spell_actions(self, visible: VisibleState, player_id: str) -> List[Action]:
        actions: List[Action] = []
        if player_id != visible.priority_holder_id:
            return actions

        card_db = getattr(visible, "card_db", {}) or {}

        for ci in visible.zones.hand:
            card_id = getattr(ci, "card_id", "")
            card = card_db.get(card_id)
            if card is None:
                continue

            if CardType.LAND in card.card_types:
                continue

            if not self._timing_allows_cast(card, visible, player_id):
                continue

            additional_costs = self._enumerate_additional_costs(
                card,
                visible,
                player_id,
                exclude_instance_id=getattr(ci, "instance_id", None),
            )
            if not additional_costs:
                continue

            cast_modes: List[Dict[str, Any]] = []
            if self._can_cast(card, visible, player_id):
                cast_modes.append({"payload": {}, "allow_x": True})

            for alt in self._alternate_cost_options(card, visible, player_id):
                if self._can_pay_alternate_cost(alt, visible, player_id):
                    cast_modes.append({"payload": {"alternate_cost": alt}, "allow_x": False})

            for mode in cast_modes:
                self._build_cast_actions(
                    actions,
                    ci,
                    card,
                    visible,
                    player_id,
                    payload_base=mode["payload"],
                    additional_costs=additional_costs,
                    allow_x=mode["allow_x"],
                )

        # Flashback from graveyard
        graveyard = (visible.zones.graveyards or {}).get(player_id, [])
        for ci in graveyard:
            card_id = getattr(ci, "card_id", "")
            card = card_db.get(card_id)
            if card is None:
                continue
            if CardType.LAND in card.card_types:
                continue
            if not getattr(card.rules, "flashback_cost", None):
                continue
            if not self._timing_allows_cast(card, visible, player_id):
                continue

            additional_costs = self._enumerate_additional_costs(
                card,
                visible,
                player_id,
                exclude_instance_id=None,
            )
            if not additional_costs:
                continue

            if not self._has_mana_cost(card.rules.flashback_cost, visible, player_id, card):
                continue

            self._build_cast_actions(
                actions,
                ci,
                card,
                visible,
                player_id,
                payload_base={"flashback": True},
                additional_costs=additional_costs,
                allow_x=False,
            )

        return actions

    def _ability_actions(self, visible: VisibleState, player_id: str) -> List[Action]:
        actions: List[Action] = []
        if player_id != visible.priority_holder_id:
            return actions

        card_db = getattr(visible, "card_db", {}) or {}
        for perm in visible.zones.battlefield:
            if getattr(perm, "controller_id", None) != player_id:
                continue
            card_id = getattr(perm, "card_id", "")
            card = card_db.get(card_id)
            if card is None:
                continue

            for idx, ability in enumerate(card.rules.activated_abilities):
                if not self._can_activate_ability(ability, perm, visible, player_id):
                    continue

                cost_choices = self._enumerate_cost_choices(ability.costs, perm, visible, player_id)
                if not cost_choices:
                    continue

                target_groups_list = self._enumerate_targets_for_effects(
                    ability.effects,
                    visible,
                    player_id,
                    source_perm=perm,
                )
                if not target_groups_list:
                    target_groups_list = [[]]

                for cost_choice in cost_choices:
                    payload = {"ability_index": idx}
                    if cost_choice:
                        payload["costs"] = cost_choice
                    for targets in target_groups_list:
                        if targets:
                            actions.append(
                                Action(
                                    ActionType.ACTIVATE_ABILITY,
                                    actor_id=player_id,
                                    object_id=getattr(perm, "instance_id", None),
                                    targets=targets,
                                    payload=payload,
                                )
                            )
                        else:
                            actions.append(
                                Action(
                                    ActionType.ACTIVATE_ABILITY,
                                    actor_id=player_id,
                                    object_id=getattr(perm, "instance_id", None),
                                    payload=payload,
                                )
                            )

        return actions

    def _can_cast(self, card: Any, visible: VisibleState, player_id: str) -> bool:
        if not self._timing_allows_cast(card, visible, player_id):
            return False
        return self._has_mana_cost(card.mana_cost, visible, player_id, card)

    def _timing_allows_cast(self, card: Any, visible: VisibleState, player_id: str) -> bool:
        if Keyword.FLASH in (card.rules.keywords or set()):
            return True
        if CardType.INSTANT in card.card_types:
            return True

        if (
            CardType.SORCERY in card.card_types
            or CardType.CREATURE in card.card_types
            or CardType.ARTIFACT in card.card_types
            or CardType.ENCHANTMENT in card.card_types
        ):
            if visible.active_player_id != player_id:
                return False
            if visible.phase not in ("MAIN1", "MAIN2"):
                return False
            if visible.stack:
                return False
            return True

        return False

    def _has_mana_cost(self, cost: Any, visible: VisibleState, player_id: str, card: Optional[Any] = None) -> bool:
        if cost is None:
            return True
        pool = visible.available_mana or {}
        colored_pool = dict(pool.get("colored", {}) or {})
        generic_pool = int(pool.get("generic", 0) or 0)

        reduction = 0
        if card is not None:
            reduction = self._cost_reduction_for_spell(card, visible, player_id)
        generic_cost = max(0, int(cost.generic) - reduction)
        for color, amount in cost.colored.items():
            available = int(colored_pool.get(color.value, 0))
            any_pool = int(colored_pool.get("ANY", 0))
            if available + any_pool < amount:
                return False
            use = min(available, amount)
            colored_pool[color.value] = available - use
            remaining = amount - use
            if remaining > 0:
                colored_pool["ANY"] = any_pool - remaining

        remaining_generic = max(0, int(generic_cost))
        if generic_pool >= remaining_generic:
            return True

        remaining_generic -= generic_pool
        remaining_colored = sum(int(v) for v in colored_pool.values())
        return remaining_colored >= remaining_generic

    def _cost_reduction_for_spell(self, card: Any, visible: VisibleState, player_id: str) -> int:
        reduction = 0
        card_db = getattr(visible, "card_db", {}) or {}
        for perm in visible.zones.battlefield:
            if getattr(perm, "controller_id", None) != player_id:
                continue
            source_card = card_db.get(getattr(perm, "card_id", ""))
            if source_card is None:
                continue
            for sa in source_card.rules.static_abilities:
                for eff in sa.effects:
                    if eff.type != EffectType.COST_REDUCTION:
                        continue
                    tags = eff.params.get("spell_tags") or []
                    if self._spell_matches_tags(card, tags):
                        reduction += int(eff.params.get("amount", 0) or 0)
                    subtype = eff.params.get("spell_subtype")
                    if subtype and subtype in card.subtypes:
                        reduction += int(eff.params.get("amount", 0) or 0)
        return reduction

    def _spell_matches_tags(self, card: Any, tags: List[str]) -> bool:
        for tag in tags:
            if tag == "AURA" and card.aura_stats is not None:
                return True
            if tag == "EQUIPMENT" and card.equipment_stats is not None:
                return True
            if tag == "ARTIFACT" and CardType.ARTIFACT in card.card_types:
                return True
            if tag == "ENCHANTMENT" and CardType.ENCHANTMENT in card.card_types:
                return True
        return False

    def _expand_modal_effects(self, card: Any) -> List[tuple[Dict[str, Any], List[Any]]]:
        if (
            len(card.rules.effects) == 1
            and card.rules.effects[0].type == EffectType.MODAL
        ):
            modal = card.rules.effects[0]
            modes = modal.params.get("modes", [])
            results = []
            for idx, mode_effects in enumerate(modes):
                results.append(({"mode_indices": [idx]}, mode_effects))
            return results
        return [({}, card.rules.effects)]

    def _enumerate_x_values(self, card: Any, visible: VisibleState, player_id: str) -> List[Optional[int]]:
        if not getattr(card.mana_cost, "x", 0):
            return [None]
        max_x = self._max_affordable_x(card, visible, player_id)
        if max_x < 0:
            return []
        return list(range(0, max_x + 1))

    def _max_affordable_x(self, card: Any, visible: VisibleState, player_id: str) -> int:
        pool = visible.available_mana or {}
        colored_pool = dict(pool.get("colored", {}) or {})
        generic_pool = int(pool.get("generic", 0) or 0)

        reduction = self._cost_reduction_for_spell(card, visible, player_id)
        generic_cost = max(0, int(card.mana_cost.generic) - reduction)
        for color, amount in card.mana_cost.colored.items():
            available = int(colored_pool.get(color.value, 0))
            any_pool = int(colored_pool.get("ANY", 0))
            if available + any_pool < amount:
                return -1
            use = min(available, amount)
            colored_pool[color.value] = available - use
            remaining = amount - use
            if remaining > 0:
                colored_pool["ANY"] = any_pool - remaining

        remaining_generic = max(0, int(generic_cost))
        total = generic_pool + sum(int(v) for v in colored_pool.values())
        remaining_total = total - remaining_generic
        return max(0, remaining_total)

    def _can_pay_alternate_cost(self, alt: Any, visible: VisibleState, player_id: str) -> bool:
        if not isinstance(alt, str):
            return False
        if alt == "CONTROL_FOREST_GAIN_LIFE":
            if visible.life_totals.get(player_id, 0) < 3:
                return False
            return self._controls_subtype(visible, player_id, "Forest")
        return False

    def _alternate_cost_options(self, card: Any, visible: VisibleState, player_id: str) -> List[str]:
        options: List[str] = []
        for alt in getattr(card.rules, "alternate_costs", []) or []:
            if isinstance(alt, dict) and alt.get("type") == "CONTROL_FOREST_GAIN_LIFE":
                if self._controls_subtype(visible, player_id, "Forest"):
                    options.append("CONTROL_FOREST_GAIN_LIFE")
        return options

    def _controls_subtype(self, visible: VisibleState, player_id: str, subtype: str) -> bool:
        card_db = getattr(visible, "card_db", {}) or {}
        for perm in visible.zones.battlefield:
            if getattr(perm, "controller_id", None) != player_id:
                continue
            card = card_db.get(getattr(perm, "card_id", ""))
            if card is None:
                continue
            if subtype in card.subtypes:
                return True
        return False

    def _enumerate_additional_costs(
        self,
        card: Any,
        visible: VisibleState,
        player_id: str,
        exclude_instance_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        additional = getattr(card.rules, "additional_costs", []) or []
        if not additional:
            return [{}]

        card_db = getattr(visible, "card_db", {}) or {}
        choices: List[Dict[str, Any]] = [{}]
        for cost in additional:
            new_choices: List[Dict[str, Any]] = []
            if cost.type == CostType.DISCARD_CARD:
                candidates = [
                    ci for ci in visible.zones.hand
                    if getattr(ci, "instance_id", None) != exclude_instance_id
                ]
                if len(candidates) < int(cost.amount or 1):
                    return []
                for combo in itertools.combinations(candidates, int(cost.amount or 1)):
                    for base in choices:
                        entry = dict(base)
                        entry["discard"] = [getattr(ci, "instance_id", None) for ci in combo]
                        new_choices.append(entry)

            elif cost.type in (CostType.SACRIFICE_CREATURE, CostType.SACRIFICE_OTHER_CREATURE):
                candidates = []
                for perm in visible.zones.battlefield:
                    if getattr(perm, "controller_id", None) != player_id:
                        continue
                    card = card_db.get(getattr(perm, "card_id", ""))
                    if card is None:
                        continue
                    if CardType.CREATURE in card.card_types:
                        candidates.append(perm)
                if len(candidates) < int(cost.amount or 1):
                    return []
                for combo in itertools.combinations(candidates, int(cost.amount or 1)):
                    for base in choices:
                        entry = dict(base)
                        entry["sacrifice"] = [getattr(perm, "instance_id", None) for perm in combo]
                        new_choices.append(entry)
            else:
                return []

            choices = new_choices

        return choices

    def _build_cast_actions(
        self,
        actions: List[Action],
        ci: Any,
        card: Any,
        visible: VisibleState,
        player_id: str,
        payload_base: Dict[str, Any],
        additional_costs: List[Dict[str, Any]],
        allow_x: bool,
    ) -> None:
        for mode_payload, effects in self._expand_modal_effects(card):
            target_groups_list = self._enumerate_targets_for_effects(
                effects,
                visible,
                player_id,
                source_perm=None,
            )
            if not target_groups_list:
                target_groups_list = [[]]

            x_values = self._enumerate_x_values(card, visible, player_id) if allow_x else [None]
            for x_value in x_values:
                for cost_choice in additional_costs:
                    payload = {"card_id": getattr(ci, "card_id", None)}
                    payload.update(payload_base)
                    payload.update(mode_payload)
                    if x_value is not None:
                        payload["x"] = x_value
                    if cost_choice:
                        payload["additional_costs"] = cost_choice

                    for targets in target_groups_list:
                        if targets:
                            actions.append(
                                Action(
                                    ActionType.CAST_SPELL,
                                    actor_id=player_id,
                                    object_id=getattr(ci, "instance_id", None),
                                    targets=targets,
                                    payload=payload,
                                )
                            )
                        else:
                            actions.append(
                                Action(
                                    ActionType.CAST_SPELL,
                                    actor_id=player_id,
                                    object_id=getattr(ci, "instance_id", None),
                                    payload=payload,
                                )
                            )

    def _enumerate_targets_for_effects(
        self,
        effects: List[Any],
        visible: VisibleState,
        player_id: str,
        source_perm: Optional[Any],
    ) -> List[List[List[Dict[str, Any]]]]:
        per_effect_choices: List[List[List[Dict[str, Any]]]] = []
        for eff in effects:
            choices = self._enumerate_targets_for_effect(eff, visible, player_id, source_perm)
            if not choices:
                choices = [[]]
            per_effect_choices.append(choices)

        combos: List[List[List[Dict[str, Any]]]] = [[]]
        for idx, choices in enumerate(per_effect_choices):
            new_combos: List[List[List[Dict[str, Any]]]] = []
            for combo in combos:
                primary = combo[0] if combo else []
                for choice in choices:
                    if idx > 0 and effects[idx].params.get("exclude_primary"):
                        if any(self._targets_equal(t, p) for t in choice for p in primary):
                            continue
                    new_combos.append(combo + [choice])
            combos = new_combos

        return combos

    def _enumerate_targets_for_effect(
        self,
        eff: Any,
        visible: VisibleState,
        player_id: str,
        source_perm: Optional[Any],
    ) -> List[List[Dict[str, Any]]]:
        params = eff.params or {}

        if eff.type == EffectType.MODAL:
            return [[]]

        if eff.type == EffectType.SACRIFICE_TARGET and params.get("chooser") == "DAMAGED_PLAYER":
            return [[]]

        if eff.type in (EffectType.CREATURE_DEALS_DAMAGE_TO_CREATURE, EffectType.RAM_THROUGH):
            source_spec = params.get("source")
            target_spec = params.get("target")
            if source_spec is None or target_spec is None:
                return [[]]
            sources = self._candidate_targets(source_spec, visible, player_id, source_perm)
            targets = self._candidate_targets(target_spec, visible, player_id, source_perm)
            groups: List[List[Dict[str, Any]]] = []
            for s in sources:
                for t in targets:
                    if self._targets_equal(s, t):
                        continue
                    s_role = dict(s)
                    s_role["role"] = "source"
                    t_role = dict(t)
                    t_role["role"] = "target"
                    groups.append([s_role, t_role])
            return groups

        if eff.type == EffectType.RETURN_TWO_DIFFERENT_CONTROLLERS:
            candidates = self._candidate_targets(
                TargetSpec(zone=Zone.BATTLEFIELD, selector=Selector.TARGET_CREATURE),
                visible,
                player_id,
                source_perm,
            )
            groups = []
            for a, b in itertools.combinations(candidates, 2):
                if self._controller_for_target(a, visible) == self._controller_for_target(b, visible):
                    continue
                groups.append([a, b])
            return groups

        if eff.type == EffectType.ATTACH_EQUIPMENT:
            target_spec = params.get("target")
            if target_spec is None:
                return [[]]
            targets = self._candidate_targets(target_spec, visible, player_id, source_perm)
            if source_perm is not None and self._perm_is_equipment(source_perm, visible):
                return [[t] for t in targets]

            equipment = [
                perm for perm in visible.zones.battlefield
                if getattr(perm, "controller_id", None) == player_id
                and self._perm_is_equipment(perm, visible)
            ]
            groups = []
            for eq in equipment:
                for tgt in targets:
                    groups.append(
                        [
                            {"type": "PERMANENT", "instance_id": getattr(eq, "instance_id", None), "role": "equipment"},
                            dict(tgt, role="target"),
                        ]
                    )
            return groups

        if "targets_any_of" in params:
            candidates: List[Dict[str, Any]] = []
            for spec in params.get("targets_any_of") or []:
                candidates.extend(self._candidate_targets(spec, visible, player_id, source_perm))
            unique: List[Dict[str, Any]] = []
            for c in candidates:
                if not any(self._targets_equal(c, u) for u in unique):
                    unique.append(c)
            return self._choose_target_combos(unique, 1, False)

        target_spec = params.get("target")
        if isinstance(target_spec, TargetSpec):
            candidates = self._candidate_targets(target_spec, visible, player_id, source_perm)
            if params.get("defending_player_only"):
                defender_id = self._other_player_id(visible, player_id)
                candidates = [
                    c for c in candidates
                    if self._controller_for_target(c, visible) == defender_id
                ]
            count = int(params.get("count", 1) or 1)
            up_to = bool(params.get("up_to", False))
            return self._choose_target_combos(candidates, count, up_to)

        return [[]]

    def _choose_target_combos(
        self,
        candidates: List[Dict[str, Any]],
        count: int,
        up_to: bool,
    ) -> List[List[Dict[str, Any]]]:
        if count <= 0:
            return [[]]
        combos: List[List[Dict[str, Any]]] = []
        sizes = range(0, count + 1) if up_to else range(count, count + 1)
        for size in sizes:
            for combo in itertools.combinations(candidates, size):
                combos.append(list(combo))
        return combos

    def _candidate_targets(
        self,
        spec: Any,
        visible: VisibleState,
        player_id: str,
        source_perm: Optional[Any],
    ) -> List[Dict[str, Any]]:
        if not isinstance(spec, TargetSpec):
            return []

        candidates: List[Dict[str, Any]] = []

        if spec.zone == Zone.PLAYER:
            if spec.selector in (Selector.ANY_PLAYER, Selector.TARGET_PLAYER):
                for pid in visible.life_totals.keys():
                    candidates.append({"type": "PLAYER", "player_id": pid})
            elif spec.selector == Selector.TARGET_OPPONENT_PLAYER:
                for pid in visible.life_totals.keys():
                    if pid != player_id:
                        candidates.append({"type": "PLAYER", "player_id": pid})
            return candidates

        if spec.zone == Zone.STACK and spec.selector == Selector.TARGET_SPELL:
            for item in visible.zones.stack:
                if getattr(item, "kind", None) == "SPELL":
                    candidates.append({"type": "STACK", "instance_id": getattr(item, "instance_id", None)})
            return candidates

        if spec.zone == Zone.GRAVEYARD and spec.selector == Selector.TARGET_CARD_GRAVEYARD:
            for pid, cards in (visible.zones.graveyards or {}).items():
                for ci in cards:
                    candidates.append(
                        {
                            "type": "CARD",
                            "instance_id": getattr(ci, "instance_id", None),
                            "player_id": pid,
                            "zone": "GRAVEYARD",
                        }
                    )
            return candidates

        if spec.zone in (Zone.BATTLEFIELD, Zone.ANY):
            for perm in visible.zones.battlefield:
                if self._perm_has_keyword(perm, Keyword.HEXPROOF, visible) and getattr(perm, "controller_id", None) != player_id:
                    continue
                if spec.selector == Selector.ANY_TARGET:
                    candidates.append({"type": "PERMANENT", "instance_id": getattr(perm, "instance_id", None)})
                elif self._perm_matches_selector(perm, spec.selector, visible, player_id):
                    candidates.append({"type": "PERMANENT", "instance_id": getattr(perm, "instance_id", None)})

            if spec.selector == Selector.ANY_TARGET and spec.zone in (Zone.ANY, Zone.PLAYER):
                for pid in visible.life_totals.keys():
                    candidates.append({"type": "PLAYER", "player_id": pid})
            return candidates

        return candidates

    def _perm_has_type(self, perm: Any, card_type: CardType, visible: VisibleState) -> bool:
        card_types = set(getattr(perm, "card_types", []) or [])
        if card_types:
            return card_type.value in card_types
        card_db = getattr(visible, "card_db", {}) or {}
        card = card_db.get(getattr(perm, "card_id", ""))
        if card is None:
            return False
        return card_type in card.card_types

    def _perm_has_keyword(self, perm: Any, keyword: Keyword, visible: VisibleState) -> bool:
        keywords = set(getattr(perm, "keywords", []) or [])
        if keywords:
            return keyword.value in keywords
        card_db = getattr(visible, "card_db", {}) or {}
        card = card_db.get(getattr(perm, "card_id", ""))
        if card is None:
            return False
        return keyword in card.rules.keywords

    def _perm_is_equipment(self, perm: Any, visible: VisibleState) -> bool:
        card_db = getattr(visible, "card_db", {}) or {}
        card = card_db.get(getattr(perm, "card_id", ""))
        if card is None:
            return False
        return card.equipment_stats is not None

    def _perm_is_equipped(self, perm: Any, visible: VisibleState) -> bool:
        attachments = getattr(perm, "attachments", []) or []
        card_db = getattr(visible, "card_db", {}) or {}
        for att_id in attachments:
            att_perm = next((p for p in visible.zones.battlefield if getattr(p, "instance_id", None) == att_id), None)
            if att_perm is None:
                continue
            card = card_db.get(getattr(att_perm, "card_id", ""))
            if card and card.equipment_stats is not None:
                return True
        return False

    def _perm_is_enchanted(self, perm: Any, visible: VisibleState) -> bool:
        attachments = getattr(perm, "attachments", []) or []
        card_db = getattr(visible, "card_db", {}) or {}
        for att_id in attachments:
            att_perm = next((p for p in visible.zones.battlefield if getattr(p, "instance_id", None) == att_id), None)
            if att_perm is None:
                continue
            card = card_db.get(getattr(att_perm, "card_id", ""))
            if card and card.aura_stats is not None:
                return True
        return False

    def _perm_matches_selector(self, perm: Any, selector: Selector, visible: VisibleState, player_id: str) -> bool:
        if selector in (Selector.ANY_PERMANENT, Selector.TARGET_PERMANENT):
            return True

        if selector in (Selector.ANY_CREATURE, Selector.TARGET_CREATURE):
            return self._perm_has_type(perm, CardType.CREATURE, visible)

        if selector in (Selector.TARGET_FRIENDLY_CREATURE, Selector.TARGET_CREATURE_YOU_CONTROL):
            return (
                getattr(perm, "controller_id", None) == player_id
                and self._perm_has_type(perm, CardType.CREATURE, visible)
            )

        if selector in (Selector.TARGET_OPPONENT_CREATURE, Selector.TARGET_CREATURE_OPPONENT_CONTROLS):
            return (
                getattr(perm, "controller_id", None) != player_id
                and self._perm_has_type(perm, CardType.CREATURE, visible)
            )

        if selector == Selector.TARGET_NON_BLACK_CREATURE:
            if not self._perm_has_type(perm, CardType.CREATURE, visible):
                return False
            card_db = getattr(visible, "card_db", {}) or {}
            card = card_db.get(getattr(perm, "card_id", ""))
            if card is None:
                return False
            return "BLACK" not in {c.value for c in card.colors}

        if selector == Selector.TARGET_FLYING_CREATURE:
            return self._perm_has_type(perm, CardType.CREATURE, visible) and self._perm_has_keyword(
                perm,
                Keyword.FLYING,
                visible,
            )

        if selector == Selector.TARGET_ARTIFACT:
            return self._perm_has_type(perm, CardType.ARTIFACT, visible)

        if selector == Selector.TARGET_ENCHANTMENT:
            return self._perm_has_type(perm, CardType.ENCHANTMENT, visible)

        if selector == Selector.TARGET_ATTACKING_CREATURE:
            if not self._perm_has_type(perm, CardType.CREATURE, visible):
                return False
            return getattr(perm, "instance_id", None) in (visible.combat_attackers or [])

        if selector == Selector.TARGET_EQUIPPED_CREATURE:
            return self._perm_has_type(perm, CardType.CREATURE, visible) and self._perm_is_equipped(perm, visible)

        if selector == Selector.TARGET_ENCHANTED_CREATURE:
            return self._perm_has_type(perm, CardType.CREATURE, visible) and self._perm_is_enchanted(perm, visible)

        return False

    def _targets_equal(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        return (
            a.get("type") == b.get("type")
            and a.get("player_id") == b.get("player_id")
            and a.get("instance_id") == b.get("instance_id")
            and a.get("zone") == b.get("zone")
        )

    def _controller_for_target(self, target: Dict[str, Any], visible: VisibleState) -> Optional[str]:
        if target.get("type") == "PERMANENT":
            perm = next((p for p in visible.zones.battlefield if getattr(p, "instance_id", None) == target.get("instance_id")), None)
            return getattr(perm, "controller_id", None) if perm else None
        if target.get("type") == "PLAYER":
            return target.get("player_id")
        return None

    def _can_activate_ability(self, ability: Any, perm: Any, visible: VisibleState, player_id: str) -> bool:
        if ability.zone != Zone.BATTLEFIELD:
            return False

        if ability.timing == TimingRestriction.SORCERY_SPEED:
            if visible.active_player_id != player_id:
                return False
            if visible.phase not in ("MAIN1", "MAIN2"):
                return False
            if visible.stack:
                return False

        if ability.timing == TimingRestriction.ONLY_WHEN_ATTACKING:
            if getattr(perm, "instance_id", None) not in (visible.combat_attackers or []):
                return False

        for cost in ability.costs:
            if cost.type == CostType.TAP:
                if getattr(perm, "tapped", False):
                    return False
                if self._perm_has_type(perm, CardType.CREATURE, visible) and getattr(perm, "summoning_sick", False):
                    if not self._perm_has_keyword(perm, Keyword.HASTE, visible):
                        return False
            if cost.type == CostType.MANA:
                if not self._has_mana_cost(cost.amount, visible, player_id):
                    return False
            if cost.type == CostType.PAY_LIFE:
                if visible.life_totals.get(player_id, 0) < int(cost.amount or 0):
                    return False
        return True

    def _enumerate_cost_choices(
        self,
        costs: List[Any],
        perm: Any,
        visible: VisibleState,
        player_id: str,
    ) -> List[Dict[str, Any]]:
        choices: List[Dict[str, Any]] = [{}]

        for cost in costs:
            if cost.type == CostType.TAP:
                if getattr(perm, "tapped", False):
                    return []
                if self._perm_has_type(perm, CardType.CREATURE, visible) and getattr(perm, "summoning_sick", False):
                    if not self._perm_has_keyword(perm, Keyword.HASTE, visible):
                        return []
                for choice in choices:
                    choice["tap"] = True
                continue

            if cost.type == CostType.MANA:
                if not self._has_mana_cost(cost.amount, visible, player_id):
                    return []
                continue

            if cost.type == CostType.PAY_LIFE:
                amount = int(cost.amount or 0)
                if visible.life_totals.get(player_id, 0) < amount:
                    return []
                for choice in choices:
                    choice["pay_life"] = amount
                continue

            if cost.type == CostType.SACRIFICE_SELF:
                for choice in choices:
                    choice["sacrifice_self"] = True
                continue

            if cost.type in (CostType.SACRIFICE_CREATURE, CostType.SACRIFICE_OTHER_CREATURE):
                count = int(cost.amount or 1)
                candidates = []
                for p in visible.zones.battlefield:
                    if getattr(p, "controller_id", None) != player_id:
                        continue
                    if not self._perm_has_type(p, CardType.CREATURE, visible):
                        continue
                    if cost.type == CostType.SACRIFICE_OTHER_CREATURE and getattr(p, "instance_id", None) == getattr(perm, "instance_id", None):
                        continue
                    candidates.append(getattr(p, "instance_id", None))
                if len(candidates) < count:
                    return []
                new_choices: List[Dict[str, Any]] = []
                for base in choices:
                    for combo in itertools.combinations(candidates, count):
                        updated = dict(base)
                        updated.setdefault("sacrifice", []).extend(list(combo))
                        new_choices.append(updated)
                choices = new_choices
                continue

            if cost.type == CostType.DISCARD_CARD:
                count = int(cost.amount or 1)
                hand_ids = [getattr(ci, "instance_id", None) for ci in visible.zones.hand]
                if len(hand_ids) < count:
                    return []
                new_choices = []
                for base in choices:
                    for combo in itertools.combinations(hand_ids, count):
                        updated = dict(base)
                        updated.setdefault("discard", []).extend(list(combo))
                        new_choices.append(updated)
                choices = new_choices
                continue

        return choices

    def _combat_actions(self, visible: VisibleState, player_id: str) -> List[Action]:
        actions: List[Action] = []

        if player_id != visible.priority_holder_id:
            return actions

        if visible.phase == "DECLARE_ATTACKERS":
            if visible.active_player_id != player_id:
                return actions
            if visible.combat_attackers_declared:
                return actions
            if visible.stack:
                return actions

            defender_id = self._other_player_id(visible, player_id)
            attackers = [
                perm for perm in visible.zones.battlefield
                if self._can_attack(perm, visible, player_id, defender_id)
            ]

            ids = [getattr(p, "instance_id", None) for p in attackers]
            required = [
                getattr(p, "instance_id", None)
                for p in attackers
                if getattr(p, "must_attack", False)
            ]
            subsets = self._all_subsets(ids)
            for subset in subsets:
                if any(r not in subset for r in required):
                    continue
                actions.append(
                    Action(
                        ActionType.DECLARE_ATTACKERS,
                        actor_id=player_id,
                        targets={"attackers": subset},
                    )
                )
            return actions

        if visible.phase == "DECLARE_BLOCKERS":
            if visible.active_player_id == player_id:
                return actions
            if visible.combat_blockers_declared:
                return actions
            if visible.stack:
                return actions

            attacker_ids = list(visible.combat_attackers)
            if not attacker_ids:
                actions.append(
                    Action(
                        ActionType.DECLARE_BLOCKERS,
                        actor_id=player_id,
                        targets={"blocks": []},
                    )
                )
                return actions

            blockers = [
                perm for perm in visible.zones.battlefield
                if getattr(perm, "controller_id", None) == player_id
                and self._perm_is_creature(perm, visible)
            ]
            blocker_ids = [getattr(p, "instance_id", None) for p in blockers]

            for mapping in self._blocker_mappings(attacker_ids, blockers, visible):
                actions.append(
                    Action(
                        ActionType.DECLARE_BLOCKERS,
                        actor_id=player_id,
                        targets={"blocks": mapping},
                    )
                )
            return actions

        return actions

    def _perm_is_creature(self, perm: Any, visible: VisibleState) -> bool:
        return self._perm_has_type(perm, CardType.CREATURE, visible)

    def _other_player_id(self, visible: VisibleState, player_id: str) -> Optional[str]:
        for pid in visible.life_totals.keys():
            if pid != player_id:
                return pid
        return None

    def _can_attack(self, perm: Any, visible: VisibleState, player_id: str, defender_id: Optional[str]) -> bool:
        if getattr(perm, "controller_id", None) != player_id:
            return False
        if not self._perm_is_creature(perm, visible):
            return False
        if getattr(perm, "tapped", False):
            return False
        if getattr(perm, "summoning_sick", False) and not self._perm_has_keyword(perm, Keyword.HASTE, visible):
            return False
        if self._perm_has_keyword(perm, Keyword.DEFENDER, visible):
            return False
        cant_attack = set(getattr(perm, "cant_attack_players", []) or [])
        if defender_id and defender_id in cant_attack:
            return False
        return True

    def _can_block(self, blocker: Any, attacker: Any, visible: VisibleState) -> bool:
        if not self._perm_is_creature(blocker, visible):
            return False
        if getattr(blocker, "tapped", False):
            return False
        if self._perm_has_keyword(attacker, Keyword.FLYING, visible):
            if not self._perm_has_keyword(blocker, Keyword.FLYING, visible) and not self._perm_has_keyword(
                blocker, Keyword.REACH, visible
            ):
                return False
        return True

    def _all_subsets(self, ids: List[Any]) -> List[List[Any]]:
        subsets: List[List[Any]] = [[]]
        for cid in ids:
            if cid is None:
                continue
            subsets += [s + [cid] for s in subsets]
        return subsets

    def _blocker_mappings(
        self,
        attackers: List[Any],
        blockers: List[Any],
        visible: VisibleState,
    ) -> List[List[Dict[str, Any]]]:
        attacker_ids = [aid for aid in attackers if aid is not None]
        blocker_ids = [getattr(p, "instance_id", None) for p in blockers if getattr(p, "instance_id", None)]
        perm_by_id = {getattr(p, "instance_id", None): p for p in visible.zones.battlefield}

        block_options: Dict[str, List[str]] = {}
        for bid in blocker_ids:
            blocker_perm = perm_by_id.get(bid)
            if blocker_perm is None:
                continue
            options = []
            for aid in attacker_ids:
                attacker_perm = perm_by_id.get(aid)
                if attacker_perm is None:
                    continue
                if self._can_block(blocker_perm, attacker_perm, visible):
                    options.append(aid)
            block_options[bid] = options

        results: List[List[Dict[str, Any]]] = []

        def mapping_valid(current: List[Dict[str, Any]]) -> bool:
            by_attacker: Dict[str, List[str]] = {aid: [] for aid in attacker_ids}
            for entry in current:
                by_attacker.setdefault(entry["attacker_id"], []).append(entry["blocker_id"])

            # Menace requires 2+ blockers if blocked
            for aid in attacker_ids:
                attacker_perm = perm_by_id.get(aid)
                if attacker_perm is None:
                    continue
                if self._perm_has_keyword(attacker_perm, Keyword.MENACE, visible):
                    if len(by_attacker.get(aid, [])) == 1:
                        return False

            # Must-be-blocked-by-all
            for aid in attacker_ids:
                attacker_perm = perm_by_id.get(aid)
                if attacker_perm is None or not getattr(attacker_perm, "must_be_blocked_by_all", False):
                    continue
                must_blockers = [bid for bid, opts in block_options.items() if aid in opts]
                if set(by_attacker.get(aid, [])) != set(must_blockers):
                    return False

            return True

        def backtrack(idx: int, current: List[Dict[str, Any]]) -> None:
            if idx >= len(blocker_ids):
                if mapping_valid(current):
                    results.append(list(current))
                return

            blocker_id = blocker_ids[idx]
            # no block
            backtrack(idx + 1, current)

            for attacker_id in block_options.get(blocker_id, []):
                current.append({"attacker_id": attacker_id, "blocker_id": blocker_id})
                backtrack(idx + 1, current)
                current.pop()

        backtrack(0, [])
        return results

    def _pass_blocked_by_combat_declaration(self, visible: VisibleState, player_id: str) -> bool:
        if visible.phase == "DECLARE_ATTACKERS" and visible.active_player_id == player_id:
            return not visible.combat_attackers_declared
        if visible.phase == "DECLARE_BLOCKERS" and visible.active_player_id != player_id:
            return not visible.combat_blockers_declared
        return False

    def _skip_phase_actions(self, visible: VisibleState, player_id: str) -> List[Action]:
        if player_id != visible.priority_holder_id:
            return []
        if visible.active_player_id != player_id:
            return []
        if visible.stack:
            return []

        actions: List[Action] = []
        if visible.phase == "MAIN1":
            actions.append(Action(ActionType.SKIP_COMBAT, actor_id=player_id))
        if visible.phase == "MAIN2":
            actions.append(Action(ActionType.SKIP_MAIN2, actor_id=player_id))
        return actions

    def _can_tap_for_mana(self, perm: object, player_id: str) -> bool:
        if getattr(perm, "controller_id", None) != player_id:
            return False
        if getattr(perm, "tapped", False):
            return False
        return self._basic_land_produces(getattr(perm, "card_id", "")) is not None

    def _basic_land_produces(self, card_id: str) -> Optional[Dict[str, int]]:
        if not isinstance(card_id, str):
            return None

        mapping = {
            "basic_swamp": {"BLACK": 1},
            "basic_mountain": {"RED": 1},
            "basic_island": {"BLUE": 1},
            "basic_forest": {"GREEN": 1},
            "basic_plains": {"WHITE": 1},
        }
        return mapping.get(card_id)

    def _decision_actions(self, pending: Any, player_id: str) -> List[Action]:
        actions: List[Action] = []
        options = getattr(pending, "options", None)
        if isinstance(options, list):
            for opt in options:
                actions.append(
                    Action(
                        ActionType.RESOLVE_DECISION,
                        actor_id=player_id,
                        payload={"choice": opt},
                    )
                )
            return actions

        actions.append(
            Action(
                ActionType.RESOLVE_DECISION,
                actor_id=player_id,
                payload={"choice": options},
            )
        )
        return actions

    def _decision_schema(self, pending: Any) -> Dict[str, Any]:
        return {
            "allowed_actions": [ActionType.RESOLVE_DECISION.value],
            "resolve_decision": {
                "kind": getattr(pending, "kind", None),
                "options": getattr(pending, "options", None),
                "context": getattr(pending, "context", None),
            },
        }
