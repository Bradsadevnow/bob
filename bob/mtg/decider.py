from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from mtg_core.actions import Action, ActionType
from mtg_core.aibase import VisibleState

from bob.mtg.serialize import serialize_visible_state_minimal


@dataclass(frozen=True)
class LLMActionDecision:
    action: Action
    reasoning: str
    raw_response: str
    prompt_payload: Dict[str, Any]
    error: Optional[str] = None


class MtgActionDecider:
    """
    Strict JSON MTG decider using a Bob ChatClient.

    Contract:
    - chooses exactly one legal action according to `action_schema`
    - on invalid model output, falls back to PASS_PRIORITY if available, else first legal action
    """

    def __init__(self, chat_client: Any) -> None:
        self.chat = chat_client

    def decide(
        self,
        *,
        visible: VisibleState,
        action_schema: Dict[str, Any],
        player_id: str,
        temperature: float = 0.4,
        timeout_s: int = 120,
    ) -> LLMActionDecision:
        payload = {
            "player_id": player_id,
            "state": serialize_visible_state_minimal(visible),
            "action_schema": action_schema,
        }

        system = (
            "You are choosing exactly one legal action in Magic: The Gathering.\n"
            "You MUST choose one of action_schema.allowed_actions and obey its constraints.\n"
            "Respond ONLY with valid JSON (no markdown, no extra keys).\n"
            'Schema: {"type":"<ACTION_TYPE>","object_id":null|"<id>","targets":null|<object>,"payload":null|<object>,"reasoning":"<short>"}'
        )

        raw = self.chat.chat_text(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=float(temperature),
            max_tokens=300,
            timeout_s=int(timeout_s),
        ).strip()

        try:
            action, reasoning = self._parse_and_validate(raw, action_schema, player_id)
            return LLMActionDecision(
                action=action,
                reasoning=reasoning,
                raw_response=raw,
                prompt_payload=payload,
                error=None,
            )
        except Exception as e:
            fallback = _fallback_action(action_schema, player_id)
            return LLMActionDecision(
                action=fallback,
                reasoning="",
                raw_response=raw,
                prompt_payload=payload,
                error=str(e),
            )

    def _parse_and_validate(self, raw: str, schema: Dict[str, Any], player_id: str) -> Tuple[Action, str]:
        data = json.loads(raw)
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

        _validate_action_against_schema(action, schema)
        return action, reasoning


def _fallback_action(schema: Dict[str, Any], player_id: str) -> Action:
    allowed = list(schema.get("allowed_actions", []) or [])
    if ActionType.PASS_PRIORITY.value in allowed:
        return Action(type=ActionType.PASS_PRIORITY, actor_id=player_id)
    # As a last resort, return SCOOP if allowed (prevents infinite loops).
    if ActionType.SCOOP.value in allowed:
        return Action(type=ActionType.SCOOP, actor_id=player_id)
    # Worst case: pick the first allowed type with null args.
    if allowed:
        return Action(type=ActionType(allowed[0]), actor_id=player_id)
    raise RuntimeError("No allowed actions for fallback")


def _target_key(target: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
    return (str(target.get("type")), target.get("player_id"), target.get("instance_id"))


def _normalize_target(target: Any) -> Optional[Dict[str, Any]]:
    if isinstance(target, dict):
        if "type" in target:
            return target
        for value in target.values():
            if isinstance(value, dict) and "type" in value:
                return value
    if isinstance(target, list) and target:
        if isinstance(target[0], dict):
            return target[0]
    return None


def _target_in_candidates(target: Any, candidates: list[Dict[str, Any]]) -> bool:
    td = _normalize_target(target)
    if td is None:
        return False
    tk = _target_key(td)
    return any(_target_key(c) == tk for c in candidates)


def _normalize_block_entry(entry: Any) -> Tuple[Optional[str], Optional[str]]:
    if isinstance(entry, dict):
        return entry.get("attacker_id"), entry.get("blocker_id")
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return entry[0], entry[1]
    if isinstance(entry, str) and "->" in entry:
        left, right = entry.split("->", 1)
        return left.strip(), right.strip()
    return None, None


def _validate_action_against_schema(action: Action, schema: Dict[str, Any]) -> None:
    allowed = set(schema.get("allowed_actions", []) or [])
    action_type = getattr(action.type, "value", str(action.type))
    if action_type not in allowed:
        raise ValueError(f"Action type not allowed: {action_type}")

    if action.type == ActionType.PLAY_LAND:
        choices = schema.get("play_land", {}).get("choices", []) or []
        valid_ids = {c.get("instance_id") for c in choices}
        if action.object_id not in valid_ids:
            raise ValueError("Invalid land selection")

    if action.type == ActionType.TAP_FOR_MANA:
        choices = schema.get("tap_for_mana", {}).get("choices", []) or []
        valid_ids = {c.get("instance_id") for c in choices}
        if action.object_id not in valid_ids:
            raise ValueError("Invalid mana source")

    if action.type == ActionType.CAST_SPELL:
        choices = schema.get("cast_spell", {}).get("choices", []) or []
        choice = next((c for c in choices if c.get("instance_id") == action.object_id), None)
        if choice is None:
            raise ValueError("Invalid spell selection")
        allowed_targets = choice.get("targets") or []
        if allowed_targets:
            if not _target_in_candidates(action.targets, list(allowed_targets)):
                raise ValueError("Invalid spell target")

    if action.type == ActionType.DECLARE_ATTACKERS:
        attackers = schema.get("declare_attackers", {}).get("attackers", []) or []
        valid_ids = {c.get("instance_id") for c in attackers}
        chosen: list[Any] = []
        if isinstance(action.targets, dict):
            chosen = list(action.targets.get("attackers", []))
        normalized: list[Any] = []
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
        attackers = set(schema.get("declare_blockers", {}).get("attackers", []) or [])
        blockers = schema.get("declare_blockers", {}).get("blockers", []) or []
        valid_blockers = {b.get("instance_id") for b in blockers}
        blocks: list[Any] = []
        if isinstance(action.targets, dict):
            blocks = list(action.targets.get("blocks", []))
        used_blockers: set[str] = set()
        used_attackers: set[str] = set()
        for entry in blocks:
            attacker_id, blocker_id = _normalize_block_entry(entry)
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

