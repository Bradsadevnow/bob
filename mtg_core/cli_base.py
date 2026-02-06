from __future__ import annotations

from typing import Any, Callable, List, Optional

from mtg_core.actions import Action, ActionType
from mtg_core.aibase import VisibleState, ResolutionResult
from mtg_core.action_surface import ActionSurface


class CLIPlayer:
    """
    Human control surface (POST-GAME-START ONLY).

    Contract:
      - Reads VisibleState from engine
      - Requests legal actions from ActionSurface
      - Submits a chosen Action to engine.submit_action(...)
    """

    def __init__(
        self,
        engine: Any,
        player_id: str,
        *,
        on_action: Optional[Callable[[VisibleState, Action, ResolutionResult, Optional[str]], None]] = None,
    ):
        self.engine = engine
        self.player_id = player_id
        self.actions = ActionSurface()
        self.on_action = on_action

    def loop(self) -> None:
        visible = self.engine.get_visible_state(self.player_id)
        actions = self.actions.get_legal_actions(visible, self.player_id)

        self.render_state(visible)
        self.render_actions(visible, actions)

        if not actions:
            input("No legal actions. Press Enter to continue...")
            return

        choice = self.prompt_for_action(len(actions))
        action = actions[choice]

        result = self.engine.submit_action(action)
        self.render_result(result)
        if self.on_action is not None:
            self.on_action(visible, action, result, None)

    # ============================
    # Rendering
    # ============================

    def render_state(self, visible: VisibleState) -> None:
        print("\n" + "=" * 80)
        print(f"Player: {self.player_id}")
        print(f"Turn: {visible.turn_number}")
        print(f"Phase: {visible.phase}")
        print(f"Priority: {visible.priority_holder_id}")
        print("-" * 80)

        print("Life Totals:")
        for pid, life in visible.life_totals.items():
            marker = " (you)" if pid == self.player_id else ""
            print(f"  {pid}: {life}{marker}")

        print("\nHand:")
        if visible.zones.hand:
            for i, ci in enumerate(visible.zones.hand):
                name = getattr(ci, "name", getattr(ci, "card_id", str(ci)))
                cost = self._format_mana_cost(getattr(ci, "mana_cost", None))
                pt = self._format_pt(ci)
                type_str = getattr(ci, "card_type", None)
                type_str = f" [{type_str}]" if type_str else ""
                print(f"  [{i}] {name} {cost}{pt}{type_str}")
        else:
            print("  (empty)")

        print("\nBattlefield:")
        if visible.zones.battlefield:
            for perm in visible.zones.battlefield:
                name = getattr(perm, "name", getattr(perm, "card_id", str(perm)))
                owner_id = getattr(perm, "owner_id", "?")
                controller_id = getattr(perm, "controller_id", "?")
                tapped = getattr(perm, "tapped", False)
                tapped_flag = " [tapped]" if tapped else ""
                pt = self._format_pt(perm)
                type_str = getattr(perm, "card_type", None)
                type_str = f" [{type_str}]" if type_str else ""
                print(f"  - {name}{pt}{type_str} (owner={owner_id}, controller={controller_id}){tapped_flag}")
        else:
            print("  (empty)")

        print("\nStack:")
        if visible.zones.stack:
            for i, item in enumerate(visible.zones.stack):
                name = getattr(item, "name", getattr(item, "card_id", str(item)))
                controller_id = getattr(item, "controller_id", "?")
                target = getattr(item, "targets", None)
                target_str = ""
                if isinstance(target, dict):
                    target_str = self._format_target(target)
                print(f"  [{i}] {name} (controller={controller_id}){target_str}")
            print("  (response window: instants allowed)")
        else:
            print("  (empty)")

        print("\nCombat:")
        if visible.combat_attackers:
            print(f"  Attackers: {visible.combat_attackers}")
        else:
            print("  Attackers: (none)")
        if visible.combat_blockers:
            print(f"  Blockers: {visible.combat_blockers}")
        else:
            print("  Blockers: (none)")
        if visible.phase == "DECLARE_ATTACKERS" and not visible.combat_attackers_declared:
            print("  (attackers not declared yet)")
        if visible.phase == "DECLARE_BLOCKERS" and not visible.combat_blockers_declared:
            print("  (blockers not declared yet)")

        print("\nGraveyards:")
        for pid, gy in visible.zones.graveyards.items():
            count = len(gy) if isinstance(gy, list) else gy
            print(f"  {pid}: {count}")

        print(f"\nLibrary size: {visible.zones.library_size}")

        if visible.available_mana is not None:
            print("\nMana Pool:")
            print(f"  {visible.available_mana}")

        print("=" * 80)

    def render_actions(self, visible: VisibleState, actions: List[Action]) -> None:
        print("\nLegal Actions:")
        for i, action in enumerate(actions):
            print(f"  [{i}] {self._format_action(action, visible)}")

    def render_result(self, result: ResolutionResult) -> None:
        success_values = {"SUCCESS"}
        status_value = getattr(result.status, "value", str(result.status))

        if status_value not in success_values:
            print("\n!!! Action Failed !!!")
            print(f"Status: {status_value}")
            if result.message:
                print(f"Message: {result.message}")
            return

        if result.payload is not None:
            print("\nAction Result:")
            print(result.payload)
            if isinstance(result.payload, dict) and "resolved_stack" in result.payload:
                print(f"Stack resolves: {result.payload.get('resolved_stack')}")

    # ============================
    # Input
    # ============================

    def prompt_for_action(self, n_actions: int) -> int:
        while True:
            raw = input(f"Choose action [0-{n_actions - 1}]: ").strip()
            if not raw.isdigit():
                print("Please enter a number.")
                continue

            idx = int(raw)
            if 0 <= idx < n_actions:
                return idx

            print("Choice out of range.")

    # ============================
    # Formatting helpers
    # ============================

    def _format_action(self, action: Action, visible: VisibleState) -> str:
        t = action.type

        if t == ActionType.PLAY_LAND:
            hand_card = self._find_hand_card(visible, action.object_id)
            label = self._format_card_label(hand_card, fallback=action.object_id)
            return f"Play land: {label}"
        if t == ActionType.TAP_FOR_MANA:
            perm = self._find_battlefield_perm(visible, action.object_id)
            label = self._format_card_label(perm, fallback=action.object_id)
            produces = None
            if isinstance(action.payload, dict):
                produces = action.payload.get("produces")
            if produces:
                return f"Tap for mana: {label} -> {produces}"
            return f"Tap for mana: {label}"
        if t == ActionType.CAST_SPELL:
            hand_card = self._find_hand_card(visible, action.object_id)
            label = self._format_card_label(hand_card, fallback=action.object_id)
            target_str = ""
            if isinstance(action.targets, dict):
                target_str = self._format_target(action.targets)
            return f"Cast spell: {label}{target_str}"
        if t == ActionType.DECLARE_ATTACKERS:
            attackers = []
            if isinstance(action.targets, dict):
                attackers = action.targets.get("attackers", [])
            return f"Declare attackers: {attackers}"
        if t == ActionType.DECLARE_BLOCKERS:
            blocks = []
            if isinstance(action.targets, dict):
                blocks = action.targets.get("blocks", [])
            return f"Declare blockers: {blocks}"
        if t == ActionType.PASS_PRIORITY:
            if visible.zones.stack:
                return "Pass priority (stack will resolve if both pass)"
            return "Pass priority"
        if t == ActionType.SCOOP:
            return "Scoop"
        if t == ActionType.SKIP_COMBAT:
            return "Skip combat"
        if t == ActionType.SKIP_MAIN2:
            return "Skip main 2"

        return f"{action.type} (object={action.object_id}, targets={action.targets}, payload={action.payload})"

    def _format_target(self, target: dict) -> str:
        if target.get("type") == "PLAYER":
            return f" -> player {target.get('player_id')}"
        if target.get("type") == "PERMANENT":
            return f" -> permanent {target.get('instance_id')}"
        return ""

    def _find_hand_card(self, visible: VisibleState, instance_id: str):
        for ci in visible.zones.hand:
            if getattr(ci, "instance_id", None) == instance_id:
                return ci
        return None

    def _find_battlefield_perm(self, visible: VisibleState, instance_id: str):
        for perm in visible.zones.battlefield:
            if getattr(perm, "instance_id", None) == instance_id:
                return perm
        return None

    def _format_card_label(self, obj: object, fallback: object) -> str:
        if obj is None:
            return str(fallback)
        name = getattr(obj, "name", None) or getattr(obj, "card_id", None) or str(fallback)
        cost = self._format_mana_cost(getattr(obj, "mana_cost", None))
        pt = self._format_pt(obj)
        return f"{name} {cost}{pt}".strip()

    def _format_mana_cost(self, mana_cost: object) -> str:
        if not isinstance(mana_cost, dict):
            return ""
        generic = mana_cost.get("generic", 0)
        colored = mana_cost.get("colored", {}) or {}
        parts = []
        if isinstance(generic, int) and generic > 0:
            parts.append(str(generic))

        order = ["WHITE", "BLUE", "BLACK", "RED", "GREEN"]
        symbols = {"WHITE": "W", "BLUE": "U", "BLACK": "B", "RED": "R", "GREEN": "G"}
        for color in order:
            count = int(colored.get(color, 0) or 0)
            if count > 0:
                parts.append(symbols[color] * count)

        if not parts:
            return "0"
        return "".join(parts)

    def _format_pt(self, obj: object) -> str:
        power = getattr(obj, "power", None)
        toughness = getattr(obj, "toughness", None)
        if power is None or toughness is None:
            return ""
        return f" [{power}/{toughness}]"
