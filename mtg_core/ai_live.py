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

        self._validate_action_against_schema(action, action_schema)
        return action, reasoning

    def _validate_action_against_schema(self, action: Action, schema: Dict[str, Any]) -> None:
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
            choices = schema.get("cast_spell", {}).get("choices", [])
            choice = next((c for c in choices if c.get("instance_id") == action.object_id), None)
            if choice is None:
                raise ValueError("Invalid spell selection")
            allowed_targets = choice.get("targets") or []
            if allowed_targets:
                if not self._target_in_candidates(action.targets, allowed_targets):
                    raise ValueError("Invalid spell target")

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

    def _target_in_candidates(self, target: Any, candidates: List[Dict[str, Any]]) -> bool:
        target_dict = self._normalize_target(target)
        if target_dict is None:
            return False
        target_key = self._target_key(target_dict)
        return any(self._target_key(c) == target_key for c in candidates)

    def _target_key(self, target: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
        return (
            str(target.get("type")),
            target.get("player_id"),
            target.get("instance_id"),
        )

    def _normalize_target(self, target: Any) -> Optional[Dict[str, Any]]:
        if isinstance(target, dict):
            if "type" in target:
                return target
            # handle shapes like {"target_1": {...}}
            for value in target.values():
                if isinstance(value, dict) and "type" in value:
                    return value
        if isinstance(target, list) and target:
            if isinstance(target[0], dict):
                return target[0]
        return None

    def _normalize_block_entry(self, entry: Any) -> Tuple[Optional[str], Optional[str]]:
        if isinstance(entry, dict):
            return entry.get("attacker_id"), entry.get("blocker_id")
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            return entry[0], entry[1]
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
