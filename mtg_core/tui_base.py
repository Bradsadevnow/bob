from __future__ import annotations

from typing import Any, Callable, List, Optional

from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, Static

from mtg_core.actions import Action, ActionType
from mtg_core.aibase import VisibleState
from mtg_core.action_surface import ActionSurface


class _TurnApp(App[Optional[int]]):
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, visible: VisibleState, actions: List[Action], player_id: str) -> None:
        super().__init__()
        self._visible_state = visible
        self._actions = actions
        self._player_id = player_id
        self._error: str = ""

        self.header = Static()
        self.player_left = Static()
        self.player_right = Static()
        self.battlefield = Static()
        self.stack = Static()
        self.priority = Static()
        self.actions_view = Static()
        self.input = Input(placeholder="Action # (0-9) or ENTER to submit")
        self.footer = Footer()

    def compose(self) -> ComposeResult:
        yield self.header
        with Horizontal():
            yield self.player_left
            yield self.player_right
        yield self.battlefield
        with Horizontal():
            yield self.stack
            yield self.priority
        yield self.actions_view
        yield self.input
        yield self.footer

    def on_mount(self) -> None:
        self._render()
        self.input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        if not raw:
            return
        if not raw.isdigit():
            self._error = "Enter a number."
            self._render()
            return
        idx = int(raw)
        if idx < 0 or idx >= len(self._actions):
            self._error = "Choice out of range."
            self._render()
            return
        self.exit(result=idx)

    def on_key(self, event) -> None:  # type: ignore[override]
        if event.key == "space":
            idx = self._pass_index()
            if idx is not None:
                self.exit(result=idx)
        elif event.key.isdigit():
            idx = int(event.key)
            if idx < len(self._actions):
                self.exit(result=idx)

    def _pass_index(self) -> Optional[int]:
        for i, action in enumerate(self._actions):
            if action.type == ActionType.PASS_PRIORITY:
                return i
        return None

    def _render(self) -> None:
        p1, p2 = self._player_order()

        header_text = Text(
            f"Turn {self._visible_state.turn_number} | "
            f"Phase: {self._visible_state.phase} | "
            f"Active: {self._visible_state.active_player_id} | "
            f"Stack: {len(self._visible_state.zones.stack)}"
        )
        self.header.update(Panel(header_text, title="GAME STATE"))

        left_text = self._player_panel_text(p1)
        right_text = self._player_panel_text(p2)
        self.player_left.update(Panel(left_text, title=p1))
        self.player_right.update(Panel(right_text, title=p2))

        battlefield_text = self._battlefield_text()
        self.battlefield.update(Panel(battlefield_text, title="BATTLEFIELD"))

        stack_text = self._stack_text()
        self.stack.update(Panel(stack_text, title="STACK"))

        priority_text = Text(f"Priority: {self._visible_state.priority_holder_id}")
        self.priority.update(Panel(priority_text, title="PRIORITY"))

        actions_text = self._actions_text()
        self.actions_view.update(Panel(actions_text, title="ACTIONS"))

    def _player_order(self) -> tuple[str, str]:
        ids = list(self._visible_state.life_totals.keys())
        if len(ids) == 2:
            if ids[0] == self._player_id:
                return ids[0], ids[1]
            return ids[1], ids[0]
        return self._player_id, "OPP"

    def _player_panel_text(self, pid: str) -> Text:
        life = self._visible_state.life_totals.get(pid, "?")
        mana = self._mana_str(pid)
        hand = self._hand_preview(pid)
        t = Text()
        t.append(f"Life: {life}\n")
        t.append(f"Mana: {mana}\n")
        t.append(f"Hand: {hand}\n")
        return t

    def _mana_str(self, pid: str) -> str:
        if pid != self._player_id:
            return "?"
        pool = self._visible_state.available_mana or {}
        colored = dict(pool.get("colored", {}) or {})
        generic = int(pool.get("generic", 0) or 0)
        symbols = {"WHITE": "W", "BLUE": "U", "BLACK": "B", "RED": "R", "GREEN": "G"}
        parts = []
        if generic > 0:
            parts.append(f"{{{generic}}}")
        for color in ["WHITE", "BLUE", "BLACK", "RED", "GREEN"]:
            n = int(colored.get(color, 0) or 0)
            if n > 0:
                parts.append("{" + symbols[color] + "}" * n)
        return "".join(parts) if parts else "{}"

    def _hand_preview(self, pid: str) -> str:
        if pid != self._player_id:
            return "(hidden)"
        cards = self._visible_state.zones.hand
        names = [getattr(ci, "name", getattr(ci, "card_id", "?")) for ci in cards]
        if not names:
            return "(empty)"
        return "[" + "][".join(names) + "]"

    def _battlefield_text(self) -> Text:
        t = Text()
        for pid in self._visible_state.life_totals.keys():
            t.append(f"{pid}:\n")
            for perm in self._visible_state.zones.battlefield:
                if getattr(perm, "controller_id", None) != pid:
                    continue
                name = getattr(perm, "name", getattr(perm, "card_id", "?"))
                pt = self._pt(perm)
                tapped = " (tapped)" if getattr(perm, "tapped", False) else ""
                t.append(f"  - {name}{pt}{tapped}\n")
        return t

    def _stack_text(self) -> Text:
        if not self._visible_state.zones.stack:
            return Text("(empty)")
        t = Text()
        for item in self._visible_state.zones.stack:
            name = getattr(item, "name", getattr(item, "card_id", "?"))
            target = getattr(item, "targets", None)
            target_str = self._format_target(target)
            t.append(f"{name}{target_str}\n")
        return t

    def _actions_text(self) -> Text:
        t = Text()
        for i, action in enumerate(self._actions):
            t.append(f"[{i}] {self._format_action(action)}\n")
        if self._error:
            t.append(f"\nError: {self._error}")
        return t

    def _format_action(self, action: Action) -> str:
        t = action.type
        if t == ActionType.PLAY_LAND:
            name = self._card_name_from_hand(action.object_id)
            if name:
                return f"Play {name}"
            return "Play land"
        if t == ActionType.TAP_FOR_MANA:
            name = self._card_name_from_battlefield(action.object_id)
            if name:
                return f"Tap {name} for mana"
            return "Tap for mana"
        if t == ActionType.CAST_SPELL:
            name = self._card_name_from_hand(action.object_id)
            cost = self._card_cost_from_hand(action.object_id)
            target = self._format_target(action.targets)
            if name:
                suffix = f" {cost}" if cost else ""
                return f"Cast {name}{suffix}{target}"
            return f"Cast spell{target}"
        if t == ActionType.DECLARE_ATTACKERS:
            attackers = []
            if isinstance(action.targets, dict):
                attackers = list(action.targets.get("attackers", []))
            names = [self._card_name_from_battlefield(a) or str(a) for a in attackers]
            if not names:
                return "Attack with: (none)"
            return "Attack with: " + ", ".join(names)
        if t == ActionType.DECLARE_BLOCKERS:
            blocks = []
            if isinstance(action.targets, dict):
                blocks = list(action.targets.get("blocks", []))
            pairs = []
            for entry in blocks:
                if isinstance(entry, dict):
                    attacker_id = entry.get("attacker_id")
                    blocker_id = entry.get("blocker_id")
                elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                    attacker_id, blocker_id = entry[0], entry[1]
                else:
                    continue
                attacker_name = self._card_name_from_battlefield(attacker_id) or str(attacker_id)
                blocker_name = self._card_name_from_battlefield(blocker_id) or str(blocker_id)
                pairs.append(f"{blocker_name} -> {attacker_name}")
            if not pairs:
                return "Declare blockers: (none)"
            return "Declare blockers: " + ", ".join(pairs)
        if t == ActionType.PASS_PRIORITY:
            return "Pass priority"
        if t == ActionType.SCOOP:
            return "Scoop"
        if t == ActionType.SKIP_COMBAT:
            return "Skip combat"
        if t == ActionType.SKIP_MAIN2:
            return "Skip main 2"
        return str(t)

    def _format_target(self, target: Any) -> str:
        if isinstance(target, dict):
            if target.get("type") == "PLAYER":
                return f" -> {target.get('player_id')}"
            if target.get("type") == "PERMANENT":
                return f" -> {target.get('instance_id')}"
        return ""

    def _card_name_from_hand(self, instance_id: Any) -> Optional[str]:
        for ci in self._visible_state.zones.hand:
            if getattr(ci, "instance_id", None) == instance_id:
                return getattr(ci, "name", getattr(ci, "card_id", None))
        return None

    def _card_cost_from_hand(self, instance_id: Any) -> Optional[str]:
        for ci in self._visible_state.zones.hand:
            if getattr(ci, "instance_id", None) == instance_id:
                return self._format_mana_cost(getattr(ci, "mana_cost", None))
        return None

    def _format_mana_cost(self, mana_cost: object) -> str:
        if not isinstance(mana_cost, dict):
            return ""
        generic = mana_cost.get("generic", 0)
        colored = mana_cost.get("colored", {}) or {}
        parts = []
        if isinstance(generic, int) and generic > 0:
            parts.append(f"{{{generic}}}")
        order = ["WHITE", "BLUE", "BLACK", "RED", "GREEN"]
        symbols = {"WHITE": "W", "BLUE": "U", "BLACK": "B", "RED": "R", "GREEN": "G"}
        for color in order:
            count = int(colored.get(color, 0) or 0)
            if count > 0:
                parts.append("{" + symbols[color] + "}" * count)
        return "".join(parts)

    def _card_name_from_battlefield(self, instance_id: Any) -> Optional[str]:
        for perm in self._visible_state.zones.battlefield:
            if getattr(perm, "instance_id", None) == instance_id:
                return getattr(perm, "name", getattr(perm, "card_id", None))
        return None

    def _pt(self, obj: object) -> str:
        power = getattr(obj, "power", None)
        toughness = getattr(obj, "toughness", None)
        if power is None or toughness is None:
            return ""
        return f" ({power}/{toughness})"


def choose_action_index_tui(visible: VisibleState, actions: List[Action], player_id: str) -> Optional[int]:
    """
    Show a single-turn Textual UI and return the chosen action index (or None if user quit).
    """
    if not actions:
        return None
    app = _TurnApp(visible, actions, player_id)
    return app.run()


class _ReasoningApp(App[Optional[str]]):
    BINDINGS = [("escape", "quit", "Skip"), ("q", "quit", "Skip")]

    def __init__(self, action_label: str) -> None:
        super().__init__()
        self._action_label = action_label
        self.info = Static()
        self.input = Input(placeholder="Optional reasoning… (Enter to submit, Esc to skip)")
        self.footer = Footer()

    def compose(self) -> ComposeResult:
        yield self.info
        yield self.input
        yield self.footer

    def on_mount(self) -> None:
        text = Text(f"Action: {self._action_label}\nEnter reasoning for this play (optional).")
        self.info.update(Panel(text, title="Reasoning"))
        self.input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        self.exit(result=raw or None)

    def action_quit(self) -> None:
        self.exit(result=None)


def prompt_reasoning_tui(action: Action) -> Optional[str]:
    label = getattr(action.type, "value", str(action.type))
    if action.object_id:
        label = f"{label} ({action.object_id})"
    app = _ReasoningApp(label)
    return app.run()


class TUIPlayer:
    def __init__(
        self,
        engine: Any,
        player_id: str,
        *,
        on_action: Optional[Callable[[VisibleState, Action, Any, Optional[str]], None]] = None,
        collect_reasoning: bool = False,
    ):
        self.engine = engine
        self.player_id = player_id
        self.actions = ActionSurface()
        self.on_action = on_action
        self.collect_reasoning = collect_reasoning

    def loop(self) -> None:
        visible = self.engine.get_visible_state(self.player_id)
        actions = self.actions.get_legal_actions(visible, self.player_id)
        if not actions:
            return

        choice = choose_action_index_tui(visible, actions, self.player_id)
        if choice is None:
            return
        action = actions[choice]
        result = self.engine.submit_action(action)
        reasoning = None
        if self.collect_reasoning:
            reasoning = prompt_reasoning_tui(action)
        if self.on_action is not None:
            self.on_action(visible, action, result, reasoning)
        # Result is rendered in the next frame via VisibleState
