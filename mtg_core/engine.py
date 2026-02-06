from __future__ import annotations

from typing import Any, Dict, List, Optional
import itertools
import uuid

from mtg_core.actions import Action, ActionType
from mtg_core.game_state import (
    GameState,
    CardInstance,
    Permanent,
    StackItem,
    StackItemKind,
    Phase,
    Step,
    TemporaryEffect,
    PendingDecision,
)
from mtg_core.action_surface import ActionSurface
from mtg_core.cards import (
    CardType,
    Color,
    EffectType,
    Zone,
    Selector,
    Keyword,
    TriggerType,
    CostType,
    TimingRestriction,
    TargetSpec,
    Effect,
    ManaCost,
    LandType,
)
from mtg_core.player_state import PlayerState
from mtg_core.aibase import (
    VisibleState,
    ZonesView,
    PermanentView,
    StackItemView,
    HandCardView,
    ResolutionResult,
    ResolutionStatus,
)


class MTGEngine:
    """
    Phase-1 MTG Engine — IN-GAME ONLY

    Responsibilities:
      - Resolve already-legal actions
      - Advance priority / steps / turns
      - Mutate GameState authoritatively

    Explicitly does NOT:
      - Decide legality
      - Enumerate actions
      - Handle mulligans / pregame
    """

    def __init__(self, game_state: GameState):
        self.game = game_state
        self._priority_holder: str = self.game.turn.active_player_id
        self._pass_streak: int = 0
        self._log("Game engine initialized.")

    @property
    def priority_holder(self) -> str:
        return self._priority_holder

    # ============================
    # Public Engine API
    # ============================

    def submit_action(self, action: Action) -> ResolutionResult:
        try:
            if not isinstance(action, Action):
                raise ValueError("Invalid action object")

            if self.game.game_over:
                return ResolutionResult(
                    status=ResolutionStatus.FAILURE,
                    message="Game is already over",
                    payload=None,
                )

            if not self.validate(action):
                return ResolutionResult(
                    status=ResolutionStatus.FAILURE,
                    message="Illegal action",
                    payload=None,
                )

            payload = self.resolve(action)
            self._apply_state_based_actions()
            self._check_win_conditions()

            return ResolutionResult(
                status=ResolutionStatus.SUCCESS,
                message=None,
                payload=payload,
            )

        except Exception as e:
            self._log(f"ENGINE ERROR resolving {action}: {e}")
            return ResolutionResult(
                status=ResolutionStatus.ERROR,
                message=str(e),
                payload=None,
            )

    def get_legal_actions(self, player_id: str) -> List[Action]:
        visible = self.get_visible_state(player_id)
        return ActionSurface().get_legal_actions(visible, player_id)

    # ============================
    # Action Handlers
    # ============================

    def _resolve_pass_priority(self, player_id: str) -> Dict[str, Any]:
        if player_id != self._priority_holder:
            raise ValueError("PASS_PRIORITY by non-priority holder")

        self._log(f"{player_id} passes priority.")
        self._pass_streak += 1

        if self._pass_streak >= 2:
            self._pass_streak = 0
            if self.game.zones.stack:
                resolved = self._resolve_top_of_stack()
                self._priority_holder = self.game.turn.active_player_id
                return {"resolved_stack": resolved}
            self._advance_step()
            self._priority_holder = self.game.turn.active_player_id
            return {"advanced_step": self.game.turn.step.value}

        self._priority_holder = self._other_player(player_id)
        return {"priority_to": self._priority_holder}

    # ============================
    # Turn / Step Advancement
    # ============================

    def _advance_step(self) -> None:
        t = self.game.turn

        self._clear_mana_pools()

        if t.step == Step.UNTAP:
            self._untap_permanents(t.active_player_id)
            t.phase = Phase.BEGINNING
            t.step = Step.DRAW
            self._handle_upkeep(t.active_player_id)
            return

        if t.step == Step.DRAW:
            if not (t.turn_number == 1 and t.active_player_id == self.game.starting_player_id):
                self._draw(t.active_player_id, 1)
            t.phase = Phase.MAIN
            t.step = Step.MAIN1
            return

        if t.step == Step.MAIN1:
            t.phase = Phase.COMBAT
            t.step = Step.DECLARE_ATTACKERS
            t.attackers = []
            t.blockers = {}
            t.attackers_declared = False
            t.blockers_declared = False
            return

        if t.step == Step.DECLARE_ATTACKERS:
            t.phase = Phase.COMBAT
            t.step = Step.DECLARE_BLOCKERS
            return

        if t.step == Step.DECLARE_BLOCKERS:
            self._resolve_combat_damage()
            t.phase = Phase.MAIN
            t.step = Step.MAIN2
            t.attackers = []
            t.blockers = {}
            t.attackers_declared = False
            t.blockers_declared = False
            return

        if t.step == Step.DAMAGE:
            t.phase = Phase.MAIN
            t.step = Step.MAIN2
            t.attackers = []
            t.blockers = {}
            t.attackers_declared = False
            t.blockers_declared = False
            return

        if t.step == Step.MAIN2:
            t.phase = Phase.ENDING
            t.step = Step.END
            return

        if t.step == Step.END:
            self._end_turn()
            return

        raise RuntimeError(f"Unhandled step transition: {t.step}")

    def _end_turn(self) -> None:
        t = self.game.turn

        for ps in self.game.players.values():
            ps.mana_pool.clear()
            ps.lands_played_this_turn = 0

        for perm in self.game.zones.battlefield.values():
            perm.state.damage_marked = 0

        self.game.damage_dealt_to_players = {pid: 0 for pid in self.game.players}

        t.turn_number += 1
        if self.game.extra_turns:
            t.active_player_id = self.game.extra_turns.pop(0)
        else:
            t.active_player_id = self._other_player(t.active_player_id)
        t.phase = Phase.BEGINNING
        t.step = Step.UNTAP
        t.attackers = []
        t.blockers = {}
        t.attackers_declared = False
        t.blockers_declared = False

        self._priority_holder = t.active_player_id
        self._log(f"Turn {t.turn_number} begins. Active player: {t.active_player_id}")

    # ============================
    # Visibility (CLI + AI)
    # ============================

    def get_visible_state(self, player_id: str) -> VisibleState:
        derived = self._derived_battlefield_state()

        def card_view(ci: CardInstance) -> Dict[str, Any]:
            card = self.game.card_db.get(ci.card_id)
            if card is None:
                return {
                    "name": ci.card_id,
                    "card_type": "UNKNOWN",
                    "card_types": [],
                    "subtypes": [],
                    "keywords": [],
                    "mana_cost": {"generic": 0, "colored": {}},
                    "power": None,
                    "toughness": None,
                }
            power = None
            toughness = None
            if card.creature_stats is not None:
                power = card.creature_stats.base_power
                toughness = card.creature_stats.base_toughness
            return {
                "name": card.name,
                "card_type": card.card_type.value,
                "card_types": [ct.value for ct in card.card_types],
                "subtypes": list(card.subtypes),
                "keywords": [kw.value for kw in card.rules.keywords],
                "mana_cost": {
                    "generic": card.mana_cost.generic,
                    "colored": {c.value: n for c, n in card.mana_cost.colored.items()},
                },
                "power": power,
                "toughness": toughness,
            }

        attachments_by_host: Dict[str, List[str]] = {}
        for perm in self.game.zones.battlefield.values():
            attached_to = perm.state.attached_to
            if attached_to:
                attachments_by_host.setdefault(attached_to, []).append(perm.instance.instance_id)

        battlefield = []
        for perm in self.game.zones.battlefield.values():
            view = card_view(perm.instance)
            d = derived.get(perm.instance.instance_id, {})
            keywords = d.get("keywords")
            subtypes = d.get("subtypes")
            battlefield.append(
                PermanentView(
                    instance_id=perm.instance.instance_id,
                    card_id=perm.instance.card_id,
                    name=view["name"],
                    card_type=view["card_type"],
                    card_types=view["card_types"],
                    subtypes=list(subtypes) if subtypes is not None else view["subtypes"],
                    mana_cost=view["mana_cost"],
                    power=d.get("power", view["power"]),
                    toughness=d.get("toughness", view["toughness"]),
                    owner_id=perm.instance.owner_id,
                    controller_id=perm.controller_id,
                    keywords=[kw.value for kw in keywords] if keywords is not None else view["keywords"],
                    tapped=perm.state.tapped,
                    damage_marked=perm.state.damage_marked,
                    counters=dict(perm.state.counters),
                    summoning_sick=perm.state.summoning_sick,
                    attached_to=perm.state.attached_to,
                    attachments=attachments_by_host.get(perm.instance.instance_id, []),
                    cant_attack_players=list(d.get("cant_attack_players", [])),
                    must_attack=bool(d.get("must_attack", False)),
                    must_be_blocked_by_all=bool(d.get("must_be_blocked_by_all", False)),
                    prevent_combat_damage=bool(d.get("prevent_combat_damage", False)),
                    assign_damage_as_unblocked=bool(d.get("assign_damage_as_unblocked", False)),
                    goaded_by=d.get("goaded_by"),
                )
            )
        stack_view = []
        for item in self.game.zones.stack:
            if item.instance is None:
                continue
            view = card_view(item.instance)
            stack_view.append(
                StackItemView(
                    kind=item.kind.value,
                    instance_id=item.instance.instance_id,
                    card_id=item.instance.card_id,
                    name=view["name"],
                    controller_id=item.controller_id,
                    targets=item.targets,
                    source_instance_id=item.source_instance_id,
                )
            )

        hand_view = []
        for ci in self._ps(player_id).hand:
            view = card_view(ci)
            hand_view.append(
                HandCardView(
                    instance_id=ci.instance_id,
                    card_id=ci.card_id,
                    name=view["name"],
                    card_type=view["card_type"],
                    mana_cost=view["mana_cost"],
                    power=view["power"],
                    toughness=view["toughness"],
                )
            )

        zones = ZonesView(
            battlefield=battlefield,
            stack=stack_view,
            graveyards={pid: list(ps.graveyard) for pid, ps in self.game.players.items()},
            exile=dict(self.game.zones.exile),
            hand=hand_view,
            library_size=len(self._ps(player_id).library),
        )

        return VisibleState(
            turn_number=self.game.turn.turn_number,
            active_player_id=self.game.turn.active_player_id,
            phase=self.game.turn.step.value,
            priority_holder_id=self._priority_holder,
            life_totals={pid: ps.life for pid, ps in self.game.players.items()},
            zones=zones,
            card_db=self.game.card_db,
            available_mana={
                "generic": self._ps(player_id).mana_pool.generic,
                "colored": dict(self._ps(player_id).mana_pool.colored),
            },
            lands_played_this_turn=self._ps(player_id).lands_played_this_turn,
            stack=stack_view,
            combat_attackers=list(self.game.turn.attackers),
            combat_blockers=dict(self.game.turn.blockers),
            combat_attackers_declared=self.game.turn.attackers_declared,
            combat_blockers_declared=self.game.turn.blockers_declared,
            pending_decision=self.game.pending_decision,
            game_over=self.game.game_over,
            winner_id=self.game.winner_id,
            end_reason=self.game.reason,
        )

    # ============================
    # Helpers
    # ============================

    def _ps(self, player_id: str) -> PlayerState:
        return self.game.players[player_id]

    def _other_player(self, player_id: str) -> str:
        for pid in self.game.players:
            if pid != player_id:
                return pid
        raise ValueError("Expected exactly two players")

    def _draw(self, player_id: str, n: int) -> None:
        ps = self._ps(player_id)
        for _ in range(n):
            if not ps.library:
                self._log(f"{player_id} tried to draw from empty library.")
                self._end_game(winner_id=self._other_player(player_id), reason="decked")
                return
            ps.hand.append(ps.library.pop())

    def _check_win_conditions(self) -> None:
        for pid, ps in self.game.players.items():
            if ps.life <= 0:
                self._end_game(winner_id=self._other_player(pid), reason="life")

    def _end_game(self, winner_id: str, reason: str) -> None:
        if self.game.game_over:
            return
        self.game.game_over = True
        self.game.winner_id = winner_id
        self.game.reason = reason
        self._log(f"Game over. Winner: {winner_id}. Reason: {reason}.")

    def _clear_mana_pools(self) -> None:
        for ps in self.game.players.values():
            ps.mana_pool.clear()

    def _untap_permanents(self, player_id: str) -> None:
        for perm in self.game.zones.battlefield.values():
            if perm.controller_id == player_id:
                perm.state.tapped = False
                if self._is_creature(perm):
                    perm.state.summoning_sick = False

    def _log(self, msg: str) -> None:
        self.game.metadata.log(msg)

    def can_play_land(self, player_id: str) -> bool:
        t = self.game.turn
        ps = self._ps(player_id)

        if t.active_player_id != player_id:
            return False

        if t.step not in (Step.MAIN1, Step.MAIN2):
            return False

        if ps.lands_played_this_turn >= 1:
            return False

        return any(self._is_land(ci) for ci in ps.hand)

    def get_playable_lands(self, player_id: str) -> List[CardInstance]:
        ps = self._ps(player_id)
        return [ci for ci in ps.hand if self._is_land(ci)]
    
    def _is_land(self, ci: CardInstance) -> bool:
        card = self.game.card_db.get(ci.card_id)
        if card is not None and CardType.LAND in card.card_types:
            return True
        return ci.card_id.startswith("basic_")

    def validate(self, action: Action) -> bool:
        if action.actor_id != self._priority_holder:
            return False

        if action.type == ActionType.PLAY_LAND:
            return self._validate_play_land(action)

        if action.type == ActionType.TAP_FOR_MANA:
            return self._validate_tap_for_mana(action)

        if action.type == ActionType.CAST_SPELL:
            return self._validate_cast_spell(action)

        if action.type == ActionType.ACTIVATE_ABILITY:
            return self._validate_activate_ability(action)

        if action.type == ActionType.DECLARE_ATTACKERS:
            return self._validate_declare_attackers(action)

        if action.type == ActionType.DECLARE_BLOCKERS:
            return self._validate_declare_blockers(action)

        if action.type == ActionType.SKIP_COMBAT:
            return self._validate_skip_combat(action.actor_id)

        if action.type == ActionType.SKIP_MAIN2:
            return self._validate_skip_main2(action.actor_id)

        if action.type == ActionType.SCOOP:
            return True

        if action.type == ActionType.PASS_PRIORITY:
            return self._validate_pass_priority(action.actor_id)

        if action.type == ActionType.RESOLVE_DECISION:
            return self._validate_resolve_decision(action)

        return False
    
    def _validate_play_land(self, action: Action) -> bool:
        if not self.can_play_land(action.actor_id):
            return False

        ps = self._ps(action.actor_id)
        return any(ci.instance_id == action.object_id for ci in ps.hand)

    def _validate_tap_for_mana(self, action: Action) -> bool:
        perm = self.game.zones.battlefield.get(action.object_id)
        if perm is None:
            return False
        if perm.controller_id != action.actor_id:
            return False
        if perm.state.tapped:
            return False
        return self._basic_land_produces(perm.instance.card_id) is not None

    def _validate_cast_spell(self, action: Action) -> bool:
        ps = self._ps(action.actor_id)
        payload = action.payload if isinstance(action.payload, dict) else {}
        flashback = bool(payload.get("flashback", False))

        if flashback:
            card_instance = next((ci for ci in ps.graveyard if ci.instance_id == action.object_id), None)
        else:
            card_instance = next((ci for ci in ps.hand if ci.instance_id == action.object_id), None)
        if card_instance is None:
            return False

        card = self.game.card_db.get(card_instance.card_id)
        if card is None:
            return False

        if CardType.LAND in card.card_types:
            return False
        if not (
            CardType.INSTANT in card.card_types
            or CardType.SORCERY in card.card_types
            or CardType.CREATURE in card.card_types
            or CardType.ARTIFACT in card.card_types
            or CardType.ENCHANTMENT in card.card_types
        ):
            return False

        if not self._timing_allows_cast(card, action.actor_id):
            return False

        x_value = int(payload.get("x", 0) or 0)

        alternate = payload.get("alternate_cost")
        if alternate:
            if not self._can_pay_alternate_cost(card, action.actor_id, alternate):
                return False
            if x_value:
                return False
        else:
            if flashback:
                if not card.rules.flashback_cost:
                    return False
                if not self._has_mana_cost(card.rules.flashback_cost, ps.mana_pool, action.actor_id, card=card):
                    return False
            else:
                if not self._has_mana(card, ps.mana_pool, action.actor_id, x_value):
                    return False

        # Additional costs
        additional_payload = payload.get("additional_costs", {}) if isinstance(payload, dict) else {}
        if card.rules.additional_costs and not isinstance(additional_payload, dict):
            return False
        for cost in card.rules.additional_costs:
            if cost.type == CostType.DISCARD_CARD:
                discards = additional_payload.get("discard", [])
                if not isinstance(discards, list) or len(discards) != int(cost.amount or 1):
                    return False
                hand_ids = {ci.instance_id for ci in ps.hand}
                for cid in discards:
                    if cid not in hand_ids:
                        return False
                    if not flashback and cid == card_instance.instance_id:
                        return False
            if cost.type in (CostType.SACRIFICE_CREATURE, CostType.SACRIFICE_OTHER_CREATURE):
                sacrifices = additional_payload.get("sacrifice", [])
                if not isinstance(sacrifices, list) or len(sacrifices) != int(cost.amount or 1):
                    return False
                for sid in sacrifices:
                    s_perm = self.game.zones.battlefield.get(sid)
                    if s_perm is None:
                        return False
                    if s_perm.controller_id != action.actor_id:
                        return False
                    if not self._is_creature(s_perm):
                        return False

        return self._targets_valid(card, action.targets, action.actor_id)

    def _validate_activate_ability(self, action: Action) -> bool:
        perm = self.game.zones.battlefield.get(action.object_id)
        if perm is None:
            return False
        if perm.controller_id != action.actor_id:
            return False

        card = self.game.card_db.get(perm.instance.card_id)
        if card is None:
            return False

        payload = action.payload if isinstance(action.payload, dict) else {}
        ability_index = payload.get("ability_index")
        if ability_index is None:
            return False
        if not isinstance(ability_index, int) or ability_index < 0:
            return False
        if ability_index >= len(card.rules.activated_abilities):
            return False
        ability = card.rules.activated_abilities[ability_index]

        if ability.zone != Zone.BATTLEFIELD:
            return False

        if ability.timing == TimingRestriction.SORCERY_SPEED:
            if self.game.turn.active_player_id != action.actor_id:
                return False
            if self.game.turn.step not in (Step.MAIN1, Step.MAIN2):
                return False
            if self.game.zones.stack:
                return False

        if ability.timing == TimingRestriction.ONLY_WHEN_ATTACKING:
            if perm.instance.instance_id not in self.game.turn.attackers:
                return False

        costs_payload = payload.get("costs", {}) if isinstance(payload, dict) else {}
        if not isinstance(costs_payload, dict):
            return False

        for cost in ability.costs:
            if cost.type == CostType.TAP:
                if perm.state.tapped:
                    return False
                if CardType.CREATURE in card.card_types and perm.state.summoning_sick:
                    if Keyword.HASTE not in card.rules.keywords:
                        return False

            if cost.type == CostType.MANA:
                if not self._has_mana_cost(cost.amount, self._ps(action.actor_id).mana_pool, player_id=action.actor_id):
                    return False

            if cost.type == CostType.PAY_LIFE:
                if self._ps(action.actor_id).life < int(cost.amount or 0):
                    return False

            if cost.type == CostType.SACRIFICE_SELF:
                if not costs_payload.get("sacrifice_self", False):
                    return False

            if cost.type in (CostType.SACRIFICE_CREATURE, CostType.SACRIFICE_OTHER_CREATURE):
                needed = int(cost.amount or 1)
                sacrifices = costs_payload.get("sacrifice", [])
                if not isinstance(sacrifices, list) or len(sacrifices) != needed:
                    return False
                for sid in sacrifices:
                    s_perm = self.game.zones.battlefield.get(sid)
                    if s_perm is None:
                        return False
                    if s_perm.controller_id != action.actor_id:
                        return False
                    if CardType.CREATURE not in self.game.card_db[s_perm.instance.card_id].card_types:
                        return False
                    if cost.type == CostType.SACRIFICE_OTHER_CREATURE and sid == perm.instance.instance_id:
                        return False

            if cost.type == CostType.DISCARD_CARD:
                needed = int(cost.amount or 1)
                discards = costs_payload.get("discard", [])
                if not isinstance(discards, list) or len(discards) != needed:
                    return False
                hand_ids = {ci.instance_id for ci in self._ps(action.actor_id).hand}
                if any(cid not in hand_ids for cid in discards):
                    return False

        if not self._targets_exist(action.targets):
            return False
        flat = self._flatten_targets(action.targets)
        if flat:
            derived = self._derived_battlefield_state()
            for target in flat:
                if target.get("type") != "PERMANENT":
                    continue
                perm = self.game.zones.battlefield.get(target.get("instance_id"))
                if perm is None:
                    continue
                if perm.controller_id != action.actor_id:
                    d = derived.get(perm.instance.instance_id)
                    if d and Keyword.HEXPROOF in d["keywords"]:
                        return False

        return True

    def _validate_resolve_decision(self, action: Action) -> bool:
        if self.game.pending_decision is None:
            return False
        return action.actor_id == self.game.pending_decision.player_id

    def _validate_declare_attackers(self, action: Action) -> bool:
        t = self.game.turn
        if t.step != Step.DECLARE_ATTACKERS:
            return False
        if t.active_player_id != action.actor_id:
            return False
        if self.game.zones.stack:
            return False
        if t.attackers_declared:
            return False

        attackers = []
        if isinstance(action.targets, dict):
            attackers = list(action.targets.get("attackers", []))

        derived = self._derived_battlefield_state()
        defender_id = self._other_player(action.actor_id)

        seen = set()
        for aid in attackers:
            if aid in seen:
                return False
            seen.add(aid)
            perm = self.game.zones.battlefield.get(aid)
            if perm is None:
                return False
            if perm.controller_id != action.actor_id:
                return False
            if not self._is_creature(perm):
                return False
            if not self._creature_can_attack(perm, derived, defender_id):
                return False

        # Must-attack creatures
        required = [
            pid for pid, d in derived.items()
            if d["controller_id"] == action.actor_id
            and d["must_attack"]
            and self._creature_can_attack(self.game.zones.battlefield[pid], derived, defender_id)
        ]
        for rid in required:
            if rid not in attackers:
                return False

        # Attack tax (temporary effect)
        tax = self._attack_tax_amount(defender_id)
        if tax > 0:
            total = tax * len(attackers)
            if not self._can_pay_generic_cost(self._ps(action.actor_id).mana_pool, total):
                return False

        return True

    def _validate_declare_blockers(self, action: Action) -> bool:
        t = self.game.turn
        if t.step != Step.DECLARE_BLOCKERS:
            return False
        if t.active_player_id == action.actor_id:
            return False
        if self.game.zones.stack:
            return False
        if t.blockers_declared:
            return False

        blocks = []
        if isinstance(action.targets, dict):
            blocks = list(action.targets.get("blocks", []))

        if not t.attackers:
            return len(blocks) == 0

        derived = self._derived_battlefield_state()
        used_blockers = set()
        mapping: Dict[str, List[str]] = {}

        for entry in blocks:
            attacker_id = entry.get("attacker_id")
            blocker_id = entry.get("blocker_id")
            if not attacker_id or not blocker_id:
                return False
            if attacker_id not in t.attackers:
                return False

            perm = self.game.zones.battlefield.get(blocker_id)
            if perm is None:
                return False
            if perm.controller_id != action.actor_id:
                return False
            if not self._is_creature(perm):
                return False
            if not self._creature_can_block(perm, attacker_id, derived):
                return False
            if blocker_id in used_blockers:
                return False
            used_blockers.add(blocker_id)
            mapping.setdefault(attacker_id, []).append(blocker_id)

        # Menace: must be blocked by 2+ creatures if blocked
        for attacker_id in t.attackers:
            d = derived.get(attacker_id, {})
            if Keyword.MENACE in d.get("keywords", set()):
                if len(mapping.get(attacker_id, [])) == 1:
                    return False

        # Require block: all creatures able to block must do so
        for attacker_id in t.attackers:
            d = derived.get(attacker_id, {})
            if not d.get("must_be_blocked_by_all", False):
                continue
            for perm in self.game.zones.battlefield.values():
                if perm.controller_id != action.actor_id:
                    continue
                if not self._is_creature(perm):
                    continue
                if not self._creature_can_block(perm, attacker_id, derived):
                    continue
                if perm.instance.instance_id not in mapping.get(attacker_id, []):
                    return False

        return True

    def _validate_pass_priority(self, player_id: str) -> bool:
        t = self.game.turn
        if t.step == Step.DECLARE_ATTACKERS and t.active_player_id == player_id:
            return t.attackers_declared
        if t.step == Step.DECLARE_BLOCKERS and t.active_player_id != player_id:
            return t.blockers_declared
        return True

    def _validate_skip_combat(self, player_id: str) -> bool:
        t = self.game.turn
        if t.active_player_id != player_id:
            return False
        if t.step != Step.MAIN1:
            return False
        if self.game.zones.stack:
            return False
        return True

    def _validate_skip_main2(self, player_id: str) -> bool:
        t = self.game.turn
        if t.active_player_id != player_id:
            return False
        if t.step != Step.MAIN2:
            return False
        if self.game.zones.stack:
            return False
        return True

    def resolve(self, action: Action) -> Any:
        if action.type == ActionType.PLAY_LAND:
            return self._resolve_play_land(action)

        if action.type == ActionType.TAP_FOR_MANA:
            return self._resolve_tap_for_mana(action)

        if action.type == ActionType.DECLARE_ATTACKERS:
            return self._resolve_declare_attackers(action)

        if action.type == ActionType.DECLARE_BLOCKERS:
            return self._resolve_declare_blockers(action)

        if action.type == ActionType.CAST_SPELL:
            return self._resolve_cast_spell(action)

        if action.type == ActionType.ACTIVATE_ABILITY:
            return self._resolve_activate_ability(action)

        if action.type == ActionType.SKIP_COMBAT:
            return self._resolve_skip_combat(action.actor_id)

        if action.type == ActionType.SKIP_MAIN2:
            return self._resolve_skip_main2(action.actor_id)

        if action.type == ActionType.SCOOP:
            return self._resolve_scoop(action.actor_id)

        if action.type == ActionType.PASS_PRIORITY:
            return self._resolve_pass_priority(action.actor_id)

        if action.type == ActionType.RESOLVE_DECISION:
            return self._resolve_decision(action)

        raise ValueError(f"Unsupported action: {action.type}")

    def _resolve_play_land(self, action: Action) -> Dict[str, Any]:
        ps = self._ps(action.actor_id)

        for i, ci in enumerate(ps.hand):
            if ci.instance_id == action.object_id:
                land = ps.hand.pop(i)
                perm = Permanent(
                    instance=land,
                    controller_id=action.actor_id,
                )
                self.game.zones.battlefield[land.instance_id] = perm
                ps.lands_played_this_turn += 1
                self._pass_streak = 0
                self._handle_etb(perm)

                self._log(f"{action.actor_id} plays land {land.card_id}.")
                return {"played_land": land.card_id}

        raise RuntimeError("Land not found in hand")

    def _resolve_tap_for_mana(self, action: Action) -> Dict[str, Any]:
        perm = self.game.zones.battlefield.get(action.object_id)
        if perm is None:
            raise RuntimeError("Permanent not found on battlefield")

        if perm.controller_id != action.actor_id:
            raise RuntimeError("Cannot tap a permanent you do not control")

        if perm.state.tapped:
            raise RuntimeError("Permanent is already tapped")

        produces = self._basic_land_produces(perm.instance.card_id)
        if produces is None:
            raise RuntimeError("Permanent does not produce mana")

        perm.state.tapped = True
        self._pass_streak = 0

        ps = self._ps(action.actor_id)
        for color, amount in produces.items():
            ps.mana_pool.colored[color] = ps.mana_pool.colored.get(color, 0) + amount

        self._log(f"{action.actor_id} taps {perm.instance.card_id} for mana.")
        return {"tapped": perm.instance.card_id, "mana_added": produces}

    def _resolve_declare_attackers(self, action: Action) -> Dict[str, Any]:
        attackers = []
        if isinstance(action.targets, dict):
            attackers = list(action.targets.get("attackers", []))

        self.game.turn.attackers = attackers
        self.game.turn.blockers = {}
        self.game.turn.attackers_declared = True
        self.game.turn.blockers_declared = False

        derived = self._derived_battlefield_state()
        for aid in attackers:
            perm = self.game.zones.battlefield.get(aid)
            if perm is not None:
                d = derived.get(aid, {})
                if Keyword.VIGILANCE not in d.get("keywords", set()):
                    perm.state.tapped = True

        defender_id = self._other_player(action.actor_id)
        tax = self._attack_tax_amount(defender_id)
        if tax > 0:
            total = tax * len(attackers)
            self._pay_generic_cost(self._ps(action.actor_id).mana_pool, total)

        self._pass_streak = 0
        self._priority_holder = self._other_player(action.actor_id)
        self._handle_attacks(attackers)
        self._log(f"{action.actor_id} declares attackers: {attackers}")
        return {"attackers": attackers}

    def _resolve_declare_blockers(self, action: Action) -> Dict[str, Any]:
        blocks = []
        if isinstance(action.targets, dict):
            blocks = list(action.targets.get("blocks", []))

        mapping: Dict[str, List[str]] = {}
        for entry in blocks:
            attacker_id = entry.get("attacker_id")
            blocker_id = entry.get("blocker_id")
            if attacker_id and blocker_id:
                mapping.setdefault(attacker_id, []).append(blocker_id)

        self.game.turn.blockers = mapping
        self.game.turn.blockers_declared = True
        self._pass_streak = 0
        self._priority_holder = self._other_player(action.actor_id)
        all_blockers = [bid for bids in mapping.values() for bid in bids]
        self._handle_blocks(all_blockers)
        self._log(f"{action.actor_id} declares blockers: {mapping}")
        return {"blockers": mapping}

    def _resolve_cast_spell(self, action: Action) -> Dict[str, Any]:
        ps = self._ps(action.actor_id)
        payload = action.payload if isinstance(action.payload, dict) else {}
        flashback = bool(payload.get("flashback", False))

        if flashback:
            idx = next((i for i, ci in enumerate(ps.graveyard) if ci.instance_id == action.object_id), None)
            if idx is None:
                raise RuntimeError("Card not found in graveyard")
            card_instance = ps.graveyard.pop(idx)
        else:
            idx = next((i for i, ci in enumerate(ps.hand) if ci.instance_id == action.object_id), None)
            if idx is None:
                raise RuntimeError("Card not found in hand")
            card_instance = ps.hand.pop(idx)
        card = self.game.card_db.get(card_instance.card_id)
        if card is None:
            raise RuntimeError("Card data not found")

        x_value = int(payload.get("x", 0) or 0)
        additional_costs = payload.get("additional_costs", {}) if isinstance(payload, dict) else {}

        # Pay additional costs
        for cost in card.rules.additional_costs:
            if cost.type == CostType.DISCARD_CARD:
                discards = additional_costs.get("discard", [])
                for cid in discards:
                    self._discard_from_hand(action.actor_id, cid)
            if cost.type in (CostType.SACRIFICE_CREATURE, CostType.SACRIFICE_OTHER_CREATURE):
                sacrifices = additional_costs.get("sacrifice", [])
                for sid in sacrifices:
                    s_perm = self.game.zones.battlefield.get(sid)
                    if s_perm is not None:
                        self._sacrifice_permanent(s_perm)

        alternate = payload.get("alternate_cost")
        if alternate:
            self._pay_alternate_cost(card, action.actor_id, alternate)
        else:
            if flashback:
                self._pay_mana_cost(card.rules.flashback_cost, ps.mana_pool, player_id=action.actor_id, card=card)
            else:
                self._pay_mana(card, ps.mana_pool, action.actor_id, x_value)
        self._pass_streak = 0

        meta = dict(payload) if isinstance(payload, dict) else {}
        if flashback:
            meta["exile_on_resolve"] = True

        self.game.zones.stack.append(
            StackItem(
                kind=StackItemKind.SPELL,
                controller_id=action.actor_id,
                instance=card_instance,
                targets=action.targets,
                meta=meta,
            )
        )
        self._priority_holder = self._other_player(action.actor_id)
        self._handle_cast_spell(action.actor_id, card)
        self._notify_becomes_target(action.targets, action.actor_id)
        self._log(f"{action.actor_id} casts {card_instance.card_id} (to stack).")
        return {"cast_spell": card_instance.card_id, "stack_size": len(self.game.zones.stack)}

    def _resolve_activate_ability(self, action: Action) -> Dict[str, Any]:
        perm = self.game.zones.battlefield.get(action.object_id)
        if perm is None:
            raise RuntimeError("Permanent not found for ability activation")

        card = self.game.card_db.get(perm.instance.card_id)
        if card is None:
            raise RuntimeError("Card data not found for ability activation")

        payload = action.payload if isinstance(action.payload, dict) else {}
        ability_index = payload.get("ability_index")
        if ability_index is None or not isinstance(ability_index, int):
            raise RuntimeError("Missing ability_index for activation")
        if ability_index < 0 or ability_index >= len(card.rules.activated_abilities):
            raise RuntimeError("Ability index out of range")

        ability = card.rules.activated_abilities[ability_index]
        costs_payload = payload.get("costs", {}) if isinstance(payload, dict) else {}

        # Pay costs
        derived = self._derived_battlefield_state()
        sacrificed_toughness = 0
        for cost in ability.costs:
            if cost.type == CostType.TAP:
                perm.state.tapped = True
            elif cost.type == CostType.MANA:
                self._pay_mana_cost(cost.amount, self._ps(action.actor_id).mana_pool, player_id=action.actor_id)
            elif cost.type == CostType.PAY_LIFE:
                self._ps(action.actor_id).life -= int(cost.amount or 0)
            elif cost.type == CostType.DISCARD_CARD:
                discards = costs_payload.get("discard", [])
                for cid in discards:
                    self._discard_from_hand(action.actor_id, cid)
            elif cost.type == CostType.SACRIFICE_SELF:
                d = derived.get(perm.instance.instance_id)
                if d and d.get("toughness") is not None:
                    sacrificed_toughness += int(d["toughness"])
                self._sacrifice_permanent(perm)
            elif cost.type in (CostType.SACRIFICE_CREATURE, CostType.SACRIFICE_OTHER_CREATURE):
                sacrifices = costs_payload.get("sacrifice", [])
                for sid in sacrifices:
                    s_perm = self.game.zones.battlefield.get(sid)
                    if s_perm is not None:
                        d = derived.get(sid)
                        if d and d.get("toughness") is not None:
                            sacrificed_toughness += int(d["toughness"])
                        self._sacrifice_permanent(s_perm)

        self._pass_streak = 0

        effects = self._materialize_effects_with_context(
            ability.effects,
            source_perm=perm,
            context={"sacrificed_toughness": sacrificed_toughness},
        )

        if self._is_mana_ability(ability):
            self._resolve_effects(
                effects,
                action.targets,
                source_instance_id=perm.instance.instance_id,
                controller_id=action.actor_id,
                meta={"sacrificed_toughness": sacrificed_toughness},
            )
            self._log(f"{action.actor_id} activates mana ability of {perm.instance.card_id}.")
            return {"activated_ability": perm.instance.card_id, "mana_ability": True}

        self.game.zones.stack.append(
            StackItem(
                kind=StackItemKind.ABILITY,
                controller_id=action.actor_id,
                source_instance_id=perm.instance.instance_id,
                effects=effects,
                targets=action.targets,
                meta={"sacrificed_toughness": sacrificed_toughness, **payload},
            )
        )
        self._priority_holder = self._other_player(action.actor_id)
        self._notify_becomes_target(action.targets, action.actor_id)
        self._log(f"{action.actor_id} activates ability of {perm.instance.card_id} (to stack).")
        return {"activated_ability": perm.instance.card_id, "stack_size": len(self.game.zones.stack)}

    def _resolve_decision(self, action: Action) -> Dict[str, Any]:
        pending = self.game.pending_decision
        if pending is None:
            raise RuntimeError("No pending decision")
        payload = action.payload if isinstance(action.payload, dict) else {}
        choice = payload.get("choice")
        kind = pending.kind
        context = pending.context or {}
        self.game.pending_decision = None

        if kind == "TRIGGER_TARGETS":
            trigger = context.get("trigger", {})
            self.game.zones.stack.append(
                StackItem(
                    kind=StackItemKind.ABILITY,
                    controller_id=trigger.get("controller_id"),
                    source_instance_id=trigger.get("source_instance_id"),
                    effects=trigger.get("effects"),
                    targets=choice,
                    meta={"trigger": "TRIGGERED"},
                )
            )
            self._notify_becomes_target(choice, trigger.get("controller_id"))
            queue = context.get("queue", [])
            if queue:
                next_item = queue.pop(0)
                self.game.pending_decision = PendingDecision(
                    player_id=next_item["trigger"]["controller_id"],
                    kind="TRIGGER_TARGETS",
                    options=next_item["options"],
                    context={"trigger": next_item["trigger"], "queue": queue},
                )
            return {"trigger_targets": True}

        if kind == "DISCARD_ONE":
            player_id = context.get("player_id")
            if player_id and choice:
                self._discard_from_hand(player_id, choice)
            self._resume_from_pending(context)
            return {"discarded": choice}

        if kind == "DISCARD_HAND_DRAW_EQUAL_DAMAGE":
            player_id = context.get("player_id")
            damage = int(context.get("damage", 0))
            if isinstance(choice, dict) and choice.get("discard"):
                ps = self._ps(player_id)
                while ps.hand:
                    card = ps.hand.pop()
                    if not card.is_token:
                        ps.graveyard.append(card)
                if damage > 0:
                    self._draw(player_id, damage)
            self._resume_from_pending(context)
            return {"discard_hand": True}

        if kind == "SEARCH_BASIC_LAND":
            player_id = context.get("player_id")
            picked = choice.get("choice") if isinstance(choice, dict) else None
            if player_id and picked:
                ps = self._ps(player_id)
                for i, ci in enumerate(ps.library):
                    if ci.instance_id == picked:
                        ps.library.pop(i)
                        perm = Permanent(instance=ci, controller_id=player_id)
                        perm.state.tapped = True
                        self.game.zones.battlefield[ci.instance_id] = perm
                        self._handle_etb(perm)
                        self._handle_creature_enters(perm)
                        ps.library = list(ps.library)
                        self.game.rng.rng.shuffle(ps.library)
                        break
            self._resume_from_pending(context)
            return {"search_basic_land": picked}

        if kind == "SEARCH_BASIC_PLAINS":
            player_id = context.get("player_id")
            picked = choice.get("choice") if isinstance(choice, dict) else None
            if player_id and picked:
                ps = self._ps(player_id)
                for i, ci in enumerate(ps.library):
                    if ci.instance_id == picked:
                        ps.library.pop(i)
                        ps.hand.append(ci)
                        self.game.rng.rng.shuffle(ps.library)
                        break
            self._resume_from_pending(context)
            return {"search_basic_plains": picked}

        if kind == "LOOK_AT_TOP_ONE":
            player_id = context.get("player_id")
            top_cards = context.get("top_cards", [])
            if player_id and top_cards:
                ps = self._ps(player_id)
                self._remove_from_library(ps, top_cards)
                chosen = next((ci for ci in top_cards if ci.instance_id == choice), None)
                if chosen is not None:
                    ps.hand.append(chosen)
                rest = [ci for ci in top_cards if ci.instance_id != choice]
                self._put_on_bottom(ps, rest)
            self._resume_from_pending(context)
            return {"look_top_one": choice}

        if kind == "LOOK_AT_TOP_LAND":
            player_id = context.get("player_id")
            top_cards = context.get("top_cards", [])
            picked = choice.get("choice") if isinstance(choice, dict) else None
            if player_id and top_cards:
                ps = self._ps(player_id)
                self._remove_from_library(ps, top_cards)
                rest = [ci for ci in top_cards if ci.instance_id != picked]
                if picked:
                    chosen = next((ci for ci in top_cards if ci.instance_id == picked), None)
                    if chosen is not None:
                        perm = Permanent(instance=chosen, controller_id=player_id)
                        perm.state.tapped = True
                        self.game.zones.battlefield[chosen.instance_id] = perm
                        self._handle_etb(perm)
                        self._handle_creature_enters(perm)
                self.game.rng.rng.shuffle(rest)
                self._put_on_bottom(ps, rest)
            self._resume_from_pending(context)
            return {"look_top_land": picked}

        if kind == "SCRY":
            player_id = context.get("player_id")
            top_cards = context.get("top_cards", [])
            if player_id and top_cards and isinstance(choice, dict):
                ps = self._ps(player_id)
                self._remove_from_library(ps, top_cards)
                top_ids = choice.get("top", [])
                bottom_ids = choice.get("bottom", [])
                id_to_card = {ci.instance_id: ci for ci in top_cards}
                bottom_cards = [id_to_card[cid] for cid in bottom_ids if cid in id_to_card]
                top_cards_sel = [id_to_card[cid] for cid in top_ids if cid in id_to_card]
                self._put_on_bottom(ps, bottom_cards)
                ps.library.extend(top_cards_sel)
                draw_count = int(context.get("draw", 0) or 0)
                if draw_count > 0:
                    self._draw(player_id, draw_count)
            self._resume_from_pending(context)
            return {"scry": True}

        if kind == "FACT_OR_FICTION_SPLIT":
            player_id = context.get("player_id")
            top_cards = context.get("top_cards", [])
            if not isinstance(choice, dict):
                self._resume_from_pending(context)
                return {"fact_or_fiction": False}
            piles = {"A": choice.get("pile_a", []), "B": choice.get("pile_b", [])}
            self.game.pending_decision = PendingDecision(
                player_id=player_id,
                kind="FACT_OR_FICTION_PICK",
                options=[{"pile": "A"}, {"pile": "B"}],
                context={"player_id": player_id, "top_cards": top_cards, "piles": piles, **context},
            )
            return {"fact_or_fiction_split": True}

        if kind == "FACT_OR_FICTION_PICK":
            player_id = context.get("player_id")
            top_cards = context.get("top_cards", [])
            piles = context.get("piles", {})
            pick = choice.get("pile") if isinstance(choice, dict) else None
            if player_id and top_cards and pick in ("A", "B"):
                ps = self._ps(player_id)
                self._remove_from_library(ps, top_cards)
                chosen_ids = set(piles.get(pick, []))
                for ci in top_cards:
                    if ci.instance_id in chosen_ids:
                        ps.hand.append(ci)
                    else:
                        if not ci.is_token:
                            ps.graveyard.append(ci)
            self._resume_from_pending(context)
            return {"fact_or_fiction_pick": pick}

        if kind == "VOTE":
            vote_type = context.get("vote_type")
            players = context.get("players", [])
            votes = context.get("votes", {})
            voters_done = len(votes)
            if voters_done < len(players):
                votes[players[voters_done]] = choice
            if len(votes) < len(players):
                next_player = players[len(votes)]
                self.game.pending_decision = PendingDecision(
                    player_id=next_player,
                    kind="VOTE",
                    options=self._vote_options(vote_type),
                    context={**context, "votes": votes},
                )
                return {"vote": True}
            counts: Dict[str, int] = {}
            for v in votes.values():
                counts[v] = counts.get(v, 0) + 1
            controller_id = context.get("controller_id")
            target = context.get("target")
            if vote_type == "PLEA_FOR_POWER":
                time_votes = counts.get("time", 0)
                knowledge_votes = counts.get("knowledge", 0)
                if controller_id and time_votes > knowledge_votes:
                    self.game.extra_turns.append(controller_id)
                elif controller_id:
                    self._draw(controller_id, 3)
            if vote_type == "SPLIT_DECISION":
                denial = counts.get("denial", 0)
                duplication = counts.get("duplication", 0)
                target_id = target.get("instance_id") if isinstance(target, dict) else None
                if denial > duplication:
                    self._counter_spell(target_id)
                else:
                    if target_id and controller_id:
                        item = next(
                            (s for s in self.game.zones.stack if s.instance is not None and s.instance.instance_id == target_id),
                            None,
                        )
                        if item is not None and item.instance is not None:
                            card = self.game.card_db.get(item.instance.card_id)
                            if card is not None:
                                effects = self._effects_for_card(card, item.meta)
                                visible = self.get_visible_state(controller_id)
                                options = ActionSurface()._enumerate_targets_for_effects(effects, visible, controller_id, source_perm=None)
                                if options and options != [[]]:
                                    if item.targets not in options:
                                        options = [item.targets] + options
                                    self.game.pending_decision = PendingDecision(
                                        player_id=controller_id,
                                        kind="COPY_SPELL_TARGETS",
                                        options=options,
                                        context={**context, "copy_target_id": target_id, "controller_id": controller_id},
                                    )
                                    return {"vote_resolved": True}
                    self._copy_spell(target_id, controller_id)
            self._resume_from_pending(context)
            return {"vote_resolved": True}

        if kind == "COUNTER_UNLESS_PAY":
            target_id = context.get("target_id")
            cost = context.get("cost")
            if isinstance(choice, dict) and choice.get("pay"):
                self._pay_mana_cost(cost, self._ps(action.actor_id).mana_pool, player_id=action.actor_id)
            else:
                self._counter_spell(target_id)
            self._resume_from_pending(context)
            return {"counter_unless_pay": True}

        if kind == "OPTIONAL_COST":
            if isinstance(choice, dict) and choice.get("pay"):
                x_val = int(choice.get("x", 0) or 0)
                cost = ManaCost(generic=x_val, colored={Color.RED: 1}, x=0)
                self._pay_mana_cost(cost, self._ps(action.actor_id).mana_pool, player_id=action.actor_id)
                eff = context.get("effect")
                group = context.get("group", [])
                if eff:
                    eff_params = dict(eff.params)
                    eff_params["amount"] = x_val
                    eff = Effect(type=eff.type, params=eff_params)
                    for t in group:
                        self._apply_deal_damage(eff, t, context.get("source_instance_id"), context.get("controller_id"), meta={"x": x_val})
            self._resume_from_pending(context)
            return {"optional_cost": True}

        if kind == "EACH_PLAYER_SACRIFICE":
            if choice:
                perm = self.game.zones.battlefield.get(choice)
                if perm is not None:
                    self._sacrifice_permanent(perm)
            queue = context.get("queue", [])
            if queue:
                next_item = queue.pop(0)
                self.game.pending_decision = PendingDecision(
                    player_id=next_item["player_id"],
                    kind="EACH_PLAYER_SACRIFICE",
                    options=next_item["options"],
                    context={**context, "queue": queue},
                )
                return {"each_player_sacrifice": True}
            self._resume_from_pending(context)
            return {"each_player_sacrifice": True}

        if kind == "SACRIFICE_CHOICE":
            if choice:
                perm = self.game.zones.battlefield.get(choice)
                if perm is not None:
                    self._sacrifice_permanent(perm)
            self._resume_from_pending(context)
            return {"sacrifice_choice": True}

        if kind == "CAST_FROM_OPPONENT_GRAVEYARD":
            opponent_id = context.get("opponent_id")
            picked = choice.get("choice") if isinstance(choice, dict) else None
            if not picked:
                self._resume_from_pending(context)
                return {"cast_from_graveyard": None}
            ps = self._ps(opponent_id)
            card_instance = next((ci for ci in ps.graveyard if ci.instance_id == picked), None)
            if card_instance is None:
                self._resume_from_pending(context)
                return {"cast_from_graveyard": None}
            ps.graveyard = [ci for ci in ps.graveyard if ci.instance_id != picked]
            card = self.game.card_db.get(card_instance.card_id)
            if card is None:
                self._resume_from_pending(context)
                return {"cast_from_graveyard": None}
            effects = self._effects_for_card(card, meta=None)
            if self._effects_need_targets(effects):
                visible = self.get_visible_state(context.get("player_id"))
                options = ActionSurface()._enumerate_targets_for_effects(effects, visible, context.get("player_id"), source_perm=None)
                if options and options != [[]]:
                    self.game.pending_decision = PendingDecision(
                        player_id=context.get("player_id"),
                        kind="CAST_FROM_OPPONENT_GRAVEYARD_TARGETS",
                        options=options,
                        context={
                            **context,
                            "card_instance": card_instance,
                            "card": card,
                        },
                    )
                    return {"cast_from_graveyard_targets": True}
            self._cast_from_graveyard_instance(context.get("player_id"), card_instance, card, None)
            self._resume_from_pending(context)
            return {"cast_from_graveyard": picked}

        if kind == "CAST_FROM_OPPONENT_GRAVEYARD_TARGETS":
            card_instance = context.get("card_instance")
            card = context.get("card")
            self._cast_from_graveyard_instance(context.get("player_id"), card_instance, card, choice)
            self._resume_from_pending(context)
            return {"cast_from_graveyard": True}

        if kind == "COPY_SPELL_TARGETS":
            target_id = context.get("copy_target_id")
            controller_id = context.get("controller_id")
            self._copy_spell(target_id, controller_id, choice)
            self._resume_from_pending(context)
            return {"copy_spell_targets": True}

        raise RuntimeError(f"Unsupported decision type: {kind}")

    def _resolve_scoop(self, player_id: str) -> Dict[str, Any]:
        self._end_game(winner_id=self._other_player(player_id), reason="scoop")
        return {"scoop": player_id}

    def _resolve_skip_combat(self, player_id: str) -> Dict[str, Any]:
        t = self.game.turn
        t.phase = Phase.MAIN
        t.step = Step.MAIN2
        t.attackers = []
        t.blockers = {}
        t.attackers_declared = False
        t.blockers_declared = False
        self._pass_streak = 0
        self._priority_holder = t.active_player_id
        self._log(f"{player_id} skips combat.")
        return {"skipped": "COMBAT"}

    def _resolve_skip_main2(self, player_id: str) -> Dict[str, Any]:
        t = self.game.turn
        t.phase = Phase.ENDING
        t.step = Step.END
        self._pass_streak = 0
        self._priority_holder = t.active_player_id
        self._log(f"{player_id} skips main 2.")
        return {"skipped": "MAIN2"}

    def _resolve_spell_effects(
        self,
        card: Any,
        targets: Any,
        controller_id: Optional[str],
    ) -> None:
        self._resolve_effects(card.rules.effects, targets, source_instance_id=None, controller_id=controller_id, meta=None, resume=None)

    def _normalize_target_groups(self, targets: Any, effect_count: int) -> List[List[Dict[str, Any]]]:
        if targets is None:
            return [[] for _ in range(effect_count)]
        if isinstance(targets, dict):
            return [[targets]] + [[] for _ in range(max(0, effect_count - 1))]
        if isinstance(targets, list):
            if not targets:
                return [[] for _ in range(effect_count)]
            if all(isinstance(t, dict) for t in targets):
                return [targets] + [[] for _ in range(max(0, effect_count - 1))]
            if all(isinstance(t, list) for t in targets):
                groups: List[List[Dict[str, Any]]] = []
                for group in targets:
                    if group is None:
                        groups.append([])
                    elif isinstance(group, list):
                        groups.append([t for t in group if isinstance(t, dict)])
                    elif isinstance(group, dict):
                        groups.append([group])
                    else:
                        groups.append([])
                if effect_count:
                    if len(groups) < effect_count:
                        groups.extend([[] for _ in range(effect_count - len(groups))])
                    if len(groups) > effect_count:
                        groups = groups[:effect_count]
                return groups
        return [[] for _ in range(effect_count)]

    def _resolve_effects(
        self,
        effects: List[Any],
        targets: Any,
        source_instance_id: Optional[str],
        controller_id: Optional[str],
        meta: Optional[Dict[str, Any]] = None,
        resume: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not effects:
            return False
        target_groups = self._normalize_target_groups(targets, len(effects))
        for idx, eff in enumerate(effects):
            etype = getattr(eff, "type", None)
            group = target_groups[idx] if idx < len(target_groups) else []

            if etype == EffectType.MODAL:
                continue

            if etype == EffectType.DEAL_DAMAGE:
                if eff.params.get("requires_optional_cost") and controller_id is not None:
                    options = self._optional_cost_options(controller_id)
                    return self._queue_resolution_decision(
                        controller_id,
                        "OPTIONAL_COST",
                        options,
                        {"effect": eff, "group": group},
                        effects,
                        idx,
                        targets,
                        source_instance_id,
                        controller_id,
                        meta,
                        resume,
                    )
                for t in group:
                    self._apply_deal_damage(eff, t, source_instance_id, controller_id, meta)
                continue

            if etype == EffectType.DRAW_CARDS:
                if not group and controller_id is not None:
                    group = [{"type": "PLAYER", "player_id": controller_id}]
                for t in group:
                    self._apply_draw_cards(eff, t)
                continue

            if etype == EffectType.DRAW_X_THEN_DISCARD:
                if controller_id is None:
                    continue
                x_value = int((meta or {}).get("x", 0) or 0)
                if x_value > 0:
                    self._draw(controller_id, x_value)
                hand_ids = [ci.instance_id for ci in self._ps(controller_id).hand]
                if hand_ids:
                    return self._queue_resolution_decision(
                        controller_id,
                        "DISCARD_ONE",
                        hand_ids,
                        {"player_id": controller_id},
                        effects,
                        idx,
                        targets,
                        source_instance_id,
                        controller_id,
                        meta,
                        resume,
                    )
                continue

            if etype == EffectType.DISCARD_HAND_DRAW_EQUAL_DAMAGE:
                if controller_id is None:
                    continue
                target = group[0] if group else None
                if target is None or target.get("type") != "PLAYER":
                    continue
                opponent_id = target.get("player_id")
                damage = self.game.damage_dealt_to_players.get(opponent_id, 0)
                options = [{"discard": False}, {"discard": True}]
                return self._queue_resolution_decision(
                    controller_id,
                    "DISCARD_HAND_DRAW_EQUAL_DAMAGE",
                    options,
                    {"player_id": controller_id, "damage": damage},
                    effects,
                    idx,
                    targets,
                    source_instance_id,
                    controller_id,
                    meta,
                    resume,
                )

            if etype == EffectType.DISCARD_HAND_DRAW_SEVEN:
                for pid, ps in self.game.players.items():
                    while ps.hand:
                        card = ps.hand.pop()
                        if not card.is_token:
                            ps.graveyard.append(card)
                    self._draw(pid, 7)
                continue

            if etype == EffectType.EACH_PLAYER_DRAWS:
                amount = int(eff.params.get("amount", 0) or 0)
                if amount <= 0:
                    continue
                for pid in self.game.players:
                    self._draw(pid, amount)
                continue

            if etype == EffectType.EACH_PLAYER_SACRIFICE:
                players = [self.game.turn.active_player_id, self._other_player(self.game.turn.active_player_id)]
                queue = []
                for pid in players:
                    creatures = [
                        perm.instance.instance_id
                        for perm in self.game.zones.battlefield.values()
                        if perm.controller_id == pid and self._is_creature(perm)
                    ]
                    if creatures:
                        queue.append({"player_id": pid, "options": creatures})
                if queue:
                    first = queue.pop(0)
                    return self._queue_resolution_decision(
                        first["player_id"],
                        "EACH_PLAYER_SACRIFICE",
                        first["options"],
                        {"queue": queue},
                        effects,
                        idx,
                        targets,
                        source_instance_id,
                        controller_id,
                        meta,
                        resume,
                    )
                continue

            if etype == EffectType.SACRIFICE_TARGET:
                chooser = eff.params.get("chooser_player_id")
                if chooser:
                    options = [
                        perm.instance.instance_id
                        for perm in self.game.zones.battlefield.values()
                        if perm.controller_id == chooser and self._is_creature(perm)
                    ]
                    if options:
                        return self._queue_resolution_decision(
                            chooser,
                            "SACRIFICE_CHOICE",
                            options,
                            {"player_id": chooser},
                            effects,
                            idx,
                            targets,
                            source_instance_id,
                            controller_id,
                            meta,
                            resume,
                        )
                    continue
                for t in group:
                    if t.get("type") != "PERMANENT":
                        continue
                    perm = self.game.zones.battlefield.get(t.get("instance_id"))
                    if perm is not None:
                        self._sacrifice_permanent(perm)
                continue

            if etype == EffectType.LOSE_LIFE:
                if not group and controller_id is not None:
                    group = [{"type": "PLAYER", "player_id": controller_id}]
                for t in group:
                    self._apply_lose_life(eff, t)
                continue

            if etype == EffectType.GAIN_LIFE:
                if controller_id is None:
                    continue
                amount = eff.params.get("amount", 0)
                if amount == "SACRIFICED_TOUGHNESS":
                    amount = int((meta or {}).get("sacrificed_toughness", 0))
                if isinstance(amount, int):
                    self.game.players[controller_id].life += int(amount)
                continue

            if etype == EffectType.ADD_MANA:
                self._apply_add_mana(eff, controller_id)
                continue

            if etype == EffectType.ADD_MANA_PER_ELF:
                self._apply_add_mana_per_elf(eff, controller_id)
                continue

            if etype == EffectType.ADD_MANA_PER_TAPPED_LANDS:
                self._apply_add_mana_per_tapped_lands(eff, controller_id)
                continue

            if etype == EffectType.CREATE_TOKEN:
                self._apply_create_token(eff, controller_id, source_instance_id)
                continue

            if etype == EffectType.ATTACK_TAX:
                duration = eff.params.get("duration", "UNTIL_YOUR_NEXT_TURN")
                self._add_temporary_effect(eff, controller_id, source_instance_id, duration, None)
                continue

            if etype == EffectType.DESTROY_CREATURE:
                for t in group:
                    self._apply_destroy_creature(eff, t)
                continue

            if etype == EffectType.DESTROY_ARTIFACT:
                for t in group:
                    self._apply_destroy_artifact(t)
                continue

            if etype == EffectType.DESTROY_FLYING_CREATURE:
                for t in group:
                    self._apply_destroy_flying_creature(t)
                continue

            if etype == EffectType.DESTROY_PERMANENT:
                for t in group:
                    self._apply_destroy_permanent_target(t)
                continue

            if etype == EffectType.EXILE_CREATURE:
                for t in group:
                    self._apply_exile_creature(eff, t)
                continue

            if etype == EffectType.EXILE_TARGET_UNTIL:
                for t in group:
                    self._apply_exile_until(eff, t, source_instance_id)
                continue

            if etype == EffectType.SEARCH_BASIC_LAND_TO_BATTLEFIELD_TAPPED:
                targets_list = self._targets_from_group_or_self(group, eff, source_instance_id)
                if targets_list:
                    target = targets_list[0]
                    if target.get("type") == "PERMANENT":
                        perm = self.game.zones.battlefield.get(target.get("instance_id"))
                        if perm is not None:
                            controller = perm.controller_id
                            self._apply_exile_creature(Effect(EffectType.EXILE_CREATURE, {"target": target}), target)
                            options = self._basic_land_choices(controller)
                            return self._queue_resolution_decision(
                                controller,
                                "SEARCH_BASIC_LAND",
                                options,
                                {"player_id": controller, "to_battlefield": True},
                                effects,
                                idx,
                                targets,
                                source_instance_id,
                                controller_id,
                                meta,
                                resume,
                            )
                continue

            if etype == EffectType.SEARCH_BASIC_PLAINS_TO_HAND:
                if controller_id is None:
                    continue
                options = self._basic_plains_choices(controller_id)
                return self._queue_resolution_decision(
                    controller_id,
                    "SEARCH_BASIC_PLAINS",
                    options,
                    {"player_id": controller_id},
                    effects,
                    idx,
                    targets,
                    source_instance_id,
                    controller_id,
                    meta,
                    resume,
                )

            if etype == EffectType.COUNTER_SPELL:
                target = group[0] if group else None
                if target is None or target.get("type") != "STACK":
                    continue
                unless = eff.params.get("unless_pay")
                if unless is None:
                    self._counter_spell(target.get("instance_id"))
                    continue
                controller = self._stack_item_controller(target.get("instance_id"))
                if controller is None:
                    continue
                options = []
                if self._has_mana_cost(unless, self._ps(controller).mana_pool, player_id=controller):
                    options.append({"pay": True})
                options.append({"pay": False})
                return self._queue_resolution_decision(
                    controller,
                    "COUNTER_UNLESS_PAY",
                    options,
                    {"target_id": target.get("instance_id"), "cost": unless},
                    effects,
                    idx,
                    targets,
                    source_instance_id,
                    controller_id,
                    meta,
                    resume,
                )

            if etype == EffectType.CREATURE_DEALS_DAMAGE_TO_CREATURE:
                self._resolve_creature_damage(group, trample_excess=False)
                continue

            if etype == EffectType.RAM_THROUGH:
                self._resolve_creature_damage(group, trample_excess=True)
                continue

            if etype == EffectType.RETURN_TO_HAND:
                for t in group:
                    self._return_to_hand(t)
                continue

            if etype == EffectType.RETURN_FROM_GRAVEYARD_TO_HAND:
                self._return_from_graveyard_to_hand(group, eff, source_instance_id)
                continue

            if etype == EffectType.RETURN_FROM_GRAVEYARD_TO_BATTLEFIELD_TAPPED:
                self._return_from_graveyard_to_battlefield_tapped(group, eff, source_instance_id)
                continue

            if etype == EffectType.RETURN_TWO_DIFFERENT_CONTROLLERS:
                for t in group:
                    self._return_to_hand(t)
                continue

            if etype == EffectType.ATTACH_EQUIPMENT:
                self._attach_equipment(group, source_instance_id)
                continue

            if etype == EffectType.ATTACH_ALL_EQUIPMENT:
                self._attach_all_equipment(group, source_instance_id, controller_id)
                continue

            if etype == EffectType.PUT_COUNTERS:
                targets_list = self._targets_from_group_or_self(group, eff, source_instance_id)
                for t in targets_list:
                    self._apply_put_counters(eff, t, controller_id)
                continue

            if etype in (EffectType.MODIFY_P_T, EffectType.ADD_KEYWORD, EffectType.SET_BASE_P_T, EffectType.ADD_SUBTYPE):
                duration = eff.params.get("duration")
                targets_list = self._targets_from_group_or_self(group, eff, source_instance_id)
                if duration:
                    for t in targets_list:
                        temp_eff = eff
                        if etype == EffectType.MODIFY_P_T:
                            temp_eff = self._materialize_pt_amount(eff, source_instance_id)
                        self._add_temporary_effect(temp_eff, controller_id, source_instance_id, duration, t)
                continue

            if etype == EffectType.TEAM_BUFF:
                duration = eff.params.get("duration", "EOT")
                self._add_temporary_effect(eff, controller_id, source_instance_id, duration, None)
                continue

            if etype == EffectType.GOAD:
                targets_list = self._targets_from_group_or_self(group, eff, source_instance_id)
                for t in targets_list:
                    if t.get("type") != "PERMANENT":
                        continue
                    perm = self.game.zones.battlefield.get(t.get("instance_id"))
                    if perm is None:
                        continue
                    perm.state.goaded_by = controller_id
                    perm.state.goaded_until_turn = self.game.turn.turn_number + 1
                    if eff.params.get("draw_on_attack"):
                        perm.state.draw_on_attack_by = controller_id
                        perm.state.draw_on_attack_until_turn = self.game.turn.turn_number
                continue

            if etype == EffectType.ADDENDUM_SCRY_DRAW:
                if controller_id is None:
                    continue
                if self.game.turn.active_player_id == controller_id and self.game.turn.step in (Step.MAIN1, Step.MAIN2):
                    top_cards = self._top_library(controller_id, int(eff.params.get("scry", 0) or 0))
                    options = self._scry_options(top_cards)
                    if not options:
                        self._draw(controller_id, int(eff.params.get("draw", 0) or 0))
                        continue
                    return self._queue_resolution_decision(
                        controller_id,
                        "SCRY",
                        options,
                        {"player_id": controller_id, "draw": int(eff.params.get("draw", 0) or 0), "top_cards": top_cards},
                        effects,
                        idx,
                        targets,
                        source_instance_id,
                        controller_id,
                        meta,
                        resume,
                    )
                self._draw(controller_id, int(eff.params.get("draw", 0) or 0))
                continue

            if etype == EffectType.LOOK_AT_TOP_N_PUT_ONE_IN_HAND_REST_BOTTOM:
                if controller_id is None:
                    continue
                top_cards = self._top_library(controller_id, int(eff.params.get("n", 0) or 0))
                options = [ci.instance_id for ci in top_cards]
                if not options:
                    continue
                return self._queue_resolution_decision(
                    controller_id,
                    "LOOK_AT_TOP_ONE",
                    options,
                    {"player_id": controller_id, "top_cards": top_cards},
                    effects,
                    idx,
                    targets,
                    source_instance_id,
                    controller_id,
                    meta,
                    resume,
                )

            if etype == EffectType.LOOK_AT_TOP_N_PUT_LAND_TO_BATTLEFIELD_REST_BOTTOM_RANDOM:
                if controller_id is None:
                    continue
                top_cards = self._top_library(controller_id, int(eff.params.get("n", 0) or 0))
                land_choices = []
                for ci in top_cards:
                    card = self.game.card_db.get(ci.card_id)
                    if card and CardType.LAND in card.card_types:
                        land_choices.append(ci.instance_id)
                options = [{"choice": None}] + [{"choice": cid} for cid in land_choices]
                return self._queue_resolution_decision(
                    controller_id,
                    "LOOK_AT_TOP_LAND",
                    options,
                    {"player_id": controller_id, "top_cards": top_cards},
                    effects,
                    idx,
                    targets,
                    source_instance_id,
                    controller_id,
                    meta,
                    resume,
                )

            if etype == EffectType.REVEAL_TOP_N_PUT_ALL_TYPE_TO_HAND_REST_BOTTOM:
                if controller_id is None:
                    continue
                top_cards = self._top_library(controller_id, int(eff.params.get("n", 0) or 0))
                subtype = eff.params.get("subtype")
                ps = self._ps(controller_id)
                keep = []
                rest = []
                for ci in top_cards:
                    card = self.game.card_db.get(ci.card_id)
                    if card and subtype in card.subtypes:
                        keep.append(ci)
                    else:
                        rest.append(ci)
                self._remove_from_library(ps, top_cards)
                for ci in keep:
                    ps.hand.append(ci)
                self._put_on_bottom(ps, rest)
                continue

            if etype == EffectType.FACT_OR_FICTION:
                if controller_id is None:
                    continue
                top_cards = self._top_library(controller_id, int(eff.params.get("n", 0) or 0))
                opponent = self._other_player(controller_id)
                options = self._fact_or_fiction_partitions(top_cards)
                if not options:
                    continue
                return self._queue_resolution_decision(
                    opponent,
                    "FACT_OR_FICTION_SPLIT",
                    options,
                    {"player_id": controller_id, "top_cards": top_cards},
                    effects,
                    idx,
                    targets,
                    source_instance_id,
                    controller_id,
                    meta,
                    resume,
                )

            if etype == EffectType.VOTE:
                if controller_id is None:
                    continue
                vote_type = eff.params.get("type")
                target = group[0] if group else None
                players = [controller_id, self._other_player(controller_id)]
                options = self._vote_options(vote_type)
                return self._queue_resolution_decision(
                    controller_id,
                    "VOTE",
                    options,
                    {"vote_type": vote_type, "players": players, "votes": {}, "target": target, "controller_id": controller_id},
                    effects,
                    idx,
                    targets,
                    source_instance_id,
                    controller_id,
                    meta,
                    resume,
                )

            if etype == EffectType.CAST_FROM_OPPONENT_GRAVEYARD:
                if controller_id is None:
                    continue
                opponent = self._other_player(controller_id)
                options = self._opponent_graveyard_spell_choices(opponent)
                return self._queue_resolution_decision(
                    controller_id,
                    "CAST_FROM_OPPONENT_GRAVEYARD",
                    options,
                    {"player_id": controller_id, "opponent_id": opponent},
                    effects,
                    idx,
                    targets,
                    source_instance_id,
                    controller_id,
                    meta,
                    resume,
                )

            raise RuntimeError(f"Effect type not yet implemented: {etype}")

        return False

    def _queue_resolution_decision(
        self,
        player_id: str,
        kind: str,
        options: Any,
        context: Dict[str, Any],
        effects: List[Any],
        idx: int,
        targets: Any,
        source_instance_id: Optional[str],
        controller_id: Optional[str],
        meta: Optional[Dict[str, Any]],
        resume: Optional[Dict[str, Any]],
    ) -> bool:
        if self.game.pending_decision is not None:
            return True
        ctx = dict(context)
        ctx.update(
            {
                "remaining_effects": effects[idx + 1 :],
                "targets": targets,
                "source_instance_id": source_instance_id,
                "controller_id": controller_id,
                "meta": meta,
                "resume": resume,
            }
        )
        self.game.pending_decision = PendingDecision(
            player_id=player_id,
            kind=kind,
            options=options,
            context=ctx,
        )
        return True

    def _optional_cost_options(self, player_id: str) -> List[Dict[str, Any]]:
        options = [{"pay": False}]
        pool = self._ps(player_id).mana_pool
        colored = dict(pool.colored)
        generic = int(pool.generic)
        red = int(colored.get("RED", 0))
        any_pool = int(colored.get("ANY", 0))
        if red + any_pool < 1:
            return options
        if red > 0:
            colored["RED"] = red - 1
            if colored["RED"] <= 0:
                colored.pop("RED", None)
        else:
            colored["ANY"] = any_pool - 1
            if colored["ANY"] <= 0:
                colored.pop("ANY", None)
        max_x = generic + sum(int(v) for v in colored.values())
        for x in range(0, max_x + 1):
            options.append({"pay": True, "x": x})
        return options

    def _resume_from_pending(self, context: Dict[str, Any]) -> None:
        remaining = context.get("remaining_effects", [])
        resume = context.get("resume")
        if not remaining:
            self._finalize_resolution(resume)
            return
        pending = self._resolve_effects(
            remaining,
            context.get("targets"),
            context.get("source_instance_id"),
            context.get("controller_id"),
            meta=context.get("meta"),
            resume=resume,
        )
        if not pending:
            self._finalize_resolution(resume)

    def _finalize_resolution(self, resume: Optional[Dict[str, Any]]) -> None:
        if not resume:
            return
        kind = resume.get("kind")
        if kind == "SPELL":
            self._finalize_spell(resume.get("stack_item"))

    def _finalize_spell(self, item: Optional[StackItem]) -> None:
        if item is None or item.instance is None:
            return
        if item.meta and item.meta.get("is_copy"):
            return
        if item.meta and item.meta.get("exile_on_resolve"):
            self.game.zones.exile[item.instance.instance_id] = item.instance
            return
        owner = item.instance.owner_id
        if owner in self.game.players:
            self.game.players[owner].graveyard.append(item.instance)

    def _effects_for_card(self, card: Any, meta: Optional[Dict[str, Any]]) -> List[Any]:
        effects = card.rules.effects
        if len(effects) == 1 and effects[0].type == EffectType.MODAL:
            modal = effects[0]
            modes = modal.params.get("modes", [])
            indices = (meta or {}).get("mode_indices") or [0]
            selected: List[Any] = []
            for idx in indices:
                if isinstance(idx, int) and 0 <= idx < len(modes):
                    selected.extend(modes[idx])
            return selected
        return effects

    def _cast_from_graveyard_instance(
        self,
        controller_id: str,
        card_instance: CardInstance,
        card: Any,
        targets: Any,
    ) -> None:
        meta = {"exile_on_resolve": True}
        if getattr(card.mana_cost, "x", 0):
            meta["x"] = 0
        self.game.zones.stack.append(
            StackItem(
                kind=StackItemKind.SPELL,
                controller_id=controller_id,
                instance=card_instance,
                targets=targets,
                meta=meta,
            )
        )
        self._handle_cast_spell(controller_id, card)
        self._notify_becomes_target(targets, controller_id)

    def _copy_spell(self, target_instance_id: Optional[str], controller_id: Optional[str], targets: Any = None) -> None:
        if target_instance_id is None or controller_id is None:
            return
        item = next(
            (s for s in self.game.zones.stack if s.instance is not None and s.instance.instance_id == target_instance_id),
            None,
        )
        if item is None or item.instance is None:
            return
        meta = dict(item.meta or {})
        meta["is_copy"] = True
        copy_instance = CardInstance(
            instance_id=str(uuid.uuid4()),
            card_id=item.instance.card_id,
            owner_id=controller_id,
            is_token=True,
        )
        self.game.zones.stack.append(
            StackItem(
                kind=StackItemKind.SPELL,
                controller_id=controller_id,
                instance=copy_instance,
                targets=targets if targets is not None else item.targets,
                meta=meta,
            )
        )

    def _targets_from_group_or_self(
        self,
        group: List[Dict[str, Any]],
        eff: Any,
        source_instance_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        if group:
            return group
        target = eff.params.get("target")
        if isinstance(target, dict):
            return [target]
        if target in ("SELF", "TRIGGER_SOURCE") and source_instance_id:
            return [{"type": "PERMANENT", "instance_id": source_instance_id}]
        return []

    def _apply_put_counters(self, eff: Any, target: Dict[str, Any], controller_id: Optional[str] = None) -> None:
        if target.get("type") != "PERMANENT":
            return
        perm = self.game.zones.battlefield.get(target.get("instance_id"))
        if perm is None:
            return
        condition = eff.params.get("condition")
        if isinstance(condition, dict) and "color" in condition:
            color = condition.get("color")
            card = self.game.card_db.get(perm.instance.card_id)
            if card is None or color not in {c.value for c in card.colors}:
                return
        counter_type = eff.params.get("counter")
        if counter_type not in perm.state.counters:
            perm.state.counters[counter_type] = 0
        amount = eff.params.get("amount", 0)
        if isinstance(amount, int):
            perm.state.counters[counter_type] += amount
            return
        if amount == "COUNT_ELVES" and controller_id is not None:
            count = 0
            for p in self.game.zones.battlefield.values():
                if p.controller_id != controller_id:
                    continue
                card = self.game.card_db.get(p.instance.card_id)
                if card and "Elf" in card.subtypes:
                    count += 1
            perm.state.counters[counter_type] += count

    def _add_temporary_effect(
        self,
        eff: Any,
        controller_id: Optional[str],
        source_instance_id: Optional[str],
        duration: str,
        target: Optional[Dict[str, Any]],
    ) -> None:
        expires_turn, expires_step = self._duration_to_expiry(duration, controller_id)
        params = dict(eff.params)
        if target is not None:
            params["target"] = target
        temp_effect = Effect(type=eff.type, params=params)
        self.game.temporary_effects.append(
            TemporaryEffect(
                effect=temp_effect,
                source_instance_id=source_instance_id,
                controller_id=controller_id,
                expires_turn=expires_turn,
                expires_step=expires_step,
            )
        )

    def _duration_to_expiry(self, duration: str, controller_id: Optional[str]) -> tuple[int, Optional[Step]]:
        if duration == "EOT":
            return self.game.turn.turn_number, Step.END
        if duration == "UNTIL_YOUR_NEXT_TURN":
            return self.game.turn.turn_number + 1, Step.UNTAP
        return self.game.turn.turn_number, Step.END


    def _apply_deal_damage(
        self,
        eff: Any,
        target: Dict[str, Any],
        source_instance_id: Optional[str],
        source_controller_id: Optional[str],
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        amount = eff.params.get("amount", 0)
        if isinstance(amount, str):
            if amount == "X":
                amount = int((meta or {}).get("x", 0) or 0)
            elif amount == "COUNT_DRAGONS":
                amount = self._count_subtype_on_battlefield("Dragon", controller_id=source_controller_id)
            elif amount == "COUNT_ELVES":
                amount = self._count_subtype_on_battlefield("Elf", controller_id=None)
            elif amount == "COUNT_OTHER_ELVES":
                amount = self._count_subtype_on_battlefield("Elf", controller_id=None, exclude_id=source_instance_id)
            else:
                amount = 0
        if not isinstance(amount, int):
            amount = int(amount or 0)
        if amount <= 0:
            return
        derived = self._derived_battlefield_state()
        source_keywords = set()
        if source_instance_id and source_instance_id in derived:
            source_keywords = derived[source_instance_id]["keywords"]
        source_controller = source_controller_id
        if source_controller is None and source_instance_id:
            perm = self.game.zones.battlefield.get(source_instance_id)
            if perm is not None:
                source_controller = perm.controller_id

        if target.get("type") == "PLAYER":
            pid = target.get("player_id")
            if pid in self.game.players:
                self.game.players[pid].life -= amount
                self.game.damage_dealt_to_players[pid] = self.game.damage_dealt_to_players.get(pid, 0) + amount
            if source_controller and Keyword.LIFELINK in source_keywords:
                self.game.players[source_controller].life += amount
            if pid in self.game.players:
                self._handle_you_lose_life(pid, amount)
            return

        if target.get("type") == "PERMANENT":
            perm = self.game.zones.battlefield.get(target.get("instance_id"))
            if perm is None:
                return
            perm.state.damage_marked += amount
            if source_controller and Keyword.LIFELINK in source_keywords:
                self.game.players[source_controller].life += amount
            self._handle_dealt_damage(perm.instance.instance_id, amount)
            if Keyword.DEATHTOUCH in source_keywords:
                self._apply_state_based_actions({perm.instance.instance_id})
            else:
                self._apply_state_based_actions()

    def _apply_draw_cards(self, eff: Any, target: Dict[str, Any]) -> None:
        amount = int(eff.params.get("amount", 0))
        if amount <= 0:
            return
        if target.get("type") != "PLAYER":
            return
        pid = target.get("player_id")
        if pid in self.game.players:
            self._draw(pid, amount)

    def _apply_lose_life(self, eff: Any, target: Dict[str, Any]) -> None:
        amount = eff.params.get("amount", 0)
        if isinstance(amount, str):
            amount = int(amount or 0)
        amount = int(amount)
        if amount <= 0:
            return
        if target.get("type") != "PLAYER":
            return
        pid = target.get("player_id")
        if pid in self.game.players:
            self.game.players[pid].life -= amount
            self._handle_you_lose_life(pid, amount)

    def _apply_add_mana(self, eff: Any, controller_id: Optional[str]) -> None:
        if controller_id is None:
            return
        mana = eff.params.get("mana")
        if mana is None:
            return
        ps = self._ps(controller_id)

        if isinstance(mana, dict):
            for color, amount in mana.items():
                ps.mana_pool.colored[color] = ps.mana_pool.colored.get(color, 0) + int(amount)
            return

        if isinstance(mana, str):
            if mana.upper() == "ANY":
                ps.mana_pool.colored["ANY"] = ps.mana_pool.colored.get("ANY", 0) + 1
                return
            for ch in mana:
                color = {"W": "WHITE", "U": "BLUE", "B": "BLACK", "R": "RED", "G": "GREEN"}.get(ch)
                if color:
                    ps.mana_pool.colored[color] = ps.mana_pool.colored.get(color, 0) + 1

    def _apply_add_mana_per_elf(self, eff: Any, controller_id: Optional[str]) -> None:
        if controller_id is None:
            return
        color = eff.params.get("color", "G")
        count = 0
        for perm in self.game.zones.battlefield.values():
            if perm.controller_id != controller_id:
                continue
            card = self.game.card_db.get(perm.instance.card_id)
            if card and "Elf" in card.subtypes:
                count += 1
        if count > 0:
            self._apply_add_mana(Effect(type=EffectType.ADD_MANA, params={"mana": color * count}), controller_id)

    def _apply_add_mana_per_tapped_lands(self, eff: Any, controller_id: Optional[str]) -> None:
        if controller_id is None:
            return
        color = eff.params.get("color", "R")
        count = 0
        for perm in self.game.zones.battlefield.values():
            if perm.controller_id == controller_id:
                continue
            card = self.game.card_db.get(perm.instance.card_id)
            if card and CardType.LAND in card.card_types and perm.state.tapped:
                count += 1
        if count > 0:
            self._apply_add_mana(Effect(type=EffectType.ADD_MANA, params={"mana": color * count}), controller_id)

    def _apply_create_token(
        self,
        eff: Any,
        controller_id: Optional[str],
        source_instance_id: Optional[str],
    ) -> None:
        if controller_id is None:
            return
        token_id = eff.params.get("token")
        if not token_id:
            return

        condition = eff.params.get("condition")
        if condition == "CONTROL_EQUIPMENT":
            if not any(
                self.game.card_db.get(p.instance.card_id, None) and self.game.card_db[p.instance.card_id].equipment_stats is not None
                for p in self.game.zones.battlefield.values()
                if p.controller_id == controller_id
            ):
                return
        if condition == "CONTROL_ANOTHER_ELF":
            count = 0
            for p in self.game.zones.battlefield.values():
                if p.controller_id != controller_id:
                    continue
                card = self.game.card_db.get(p.instance.card_id)
                if card and "Elf" in card.subtypes:
                    count += 1
            if count <= 1:
                return

        count = eff.params.get("count", 1)
        if count == "PER_ELF_YOU_CONTROL":
            count = 0
            for p in self.game.zones.battlefield.values():
                if p.controller_id != controller_id:
                    continue
                card = self.game.card_db.get(p.instance.card_id)
                if card and "Elf" in card.subtypes:
                    count += 1

        count = int(count) if isinstance(count, int) or isinstance(count, str) else 1
        created_ids = []
        for _ in range(count):
            instance_id = str(uuid.uuid4())
            token_ci = CardInstance(
                instance_id=instance_id,
                card_id=token_id,
                owner_id=controller_id,
                is_token=True,
            )
            perm = Permanent(
                instance=token_ci,
                controller_id=controller_id,
            )
            self.game.zones.battlefield[instance_id] = perm
            created_ids.append(instance_id)
            self._handle_etb(perm)
            self._handle_creature_enters(perm)

        if eff.params.get("attach_equipment") and source_instance_id and created_ids:
            source_perm = self.game.zones.battlefield.get(source_instance_id)
            if source_perm is not None:
                source_perm.state.attached_to = created_ids[0]

    def _apply_destroy_creature(self, eff: Any, target: Dict[str, Any]) -> None:
        if target.get("type") != "PERMANENT":
            return
        perm = self.game.zones.battlefield.get(target.get("instance_id"))
        if perm is None:
            return
        if not self._is_creature(perm):
            return
        derived = self._derived_battlefield_state()
        d = derived.get(perm.instance.instance_id)
        min_toughness = eff.params.get("min_toughness")
        if d and min_toughness is not None and d.get("toughness") is not None:
            if int(d["toughness"]) < int(min_toughness):
                return
        if d and Keyword.INDESTRUCTIBLE in d["keywords"]:
            return
        self._destroy_permanent(perm)

    def _apply_destroy_artifact(self, target: Dict[str, Any]) -> None:
        if target.get("type") != "PERMANENT":
            return
        perm = self.game.zones.battlefield.get(target.get("instance_id"))
        if perm is None:
            return
        card = self.game.card_db.get(perm.instance.card_id)
        if card is None or CardType.ARTIFACT not in card.card_types:
            return
        derived = self._derived_battlefield_state()
        d = derived.get(perm.instance.instance_id)
        if d and Keyword.INDESTRUCTIBLE in d["keywords"]:
            return
        self._destroy_permanent(perm)

    def _destroy_permanent(self, perm: Permanent) -> None:
        self._handle_dies(perm)
        self._return_exiled_for_source(perm.instance.instance_id)
        self.game.zones.battlefield.pop(perm.instance.instance_id, None)
        if perm.instance.is_token:
            return
        owner = perm.instance.owner_id
        if owner in self.game.players:
            self.game.players[owner].graveyard.append(perm.instance)

    def _apply_destroy_flying_creature(self, target: Dict[str, Any]) -> None:
        if target.get("type") != "PERMANENT":
            return
        perm = self.game.zones.battlefield.get(target.get("instance_id"))
        if perm is None or not self._is_creature(perm):
            return
        derived = self._derived_battlefield_state()
        d = derived.get(perm.instance.instance_id)
        if d is None or Keyword.FLYING not in d.get("keywords", set()):
            return
        if Keyword.INDESTRUCTIBLE in d.get("keywords", set()):
            return
        self._destroy_permanent(perm)

    def _apply_destroy_permanent_target(self, target: Dict[str, Any]) -> None:
        if target.get("type") != "PERMANENT":
            return
        perm = self.game.zones.battlefield.get(target.get("instance_id"))
        if perm is None:
            return
        derived = self._derived_battlefield_state()
        d = derived.get(perm.instance.instance_id)
        if d and Keyword.INDESTRUCTIBLE in d.get("keywords", set()):
            return
        self._destroy_permanent(perm)

    def _apply_exile_creature(self, eff: Any, target: Dict[str, Any]) -> None:
        if target.get("type") != "PERMANENT":
            return
        perm = self.game.zones.battlefield.get(target.get("instance_id"))
        if perm is None or not self._is_creature(perm):
            return
        derived = self._derived_battlefield_state()
        d = derived.get(perm.instance.instance_id)
        if eff.params.get("gain_life_equal_power") and perm.controller_id in self.game.players:
            power = 0
            if d and d.get("power") is not None:
                power = int(d["power"])
            self.game.players[perm.controller_id].life += power
        self._return_exiled_for_source(perm.instance.instance_id)
        self.game.zones.battlefield.pop(perm.instance.instance_id, None)
        if perm.instance.is_token:
            return
        self.game.zones.exile[perm.instance.instance_id] = perm.instance

    def _apply_exile_until(self, eff: Any, target: Dict[str, Any], source_instance_id: Optional[str]) -> None:
        if target.get("type") != "PERMANENT":
            return
        perm = self.game.zones.battlefield.get(target.get("instance_id"))
        if perm is None:
            return
        self._return_exiled_for_source(perm.instance.instance_id)
        self.game.zones.battlefield.pop(perm.instance.instance_id, None)
        if perm.instance.is_token:
            return
        self.game.zones.exile[perm.instance.instance_id] = perm.instance
        if source_instance_id:
            self.game.exile_links[perm.instance.instance_id] = source_instance_id

    def _basic_land_choices(self, player_id: str) -> List[Dict[str, Any]]:
        ps = self._ps(player_id)
        options = [{"choice": None}]
        for ci in ps.library:
            card = self.game.card_db.get(ci.card_id)
            if card and CardType.LAND in card.card_types and ci.card_id.startswith("basic_"):
                options.append({"choice": ci.instance_id})
        return options

    def _basic_plains_choices(self, player_id: str) -> List[Dict[str, Any]]:
        ps = self._ps(player_id)
        options = [{"choice": None}]
        for ci in ps.library:
            card = self.game.card_db.get(ci.card_id)
            if card and CardType.LAND in card.card_types:
                if LandType.PLAINS in (card.land_stats.land_types if card.land_stats else set()):
                    options.append({"choice": ci.instance_id})
        return options

    def _stack_item_controller(self, instance_id: Optional[str]) -> Optional[str]:
        if instance_id is None:
            return None
        for item in self.game.zones.stack:
            if item.instance is not None and item.instance.instance_id == instance_id:
                return item.controller_id
        return None

    def _counter_spell(self, instance_id: Optional[str]) -> None:
        if instance_id is None:
            return
        for i, item in enumerate(self.game.zones.stack):
            if item.instance is None or item.instance.instance_id != instance_id:
                continue
            self.game.zones.stack.pop(i)
            if item.meta and item.meta.get("is_copy"):
                return
            if item.meta and item.meta.get("exile_on_resolve"):
                self.game.zones.exile[item.instance.instance_id] = item.instance
                return
            owner = item.instance.owner_id
            if owner in self.game.players:
                self.game.players[owner].graveyard.append(item.instance)
            return

    def _return_to_hand(self, target: Dict[str, Any]) -> None:
        if target.get("type") != "PERMANENT":
            return
        perm = self.game.zones.battlefield.get(target.get("instance_id"))
        if perm is None:
            return
        self._return_exiled_for_source(perm.instance.instance_id)
        self.game.zones.battlefield.pop(perm.instance.instance_id, None)
        if perm.instance.is_token:
            return
        owner = perm.instance.owner_id
        if owner in self.game.players:
            self.game.players[owner].hand.append(perm.instance)

    def _return_from_graveyard_to_hand(
        self,
        group: List[Dict[str, Any]],
        eff: Any,
        source_instance_id: Optional[str],
    ) -> None:
        if group:
            for target in group:
                if target.get("type") != "CARD":
                    continue
                pid = target.get("player_id")
                if pid not in self.game.players:
                    continue
                ps = self._ps(pid)
                for i, ci in enumerate(ps.graveyard):
                    if ci.instance_id == target.get("instance_id"):
                        ps.graveyard.pop(i)
                        ps.hand.append(ci)
                        break
            return
        target = eff.params.get("target")
        if target == "SELF" and source_instance_id:
            for ps in self.game.players.values():
                for i, ci in enumerate(ps.graveyard):
                    if ci.instance_id == source_instance_id:
                        ps.graveyard.pop(i)
                        ps.hand.append(ci)
                        return

    def _return_from_graveyard_to_battlefield_tapped(
        self,
        group: List[Dict[str, Any]],
        eff: Any,
        source_instance_id: Optional[str],
    ) -> None:
        def move_card(ci: CardInstance) -> None:
            perm = Permanent(instance=ci, controller_id=ci.owner_id)
            perm.state.tapped = True
            self.game.zones.battlefield[ci.instance_id] = perm
            self._handle_etb(perm)
            self._handle_creature_enters(perm)

        if group:
            for target in group:
                if target.get("type") != "CARD":
                    continue
                pid = target.get("player_id")
                if pid not in self.game.players:
                    continue
                ps = self._ps(pid)
                for i, ci in enumerate(ps.graveyard):
                    if ci.instance_id == target.get("instance_id"):
                        ps.graveyard.pop(i)
                        move_card(ci)
                        break
            return
        target = eff.params.get("target")
        if target == "SELF" and source_instance_id:
            for ps in self.game.players.values():
                for i, ci in enumerate(ps.graveyard):
                    if ci.instance_id == source_instance_id:
                        ps.graveyard.pop(i)
                        move_card(ci)
                        return

    def _attach_equipment(self, group: List[Dict[str, Any]], source_instance_id: Optional[str]) -> None:
        equipment_id = None
        target_id = None
        for t in group:
            role = t.get("role")
            if role == "equipment":
                equipment_id = t.get("instance_id")
            elif role == "target":
                target_id = t.get("instance_id")
        if target_id is None and group:
            target_id = group[0].get("instance_id")
        if equipment_id is None:
            equipment_id = source_instance_id
        if equipment_id is None or target_id is None:
            return
        equipment = self.game.zones.battlefield.get(equipment_id)
        target = self.game.zones.battlefield.get(target_id)
        if equipment is None or target is None:
            return
        if not self._is_creature(target):
            return
        equipment.state.attached_to = target_id

    def _attach_all_equipment(
        self,
        group: List[Dict[str, Any]],
        source_instance_id: Optional[str],
        controller_id: Optional[str],
    ) -> None:
        target_id = None
        if group:
            target_id = group[0].get("instance_id")
        if target_id is None:
            target_id = source_instance_id
        if target_id is None or controller_id is None:
            return
        target = self.game.zones.battlefield.get(target_id)
        if target is None or not self._is_creature(target):
            return
        for perm in self.game.zones.battlefield.values():
            if perm.controller_id != controller_id:
                continue
            card = self.game.card_db.get(perm.instance.card_id)
            if card is None:
                continue
            if card.equipment_stats is not None or card.aura_stats is not None:
                perm.state.attached_to = target_id

    def _materialize_pt_amount(self, eff: Any, source_instance_id: Optional[str]) -> Any:
        amount = eff.params.get("amount")
        if isinstance(amount, dict):
            return eff
        count = 0
        if amount == "COUNT_ELVES":
            count = self._count_subtype_on_battlefield("Elf")
        if amount == "COUNT_OTHER_ELVES":
            count = self._count_subtype_on_battlefield("Elf", exclude_id=source_instance_id)
        params = dict(eff.params)
        params["amount"] = {"power": int(count), "toughness": int(count)}
        return Effect(type=eff.type, params=params)

    def _resolve_creature_damage(self, group: List[Dict[str, Any]], trample_excess: bool) -> None:
        source = next((t for t in group if t.get("role") == "source"), None)
        target = next((t for t in group if t.get("role") == "target"), None)
        if source is None or target is None:
            return
        source_perm = self.game.zones.battlefield.get(source.get("instance_id"))
        target_perm = self.game.zones.battlefield.get(target.get("instance_id"))
        if source_perm is None or target_perm is None:
            return
        derived = self._derived_battlefield_state()
        d_source = derived.get(source_perm.instance.instance_id)
        d_target = derived.get(target_perm.instance.instance_id)
        if d_source is None or d_source.get("power") is None:
            return
        power = int(d_source["power"])
        self._apply_deal_damage(
            Effect(EffectType.DEAL_DAMAGE, {"amount": power}),
            {"type": "PERMANENT", "instance_id": target_perm.instance.instance_id},
            source_perm.instance.instance_id,
            source_perm.controller_id,
            meta=None,
        )
        if trample_excess and Keyword.TRAMPLE in d_source.get("keywords", set()):
            if d_target and d_target.get("toughness") is not None:
                lethal = int(d_target["toughness"]) - int(target_perm.state.damage_marked)
                if Keyword.DEATHTOUCH in d_source.get("keywords", set()):
                    lethal = 1
                excess = max(0, power - max(0, lethal))
                if excess > 0:
                    self._apply_deal_damage(
                        Effect(EffectType.DEAL_DAMAGE, {"amount": excess}),
                        {"type": "PLAYER", "player_id": target_perm.controller_id},
                        source_perm.instance.instance_id,
                        source_perm.controller_id,
                        meta=None,
                    )

    def _top_library(self, player_id: str, n: int) -> List[CardInstance]:
        ps = self._ps(player_id)
        if n <= 0:
            return []
        return list(ps.library[-n:])

    def _remove_from_library(self, ps: PlayerState, cards: List[CardInstance]) -> None:
        ids = {ci.instance_id for ci in cards}
        ps.library = [ci for ci in ps.library if ci.instance_id not in ids]

    def _put_on_bottom(self, ps: PlayerState, cards: List[CardInstance]) -> None:
        ps.library = list(cards) + ps.library

    def _scry_options(self, top_cards: List[CardInstance]) -> List[Dict[str, Any]]:
        ids = [ci.instance_id for ci in top_cards]
        if not ids:
            return []
        options: List[Dict[str, Any]] = []
        for perm in itertools.permutations(ids, len(ids)):
            for split in range(len(ids) + 1):
                top = list(perm[: len(ids) - split])
                bottom = list(perm[len(ids) - split :])
                options.append({"top": top, "bottom": bottom})
        return options

    def _fact_or_fiction_partitions(self, top_cards: List[CardInstance]) -> List[Dict[str, Any]]:
        ids = [ci.instance_id for ci in top_cards]
        if not ids:
            return []
        options: List[Dict[str, Any]] = []
        if len(ids) == 1:
            return [{"pile_a": ids, "pile_b": []}]
        for mask in range(1 << (len(ids) - 1)):
            pile_a = [ids[0]]
            pile_b = []
            for i, cid in enumerate(ids[1:], start=1):
                if mask & (1 << (i - 1)):
                    pile_a.append(cid)
                else:
                    pile_b.append(cid)
            options.append({"pile_a": pile_a, "pile_b": pile_b})
        return options

    def _vote_options(self, vote_type: str) -> List[str]:
        if vote_type == "PLEA_FOR_POWER":
            return ["time", "knowledge"]
        if vote_type == "SPLIT_DECISION":
            return ["denial", "duplication"]
        return []

    def _opponent_graveyard_spell_choices(self, opponent_id: str) -> List[Dict[str, Any]]:
        options = [{"choice": None}]
        ps = self._ps(opponent_id)
        for ci in ps.graveyard:
            card = self.game.card_db.get(ci.card_id)
            if card is None:
                continue
            if CardType.INSTANT in card.card_types or CardType.SORCERY in card.card_types:
                options.append({"choice": ci.instance_id})
        return options

    def _resolve_combat_damage(self) -> None:
        t = self.game.turn
        defending_player = self._other_player(t.active_player_id)
        derived = self._derived_battlefield_state()

        def deals_in_first_strike_step(d: Dict[str, Any]) -> bool:
            return Keyword.FIRST_STRIKE in d["keywords"] or Keyword.DOUBLE_STRIKE in d["keywords"]

        def deals_in_normal_step(d: Dict[str, Any]) -> bool:
            if Keyword.DOUBLE_STRIKE in d["keywords"]:
                return True
            return Keyword.FIRST_STRIKE not in d["keywords"]

        def combat_damage_step(first_strike: bool) -> None:
            damage_events: List[Dict[str, Any]] = []
            deathtouch_marked: set[str] = set()

            # Attacker damage
            for attacker_id in t.attackers:
                attacker = self.game.zones.battlefield.get(attacker_id)
                if attacker is None:
                    continue
                d_att = derived.get(attacker_id)
                if d_att is None or d_att["power"] is None:
                    continue
                if first_strike and not deals_in_first_strike_step(d_att):
                    continue
                if not first_strike and not deals_in_normal_step(d_att):
                    continue

                blockers = [bid for bid in t.blockers.get(attacker_id, []) if bid in self.game.zones.battlefield]
                assign_unblocked = bool(d_att.get("assign_damage_as_unblocked", False))
                damage_to_blockers = [] if assign_unblocked else blockers

                if not damage_to_blockers:
                    if not d_att.get("prevent_combat_damage", False):
                        damage_events.append(
                            {
                                "source_id": attacker_id,
                                "source_controller": attacker.controller_id,
                                "source_keywords": d_att["keywords"],
                                "target_type": "PLAYER",
                                "target_id": defending_player,
                                "amount": int(d_att["power"]),
                            }
                        )
                else:
                    remaining = int(d_att["power"])
                    for blocker_id in damage_to_blockers:
                        d_blk = derived.get(blocker_id)
                        if d_blk is None or d_blk["toughness"] is None:
                            continue
                        if d_blk.get("prevent_combat_damage", False) or d_att.get("prevent_combat_damage", False):
                            continue
                        lethal = 1 if Keyword.DEATHTOUCH in d_att["keywords"] else int(d_blk["toughness"])
                        assign = min(remaining, lethal)
                        if assign > 0:
                            damage_events.append(
                                {
                                    "source_id": attacker_id,
                                    "source_controller": attacker.controller_id,
                                    "source_keywords": d_att["keywords"],
                                    "target_type": "CREATURE",
                                    "target_id": blocker_id,
                                    "amount": assign,
                                }
                            )
                            if Keyword.DEATHTOUCH in d_att["keywords"]:
                                deathtouch_marked.add(blocker_id)
                        remaining -= assign
                        if remaining <= 0:
                            break
                    if remaining > 0 and Keyword.TRAMPLE in d_att["keywords"]:
                        damage_events.append(
                            {
                                "source_id": attacker_id,
                                "source_controller": attacker.controller_id,
                                "source_keywords": d_att["keywords"],
                                "target_type": "PLAYER",
                                "target_id": defending_player,
                                "amount": remaining,
                            }
                        )

            # Blocker damage
            for attacker_id, blocker_ids in t.blockers.items():
                attacker = self.game.zones.battlefield.get(attacker_id)
                if attacker is None:
                    continue
                d_att = derived.get(attacker_id)
                if d_att is None or d_att["toughness"] is None:
                    continue
                for blocker_id in blocker_ids:
                    blocker = self.game.zones.battlefield.get(blocker_id)
                    if blocker is None:
                        continue
                    d_blk = derived.get(blocker_id)
                    if d_blk is None or d_blk["power"] is None:
                        continue
                    if first_strike and not deals_in_first_strike_step(d_blk):
                        continue
                    if not first_strike and not deals_in_normal_step(d_blk):
                        continue
                    if d_blk.get("prevent_combat_damage", False) or d_att.get("prevent_combat_damage", False):
                        continue
                    damage_events.append(
                        {
                            "source_id": blocker_id,
                            "source_controller": blocker.controller_id,
                            "source_keywords": d_blk["keywords"],
                            "target_type": "CREATURE",
                            "target_id": attacker_id,
                            "amount": int(d_blk["power"]),
                        }
                    )
                    if Keyword.DEATHTOUCH in d_blk["keywords"]:
                        deathtouch_marked.add(attacker_id)

            # Apply damage
            for event in damage_events:
                amount = int(event["amount"])
                if amount <= 0:
                    continue
                if event["target_type"] == "PLAYER":
                    pid = event["target_id"]
                    if pid in self.game.players:
                        self.game.players[pid].life -= amount
                        self.game.damage_dealt_to_players[pid] = self.game.damage_dealt_to_players.get(pid, 0) + amount
                    if Keyword.LIFELINK in event["source_keywords"]:
                        self.game.players[event["source_controller"]].life += amount
                    if pid in self.game.players:
                        self._handle_combat_damage_to_player(event["source_id"], pid)
                        self._handle_you_lose_life(pid, amount)
                elif event["target_type"] == "CREATURE":
                    perm = self.game.zones.battlefield.get(event["target_id"])
                    if perm is not None:
                        perm.state.damage_marked += amount
                    if Keyword.LIFELINK in event["source_keywords"]:
                        self.game.players[event["source_controller"]].life += amount
                    if perm is not None:
                        self._handle_dealt_damage(perm.instance.instance_id, amount)

            self._apply_state_based_actions(deathtouch_marked)

        combat_damage_step(first_strike=True)
        combat_damage_step(first_strike=False)

    def _apply_state_based_actions(self, deathtouch_marked: Optional[set[str]] = None) -> None:
        derived = self._derived_battlefield_state()
        to_destroy: List[str] = []

        for perm_id, perm in self.game.zones.battlefield.items():
            d = derived.get(perm_id)
            if d is None or d["toughness"] is None:
                continue
            if d["toughness"] <= 0 and Keyword.INDESTRUCTIBLE not in d["keywords"]:
                to_destroy.append(perm_id)
                continue
            if perm.state.damage_marked >= d["toughness"]:
                if Keyword.INDESTRUCTIBLE not in d["keywords"]:
                    to_destroy.append(perm_id)
                    continue
            if deathtouch_marked and perm_id in deathtouch_marked:
                if Keyword.INDESTRUCTIBLE not in d["keywords"]:
                    to_destroy.append(perm_id)

        for perm_id in to_destroy:
            perm = self.game.zones.battlefield.get(perm_id)
            if perm is not None:
                self._destroy_permanent(perm)

        # Aura attachment checks
        for perm in list(self.game.zones.battlefield.values()):
            card = self.game.card_db.get(perm.instance.card_id)
            if card is None or card.aura_stats is None:
                continue
            if perm.state.attached_to is None:
                self._destroy_permanent(perm)
                continue
            attached = self.game.zones.battlefield.get(perm.state.attached_to)
            if attached is None or not self._is_creature(attached):
                self._destroy_permanent(perm)

        # Equipment detaches if illegal
        for perm in list(self.game.zones.battlefield.values()):
            card = self.game.card_db.get(perm.instance.card_id)
            if card is None or card.equipment_stats is None:
                continue
            if perm.state.attached_to is None:
                continue
            attached = self.game.zones.battlefield.get(perm.state.attached_to)
            if attached is None or not self._is_creature(attached):
                perm.state.attached_to = None

    def _is_creature_lethal(self, perm: Permanent) -> bool:
        card = self.game.card_db.get(perm.instance.card_id)
        if card is None or card.creature_stats is None:
            return False
        toughness = card.creature_stats.base_toughness
        return perm.state.damage_marked >= toughness

    def _is_creature(self, perm: Permanent) -> bool:
        card = self.game.card_db.get(perm.instance.card_id)
        if card is None:
            return False
        return CardType.CREATURE in card.card_types

    def _normalize_target(self, targets: Any) -> Optional[Dict[str, Any]]:
        if isinstance(targets, dict):
            return targets
        if isinstance(targets, list) and targets:
            if isinstance(targets[0], dict):
                return targets[0]
        return None

    def _targets_valid(self, card: Any, targets: Any, actor_id: Optional[str] = None) -> bool:
        required = self._required_target_specs(card)
        if not required:
            return True

        flat = self._flatten_targets(targets)
        if not flat:
            return False

        derived = self._derived_battlefield_state()
        for target in flat:
            if target.get("type") == "PLAYER":
                pid = target.get("player_id")
                if pid not in self.game.players:
                    return False
                continue
            if target.get("type") == "PERMANENT":
                perm = self.game.zones.battlefield.get(target.get("instance_id"))
                if perm is None:
                    return False
                if actor_id and perm.controller_id != actor_id:
                    d = derived.get(perm.instance.instance_id)
                    if d and Keyword.HEXPROOF in d["keywords"]:
                        return False
                continue
            if target.get("type") == "STACK":
                if target.get("instance_id") not in {s.instance.instance_id for s in self.game.zones.stack if s.instance is not None}:
                    return False
                continue
            if target.get("type") == "CARD":
                all_grave = {ci.instance_id for ps in self.game.players.values() for ci in ps.graveyard}
                if target.get("instance_id") not in all_grave:
                    return False
                continue

        return True

    def _perm_matches_any_spec(self, perm: Permanent, specs: List[Any]) -> bool:
        for spec in specs:
            if spec.zone != Zone.BATTLEFIELD:
                continue
            if spec.selector == Selector.ANY_CREATURE:
                return self._is_creature(perm)
            if spec.selector == Selector.NON_BLACK_CREATURE:
                if not self._is_creature(perm):
                    continue
                card = self.game.card_db.get(perm.instance.card_id)
                if card is None:
                    continue
                if "BLACK" not in {c.value for c in card.colors}:
                    return True
        return False

    def _target_matches_spec(self, target: Dict[str, Any], spec: Any) -> bool:
        if spec.zone == Zone.PLAYER and spec.selector == Selector.ANY_PLAYER:
            return target.get("type") == "PLAYER" and target.get("player_id") in self.game.players

        if spec.zone == Zone.BATTLEFIELD and target.get("type") == "PERMANENT":
            perm = self.game.zones.battlefield.get(target.get("instance_id"))
            if perm is None:
                return False
            if spec.selector == Selector.ANY_CREATURE:
                return self._is_creature(perm)
            if spec.selector == Selector.NON_BLACK_CREATURE:
                if not self._is_creature(perm):
                    return False
                card = self.game.card_db.get(perm.instance.card_id)
                if card is None:
                    return False
                return "BLACK" not in {c.value for c in card.colors}

        return False

    def _resolve_top_of_stack(self) -> str:
        item = self.game.zones.stack.pop()
        if item.kind == StackItemKind.ABILITY:
            if item.effects:
                pending = self._resolve_effects(
                    item.effects,
                    item.targets,
                    source_instance_id=item.source_instance_id,
                    controller_id=item.controller_id,
                    meta=item.meta,
                    resume={"kind": "ABILITY", "stack_item": item},
                )
                if pending:
                    return "PENDING"
            self._log(f"{item.controller_id}'s ability resolves.")
            return "ABILITY"

        if item.instance is None:
            raise RuntimeError("Stack SPELL missing instance")
        card = self.game.card_db.get(item.instance.card_id)
        if card is None:
            raise RuntimeError("Card data not found for stack item")

        # Permanents: creature/artifact/enchantment enter battlefield
        if CardType.CREATURE in card.card_types or CardType.ARTIFACT in card.card_types or CardType.ENCHANTMENT in card.card_types:
            perm = Permanent(instance=item.instance, controller_id=item.controller_id)
            if card.aura_stats is not None:
                target = self._normalize_target(item.targets)
                if target is None or target.get("type") != "PERMANENT":
                    # fizzles
                    owner_id = item.instance.owner_id
                    if owner_id in self.game.players:
                        self.game.players[owner_id].graveyard.append(item.instance)
                    self._log(f"{item.controller_id}'s {item.instance.card_id} fizzles (no target).")
                    return item.instance.card_id
                perm.state.attached_to = target.get("instance_id")
            self.game.zones.battlefield[item.instance.instance_id] = perm
            self._handle_etb(perm)
            self._handle_creature_enters(perm)
            self._log(f"{item.controller_id}'s {item.instance.card_id} resolves.")
            return item.instance.card_id

        # Instant/Sorcery: resolve effects then move to graveyard
        if card.rules and card.rules.effects:
            effects = self._effects_for_card(card, item.meta)
            pending = self._resolve_effects(
                effects,
                item.targets,
                source_instance_id=item.instance.instance_id,
                controller_id=item.controller_id,
                meta=item.meta,
                resume={"kind": "SPELL", "stack_item": item},
            )
            if pending:
                return "PENDING"
        self._finalize_spell(item)
        self._log(f"{item.controller_id}'s {item.instance.card_id} resolves.")
        return item.instance.card_id

    def _required_target_specs(self, card: Any) -> List[Any]:
        specs: List[Any] = []
        for eff in card.rules.effects:
            for key in ("target", "source"):
                target = eff.params.get(key)
                if isinstance(target, TargetSpec):
                    specs.append(target)
        return specs

    def _timing_allows_cast(self, card: Any, player_id: str) -> bool:
        if Keyword.FLASH in (card.rules.keywords or set()):
            return True
        if CardType.INSTANT in card.card_types:
            return True
        if CardType.SORCERY in card.card_types or CardType.CREATURE in card.card_types or CardType.ARTIFACT in card.card_types or CardType.ENCHANTMENT in card.card_types:
            if self.game.turn.active_player_id != player_id:
                return False
            if self.game.turn.step not in (Step.MAIN1, Step.MAIN2):
                return False
            if self.game.zones.stack:
                return False
            return True
        return False

    def _can_pay_alternate_cost(self, card: Any, player_id: str, alternate: str) -> bool:
        if not any(isinstance(alt, dict) and alt.get("type") == alternate for alt in (card.rules.alternate_costs or [])):
            return False
        if alternate == "CONTROL_FOREST_GAIN_LIFE":
            if self._ps(player_id).life < 3:
                return False
            return self._player_controls_subtype(player_id, "Forest")
        return False

    def _pay_alternate_cost(self, card: Any, player_id: str, alternate: str) -> None:
        if alternate == "CONTROL_FOREST_GAIN_LIFE":
            self._ps(player_id).life -= 3

    def _player_controls_subtype(self, player_id: str, subtype: str) -> bool:
        for perm in self.game.zones.battlefield.values():
            if perm.controller_id != player_id:
                continue
            card = self.game.card_db.get(perm.instance.card_id)
            if card and subtype in card.subtypes:
                return True
        return False

    def _has_mana(
        self,
        card: Any,
        pool: Any,
        player_id: str,
        x_value: int = 0,
        cost_override: Optional[ManaCost] = None,
    ) -> bool:
        colored_pool = dict(pool.colored)
        generic_pool = int(pool.generic)

        cost = self._effective_mana_cost(card, player_id, x_value, cost_override)
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

        remaining_generic = int(cost.generic)
        if generic_pool >= remaining_generic:
            return True

        remaining_generic -= generic_pool
        remaining_colored = sum(int(v) for v in colored_pool.values())
        return remaining_colored >= remaining_generic

    def _pay_mana(
        self,
        card: Any,
        pool: Any,
        player_id: str,
        x_value: int = 0,
        cost_override: Optional[ManaCost] = None,
    ) -> None:
        cost = self._effective_mana_cost(card, player_id, x_value, cost_override)
        for color, amount in cost.colored.items():
            available = pool.colored.get(color.value, 0)
            use = min(available, int(amount))
            pool.colored[color.value] = available - use
            remaining = int(amount) - use
            if pool.colored[color.value] <= 0:
                pool.colored.pop(color.value, None)
            if remaining > 0:
                any_pool = pool.colored.get("ANY", 0)
                spend = min(any_pool, remaining)
                pool.colored["ANY"] = any_pool - spend
                if pool.colored["ANY"] <= 0:
                    pool.colored.pop("ANY", None)

        remaining_generic = int(cost.generic)
        if remaining_generic <= 0:
            return

        use_generic = min(pool.generic, remaining_generic)
        pool.generic -= use_generic
        remaining_generic -= use_generic

        if remaining_generic <= 0:
            return

        for color in sorted(list(pool.colored.keys())):
            if remaining_generic <= 0:
                break
            available = pool.colored.get(color, 0)
            if available <= 0:
                continue
            spend = min(available, remaining_generic)
            pool.colored[color] = available - spend
            if pool.colored[color] <= 0:
                pool.colored.pop(color, None)
            remaining_generic -= spend

    def _effective_mana_cost(
        self,
        card: Any,
        player_id: str,
        x_value: int = 0,
        cost_override: Optional[ManaCost] = None,
    ) -> Any:
        reduction = self._cost_reduction_for_spell(card, player_id)
        cost = cost_override or card.mana_cost
        generic = max(0, int(cost.generic) - reduction) + int(x_value)
        return ManaCost(generic=generic, colored=cost.colored, x=cost.x)

    def _cost_reduction_for_spell(self, card: Any, player_id: str) -> int:
        reduction = 0
        for perm in self.game.zones.battlefield.values():
            if perm.controller_id != player_id:
                continue
            source_card = self.game.card_db.get(perm.instance.card_id)
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

    def _has_mana_cost(
        self,
        cost: Any,
        pool: Any,
        player_id: Optional[str] = None,
        card: Optional[Any] = None,
    ) -> bool:
        if cost is None:
            return True
        colored_pool = dict(pool.colored)
        generic_pool = int(pool.generic)

        reduction = 0
        if card is not None and player_id is not None:
            reduction = self._cost_reduction_for_spell(card, player_id)
        remaining_generic = max(0, int(cost.generic) - reduction)

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

        if generic_pool >= remaining_generic:
            return True

        remaining_generic -= generic_pool
        remaining_colored = sum(int(v) for v in colored_pool.values())
        return remaining_colored >= remaining_generic

    def _pay_mana_cost(
        self,
        cost: Any,
        pool: Any,
        player_id: Optional[str] = None,
        card: Optional[Any] = None,
    ) -> None:
        if cost is None:
            return
        reduction = 0
        if card is not None and player_id is not None:
            reduction = self._cost_reduction_for_spell(card, player_id)
        remaining_generic = max(0, int(cost.generic) - reduction)
        for color, amount in cost.colored.items():
            available = pool.colored.get(color.value, 0)
            use = min(available, int(amount))
            pool.colored[color.value] = available - use
            remaining = int(amount) - use
            if pool.colored[color.value] <= 0:
                pool.colored.pop(color.value, None)
            if remaining > 0:
                any_pool = pool.colored.get("ANY", 0)
                spend = min(any_pool, remaining)
                pool.colored["ANY"] = any_pool - spend
                if pool.colored["ANY"] <= 0:
                    pool.colored.pop("ANY", None)

        if remaining_generic <= 0:
            return

        use_generic = min(pool.generic, remaining_generic)
        pool.generic -= use_generic
        remaining_generic -= use_generic

        if remaining_generic <= 0:
            return

        for color in sorted(list(pool.colored.keys())):
            if remaining_generic <= 0:
                break
            available = pool.colored.get(color, 0)
            if available <= 0:
                continue
            spend = min(available, remaining_generic)
            pool.colored[color] = available - spend
            if pool.colored[color] <= 0:
                pool.colored.pop(color, None)
            remaining_generic -= spend

    def _discard_from_hand(self, player_id: str, instance_id: str) -> None:
        ps = self._ps(player_id)
        for i, ci in enumerate(ps.hand):
            if ci.instance_id == instance_id:
                card = ps.hand.pop(i)
                if not card.is_token:
                    ps.graveyard.append(card)
                return

    def _sacrifice_permanent(self, perm: Permanent) -> None:
        self._handle_dies(perm)
        self._return_exiled_for_source(perm.instance.instance_id)
        self.game.zones.battlefield.pop(perm.instance.instance_id, None)
        if perm.instance.is_token:
            return
        owner = perm.instance.owner_id
        if owner in self.game.players:
            self.game.players[owner].graveyard.append(perm.instance)

    def _is_mana_ability(self, ability: Any) -> bool:
        if any(getattr(eff, "params", {}).get("target") is not None for eff in ability.effects):
            return False
        for eff in ability.effects:
            if eff.type not in (
                EffectType.ADD_MANA,
                EffectType.ADD_MANA_PER_TAPPED_LANDS,
                EffectType.ADD_MANA_PER_ELF,
            ):
                return False
        return True

    def _targets_exist(self, targets: Any) -> bool:
        flat = self._flatten_targets(targets)
        if targets is None:
            return True
        for t in flat:
            ttype = t.get("type")
            if ttype == "PLAYER":
                if t.get("player_id") not in self.game.players:
                    return False
            elif ttype == "PERMANENT":
                if t.get("instance_id") not in self.game.zones.battlefield:
                    return False
            elif ttype == "STACK":
                if t.get("instance_id") not in {s.instance.instance_id for s in self.game.zones.stack if s.instance is not None}:
                    return False
            elif ttype == "CARD":
                if t.get("instance_id") not in {ci.instance_id for ps in self.game.players.values() for ci in ps.graveyard}:
                    return False
        return True

    def _flatten_targets(self, targets: Any) -> List[Dict[str, Any]]:
        if targets is None:
            return []
        if isinstance(targets, dict):
            return [targets]
        if isinstance(targets, list):
            if not targets:
                return []
            if all(isinstance(t, dict) for t in targets):
                return list(targets)
            if all(isinstance(t, list) for t in targets):
                flat: List[Dict[str, Any]] = []
                for group in targets:
                    if isinstance(group, list):
                        flat.extend([t for t in group if isinstance(t, dict)])
                return flat
        return []

    def _notify_becomes_target(self, targets: Any, source_controller_id: str) -> None:
        for target in self._flatten_targets(targets):
            if target.get("type") != "PERMANENT":
                continue
            perm = self.game.zones.battlefield.get(target.get("instance_id"))
            if perm is None:
                continue
            if perm.controller_id != source_controller_id:
                self._handle_becomes_target(perm.instance.instance_id, source_controller_id)

    def _attachments_by_host(self) -> Dict[str, List[str]]:
        mapping: Dict[str, List[str]] = {}
        for perm in self.game.zones.battlefield.values():
            attached_to = perm.state.attached_to
            if attached_to:
                mapping.setdefault(attached_to, []).append(perm.instance.instance_id)
        return mapping

    def _derived_battlefield_state(self) -> Dict[str, Dict[str, Any]]:
        derived: Dict[str, Dict[str, Any]] = {}
        attachments_by_host = self._attachments_by_host()

        for perm in self.game.zones.battlefield.values():
            card = self.game.card_db.get(perm.instance.card_id)
            if card is None:
                continue
            base_power = None
            base_toughness = None
            if card.creature_stats is not None:
                base_power = card.creature_stats.base_power
                base_toughness = card.creature_stats.base_toughness

            plus = perm.state.counters.get("+1/+1", 0)
            minus = perm.state.counters.get("-1/-1", 0)
            counter_mod = plus - minus

            derived[perm.instance.instance_id] = {
                "base_power": base_power,
                "base_toughness": base_toughness,
                "base_override": None,
                "pt_mod": [0, 0],
                "counter_mod": counter_mod,
                "power": base_power,
                "toughness": base_toughness,
                "keywords": set(card.rules.keywords),
                "subtypes": set(card.subtypes),
                "cant_attack_players": set(),
                "must_attack": False,
                "must_be_blocked_by_all": False,
                "prevent_combat_damage": False,
                "assign_damage_as_unblocked": False,
                "goaded_by": None,
                "controller_id": perm.controller_id,
            }

        # Apply static abilities
        for source_perm in self.game.zones.battlefield.values():
            card = self.game.card_db.get(source_perm.instance.card_id)
            if card is None:
                continue
            for sa in card.rules.static_abilities:
                for eff in sa.effects:
                    self._apply_continuous_effect(
                        eff,
                        source_perm,
                        derived,
                        attachments_by_host,
                        controller_id=source_perm.controller_id,
                    )

        # Apply temporary effects
        for temp in self.game.temporary_effects:
            if not self._temp_effect_active(temp):
                continue
            source_perm = None
            if temp.source_instance_id:
                source_perm = self.game.zones.battlefield.get(temp.source_instance_id)
            self._apply_continuous_effect(
                temp.effect,
                source_perm,
                derived,
                attachments_by_host,
                controller_id=temp.controller_id,
            )

        # Apply goad from permanent state
        for perm in self.game.zones.battlefield.values():
            if perm.instance.instance_id not in derived:
                continue
            if perm.state.goaded_by and perm.state.goaded_until_turn is not None:
                if self.game.turn.turn_number <= perm.state.goaded_until_turn:
                    derived[perm.instance.instance_id]["goaded_by"] = perm.state.goaded_by
                    derived[perm.instance.instance_id]["must_attack"] = True

        # Finalize power/toughness
        for pid, d in derived.items():
            if d["base_power"] is None or d["base_toughness"] is None:
                d["power"] = None
                d["toughness"] = None
                continue
            base_power = d["base_override"][0] if d["base_override"] else d["base_power"]
            base_toughness = d["base_override"][1] if d["base_override"] else d["base_toughness"]
            p = int(base_power) + int(d["counter_mod"]) + int(d["pt_mod"][0])
            t = int(base_toughness) + int(d["counter_mod"]) + int(d["pt_mod"][1])
            d["power"] = p
            d["toughness"] = t

        return derived

    def _temp_effect_active(self, temp: TemporaryEffect) -> bool:
        if self.game.turn.turn_number > temp.expires_turn:
            return False
        if self.game.turn.turn_number < temp.expires_turn:
            return True
        if temp.expires_step is None:
            return True
        order = {
            Step.UNTAP: 0,
            Step.DRAW: 1,
            Step.MAIN1: 2,
            Step.DECLARE_ATTACKERS: 3,
            Step.DECLARE_BLOCKERS: 4,
            Step.DAMAGE: 5,
            Step.MAIN2: 6,
            Step.END: 7,
        }
        return order.get(self.game.turn.step, 0) <= order.get(temp.expires_step, 0)

    def _apply_continuous_effect(
        self,
        eff: Any,
        source_perm: Optional[Permanent],
        derived: Dict[str, Dict[str, Any]],
        attachments_by_host: Dict[str, List[str]],
        controller_id: Optional[str],
    ) -> None:
        if not derived:
            return

        if eff.type == EffectType.COST_REDUCTION:
            return

        if eff.type == EffectType.OTHER_CONTROLLED_BUFF_PER_ATTACHMENT:
            if source_perm is None:
                return
            amount = eff.params.get("amount_per_attachment", {})
            for pid, d in derived.items():
                if d["controller_id"] != source_perm.controller_id:
                    continue
                if pid == source_perm.instance.instance_id:
                    continue
                count = len(attachments_by_host.get(pid, []))
                d["pt_mod"][0] += int(amount.get("power", 0)) * count
                d["pt_mod"][1] += int(amount.get("toughness", 0)) * count
            return

        if eff.type in (EffectType.CONTROLLED_TYPE_LORD, EffectType.OTHER_CONTROLLED_TYPE_LORD):
            if source_perm is None:
                return
            subtype = eff.params.get("subtype")
            amount = eff.params.get("amount", {})
            keywords = eff.params.get("keywords") or []
            for pid, d in derived.items():
                if d["controller_id"] != source_perm.controller_id:
                    continue
                if subtype and subtype not in d["subtypes"]:
                    continue
                if eff.type == EffectType.OTHER_CONTROLLED_TYPE_LORD and pid == source_perm.instance.instance_id:
                    continue
                d["pt_mod"][0] += int(amount.get("power", 0))
                d["pt_mod"][1] += int(amount.get("toughness", 0))
                for kw in keywords:
                    try:
                        d["keywords"].add(Keyword[kw])
                    except Exception:
                        continue
            return

        if eff.type == EffectType.EQUIPPED_ONLY:
            if source_perm is None or not source_perm.state.attached_to:
                return
            target_id = source_perm.state.attached_to
            for inner in eff.params.get("effects", []):
                self._apply_effect_to_target(inner, target_id, source_perm, derived, controller_id)
            return

        targets = self._continuous_targets(eff, source_perm, derived)
        if not targets:
            return

        if not self._condition_met(eff.params.get("condition"), source_perm, controller_id, derived):
            return

        if eff.type == EffectType.SET_BASE_P_T:
            power = int(eff.params.get("power", 0))
            toughness = int(eff.params.get("toughness", 0))
            for tid in targets:
                derived[tid]["base_override"] = (power, toughness)
            return

        if eff.type == EffectType.MODIFY_P_T:
            amount = eff.params.get("amount", {})
            for tid in targets:
                derived[tid]["pt_mod"][0] += int(amount.get("power", 0))
                derived[tid]["pt_mod"][1] += int(amount.get("toughness", 0))
            return

        if eff.type == EffectType.ADD_KEYWORD:
            kw = eff.params.get("keyword")
            if isinstance(kw, str):
                try:
                    kw_enum = Keyword[kw]
                except Exception:
                    kw_enum = None
            else:
                kw_enum = kw
            for tid in targets:
                if kw_enum is not None:
                    derived[tid]["keywords"].add(kw_enum)
            return

        if eff.type == EffectType.REMOVE_KEYWORD:
            kw = eff.params.get("keyword")
            if isinstance(kw, str):
                try:
                    kw_enum = Keyword[kw]
                except Exception:
                    kw_enum = None
            else:
                kw_enum = kw
            for tid in targets:
                if kw_enum is not None and kw_enum in derived[tid]["keywords"]:
                    derived[tid]["keywords"].remove(kw_enum)
            return

        if eff.type == EffectType.ADD_SUBTYPE:
            subtype = eff.params.get("subtype")
            if subtype:
                for tid in targets:
                    derived[tid]["subtypes"].add(subtype)
            return

        if eff.type == EffectType.CANT_ATTACK_PLAYER:
            if controller_id is None:
                return
            for tid in targets:
                derived[tid]["cant_attack_players"].add(controller_id)
            return

        if eff.type == EffectType.REQUIRE_ATTACK:
            controller = eff.params.get("controller")
            if controller == "OPPONENTS" and source_perm is not None:
                for pid, d in derived.items():
                    if d["controller_id"] != source_perm.controller_id:
                        d["must_attack"] = True
            return

        if eff.type == EffectType.REQUIRE_BLOCK:
            for tid in targets:
                derived[tid]["must_be_blocked_by_all"] = True
            return

        if eff.type == EffectType.PREVENT_COMBAT_DAMAGE:
            for tid in targets:
                derived[tid]["prevent_combat_damage"] = True
            return

        if eff.type == EffectType.ASSIGN_DAMAGE_AS_UNBLOCKED:
            for tid in targets:
                derived[tid]["assign_damage_as_unblocked"] = True
            return

        if eff.type == EffectType.GOAD:
            if controller_id is None:
                return
            for tid in targets:
                derived[tid]["goaded_by"] = controller_id
                derived[tid]["must_attack"] = True
            return

        if eff.type == EffectType.TEAM_BUFF:
            if controller_id is None:
                return
            subtype = eff.params.get("subtype")
            amount = eff.params.get("amount", {})
            keywords = eff.params.get("keywords") or []
            for pid, d in derived.items():
                if d["controller_id"] != controller_id:
                    continue
                if subtype and subtype not in d["subtypes"]:
                    continue
                if eff.params.get("exclude_source") and source_perm is not None:
                    if pid == source_perm.instance.instance_id:
                        continue
                d["pt_mod"][0] += int(amount.get("power", 0))
                d["pt_mod"][1] += int(amount.get("toughness", 0))
                for kw in keywords:
                    try:
                        d["keywords"].add(Keyword[kw])
                    except Exception:
                        continue
            return

    def _apply_effect_to_target(
        self,
        eff: Any,
        target_id: str,
        source_perm: Optional[Permanent],
        derived: Dict[str, Dict[str, Any]],
        controller_id: Optional[str],
    ) -> None:
        if target_id not in derived:
            return
        if eff.type == EffectType.MODIFY_P_T:
            amount = eff.params.get("amount", {})
            derived[target_id]["pt_mod"][0] += int(amount.get("power", 0))
            derived[target_id]["pt_mod"][1] += int(amount.get("toughness", 0))
            return
        if eff.type == EffectType.ADD_KEYWORD:
            kw = eff.params.get("keyword")
            if isinstance(kw, str):
                try:
                    kw_enum = Keyword[kw]
                except Exception:
                    kw_enum = None
            else:
                kw_enum = kw
            if kw_enum is not None:
                derived[target_id]["keywords"].add(kw_enum)
            return
        if eff.type == EffectType.REMOVE_KEYWORD:
            kw = eff.params.get("keyword")
            if isinstance(kw, str):
                try:
                    kw_enum = Keyword[kw]
                except Exception:
                    kw_enum = None
            else:
                kw_enum = kw
            if kw_enum is not None and kw_enum in derived[target_id]["keywords"]:
                derived[target_id]["keywords"].remove(kw_enum)
            return

    def _continuous_targets(
        self,
        eff: Any,
        source_perm: Optional[Permanent],
        derived: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        target = eff.params.get("target")
        if isinstance(target, dict) and target.get("type") == "PERMANENT":
            tid = target.get("instance_id")
            return [tid] if tid in derived else []
        if target == "SELF" and source_perm is not None:
            return [source_perm.instance.instance_id]
        if isinstance(target, TargetSpec):
            if target.selector == Selector.TARGET_EQUIPPED_CREATURE and source_perm is not None:
                if source_perm.state.attached_to:
                    return [source_perm.state.attached_to]
                return []
            if target.selector == Selector.TARGET_ENCHANTED_CREATURE and source_perm is not None:
                if source_perm.state.attached_to:
                    return [source_perm.state.attached_to]
                return []
            return [pid for pid in derived if self._perm_matches_spec(pid, target, derived, source_perm)]
        return []

    def _perm_matches_spec(
        self,
        perm_id: str,
        spec: TargetSpec,
        derived: Dict[str, Dict[str, Any]],
        source_perm: Optional[Permanent],
    ) -> bool:
        d = derived.get(perm_id)
        if d is None:
            return False
        if spec.zone != Zone.BATTLEFIELD:
            return False
        if spec.selector in (Selector.ANY_CREATURE, Selector.TARGET_CREATURE):
            return d["base_power"] is not None
        if spec.selector == Selector.TARGET_CREATURE_YOU_CONTROL and source_perm is not None:
            return d["base_power"] is not None and d["controller_id"] == source_perm.controller_id
        if spec.selector == Selector.TARGET_CREATURE_OPPONENT_CONTROLS and source_perm is not None:
            return d["base_power"] is not None and d["controller_id"] != source_perm.controller_id
        return False

    def _condition_met(
        self,
        condition: Optional[Dict[str, Any]],
        source_perm: Optional[Permanent],
        controller_id: Optional[str],
        derived: Dict[str, Dict[str, Any]],
    ) -> bool:
        if not condition:
            return True
        if condition.get("during_your_turn") and controller_id is not None:
            return self.game.turn.active_player_id == controller_id
        if "control_subtype" in condition and controller_id is not None:
            subtype = condition.get("control_subtype")
            for d in derived.values():
                if d["controller_id"] == controller_id and subtype in d["subtypes"]:
                    return True
            return False
        return True

    def _creature_can_attack(self, perm: Permanent, derived: Dict[str, Dict[str, Any]], defender_id: str) -> bool:
        d = derived.get(perm.instance.instance_id)
        if d is None or d["base_power"] is None:
            return False
        if perm.state.tapped:
            return False
        if perm.state.summoning_sick and Keyword.HASTE not in d["keywords"]:
            return False
        if Keyword.DEFENDER in d["keywords"]:
            return False
        if defender_id in d["cant_attack_players"]:
            return False
        return True

    def _creature_can_block(self, blocker: Permanent, attacker_id: str, derived: Dict[str, Dict[str, Any]]) -> bool:
        d_blocker = derived.get(blocker.instance.instance_id)
        d_attacker = derived.get(attacker_id)
        if d_blocker is None or d_blocker["base_power"] is None:
            return False
        if blocker.state.tapped:
            return False
        if d_attacker is None:
            return False
        if Keyword.FLYING in d_attacker["keywords"]:
            if Keyword.FLYING not in d_blocker["keywords"] and Keyword.REACH not in d_blocker["keywords"]:
                return False
        return True

    def _attack_tax_amount(self, defender_id: str) -> int:
        amount = 0
        for temp in self.game.temporary_effects:
            if temp.effect.type != EffectType.ATTACK_TAX:
                continue
            if not self._temp_effect_active(temp):
                continue
            if temp.controller_id != defender_id:
                continue
            amount += int(temp.effect.params.get("amount", 0) or 0)
        return amount

    def _return_exiled_for_source(self, source_instance_id: str) -> None:
        to_return = [eid for eid, sid in self.game.exile_links.items() if sid == source_instance_id]
        for eid in to_return:
            card = self.game.zones.exile.pop(eid, None)
            self.game.exile_links.pop(eid, None)
            if card is None or card.is_token:
                continue
            perm = Permanent(instance=card, controller_id=card.owner_id)
            self.game.zones.battlefield[card.instance_id] = perm
            self._handle_etb(perm)
            self._handle_creature_enters(perm)

    def _can_pay_generic_cost(self, pool: Any, amount: int) -> bool:
        total = int(pool.generic) + sum(int(v) for v in pool.colored.values())
        return total >= amount

    def _pay_generic_cost(self, pool: Any, amount: int) -> None:
        remaining = int(amount)
        if remaining <= 0:
            return
        use_generic = min(pool.generic, remaining)
        pool.generic -= use_generic
        remaining -= use_generic
        if remaining <= 0:
            return
        for color in sorted(list(pool.colored.keys())):
            if remaining <= 0:
                break
            available = pool.colored.get(color, 0)
            if available <= 0:
                continue
            spend = min(available, remaining)
            pool.colored[color] = available - spend
            if pool.colored[color] <= 0:
                pool.colored.pop(color, None)
            remaining -= spend

    def _count_subtype_on_battlefield(
        self,
        subtype: str,
        controller_id: Optional[str] = None,
        exclude_id: Optional[str] = None,
    ) -> int:
        count = 0
        for perm in self.game.zones.battlefield.values():
            if exclude_id and perm.instance.instance_id == exclude_id:
                continue
            if controller_id is not None and perm.controller_id != controller_id:
                continue
            card = self.game.card_db.get(perm.instance.card_id)
            if card and subtype in card.subtypes:
                count += 1
        return count

    def _trigger_condition_met(self, condition: Optional[Dict[str, Any]], source_perm: Permanent, context: Dict[str, Any]) -> bool:
        if not condition:
            return True
        if condition.get("during_opponent_turn"):
            caster_id = context.get("caster_id")
            return caster_id is not None and self.game.turn.active_player_id != caster_id
        if condition.get("controller") == "YOU":
            entered_perm = context.get("entered_perm")
            return entered_perm is not None and entered_perm.controller_id == source_perm.controller_id
        if condition.get("controller") == "OPPONENT":
            source_controller = context.get("source_controller_id")
            return source_controller is not None and source_controller != source_perm.controller_id
        if "has_keyword" in condition:
            entered_perm = context.get("entered_perm")
            if entered_perm is None:
                return False
            derived = self._derived_battlefield_state()
            d = derived.get(entered_perm.instance.instance_id)
            if d is None:
                return False
            try:
                kw = Keyword[condition.get("has_keyword")]
            except Exception:
                return False
            return kw in d["keywords"]
        if "subtype" in condition:
            entered_perm = context.get("entered_perm")
            if entered_perm is None:
                return False
            card = self.game.card_db.get(entered_perm.instance.card_id)
            if card is None:
                return False
            return condition.get("subtype") in card.subtypes
        if "spell_type" in condition:
            spell_card = context.get("spell_card")
            if spell_card is None:
                return False
            if condition.get("spell_type") == "CREATURE":
                return CardType.CREATURE in spell_card.card_types
            if condition.get("spell_type") == "INSTANT_OR_SORCERY":
                return CardType.INSTANT in spell_card.card_types or CardType.SORCERY in spell_card.card_types
        if "control_subtype_count" in condition:
            info = condition.get("control_subtype_count") or {}
            subtype = info.get("subtype")
            min_count = int(info.get("min", 0))
            count = 0
            for perm in self.game.zones.battlefield.values():
                if perm.controller_id != source_perm.controller_id:
                    continue
                card = self.game.card_db.get(perm.instance.card_id)
                if card and subtype in card.subtypes:
                    count += 1
            return count >= min_count
        return True

    def _materialize_trigger_effects(
        self,
        effects: List[Any],
        source_perm: Permanent,
        context: Dict[str, Any],
    ) -> List[Any]:
        materialized: List[Any] = []
        for eff in effects:
            params = dict(eff.params)
            amount = params.get("amount")
            if amount == "DAMAGE":
                params["amount"] = int(context.get("damage", 0))
            if amount == "LIFE_LOST":
                params["amount"] = int(context.get("life_lost", 0))
            if amount == "COUNTERS_ON_SELF":
                params["amount"] = int(source_perm.state.counters.get("+1/+1", 0))
            if params.get("target") == "TRIGGER_SOURCE":
                params["target"] = {"type": "PERMANENT", "instance_id": context.get("trigger_source_id", source_perm.instance.instance_id)}
            if params.get("chooser") == "DAMAGED_PLAYER":
                params["chooser_player_id"] = context.get("damaged_player_id")
            materialized.append(Effect(type=eff.type, params=params))
        return materialized

    def _materialize_effects_with_context(
        self,
        effects: List[Any],
        source_perm: Optional[Permanent],
        context: Dict[str, Any],
    ) -> List[Any]:
        materialized: List[Any] = []
        for eff in effects:
            params = dict(eff.params)
            amount = params.get("amount")
            if amount == "SACRIFICED_TOUGHNESS":
                params["amount"] = int(context.get("sacrificed_toughness", 0))
            if amount == "COUNTERS_ON_SELF" and source_perm is not None:
                params["amount"] = int(source_perm.state.counters.get("+1/+1", 0))
            materialized.append(Effect(type=eff.type, params=params))
        return materialized

    def _effects_need_targets(self, effects: List[Any]) -> bool:
        for eff in effects:
            if isinstance(eff.params.get("target"), TargetSpec):
                return True
            if isinstance(eff.params.get("source"), TargetSpec):
                return True
            if isinstance(eff.params.get("targets_any_of"), list):
                return True
        return False

    def _enqueue_trigger_decision(self, trigger_info: Dict[str, Any], options: List[Any]) -> None:
        if self.game.pending_decision is None:
            self.game.pending_decision = PendingDecision(
                player_id=trigger_info["controller_id"],
                kind="TRIGGER_TARGETS",
                options=options,
                context={"trigger": trigger_info, "queue": []},
            )
            return
        queue = self.game.pending_decision.context.setdefault("queue", [])
        queue.append({"trigger": trigger_info, "options": options})

    def _queue_triggered_ability(self, source_perm: Permanent, ability: Any, context: Dict[str, Any]) -> None:
        controller_id = source_perm.controller_id
        effects = self._materialize_trigger_effects(ability.effects, source_perm, context)

        if self._effects_need_targets(effects):
            visible = self.get_visible_state(controller_id)
            source_view = next(
                (p for p in visible.zones.battlefield if getattr(p, "instance_id", None) == source_perm.instance.instance_id),
                None,
            )
            options = ActionSurface()._enumerate_targets_for_effects(
                effects,
                visible,
                controller_id,
                source_perm=source_view,
            )
            if options and options != [[]]:
                trigger_info = {
                    "source_instance_id": source_perm.instance.instance_id,
                    "controller_id": controller_id,
                    "effects": effects,
                }
                self._enqueue_trigger_decision(trigger_info, options)
                return

        self.game.zones.stack.append(
            StackItem(
                kind=StackItemKind.ABILITY,
                controller_id=controller_id,
                source_instance_id=source_perm.instance.instance_id,
                effects=effects,
                targets=None,
                meta={"trigger": ability.trigger.value},
            )
        )

    def _handle_etb(self, perm: Permanent) -> None:
        card = self.game.card_db.get(perm.instance.card_id)
        if card is None:
            return
        for ability in card.rules.triggered_abilities:
            if ability.trigger != TriggerType.ETB:
                continue
            if not self._trigger_condition_met(ability.condition, perm, {}):
                continue
            self._queue_triggered_ability(perm, ability, {"trigger_source_id": perm.instance.instance_id})

    def _handle_creature_enters(self, perm: Permanent) -> None:
        card = self.game.card_db.get(perm.instance.card_id)
        if card is None or CardType.CREATURE not in card.card_types:
            return
        for source_perm in self.game.zones.battlefield.values():
            source_card = self.game.card_db.get(source_perm.instance.card_id)
            if source_card is None:
                continue
            for ability in source_card.rules.triggered_abilities:
                if ability.trigger != TriggerType.CREATURE_ENTERS:
                    continue
                context = {"entered_perm": perm, "trigger_source_id": perm.instance.instance_id}
                if not self._trigger_condition_met(ability.condition, source_perm, context):
                    continue
                self._queue_triggered_ability(source_perm, ability, context)

    def _handle_cast_spell(self, caster_id: str, spell_card: Any) -> None:
        for perm in self.game.zones.battlefield.values():
            perm_card = self.game.card_db.get(perm.instance.card_id)
            if perm_card is None:
                continue
            for ability in perm_card.rules.triggered_abilities:
                if ability.trigger != TriggerType.CAST_SPELL:
                    continue
                context = {"caster_id": caster_id, "spell_card": spell_card}
                if not self._trigger_condition_met(ability.condition, perm, context):
                    continue
                self._queue_triggered_ability(perm, ability, context)

    def _handle_attacks(self, attacker_ids: List[str]) -> None:
        attachments = self._attachments_by_host()
        for attacker_id in attacker_ids:
            perm = self.game.zones.battlefield.get(attacker_id)
            if perm is None:
                continue
            card = self.game.card_db.get(perm.instance.card_id)
            if card is None:
                continue
            if perm.state.draw_on_attack_by and perm.state.draw_on_attack_until_turn is not None:
                if self.game.turn.turn_number <= perm.state.draw_on_attack_until_turn:
                    defending_player = self._other_player(perm.controller_id)
                    if self._other_player(perm.state.draw_on_attack_by) == defending_player:
                        self._draw(perm.state.draw_on_attack_by, 1)
            for ability in card.rules.triggered_abilities:
                if ability.trigger in (TriggerType.ATTACKS, TriggerType.ATTACKS_OR_BLOCKS):
                    self._queue_triggered_ability(perm, ability, {"trigger_source_id": perm.instance.instance_id})

            # Equipped creature attacks triggers on equipment
            for eq_id in attachments.get(attacker_id, []):
                eq_perm = self.game.zones.battlefield.get(eq_id)
                if eq_perm is None:
                    continue
                eq_card = self.game.card_db.get(eq_perm.instance.card_id)
                if eq_card is None:
                    continue
                for ability in eq_card.rules.triggered_abilities:
                    if ability.trigger == TriggerType.EQUIPPED_CREATURE_ATTACKS:
                        self._queue_triggered_ability(eq_perm, ability, {"trigger_source_id": attacker_id})

    def _handle_blocks(self, blocker_ids: List[str]) -> None:
        for blocker_id in blocker_ids:
            perm = self.game.zones.battlefield.get(blocker_id)
            if perm is None:
                continue
            card = self.game.card_db.get(perm.instance.card_id)
            if card is None:
                continue
            for ability in card.rules.triggered_abilities:
                if ability.trigger == TriggerType.ATTACKS_OR_BLOCKS:
                    self._queue_triggered_ability(perm, ability, {"trigger_source_id": perm.instance.instance_id})

    def _handle_combat_damage_to_player(self, source_id: str, player_id: str) -> None:
        perm = self.game.zones.battlefield.get(source_id)
        if perm is None:
            return
        card = self.game.card_db.get(perm.instance.card_id)
        if card is None:
            return
        for ability in card.rules.triggered_abilities:
            if ability.trigger == TriggerType.COMBAT_DAMAGE_TO_PLAYER:
                self._queue_triggered_ability(perm, ability, {"damaged_player_id": player_id})

    def _handle_dealt_damage(self, target_id: str, amount: int) -> None:
        perm = self.game.zones.battlefield.get(target_id)
        if perm is None:
            return
        card = self.game.card_db.get(perm.instance.card_id)
        if card is None:
            return
        for ability in card.rules.triggered_abilities:
            if ability.trigger == TriggerType.DEALT_DAMAGE:
                self._queue_triggered_ability(perm, ability, {"damage": amount})

    def _handle_you_lose_life(self, player_id: str, amount: int) -> None:
        for perm in self.game.zones.battlefield.values():
            if perm.controller_id != player_id:
                continue
            card = self.game.card_db.get(perm.instance.card_id)
            if card is None:
                continue
            for ability in card.rules.triggered_abilities:
                if ability.trigger == TriggerType.YOU_LOSE_LIFE:
                    self._queue_triggered_ability(perm, ability, {"life_lost": amount})

    def _handle_becomes_target(self, target_id: str, source_controller_id: str) -> None:
        perm = self.game.zones.battlefield.get(target_id)
        if perm is None:
            return
        card = self.game.card_db.get(perm.instance.card_id)
        if card is None:
            return
        for ability in card.rules.triggered_abilities:
            if ability.trigger != TriggerType.BECOMES_TARGET:
                continue
            context = {"source_controller_id": source_controller_id}
            if not self._trigger_condition_met(ability.condition, perm, context):
                continue
            self._queue_triggered_ability(perm, ability, context)

    def _handle_dies(self, perm: Permanent) -> None:
        card = self.game.card_db.get(perm.instance.card_id)
        if card is None:
            return
        derived = self._derived_battlefield_state()
        d = derived.get(perm.instance.instance_id)
        if d and Keyword.UNDEAD_RETURN in d.get("keywords", set()):
            self.game.zones.stack.append(
                StackItem(
                    kind=StackItemKind.ABILITY,
                    controller_id=perm.controller_id,
                    source_instance_id=perm.instance.instance_id,
                    effects=[Effect(EffectType.RETURN_FROM_GRAVEYARD_TO_BATTLEFIELD_TAPPED, {"target": "SELF"})],
                    targets=None,
                    meta={"trigger": "UNDEAD_RETURN"},
                )
            )
        for ability in card.rules.triggered_abilities:
            if ability.trigger == TriggerType.DIES:
                self._queue_triggered_ability(perm, ability, {})

        for other in self.game.zones.battlefield.values():
            other_card = self.game.card_db.get(other.instance.card_id)
            if other_card is None:
                continue
            for ability in other_card.rules.triggered_abilities:
                if ability.trigger == TriggerType.OTHER_FRIENDLY_DIES:
                    if other.controller_id == perm.controller_id and other.instance.instance_id != perm.instance.instance_id:
                        self._queue_triggered_ability(other, ability, {})
                if ability.trigger == TriggerType.OTHER_DIES_DURING_YOUR_TURN:
                    if self.game.turn.active_player_id == other.controller_id and other.instance.instance_id != perm.instance.instance_id:
                        self._queue_triggered_ability(other, ability, {})

    def _handle_upkeep(self, player_id: str) -> None:
        for perm in self.game.zones.battlefield.values():
            if perm.controller_id != player_id:
                continue
            card = self.game.card_db.get(perm.instance.card_id)
            if card is None:
                continue
            for ability in card.rules.triggered_abilities:
                if ability.trigger == TriggerType.UPKEEP:
                    self._queue_triggered_ability(perm, ability, {})
    def _basic_land_produces(self, card_id: str) -> Optional[Dict[str, int]]:
        card = self.game.card_db.get(card_id)
        if card is not None and card.land_stats is not None:
            return {c.value: n for c, n in card.land_stats.produces.items()}
        mapping = {
            "basic_swamp": {"BLACK": 1},
            "basic_mountain": {"RED": 1},
            "basic_island": {"BLUE": 1},
            "basic_forest": {"GREEN": 1},
            "basic_plains": {"WHITE": 1},
        }
        return mapping.get(card_id)
