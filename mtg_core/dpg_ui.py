from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from mtg_core.actions import Action, ActionType
from mtg_core.action_surface import ActionSurface
from mtg_core.aibase import VisibleState, ResolutionResult

try:
    import dearpygui.dearpygui as dpg
except Exception as e:  # pragma: no cover - import-time guard
    dpg = None
    _DPG_IMPORT_ERROR = e


class HoldPriorityGate:
    def __init__(self) -> None:
        self.active = False


@dataclass
class DiscussionCallbacks:
    start: Optional[Callable[[], Optional[str]]] = None
    send: Optional[Callable[[str], Optional[str]]] = None


class _ArtCache:
    def __init__(self, runtime_dir: str, game_id: str) -> None:
        self.base_dir = os.path.join(runtime_dir, "mtg", game_id, "art")
        os.makedirs(self.base_dir, exist_ok=True)
        self._failed: set[str] = set()

    def get_path(self, card_id: str) -> str:
        safe = card_id.replace("/", "_")
        return os.path.join(self.base_dir, f"{safe}.jpg")

    def fetch(self, card_name: str, card_id: str) -> Optional[str]:
        path = self.get_path(card_id)
        if os.path.exists(path):
            return path
        if card_id in self._failed:
            return None
        try:
            import requests

            url = "https://api.scryfall.com/cards/named"
            resp = requests.get(url, params={"exact": card_name}, timeout=8)
            if resp.status_code != 200:
                self._failed.add(card_id)
                return None
            data = resp.json()
            image = (data.get("image_uris") or {}).get("small")
            if not image:
                self._failed.add(card_id)
                return None
            img_resp = requests.get(image, timeout=8)
            if img_resp.status_code != 200:
                self._failed.add(card_id)
                return None
            with open(path, "wb") as f:
                f.write(img_resp.content)
            return path
        except Exception:
            self._failed.add(card_id)
            return None

    def clear(self) -> None:
        try:
            for name in os.listdir(self.base_dir):
                os.remove(os.path.join(self.base_dir, name))
        except Exception:
            pass


class _DPGPlaytestUI:
    TAG_ROOT = "mtg_playtest_root"
    TAG_BF_OPP = "mtg_bf_opp"
    TAG_BF_YOU = "mtg_bf_you"
    TAG_HAND = "mtg_hand"
    TAG_STACK = "mtg_stack"
    TAG_INFO = "mtg_info"
    TAG_DISCUSS = "mtg_discuss"
    TAG_ACTIONS = "mtg_actions"
    TAG_STATUS = "mtg_status"
    TAG_SELECTED = "mtg_selected_target"
    TAG_REASONING = "mtg_reasoning"
    TAG_HOLD = "mtg_hold_priority"
    TAG_DISCUSS_LOG = "mtg_discuss_log"
    TAG_DISCUSS_INPUT = "mtg_discuss_input"

    def __init__(self) -> None:
        if dpg is None:
            raise RuntimeError(f"Dear PyGui is not available: {_DPG_IMPORT_ERROR}")

        self._visible: Optional[VisibleState] = None
        self._schema: Dict[str, Any] = {}
        self._player_id: Optional[str] = None
        self._pending_action: Optional[Action] = None
        self._selected_target: Optional[Dict[str, Any]] = None
        self._perm_label_by_id: Dict[str, str] = {}
        self._attacker_tags: List[tuple[str, str]] = []
        self._blocks: List[Dict[str, str]] = []
        self._discussion_log: List[str] = []
        self._discussion_callbacks = DiscussionCallbacks()
        self._hold_gate: Optional[HoldPriorityGate] = None
        self._art_cache: Optional[_ArtCache] = None
        self._textures: Dict[str, int] = {}
        self._cast_choice_by_instance: Dict[str, Dict[str, Any]] = {}

        dpg.create_context()
        dpg.create_viewport(title="MTG Playtest UI v0", width=1200, height=900)

        with dpg.window(tag=self.TAG_ROOT, label="MTG Playtest UI v0"):
            dpg.add_text("Battlefield - Opponent")
            dpg.add_child_window(tag=self.TAG_BF_OPP, height=160, border=True)
            dpg.add_text("Battlefield - You")
            dpg.add_child_window(tag=self.TAG_BF_YOU, height=160, border=True)
            dpg.add_text("Hand")
            dpg.add_child_window(tag=self.TAG_HAND, height=170, border=True)

            with dpg.group(horizontal=True):
                dpg.add_child_window(tag=self.TAG_STACK, width=360, height=200, border=True)
                dpg.add_child_window(tag=self.TAG_INFO, width=360, height=200, border=True)
                dpg.add_child_window(tag=self.TAG_DISCUSS, width=360, height=200, border=True)

            dpg.add_text("Actions")
            dpg.add_child_window(tag=self.TAG_ACTIONS, height=220, border=True)
            dpg.add_text("", tag=self.TAG_STATUS)

        with dpg.texture_registry(tag="mtg_textures"):
            pass

        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window(self.TAG_ROOT, True)

    def set_hold_gate(self, gate: Optional[HoldPriorityGate]) -> None:
        self._hold_gate = gate

    def set_discussion_callbacks(
        self,
        *,
        start: Optional[Callable[[], Optional[str]]] = None,
        send: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self._discussion_callbacks = DiscussionCallbacks(start=start, send=send)

    def configure_game(self, *, runtime_dir: Optional[str], game_id: Optional[str]) -> None:
        if runtime_dir and game_id:
            self._art_cache = _ArtCache(runtime_dir, game_id)
            self._textures.clear()

    def set_state(self, visible: VisibleState, schema: Dict[str, Any], player_id: str) -> None:
        self._visible = visible
        self._schema = schema
        self._player_id = player_id
        self._pending_action = None
        self._selected_target = None
        self._attacker_tags = []
        self._blocks = []
        self._cast_choice_by_instance = {
            c.get("instance_id"): c for c in schema.get("cast_spell", {}).get("choices", []) if c.get("instance_id")
        }
        self._perm_label_by_id = self._build_perm_label_map(visible)
        self._refresh_info()
        self._refresh_battlefield()
        self._refresh_hand()
        self._refresh_stack()
        self._refresh_discuss_panel()
        self._refresh_actions()

    def pump(self) -> None:
        if not dpg.is_dearpygui_running():
            raise SystemExit(0)
        self._sync_hold_gate()
        dpg.render_dearpygui_frame()

    def wait_for_action(self) -> Action:
        while self._pending_action is None:
            self.pump()
        return self._pending_action

    def consume_reasoning(self) -> Optional[str]:
        if not dpg.does_item_exist(self.TAG_REASONING):
            return None
        text = (dpg.get_value(self.TAG_REASONING) or "").strip()
        dpg.set_value(self.TAG_REASONING, "")
        return text or None

    def append_discussion(self, role: str, text: str) -> None:
        prefix = "You" if role == "user" else "Bob"
        self._discussion_log.append(f"{prefix}: {text}")
        self._render_discussion_log()

    def clear_discussion(self) -> None:
        self._discussion_log = []
        self._render_discussion_log()

    def shutdown(self) -> None:
        if self._art_cache is not None:
            self._art_cache.clear()
        dpg.destroy_context()

    # ----------------------------
    # Rendering
    # ----------------------------

    def _refresh_info(self) -> None:
        dpg.delete_item(self.TAG_INFO, children_only=True)
        if self._visible is None:
            return
        v = self._visible
        dpg.add_text(f"Turn: {v.turn_number}", parent=self.TAG_INFO)
        dpg.add_text(f"Step: {v.phase}", parent=self.TAG_INFO)
        dpg.add_text(f"Active: {v.active_player_id}", parent=self.TAG_INFO)
        dpg.add_text(f"Priority: {v.priority_holder_id}", parent=self.TAG_INFO)
        dpg.add_separator(parent=self.TAG_INFO)
        dpg.add_text("Life Totals:", parent=self.TAG_INFO)
        for pid, life in v.life_totals.items():
            label = f"{pid}: {life}"
            dpg.add_button(
                label=label,
                parent=self.TAG_INFO,
                callback=self._on_select_player,
                user_data=pid,
            )
        dpg.add_separator(parent=self.TAG_INFO)
        dpg.add_text("Selected target:", parent=self.TAG_INFO)
        dpg.add_text(self._format_selected_target(), parent=self.TAG_INFO, tag=self.TAG_SELECTED)

    def _refresh_battlefield(self) -> None:
        dpg.delete_item(self.TAG_BF_OPP, children_only=True)
        dpg.delete_item(self.TAG_BF_YOU, children_only=True)
        if self._visible is None or self._player_id is None:
            return
        v = self._visible
        for perm in v.zones.battlefield:
            parent = self.TAG_BF_YOU if getattr(perm, "controller_id", None) == self._player_id else self.TAG_BF_OPP
            instance_id = getattr(perm, "instance_id", None)
            tex = self._get_card_texture(getattr(perm, "card_id", ""), getattr(perm, "name", ""))
            with dpg.group(parent=parent):
                if tex is not None:
                    dpg.add_image_button(
                        tex,
                        width=70,
                        height=100,
                        callback=self._on_select_perm,
                        user_data=instance_id,
                    )
                else:
                    dpg.add_button(
                        label="",
                        width=70,
                        height=100,
                        callback=self._on_select_perm,
                        user_data=instance_id,
                    )
                stats = self._format_perm_stats(perm)
                if stats:
                    dpg.add_text(stats)

    def _refresh_hand(self) -> None:
        dpg.delete_item(self.TAG_HAND, children_only=True)
        if self._visible is None:
            return
        with dpg.group(horizontal=True, parent=self.TAG_HAND):
            for ci in self._visible.zones.hand:
                tex = self._get_card_texture(getattr(ci, "card_id", ""), getattr(ci, "name", ""))
                if tex is not None:
                    instance_id = getattr(ci, "instance_id", None)
                    if instance_id in self._cast_choice_by_instance:
                        dpg.add_image_button(
                            tex,
                            width=70,
                            height=100,
                            callback=self._on_cast_from_hand,
                            user_data=self._cast_choice_by_instance.get(instance_id),
                        )
                    else:
                        dpg.add_image(tex, width=70, height=100)
                else:
                    # Placeholder when art is missing; no card names in UI.
                    dpg.add_button(label="", width=70, height=100)

    def _refresh_stack(self) -> None:
        dpg.delete_item(self.TAG_STACK, children_only=True)
        if self._visible is None:
            return
        if not self._visible.zones.stack:
            dpg.add_text("(empty)", parent=self.TAG_STACK)
            return
        for item in self._visible.zones.stack:
            label = f"{getattr(item, 'name', getattr(item, 'card_id', '?'))}"
            target = getattr(item, "targets", None)
            if isinstance(target, dict):
                label += f" -> {self._format_target(target)}"
            dpg.add_text(label, parent=self.TAG_STACK)

    def _refresh_discuss_panel(self) -> None:
        dpg.delete_item(self.TAG_DISCUSS, children_only=True)
        dpg.add_checkbox(label="Hold priority", tag=self.TAG_HOLD, parent=self.TAG_DISCUSS)
        dpg.add_text("Reasoning (optional):", parent=self.TAG_DISCUSS)
        dpg.add_input_text(tag=self.TAG_REASONING, parent=self.TAG_DISCUSS)
        dpg.add_separator(parent=self.TAG_DISCUSS)
        dpg.add_text("Discuss with Bob:", parent=self.TAG_DISCUSS)
        dpg.add_child_window(tag=self.TAG_DISCUSS_LOG, height=80, parent=self.TAG_DISCUSS)
        dpg.add_input_text(tag=self.TAG_DISCUSS_INPUT, parent=self.TAG_DISCUSS)
        dpg.add_button(label="Discuss", parent=self.TAG_DISCUSS, callback=self._on_discuss_start)
        dpg.add_button(label="Send", parent=self.TAG_DISCUSS, callback=self._on_discuss_send)
        self._render_discussion_log()

    def _refresh_actions(self) -> None:
        dpg.delete_item(self.TAG_ACTIONS, children_only=True)
        if self._schema is None or self._player_id is None:
            return
        allowed = self._schema.get("allowed_actions", [])
        if not allowed:
            dpg.add_text("No legal actions.", parent=self.TAG_ACTIONS)
            return

        if ActionType.PLAY_LAND.value in allowed:
            dpg.add_text("Play Land", parent=self.TAG_ACTIONS)
            for choice in self._schema.get("play_land", {}).get("choices", []):
                label = f"Play {choice.get('name') or choice.get('card_id')}"
                action = Action(
                    ActionType.PLAY_LAND,
                    actor_id=self._player_id,
                    object_id=choice.get("instance_id"),
                    payload={"card_id": choice.get("card_id")},
                )
                dpg.add_button(label=label, parent=self.TAG_ACTIONS, callback=self._on_action_button, user_data=action)

        if ActionType.TAP_FOR_MANA.value in allowed:
            dpg.add_separator(parent=self.TAG_ACTIONS)
            dpg.add_text("Tap For Mana", parent=self.TAG_ACTIONS)
            for choice in self._schema.get("tap_for_mana", {}).get("choices", []):
                produces = choice.get("produces")
                label = f"Tap {choice.get('name') or choice.get('card_id')} -> {produces}"
                action = Action(
                    ActionType.TAP_FOR_MANA,
                    actor_id=self._player_id,
                    object_id=choice.get("instance_id"),
                    payload={"card_id": choice.get("card_id"), "produces": produces},
                )
                dpg.add_button(label=label, parent=self.TAG_ACTIONS, callback=self._on_action_button, user_data=action)

        if ActionType.CAST_SPELL.value in allowed:
            dpg.add_separator(parent=self.TAG_ACTIONS)
            dpg.add_text("Cast Spell", parent=self.TAG_ACTIONS)
            for choice in self._schema.get("cast_spell", {}).get("choices", []):
                card_name = choice.get("name") or choice.get("card_id")
                cost = self._format_mana_cost(choice.get("mana_cost"))
                targets = choice.get("targets") or []
                if targets:
                    combo_tag = f"cast_target_{choice.get('instance_id')}"
                    target_map = self._build_target_map(targets)
                    dpg.add_combo(
                        list(target_map.keys()),
                        parent=self.TAG_ACTIONS,
                        tag=combo_tag,
                        user_data=target_map,
                        width=220,
                    )
                    label = f"Cast {card_name} {cost}".strip()
                    dpg.add_button(
                        label=label,
                        parent=self.TAG_ACTIONS,
                        callback=self._on_cast_with_target,
                        user_data={
                            "instance_id": choice.get("instance_id"),
                            "card_id": choice.get("card_id"),
                            "combo_tag": combo_tag,
                        },
                    )
                else:
                    action = Action(
                        ActionType.CAST_SPELL,
                        actor_id=self._player_id,
                        object_id=choice.get("instance_id"),
                        payload={"card_id": choice.get("card_id")},
                    )
                    label = f"Cast {card_name} {cost}".strip()
                    dpg.add_button(label=label, parent=self.TAG_ACTIONS, callback=self._on_action_button, user_data=action)

        if ActionType.DECLARE_ATTACKERS.value in allowed:
            dpg.add_separator(parent=self.TAG_ACTIONS)
            dpg.add_text("Declare Attackers", parent=self.TAG_ACTIONS)
            self._attacker_tags = []
            for attacker in self._schema.get("declare_attackers", {}).get("attackers", []):
                instance_id = attacker.get("instance_id")
                tag = f"attacker_{instance_id}"
                self._attacker_tags.append((instance_id, tag))
                label = attacker.get("name") or instance_id
                dpg.add_checkbox(label=label, parent=self.TAG_ACTIONS, tag=tag)
            dpg.add_button(
                label="Submit Attackers",
                parent=self.TAG_ACTIONS,
                callback=self._on_submit_attackers,
            )

        if ActionType.DECLARE_BLOCKERS.value in allowed:
            dpg.add_separator(parent=self.TAG_ACTIONS)
            dpg.add_text("Declare Blockers", parent=self.TAG_ACTIONS)
            attacker_ids = self._schema.get("declare_blockers", {}).get("attackers", [])
            blocker_choices = self._schema.get("declare_blockers", {}).get("blockers", [])
            attacker_map = {self._label_for_perm(aid): aid for aid in attacker_ids}
            blocker_map = {self._label_for_perm(b.get("instance_id")): b.get("instance_id") for b in blocker_choices}
            dpg.add_combo(list(attacker_map.keys()), parent=self.TAG_ACTIONS, tag="block_attacker_combo", user_data=attacker_map)
            dpg.add_combo(list(blocker_map.keys()), parent=self.TAG_ACTIONS, tag="block_blocker_combo", user_data=blocker_map)
            dpg.add_button(label="Add Block", parent=self.TAG_ACTIONS, callback=self._on_add_block)
            dpg.add_child_window(tag="block_list", height=70, parent=self.TAG_ACTIONS)
            dpg.add_button(label="Submit Blocks", parent=self.TAG_ACTIONS, callback=self._on_submit_blocks)
            dpg.add_button(label="Clear Blocks", parent=self.TAG_ACTIONS, callback=self._on_clear_blocks)

        for action_type in (
            ActionType.SKIP_COMBAT,
            ActionType.SKIP_MAIN2,
            ActionType.PASS_PRIORITY,
            ActionType.SCOOP,
        ):
            if action_type.value in allowed:
                dpg.add_separator(parent=self.TAG_ACTIONS)
                action = Action(action_type, actor_id=self._player_id)
                dpg.add_button(
                    label=action_type.value.replace("_", " ").title(),
                    parent=self.TAG_ACTIONS,
                    callback=self._on_action_button,
                    user_data=action,
                )

    def _render_discussion_log(self) -> None:
        if not dpg.does_item_exist(self.TAG_DISCUSS_LOG):
            return
        dpg.delete_item(self.TAG_DISCUSS_LOG, children_only=True)
        if not self._discussion_log:
            dpg.add_text("(no messages)", parent=self.TAG_DISCUSS_LOG)
            return
        for line in self._discussion_log[-12:]:
            dpg.add_text(line, parent=self.TAG_DISCUSS_LOG)

    # ----------------------------
    # Callbacks
    # ----------------------------

    def _on_action_button(self, sender, app_data, user_data):  # type: ignore[override]
        self._set_pending_action(user_data)

    def _on_select_perm(self, sender, app_data, user_data):  # type: ignore[override]
        if user_data:
            self._selected_target = {"type": "PERMANENT", "instance_id": user_data}
            if dpg.does_item_exist(self.TAG_SELECTED):
                dpg.set_value(self.TAG_SELECTED, self._format_selected_target())

    def _on_select_player(self, sender, app_data, user_data):  # type: ignore[override]
        if user_data:
            self._selected_target = {"type": "PLAYER", "player_id": user_data}
            if dpg.does_item_exist(self.TAG_SELECTED):
                dpg.set_value(self.TAG_SELECTED, self._format_selected_target())

    def _on_cast_with_target(self, sender, app_data, user_data):  # type: ignore[override]
        combo_tag = user_data.get("combo_tag")
        if combo_tag is None:
            raise RuntimeError("Missing target combo for cast")
        label = dpg.get_value(combo_tag)
        target_map = dpg.get_item_user_data(combo_tag) or {}
        target = target_map.get(label)
        if target is None:
            raise RuntimeError("Cast spell requires a valid target")
        action = Action(
            ActionType.CAST_SPELL,
            actor_id=self._player_id or "",
            object_id=user_data.get("instance_id"),
            targets=target,
            payload={"card_id": user_data.get("card_id")},
        )
        self._set_pending_action(action)

    def _on_cast_from_hand(self, sender, app_data, user_data):  # type: ignore[override]
        if not isinstance(user_data, dict):
            return
        targets = user_data.get("targets") or []
        target = None
        if targets:
            if self._selected_target is None:
                raise RuntimeError("Cast spell requires a selected target")
            if self._selected_target not in targets:
                raise RuntimeError("Selected target not allowed for this spell")
            target = self._selected_target
        action = Action(
            ActionType.CAST_SPELL,
            actor_id=self._player_id or "",
            object_id=user_data.get("instance_id"),
            targets=target,
            payload={"card_id": user_data.get("card_id")},
        )
        self._set_pending_action(action)

    def _on_submit_attackers(self, sender, app_data, user_data=None):  # type: ignore[override]
        attackers = []
        for instance_id, tag in self._attacker_tags:
            if dpg.get_value(tag):
                attackers.append(instance_id)
        action = Action(
            ActionType.DECLARE_ATTACKERS,
            actor_id=self._player_id or "",
            targets={"attackers": attackers},
        )
        self._set_pending_action(action)

    def _on_add_block(self, sender, app_data, user_data=None):  # type: ignore[override]
        attacker_label = dpg.get_value("block_attacker_combo")
        blocker_label = dpg.get_value("block_blocker_combo")
        attacker_map = dpg.get_item_user_data("block_attacker_combo") or {}
        blocker_map = dpg.get_item_user_data("block_blocker_combo") or {}
        attacker_id = attacker_map.get(attacker_label)
        blocker_id = blocker_map.get(blocker_label)
        if not attacker_id or not blocker_id:
            raise RuntimeError("Blocker mapping requires both attacker and blocker")
        if any(b["attacker_id"] == attacker_id for b in self._blocks):
            raise RuntimeError("Attacker already blocked")
        if any(b["blocker_id"] == blocker_id for b in self._blocks):
            raise RuntimeError("Blocker already assigned")
        self._blocks.append({"attacker_id": attacker_id, "blocker_id": blocker_id})
        self._render_block_list()

    def _on_submit_blocks(self, sender, app_data, user_data=None):  # type: ignore[override]
        action = Action(
            ActionType.DECLARE_BLOCKERS,
            actor_id=self._player_id or "",
            targets={"blocks": list(self._blocks)},
        )
        self._set_pending_action(action)

    def _on_clear_blocks(self, sender, app_data, user_data=None):  # type: ignore[override]
        self._blocks = []
        self._render_block_list()

    def _on_discuss_start(self, sender, app_data, user_data=None):  # type: ignore[override]
        cb = self._discussion_callbacks.start
        if cb is None:
            return
        resp = cb()
        if resp:
            self.append_discussion("assistant", resp)

    def _on_discuss_send(self, sender, app_data, user_data=None):  # type: ignore[override]
        cb = self._discussion_callbacks.send
        if cb is None:
            return
        text = (dpg.get_value(self.TAG_DISCUSS_INPUT) or "").strip()
        if not text:
            return
        dpg.set_value(self.TAG_DISCUSS_INPUT, "")
        self.append_discussion("user", text)
        resp = cb(text)
        if resp:
            self.append_discussion("assistant", resp)

    # ----------------------------
    # Helpers
    # ----------------------------

    def _set_pending_action(self, action: Action) -> None:
        if self._pending_action is None:
            self._pending_action = action

    def _render_block_list(self) -> None:
        if not dpg.does_item_exist("block_list"):
            return
        dpg.delete_item("block_list", children_only=True)
        if not self._blocks:
            dpg.add_text("(no blocks)", parent="block_list")
            return
        for blk in self._blocks:
            a = self._label_for_perm(blk.get("attacker_id"))
            b = self._label_for_perm(blk.get("blocker_id"))
            dpg.add_text(f"{b} -> {a}", parent="block_list")

    def _build_perm_label_map(self, visible: VisibleState) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for perm in visible.zones.battlefield:
            instance_id = getattr(perm, "instance_id", None)
            if instance_id:
                out[instance_id] = self._format_perm_label(perm)
        return out

    def _label_for_perm(self, instance_id: Optional[str]) -> str:
        if not instance_id:
            return "?"
        return self._perm_label_by_id.get(instance_id, instance_id)

    def _format_perm_label(self, perm: Any) -> str:
        name = getattr(perm, "name", getattr(perm, "card_id", "?"))
        pt = ""
        power = getattr(perm, "power", None)
        toughness = getattr(perm, "toughness", None)
        if power is not None and toughness is not None:
            pt = f" [{power}/{toughness}]"
        tapped = " (tapped)" if getattr(perm, "tapped", False) else ""
        return f"{name}{pt}{tapped}"

    def _format_perm_stats(self, perm: Any) -> str:
        power = getattr(perm, "power", None)
        toughness = getattr(perm, "toughness", None)
        tapped = getattr(perm, "tapped", False)
        pt = ""
        if power is not None and toughness is not None:
            pt = f"{power}/{toughness}"
        if tapped:
            return f"{pt} T".strip()
        return pt

    def _format_card_label(self, ci: Any) -> str:
        name = getattr(ci, "name", getattr(ci, "card_id", "?"))
        cost = self._format_mana_cost(getattr(ci, "mana_cost", None))
        return f"{name} {cost}".strip()

    def _format_mana_cost(self, mana_cost: Any) -> str:
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
        return "".join(parts) if parts else "0"

    def _build_target_map(self, targets: List[Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if not targets:
            return out
        if all(isinstance(t, dict) for t in targets):
            combos: List[Any] = [targets]
        else:
            combos = list(targets)
        for combo in combos:
            combo_list = combo
            if isinstance(combo, dict):
                combo_list = [combo]
            label = self._format_target_group(combo_list)
            if label in out:
                label = f"{label} ({len(out)})"
            out[label] = combo_list
        return out

    def _format_target_group(self, targets: Any) -> str:
        if isinstance(targets, dict):
            return self._format_target(targets)
        if not isinstance(targets, list):
            return "Unknown target"
        labels = [self._format_target(t) for t in targets if isinstance(t, dict)]
        return " + ".join(labels) if labels else "Unknown target"

    def _format_target(self, target: Any) -> str:
        if not isinstance(target, dict):
            if isinstance(target, list):
                return self._format_target_group(target)
            return "Unknown target"
        if target.get("type") == "PLAYER":
            return f"Player {target.get('player_id')}"
        if target.get("type") == "PERMANENT":
            return self._label_for_perm(target.get("instance_id"))
        return "Unknown target"

    def _format_selected_target(self) -> str:
        if not self._selected_target:
            return "(none)"
        return self._format_target(self._selected_target)

    def _sync_hold_gate(self) -> None:
        if self._hold_gate is None or not dpg.does_item_exist(self.TAG_HOLD):
            return
        self._hold_gate.active = bool(dpg.get_value(self.TAG_HOLD))

    def _get_card_texture(self, card_id: str, card_name: str) -> Optional[int]:
        if not card_id or not card_name or self._art_cache is None:
            return None
        if card_id in self._textures:
            return self._textures[card_id]
        path = self._art_cache.fetch(card_name, card_id)
        if not path:
            return None
        width, height, channels, data = dpg.load_image(path)
        texture_id = dpg.add_static_texture(width, height, data, parent="mtg_textures")
        self._textures[card_id] = texture_id
        return texture_id


_UI_SINGLETON: Optional[_DPGPlaytestUI] = None


def get_playtest_ui() -> _DPGPlaytestUI:
    global _UI_SINGLETON
    if _UI_SINGLETON is None:
        _UI_SINGLETON = _DPGPlaytestUI()
    return _UI_SINGLETON


class DPGPlayer:
    """
    Dear PyGui control surface (POST-GAME-START ONLY).
    """

    def __init__(
        self,
        engine: Any,
        player_id: str,
        *,
        runtime_dir: Optional[str] = None,
        game_id: Optional[str] = None,
        hold_gate: Optional[HoldPriorityGate] = None,
        discussion_start: Optional[Callable[[], Optional[str]]] = None,
        discussion_send: Optional[Callable[[str], Optional[str]]] = None,
        on_action: Optional[Callable[[VisibleState, Action, ResolutionResult, Optional[str]], None]] = None,
    ) -> None:
        self.engine = engine
        self.player_id = player_id
        self.actions = ActionSurface()
        self.on_action = on_action
        self._discussion_start = discussion_start
        self._discussion_send = discussion_send
        self.ui = get_playtest_ui()
        self.ui.configure_game(runtime_dir=runtime_dir, game_id=game_id)
        self.ui.set_hold_gate(hold_gate)

    def loop(self) -> None:
        visible = self.engine.get_visible_state(self.player_id)
        schema = self.actions.get_action_schema(visible, self.player_id)
        self.ui.set_discussion_callbacks(start=self._discussion_start, send=self._discussion_send)
        self.ui.set_state(visible, schema, self.player_id)

        if not schema.get("allowed_actions"):
            self.ui.pump()
            return

        action = self.ui.wait_for_action()
        result = self.engine.submit_action(action)
        reasoning = self.ui.consume_reasoning()
        if self.on_action is not None:
            self.on_action(visible, action, result, reasoning)
        self.ui.clear_discussion()

    def shutdown(self) -> None:
        self.ui.shutdown()
