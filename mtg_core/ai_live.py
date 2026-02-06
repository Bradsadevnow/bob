# mtg_core/ai_live.py
from __future__ import annotations

import os
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple

from mtg_core.ai_broker import get_broker

from mtg_core.actions import Action, ActionType
from mtg_core.aibase import VisibleState


@dataclass(frozen=True)
class LiveAIDecision:
    action: Action
    reasoning: str
    raw_response: str
    prompt: Dict[str, Any]
    attempts: List[Dict[str, Any]]


class LiveAIDecider:
    """
    Live-game AI decision surface.

    - No engine access
    - No state mutation
    - Strict JSON I/O
    - Retry once, then fail
    """

    def __init__(self, model: str = "gpt-5.2", chat_client: Optional["ChatClient"] = None):
        self.model = model
        self.timeout = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "120"))
        self.chat_client = chat_client

    def decide_action(self, visible: VisibleState, action_schema: Dict[str, Any], player_id: str) -> LiveAIDecision:
        allowed = action_schema.get("allowed_actions", [])
        if not allowed:
            raise RuntimeError("No legal actions provided to LiveAIDecider")

        prompt = self._build_prompt(visible, action_schema, player_id)
        attempts: List[Dict[str, Any]] = []

        raw = self._call_llm_blocking(prompt)
        try:
            action, reasoning = self._parse_response(raw, action_schema, player_id)
            attempts.append({"raw": raw, "error": None})
            return LiveAIDecision(
                action=action,
                reasoning=reasoning,
                raw_response=raw,
                prompt=prompt,
                attempts=attempts,
            )
        except Exception as e:
            attempts.append({"raw": raw, "error": str(e)})

        retry_prompt = self._build_retry_prompt(prompt, attempts[-1]["error"])
        raw_retry = self._call_llm_blocking(retry_prompt)
        try:
            action, reasoning = self._parse_response(raw_retry, action_schema, player_id)
            attempts.append({"raw": raw_retry, "error": None})
            return LiveAIDecision(
                action=action,
                reasoning=reasoning,
                raw_response=raw_retry,
                prompt=retry_prompt,
                attempts=attempts,
            )
        except Exception as e:
            attempts.append({"raw": raw_retry, "error": str(e)})
            raise RuntimeError(f"Live AI failed twice: {attempts}") from e

    # ========================================================
    # Prompt + Parsing
    # ========================================================

    def _build_prompt(self, visible: VisibleState, action_schema: Dict[str, Any], player_id: str) -> Dict[str, Any]:
        return {
            "system": (
                "You are an AI making exactly one legal action in Magic: The Gathering.\n"
                "You MUST choose one of the allowed action types and obey the action schema.\n"
                "Pick object_id and targets directly from action_schema choices; do NOT invent ids.\n"
                "For targets, copy the full target object(s) (type/player_id/instance_id) exactly as given.\n"
                "Respond ONLY with valid JSON.\n"
                "Schema:\n"
                "{ \"type\": \"<ACTION_TYPE>\", \"object_id\": \"<string|null>\", "
                "\"targets\": <object|null>, \"payload\": <object|null>, \"reasoning\": \"<string>\" }"
            ),
            "payload": {
                "state": self._serialize_visible_state(visible),
                "player_id": player_id,
                "action_schema": action_schema,
            },
        }

    def _build_retry_prompt(self, base_prompt: Dict[str, Any], error: str) -> Dict[str, Any]:
        prompt = dict(base_prompt)
        prompt["system"] = (
            f"{base_prompt.get('system', '')}\n"
            f"Previous response was invalid: {error}\n"
            "Return ONLY valid JSON matching the schema."
        )
        return prompt

    def _serialize_visible_state(self, visible: VisibleState) -> Dict[str, Any]:
        zones = visible.zones
        battlefield = []
        for perm in zones.battlefield:
            battlefield.append(
                {
                    "instance_id": getattr(perm, "instance_id", None),
                    "card_id": getattr(perm, "card_id", None),
                    "owner_id": getattr(perm, "owner_id", None),
                    "controller_id": getattr(perm, "controller_id", None),
                    "tapped": getattr(perm, "tapped", None),
                    "damage_marked": getattr(perm, "damage_marked", None),
                }
            )

        hand = []
        for ci in zones.hand:
            hand.append(
                {
                    "instance_id": getattr(ci, "instance_id", None),
                    "card_id": getattr(ci, "card_id", None),
                }
            )

        graveyards = {}
        for pid, gy in zones.graveyards.items():
            if isinstance(gy, list):
                graveyards[pid] = [
                    {"instance_id": getattr(ci, "instance_id", None), "card_id": getattr(ci, "card_id", None)}
                    for ci in gy
                ]
            else:
                graveyards[pid] = gy

        exile = {}
        if isinstance(zones.exile, dict):
            for cid, card in zones.exile.items():
                exile[cid] = {
                    "instance_id": getattr(card, "instance_id", None),
                    "card_id": getattr(card, "card_id", None),
                    "owner_id": getattr(card, "owner_id", None),
                }
        else:
            exile = zones.exile

        stack = []
        if isinstance(zones.stack, list):
            for item in zones.stack:
                stack.append(
                    {
                        "instance_id": getattr(item, "instance_id", None),
                        "card_id": getattr(item, "card_id", None),
                        "controller_id": getattr(item, "controller_id", None),
                        "targets": getattr(item, "targets", None),
                    }
                )

        return {
            "turn_number": visible.turn_number,
            "active_player_id": visible.active_player_id,
            "phase": visible.phase,
            "priority_holder_id": visible.priority_holder_id,
            "life_totals": dict(visible.life_totals),
            "lands_played_this_turn": visible.lands_played_this_turn,
            "combat_attackers": list(getattr(visible, "combat_attackers", [])),
            "combat_blockers": dict(getattr(visible, "combat_blockers", {})),
            "combat_attackers_declared": bool(getattr(visible, "combat_attackers_declared", False)),
            "combat_blockers_declared": bool(getattr(visible, "combat_blockers_declared", False)),
            "zones": {
                "hand": hand,
                "battlefield": battlefield,
                "graveyards": graveyards,
                "exile": exile,
                "library_size": zones.library_size,
                "stack": stack,
            },
            "available_mana": visible.available_mana,
        }

    def _serialize_action(self, action: Action) -> Dict[str, Any]:
        action_type = getattr(action.type, "value", str(action.type))
        return {
            "type": action_type,
            "object_id": action.object_id,
            "targets": action.targets,
            "payload": action.payload,
        }

    def _parse_response(
        self, raw: str, action_schema: Dict[str, Any], player_id: str
    ) -> Tuple[Action, str]:
        data = _load_json(raw)
        action_type = data.get("type")
        object_id = data.get("object_id")
        targets = data.get("targets")
        payload = data.get("payload")
        reasoning = data.get("reasoning", "")
        extras = {
            k: v
            for k, v in data.items()
            if k not in {"type", "object_id", "targets", "payload", "reasoning"}
        }
        if extras:
            if payload is None:
                payload = dict(extras)
            elif isinstance(payload, dict):
                for key, value in extras.items():
                    payload.setdefault(key, value)

        if not isinstance(action_type, str):
            raise ValueError("type must be a string")

        try:
            action_enum = ActionType(action_type)
        except Exception as e:
            raise ValueError(f"Unknown action type: {action_type}") from e

        if reasoning is None:
            reasoning = ""
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)

        action = Action(
            type=action_enum,
            actor_id=player_id,
            object_id=object_id,
            targets=targets,
            payload=payload,
        )

        action = self._normalize_action(action, action_schema)
        action = self._validate_action_against_schema(action, action_schema)
        return action, reasoning

    def _normalize_action(self, action: Action, schema: Dict[str, Any]) -> Action:
        if action.type == ActionType.PLAY_LAND:
            return self._normalize_play_land(action, schema)
        if action.type == ActionType.TAP_FOR_MANA:
            return self._normalize_tap_for_mana(action, schema)
        if action.type == ActionType.CAST_SPELL:
            return self._normalize_cast_spell(action, schema)
        if action.type == ActionType.ACTIVATE_ABILITY:
            return self._normalize_activate_ability(action, schema)
        if action.type == ActionType.DECLARE_ATTACKERS:
            return self._normalize_attackers(action, schema)
        if action.type == ActionType.DECLARE_BLOCKERS:
            return self._normalize_blockers(action, schema)
        if action.type == ActionType.RESOLVE_DECISION:
            return self._normalize_resolve_decision(action, schema)
        return action

    def _validate_action_against_schema(self, action: Action, schema: Dict[str, Any]) -> Action:
        allowed = set(schema.get("allowed_actions", []))
        action_type = getattr(action.type, "value", str(action.type))
        if action_type not in allowed:
            raise ValueError(f"Action type not allowed: {action_type}")

        if action.type == ActionType.PLAY_LAND:
            choices = schema.get("play_land", {}).get("choices", [])
            valid_ids = {c.get("instance_id") for c in choices}
            if action.object_id not in valid_ids:
                raise ValueError("Invalid land selection")

        if action.type == ActionType.TAP_FOR_MANA:
            choices = schema.get("tap_for_mana", {}).get("choices", [])
            valid_ids = {c.get("instance_id") for c in choices}
            if action.object_id not in valid_ids:
                raise ValueError("Invalid mana source")

        if action.type == ActionType.CAST_SPELL:
            choices = schema.get("cast_spell", {}).get("choices", []) or []
            choices = [c for c in choices if c.get("instance_id") == action.object_id]
            if not choices:
                raise ValueError("Invalid spell selection")
            choice, resolved = self._match_choice_targets(action.targets, choices)
            if choice is None:
                raise ValueError("Invalid spell target")
            if resolved is not action.targets:
                action = Action(
                    type=action.type,
                    actor_id=action.actor_id,
                    object_id=action.object_id,
                    targets=resolved,
                    payload=action.payload,
                )

        if action.type == ActionType.ACTIVATE_ABILITY:
            choices = schema.get("activate_ability", {}).get("choices", []) or []
            choices = [c for c in choices if c.get("instance_id") == action.object_id]
            if not choices:
                raise ValueError("Invalid ability selection")
            payload = action.payload if isinstance(action.payload, dict) else {}
            ability_index = payload.get("ability_index")
            if not isinstance(ability_index, int):
                raise ValueError("Missing ability_index")
            choices = [c for c in choices if c.get("ability_index") == ability_index]
            if not choices:
                raise ValueError("Invalid ability selection")
            choice, resolved = self._match_choice_targets(action.targets, choices)
            if choice is None:
                raise ValueError("Invalid ability target")
            if resolved is not action.targets:
                action = Action(
                    type=action.type,
                    actor_id=action.actor_id,
                    object_id=action.object_id,
                    targets=resolved,
                    payload=action.payload,
                )
            cost_choices = choice.get("cost_choices") or []
            if cost_choices:
                costs = payload.get("costs")
                if costs is not None and not self._costs_match_any(costs, cost_choices):
                    raise ValueError("Invalid ability costs")

        if action.type == ActionType.DECLARE_ATTACKERS:
            attackers = schema.get("declare_attackers", {}).get("attackers", [])
            valid_ids = {c.get("instance_id") for c in attackers}
            chosen = []
            if isinstance(action.targets, dict):
                chosen = list(action.targets.get("attackers", []))
            normalized = []
            for entry in chosen:
                if isinstance(entry, dict):
                    normalized.append(entry.get("instance_id"))
                elif isinstance(entry, (list, tuple)) and entry:
                    normalized.append(entry[0])
                else:
                    normalized.append(entry)
            if any(a not in valid_ids for a in normalized):
                raise ValueError("Invalid attacker selection")
            if len(set(normalized)) != len(normalized):
                raise ValueError("Duplicate attackers")

        if action.type == ActionType.DECLARE_BLOCKERS:
            attackers = set(schema.get("declare_blockers", {}).get("attackers", []))
            blockers = schema.get("declare_blockers", {}).get("blockers", [])
            valid_blockers = {b.get("instance_id") for b in blockers}
            blocks = []
            if isinstance(action.targets, dict):
                blocks = list(action.targets.get("blocks", []))
            used_blockers = set()
            used_attackers = set()
            for entry in blocks:
                attacker_id, blocker_id = self._normalize_block_entry(entry)
                if attacker_id is None or blocker_id is None:
                    raise ValueError("Invalid block entry")
                if attacker_id not in attackers:
                    raise ValueError("Invalid attacker in blocks")
                if blocker_id not in valid_blockers:
                    raise ValueError("Invalid blocker in blocks")
                if attacker_id in used_attackers:
                    raise ValueError("Duplicate attacker in blocks")
                if blocker_id in used_blockers:
                    raise ValueError("Duplicate blocker in blocks")
                used_attackers.add(attacker_id)
                used_blockers.add(blocker_id)

        if action.type == ActionType.RESOLVE_DECISION:
            payload = action.payload if isinstance(action.payload, dict) else {}
            choice = payload.get("choice")
            options = schema.get("resolve_decision", {}).get("options")
            if isinstance(options, list):
                if choice not in options:
                    raise ValueError("Invalid decision choice")
            elif options is not None:
                if choice != options:
                    raise ValueError("Invalid decision choice")

        return action

    def _normalize_play_land(self, action: Action, schema: Dict[str, Any]) -> Action:
        choices = schema.get("play_land", {}).get("choices", []) or []
        object_id = self._normalize_object_id_from_choices(action.object_id, choices, action.payload)
        payload, payload_changed = self._ensure_payload_dict(action.payload)
        if object_id and payload is not None:
            choice = next((c for c in choices if c.get("instance_id") == object_id), None)
            if choice and choice.get("card_id") is not None and "card_id" not in payload:
                payload["card_id"] = choice.get("card_id")
                payload_changed = True
        if object_id and payload is None:
            choice = next((c for c in choices if c.get("instance_id") == object_id), None)
            if choice and choice.get("card_id") is not None:
                payload = {"card_id": choice.get("card_id")}
                payload_changed = True
        return self._with_updates(action, object_id=object_id, payload=self._finalize_payload(action.payload, payload, payload_changed))

    def _normalize_tap_for_mana(self, action: Action, schema: Dict[str, Any]) -> Action:
        choices = schema.get("tap_for_mana", {}).get("choices", []) or []
        object_id = self._normalize_object_id_from_choices(action.object_id, choices, action.payload)
        payload, payload_changed = self._ensure_payload_dict(action.payload)
        if object_id and payload is not None:
            choice = next((c for c in choices if c.get("instance_id") == object_id), None)
            if choice:
                if choice.get("card_id") is not None and "card_id" not in payload:
                    payload["card_id"] = choice.get("card_id")
                    payload_changed = True
                if choice.get("produces") is not None and "produces" not in payload:
                    payload["produces"] = choice.get("produces")
                    payload_changed = True
        if object_id and payload is None:
            choice = next((c for c in choices if c.get("instance_id") == object_id), None)
            if choice:
                payload = {}
                if choice.get("card_id") is not None:
                    payload["card_id"] = choice.get("card_id")
                if choice.get("produces") is not None:
                    payload["produces"] = choice.get("produces")
                if payload:
                    payload_changed = True
        return self._with_updates(action, object_id=object_id, payload=self._finalize_payload(action.payload, payload, payload_changed))

    def _normalize_cast_spell(self, action: Action, schema: Dict[str, Any]) -> Action:
        choices = schema.get("cast_spell", {}).get("choices", []) or []
        object_id = self._normalize_object_id_from_choices(action.object_id, choices, action.payload)
        payload, payload_changed = self._ensure_payload_dict(action.payload)
        targets = action.targets

        payload, payload_changed, targets = self._extract_mode_from_targets(payload, payload_changed, targets)
        payload, payload_changed = self._normalize_cast_payload(payload, payload_changed)

        choices_for_instance = [c for c in choices if c.get("instance_id") == object_id] if object_id else []
        selected = None
        resolved_targets = targets
        if choices_for_instance:
            filtered = self._filter_cast_choices_by_payload(choices_for_instance, payload)
            selected, resolved_targets = self._match_choice_targets(targets, filtered or choices_for_instance)
            if selected is None:
                selected, resolved_targets = self._match_choice_targets(targets, choices_for_instance)

        if selected:
            payload, payload_changed = self._apply_cast_choice_payload(payload, payload_changed, selected)
        if resolved_targets is not None and resolved_targets is not targets:
            targets = resolved_targets

        return self._with_updates(
            action,
            object_id=object_id,
            targets=targets,
            payload=self._finalize_payload(action.payload, payload, payload_changed),
        )

    def _normalize_activate_ability(self, action: Action, schema: Dict[str, Any]) -> Action:
        choices = schema.get("activate_ability", {}).get("choices", []) or []
        object_id = self._normalize_object_id_from_choices(action.object_id, choices, action.payload)
        payload, payload_changed = self._ensure_payload_dict(action.payload)
        targets = action.targets

        payload, payload_changed = self._normalize_activate_payload(payload, payload_changed)

        if object_id is None and choices:
            if len(choices) == 1:
                object_id = choices[0].get("instance_id")

        if object_id:
            ability_choices = [c for c in choices if c.get("instance_id") == object_id]
        else:
            ability_choices = list(choices)

        ability_index = payload.get("ability_index") if payload is not None else None
        if ability_index is None and ability_choices and len(ability_choices) == 1:
            ability_index = ability_choices[0].get("ability_index")
            if payload is None:
                payload = {}
            payload["ability_index"] = ability_index
            payload_changed = True
        if isinstance(ability_index, str) and ability_index.isdigit():
            ability_index = int(ability_index)
            if payload is None:
                payload = {}
            payload["ability_index"] = ability_index
            payload_changed = True

        selected = None
        if ability_index is not None:
            ability_choices = [c for c in ability_choices if c.get("ability_index") == ability_index]
        if ability_choices:
            selected = ability_choices[0]

        if selected:
            cost_choices = selected.get("cost_choices") or []
            payload, payload_changed = self._apply_activate_costs(payload, payload_changed, cost_choices)
            selected, resolved_targets = self._match_choice_targets(targets, [selected])
            if resolved_targets is not None and resolved_targets is not targets:
                targets = resolved_targets
            if payload is None:
                payload = {}
            if "card_id" not in payload and selected.get("card_id") is not None:
                payload["card_id"] = selected.get("card_id")
                payload_changed = True

        return self._with_updates(
            action,
            object_id=object_id,
            targets=targets,
            payload=self._finalize_payload(action.payload, payload, payload_changed),
        )

    def _normalize_attackers(self, action: Action, schema: Dict[str, Any]) -> Action:
        attackers = schema.get("declare_attackers", {}).get("attackers", []) or []
        allowed_ids = [a.get("instance_id") for a in attackers if a.get("instance_id") is not None]
        sources = [action.targets]
        if isinstance(action.payload, dict) and action.payload:
            sources.append(action.payload.get("attackers"))
            sources.append(action.payload.get("targets"))
        attackers_list = None
        for source in sources:
            attackers_list = self._normalize_id_list(source, allowed_ids)
            if attackers_list:
                break
        if attackers_list is None:
            attackers_list = []
        return self._with_updates(action, targets={"attackers": attackers_list})

    def _normalize_blockers(self, action: Action, schema: Dict[str, Any]) -> Action:
        attackers = schema.get("declare_blockers", {}).get("attackers", []) or []
        blockers = schema.get("declare_blockers", {}).get("blockers", []) or []
        allowed_attackers = set(attackers)
        allowed_blockers = {b.get("instance_id") for b in blockers if b.get("instance_id") is not None}

        sources = [action.targets]
        if isinstance(action.payload, dict) and action.payload:
            sources.append(action.payload.get("blocks"))
            sources.append(action.payload.get("blockers"))

        blocks_raw = None
        for source in sources:
            if source is None:
                continue
            if isinstance(source, dict):
                if "blocks" in source:
                    blocks_raw = source.get("blocks")
                    break
                if "attacker_id" in source or "blocker_id" in source:
                    blocks_raw = [source]
                    break
                blocks_raw = [
                    {"attacker_id": key, "blocker_id": value}
                    for key, value in source.items()
                ]
                break
            blocks_raw = source
            break

        normalized_blocks: List[Dict[str, Any]] = []
        if blocks_raw:
            if isinstance(blocks_raw, dict):
                blocks_iter = [blocks_raw]
            else:
                blocks_iter = list(blocks_raw) if isinstance(blocks_raw, list) else [blocks_raw]
            for entry in blocks_iter:
                attacker_id, blocker_id = self._normalize_block_entry(entry)
                if attacker_id is None and blocker_id is None:
                    continue
                if attacker_id not in allowed_attackers and blocker_id in allowed_attackers and attacker_id in allowed_blockers:
                    attacker_id, blocker_id = blocker_id, attacker_id
                normalized_blocks.append(
                    {"attacker_id": attacker_id, "blocker_id": blocker_id}
                )

        return self._with_updates(action, targets={"blocks": normalized_blocks})

    def _normalize_resolve_decision(self, action: Action, schema: Dict[str, Any]) -> Action:
        payload, payload_changed = self._ensure_payload_dict(action.payload)
        choice = payload.get("choice") if payload is not None else None
        if choice is None:
            if action.object_id is not None:
                choice = action.object_id
            elif isinstance(action.targets, dict) and "choice" in action.targets:
                choice = action.targets.get("choice")
            elif isinstance(action.targets, list) and action.targets:
                choice = action.targets[0]
            elif payload is not None and "option" in payload:
                choice = payload.get("option")
        options = schema.get("resolve_decision", {}).get("options")
        if isinstance(options, list):
            idx = self._coerce_index(choice)
            if idx is not None and 0 <= idx < len(options):
                choice = options[idx]
        if choice is not None:
            if payload is None:
                payload = {}
            if payload.get("choice") != choice:
                payload["choice"] = choice
                payload_changed = True
        return self._with_updates(action, payload=self._finalize_payload(action.payload, payload, payload_changed))

    def _normalize_object_id_from_choices(self, object_id: Any, choices: List[Dict[str, Any]], payload: Any) -> Any:
        if not choices:
            return object_id
        candidate_ids = [c.get("instance_id") for c in choices if c.get("instance_id") is not None]
        raw = self._coerce_instance_id(object_id)
        if raw in candidate_ids:
            return raw

        idx = self._coerce_index(raw)
        if idx is not None and 0 <= idx < len(choices):
            return choices[idx].get("instance_id")

        hints: List[Tuple[str, Any]] = []
        if isinstance(raw, str):
            hints.append(("card_id", raw))
            hints.append(("name", raw))
        if isinstance(object_id, dict):
            for key in ("instance_id", "card_id", "name"):
                if key in object_id:
                    hints.append((key, object_id.get(key)))
        if isinstance(payload, dict):
            for key in ("instance_id", "card_id", "name"):
                if key in payload:
                    hints.append((key, payload.get(key)))

        for field, value in hints:
            match_id = self._match_choice_by_field(choices, field, value)
            if match_id is not None:
                return match_id

        return object_id

    def _match_choice_by_field(self, choices: List[Dict[str, Any]], field: str, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            matches = [c for c in choices if isinstance(c.get(field), str) and c.get(field).lower() == value.lower()]
        else:
            matches = [c for c in choices if c.get(field) == value]
        if len(matches) == 1:
            return matches[0].get("instance_id")
        return None

    def _coerce_instance_id(self, value: Any) -> Any:
        if isinstance(value, dict):
            for key in ("instance_id", "id"):
                if key in value:
                    return value.get(key)
        if isinstance(value, list) and value:
            return self._coerce_instance_id(value[0])
        return value

    def _coerce_index(self, value: Any) -> Optional[int]:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    def _normalize_id_list(self, value: Any, allowed_ids: List[Any]) -> List[Any]:
        if value is None:
            return []
        if isinstance(value, dict):
            value = value.get("attackers", value.get("targets", value.get("ids", [])))
        if not isinstance(value, (list, tuple)):
            value = [value]
        ids: List[Any] = []
        for entry in value:
            if isinstance(entry, dict):
                entry_id = entry.get("instance_id") or entry.get("id") or entry.get("attacker_id")
                ids.append(entry_id)
            elif isinstance(entry, (list, tuple)) and entry:
                ids.append(entry[0])
            else:
                ids.append(entry)
        normalized: List[Any] = []
        for entry in ids:
            if entry in allowed_ids:
                normalized.append(entry)
                continue
            idx = self._coerce_index(entry)
            if idx is not None and 0 <= idx < len(allowed_ids):
                normalized.append(allowed_ids[idx])
            else:
                normalized.append(entry)
        return normalized

    def _extract_mode_from_targets(
        self, payload: Optional[Dict[str, Any]], payload_changed: bool, targets: Any
    ) -> Tuple[Optional[Dict[str, Any]], bool, Any]:
        def apply_mode_info(source: Dict[str, Any]) -> None:
            nonlocal payload, payload_changed
            if payload is None:
                payload = {}
            for key in ("mode_index", "mode_indices"):
                if key in source and key not in payload:
                    payload[key] = source.get(key)
                    payload_changed = True
            if "mode_payload" in source and isinstance(source.get("mode_payload"), dict):
                mp = source.get("mode_payload") or {}
                if "mode_indices" in mp and "mode_indices" not in payload:
                    payload["mode_indices"] = mp.get("mode_indices")
                    payload_changed = True

        if isinstance(targets, dict):
            if "targets" in targets and not self._dict_has_target_keys(targets):
                apply_mode_info(targets)
                targets = targets.get("targets")
        elif isinstance(targets, list) and len(targets) == 1 and isinstance(targets[0], dict):
            inner = targets[0]
            if "targets" in inner and not self._dict_has_target_keys(inner):
                apply_mode_info(inner)
                targets = inner.get("targets")
        return payload, payload_changed, targets

    def _normalize_cast_payload(
        self, payload: Optional[Dict[str, Any]], payload_changed: bool
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        if payload is None:
            return payload, payload_changed
        if "mode_index" in payload and "mode_indices" not in payload:
            payload["mode_indices"] = [payload.pop("mode_index")]
            payload_changed = True
        if "mode_indices" in payload and not isinstance(payload["mode_indices"], list):
            payload["mode_indices"] = [payload["mode_indices"]]
            payload_changed = True
        if "mode_payload" in payload and isinstance(payload.get("mode_payload"), dict):
            mp = payload.get("mode_payload") or {}
            if "mode_indices" in mp and "mode_indices" not in payload:
                payload["mode_indices"] = mp.get("mode_indices")
                payload_changed = True
        if "mode_indices" in payload:
            cleaned = []
            for entry in payload.get("mode_indices") or []:
                idx = self._coerce_index(entry)
                cleaned.append(idx if idx is not None else entry)
            payload["mode_indices"] = cleaned
            payload_changed = True
        if "x" in payload:
            x_val = payload.get("x")
            idx = self._coerce_index(x_val)
            if idx is not None:
                payload["x"] = idx
                payload_changed = True
        if isinstance(payload.get("additional_costs"), list):
            costs_list = payload.get("additional_costs") or []
            if len(costs_list) == 1:
                payload["additional_costs"] = costs_list[0]
                payload_changed = True
        return payload, payload_changed

    def _filter_cast_choices_by_payload(
        self, choices: List[Dict[str, Any]], payload: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not choices or not isinstance(payload, dict):
            return choices
        filtered = list(choices)
        if "mode_indices" in payload:
            filtered = [
                c for c in filtered
                if isinstance(c.get("mode_payload"), dict)
                and c.get("mode_payload", {}).get("mode_indices") == payload.get("mode_indices")
            ]
        if "alternate_cost" in payload:
            filtered = [c for c in filtered if c.get("alternate_cost") == payload.get("alternate_cost")]
        if "flashback" in payload:
            filtered = [c for c in filtered if bool(c.get("flashback")) == bool(payload.get("flashback"))]
        if "x" in payload:
            filtered = [c for c in filtered if payload.get("x") in (c.get("x_values") or [])]
        if "additional_costs" in payload:
            filtered = [
                c for c in filtered
                if payload.get("additional_costs") in (c.get("additional_costs") or [])
            ]
        return filtered

    def _apply_cast_choice_payload(
        self, payload: Optional[Dict[str, Any]], payload_changed: bool, choice: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        if payload is None:
            payload = {}
        if "mode_payload" in choice and isinstance(choice.get("mode_payload"), dict):
            mp = choice.get("mode_payload") or {}
            if "mode_indices" in mp and "mode_indices" not in payload:
                payload["mode_indices"] = mp.get("mode_indices")
                payload_changed = True
        if "alternate_cost" in choice and "alternate_cost" not in payload:
            payload["alternate_cost"] = choice.get("alternate_cost")
            payload_changed = True
        if choice.get("flashback") and "flashback" not in payload:
            payload["flashback"] = True
            payload_changed = True
        if "additional_costs" not in payload:
            costs = choice.get("additional_costs") or []
            if len(costs) == 1:
                payload["additional_costs"] = costs[0]
                payload_changed = True
        if "card_id" not in payload and choice.get("card_id") is not None:
            payload["card_id"] = choice.get("card_id")
            payload_changed = True
        return payload, payload_changed

    def _normalize_activate_payload(
        self, payload: Optional[Dict[str, Any]], payload_changed: bool
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        if payload is None:
            return payload, payload_changed
        if "ability_index" not in payload:
            for key in ("ability", "ability_idx", "index"):
                if key in payload:
                    payload["ability_index"] = payload.get(key)
                    payload_changed = True
                    break
        if "ability_index" in payload:
            idx = self._coerce_index(payload.get("ability_index"))
            if idx is not None:
                payload["ability_index"] = idx
                payload_changed = True
        if "costs" not in payload:
            costs = {}
            for key in ("discard", "sacrifice", "sacrifice_self", "pay_life", "tap"):
                if key in payload:
                    costs[key] = payload.get(key)
            if costs:
                payload["costs"] = costs
                payload_changed = True
        return payload, payload_changed

    def _apply_activate_costs(
        self, payload: Optional[Dict[str, Any]], payload_changed: bool, cost_choices: List[Dict[str, Any]]
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        if payload is None:
            payload = {}
        costs = payload.get("costs") if isinstance(payload.get("costs"), dict) else None
        if costs is None:
            if len(cost_choices) == 1:
                payload["costs"] = cost_choices[0]
                payload_changed = True
            return payload, payload_changed
        if self._costs_match_any(costs, cost_choices):
            for choice in cost_choices:
                if self._costs_match(costs, choice):
                    if costs != choice:
                        payload["costs"] = choice
                        payload_changed = True
                    break
        return payload, payload_changed

    def _match_choice_targets(
        self, targets: Any, choices: List[Dict[str, Any]]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Any]]:
        if not choices:
            return None, None
        normalized = self._normalize_target_groups(targets)
        for choice in choices:
            allowed_targets = choice.get("targets") or []
            if allowed_targets:
                resolved = self._resolve_targets_from_candidates(targets, list(allowed_targets))
                if resolved is not None:
                    return choice, resolved
            else:
                if normalized is not None and not self._groups_have_targets(normalized):
                    return choice, None
        return None, None

    def _costs_match_any(self, costs: Any, choices: List[Dict[str, Any]]) -> bool:
        if not isinstance(costs, dict):
            return False
        for choice in choices:
            if self._costs_match(costs, choice):
                return True
        return False

    def _costs_match(self, costs: Dict[str, Any], choice: Dict[str, Any]) -> bool:
        for key in ("sacrifice_self", "tap", "pay_life"):
            if key in costs and costs.get(key) != choice.get(key):
                return False
        for key in ("discard", "sacrifice"):
            if key in costs:
                if sorted(costs.get(key) or []) != sorted(choice.get(key) or []):
                    return False
        return True

    def _ensure_payload_dict(self, payload: Any) -> Tuple[Optional[Dict[str, Any]], bool]:
        if isinstance(payload, dict):
            return dict(payload), False
        return None, False

    def _finalize_payload(self, original: Any, payload: Optional[Dict[str, Any]], changed: bool) -> Any:
        if payload is None:
            return original
        if not changed and not isinstance(original, dict) and payload == {}:
            return original
        if changed or not isinstance(original, dict):
            return payload
        return original

    def _with_updates(
        self,
        action: Action,
        object_id: Any = None,
        targets: Any = None,
        payload: Any = None,
    ) -> Action:
        if object_id is None:
            object_id = action.object_id
        if targets is None:
            targets = action.targets
        if payload is None:
            payload = action.payload
        if object_id == action.object_id and targets == action.targets and payload == action.payload:
            return action
        return Action(
            type=action.type,
            actor_id=action.actor_id,
            object_id=object_id,
            targets=targets,
            payload=payload,
        )

    def _resolve_targets_from_candidates(self, targets: Any, candidates: List[Any]) -> Optional[Any]:
        normalized = self._normalize_target_groups(targets)
        if normalized is None:
            return None

        if not self._groups_have_targets(normalized):
            for candidate in candidates:
                candidate_groups = self._normalize_target_groups(candidate)
                if candidate_groups is None:
                    continue
                if not self._groups_have_targets(candidate_groups):
                    return candidate
            return None

        for candidate in candidates:
            candidate_groups = self._normalize_target_groups(candidate)
            if candidate_groups is None:
                continue
            if self._target_groups_match(normalized, candidate_groups):
                return candidate
        return None

    def _normalize_target_groups(self, targets: Any) -> Optional[List[List[Dict[str, Any]]]]:
        targets = self._unwrap_target_wrappers(targets)
        if targets is None:
            return []

        if isinstance(targets, dict):
            entry = self._normalize_target_entry(targets)
            if entry is None:
                return None
            return [[entry]]

        if isinstance(targets, list):
            if not targets:
                return [[]]
            if all(isinstance(t, dict) for t in targets):
                group: List[Dict[str, Any]] = []
                for t in targets:
                    entry = self._normalize_target_entry(t)
                    if entry is None:
                        return None
                    group.append(entry)
                return [group]
            if all(isinstance(t, list) for t in targets):
                groups: List[List[Dict[str, Any]]] = []
                for group_raw in targets:
                    if not group_raw:
                        groups.append([])
                        continue
                    group: List[Dict[str, Any]] = []
                    for entry_raw in group_raw:
                        entry = self._normalize_target_entry(entry_raw)
                        if entry is None:
                            return None
                        group.append(entry)
                    groups.append(group)
                return groups
        return None

    def _unwrap_target_wrappers(self, targets: Any) -> Any:
        if isinstance(targets, dict):
            if "targets" in targets and not self._dict_has_target_keys(targets):
                return targets.get("targets")
        if isinstance(targets, list) and len(targets) == 1 and isinstance(targets[0], dict):
            inner = targets[0]
            if "targets" in inner and not self._dict_has_target_keys(inner):
                return inner.get("targets")
        return targets

    def _dict_has_target_keys(self, payload: Dict[str, Any]) -> bool:
        return any(k in payload for k in ("type", "instance_id", "player_id", "id"))

    def _normalize_target_entry(self, entry: Any) -> Optional[Dict[str, Any]]:
        if isinstance(entry, dict):
            if self._dict_has_target_keys(entry):
                return entry
            for value in entry.values():
                if isinstance(value, dict) and self._dict_has_target_keys(value):
                    return value
            return None
        if isinstance(entry, list) and entry and isinstance(entry[0], dict):
            return self._normalize_target_entry(entry[0])
        if isinstance(entry, str):
            return {"id": entry}
        return None

    def _groups_have_targets(self, groups: List[List[Dict[str, Any]]]) -> bool:
        return any(group for group in groups)

    def _target_groups_match(
        self,
        provided: List[List[Dict[str, Any]]],
        candidates: List[List[Dict[str, Any]]],
    ) -> bool:
        if len(provided) != len(candidates):
            return False
        for provided_group, candidate_group in zip(provided, candidates):
            if len(provided_group) != len(candidate_group):
                return False
            if not self._group_matches_candidates(provided_group, candidate_group):
                return False
        return True

    def _group_matches_candidates(
        self,
        provided_group: List[Dict[str, Any]],
        candidate_group: List[Dict[str, Any]],
    ) -> bool:
        used = [False] * len(candidate_group)
        for provided in provided_group:
            matched = False
            for idx, candidate in enumerate(candidate_group):
                if used[idx]:
                    continue
                if self._target_matches_candidate(provided, candidate):
                    used[idx] = True
                    matched = True
                    break
            if not matched:
                return False
        return True

    def _target_matches_candidate(self, provided: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
        if "id" in provided and provided.get("id") is not None:
            pid = provided.get("id")
            return candidate.get("instance_id") == pid or candidate.get("player_id") == pid

        if "type" in provided and provided.get("type") is not None:
            if str(candidate.get("type")).upper() != str(provided.get("type")).upper():
                return False
        for key in ("instance_id", "player_id", "role", "zone"):
            if key in provided and provided.get(key) is not None:
                if candidate.get(key) != provided.get(key):
                    return False
        return True

    def _normalize_block_entry(self, entry: Any) -> Tuple[Optional[str], Optional[str]]:
        if isinstance(entry, dict):
            attacker = entry.get("attacker_id") or entry.get("attacker")
            blocker = entry.get("blocker_id") or entry.get("blocker")
            if attacker is None and blocker is None and len(entry) == 1:
                key, value = next(iter(entry.items()))
                attacker = key
                blocker = value
            return self._coerce_instance_id(attacker), self._coerce_instance_id(blocker)
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            return self._coerce_instance_id(entry[0]), self._coerce_instance_id(entry[1])
        if isinstance(entry, str):
            if "->" in entry:
                left, right = entry.split("->", 1)
                return left.strip(), right.strip()
        return None, None

    # ========================================================
    # LLM Boundary
    # ========================================================

    def _call_llm_blocking(self, prompt: Dict[str, Any]) -> str:
        if self.chat_client is not None:
            return self.chat_client.chat_text(
                messages=[
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user", "content": json.dumps(prompt["payload"])},
                ],
                temperature=0.6,
                max_tokens=300,
                timeout_s=self.timeout,
            )

        broker = get_broker()
        return broker.responses_create_text(
            model=self.model,
            input=[
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": json.dumps(prompt["payload"])},
            ],
            temperature=0.6,
            max_output_tokens=300,
            timeout=self.timeout,
        )


class ChatClient(Protocol):
    def chat_text(self, *, messages: list[dict[str, Any]], temperature: float, max_tokens: int, timeout_s: int) -> str: ...


def _strip_code_fences(raw: str) -> str:
    text = (raw or "").strip()
    if "```" not in text:
        return text
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("```"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _extract_json_object(raw: str) -> str:
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def _load_json(raw: str) -> Dict[str, Any]:
    text = _strip_code_fences(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    text = _extract_json_object(text)
    return json.loads(text)
