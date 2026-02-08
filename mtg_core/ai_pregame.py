# mtg_core/ai_pregame.py
from __future__ import annotations

import json
import re
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Protocol

from mtg_core.ai_broker import get_broker

# ============================================================
# Data Contracts (PURE, IMMUTABLE)
# ============================================================

@dataclass(frozen=True)
class CardView:
    instance_id: str
    card_id: str


@dataclass(frozen=True)
class MulliganContext:
    player_id: str
    deck_name: str
    on_play: bool
    mulligans_taken: int
    hand: List[CardView]


@dataclass(frozen=True)
class BottomContext:
    player_id: str
    deck_name: str
    hand: List[CardView]
    bottoming_required: int


@dataclass(frozen=True)
class MulliganDecision:
    decision: Literal["KEEP", "MULLIGAN"]
    reasoning: str | None = None


@dataclass(frozen=True)
class BottomDecision:
    bottom: List[str]
    reasoning: str | None = None


# ============================================================
# AIPregameDecider
# ============================================================

class AIPregameDecider:
    """
    Pregame-only AI decision surface.

    - No engine access
    - No state mutation
    - Blocking at decision boundary ONLY
    - Strict JSON I/O
    """

    def __init__(self, model: str = "gpt-5.2", chat_client: Optional["ChatClient"] = None):
        self.model = model
        self.timeout = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "120"))
        self.chat_client = chat_client

    # ========================================================
    # Public API
    # ========================================================

    def decide_mulligan(self, ctx: MulliganContext) -> MulliganDecision:
        system_prompt = (
            "You are deciding whether to KEEP or MULLIGAN an opening hand in Magic: The Gathering.\n"
            "This is a pregame strategic decision.\n"
            "Respond ONLY with valid JSON.\n"
            "Include a short reasoning string explaining the decision.\n"
            "Schema:\n"
            "{ \"decision\": \"<KEEP|MULLIGAN>\", \"reasoning\": \"<string>\" }"
        )

        payload = {
            "player_id": ctx.player_id,
            "deck_name": ctx.deck_name,
            "on_play": ctx.on_play,
            "mulligans_taken": ctx.mulligans_taken,
            "hand": [
                {"instance_id": c.instance_id, "card_id": c.card_id}
                for c in ctx.hand
            ],
        }
        raw = self._call_llm_blocking(system_prompt, payload)
        # Retry once if the AI returns an empty string
        if not raw or not raw.strip():
            raw = self._call_llm_blocking(system_prompt, payload)
            if not raw or not raw.strip():
                raise RuntimeError("Empty response from mulligan AI.")

        try:
            data = _load_json(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON from mulligan AI:\n{raw}") from e

        decision = data.get("decision")
        reasoning = data.get("reasoning", "")

        if decision not in ("KEEP", "MULLIGAN"):
            raise RuntimeError(f"Invalid mulligan decision value: {data}")

        from mtg_core.ai_trace import log_ai_event

        log_ai_event(
            "mulligan_decision",
            {
                **payload,
                "decision": decision,
                "reasoning": reasoning,
            },
        )

        return MulliganDecision(
            decision=decision,
            reasoning=reasoning,
        )

    def decide_bottom(self, ctx: BottomContext) -> BottomDecision:
        system_prompt = (
            "You are choosing cards to bottom for a London mulligan in Magic: The Gathering.\n"
            "You MUST choose exactly the required number of cards.\n"
            "Respond ONLY with valid JSON.\n"
            "Schema:\n"
            "{ \"bottom\": [\"<instance_id>\", ...], \"reasoning\": \"<string>\" }"
        )
        payload = {
            "player_id": ctx.player_id,
            "deck_name": ctx.deck_name,
            "bottoming_required": ctx.bottoming_required,
            "hand": [
                {"instance_id": c.instance_id, "card_id": c.card_id}
                for c in ctx.hand
            ],
        }

        raw = self._call_llm_blocking(system_prompt, payload)

        try:
            data = _load_json(raw)
        except json.JSONDecodeError as e:
            recovered = _recover_bottom_from_raw(raw)
            if recovered is None:
                raise RuntimeError(f"Invalid JSON from bottom AI:\n{raw}") from e
            data = {"bottom": recovered, "reasoning": ""}

        bottom = data.get("bottom")
        bottom = _normalize_bottom_selection(bottom, ctx)
        if not isinstance(bottom, list):
            raise RuntimeError(f"Invalid bottom payload: {data}")

        if len(bottom) != ctx.bottoming_required:
            raise RuntimeError(
                f"AI bottomed {len(bottom)} cards, expected {ctx.bottoming_required}"
            )

        valid_ids = {c.instance_id for c in ctx.hand}
        for cid in bottom:
            if cid not in valid_ids:
                raise RuntimeError(f"Invalid instance_id to bottom: {cid}")

        from mtg_core.ai_trace import log_ai_event

        log_ai_event(
            "bottom_decision",
            {
                "player_id": ctx.player_id,
                "deck_name": ctx.deck_name,
                "bottoming_required": ctx.bottoming_required,
                "hand": [
                    {"instance_id": c.instance_id, "card_id": c.card_id}
                    for c in ctx.hand
                ],
                "bottom": bottom,
                "reasoning": data.get("reasoning", ""),
            },
        )

        return BottomDecision(
            bottom=bottom,
            reasoning=data.get("reasoning", ""),
        )

    # ========================================================
    # Internal LLM Boundary
    # ========================================================

    def _call_llm_blocking(self, system_prompt: str, payload: Dict) -> str:
        if self.chat_client is not None:
            return self.chat_client.chat_text(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload)},
                ],
                temperature=0.7,
                max_tokens=200,
                timeout_s=self.timeout,
            )

        print("[AI PRE-GAME] calling OpenAI...")
        broker = get_broker()
        result = broker.responses_create_text(
            model=self.model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.7,
            max_output_tokens=200,
            timeout=self.timeout,
        )
        print("[AI PRE-GAME] response received")
        return result


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


def _strip_reasoning_block(raw: str) -> str:
    text = raw.strip()
    key = '"reasoning"'
    idx = text.find(key)
    if idx == -1:
        return text
    cut = text.rfind(",", 0, idx)
    if cut == -1:
        return text
    return text[:cut].rstrip() + "\n}"


def _load_json(raw: str) -> Dict[str, Any]:
    # Strip any surrounding code fences or extraneous text.
    text = _strip_code_fences(raw).strip()
    # Attempt to locate the first complete JSON object in the string.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_candidate = text[start:end+1]
    else:
        json_candidate = text
    # Try parsing the cleaned candidate first.
    try:
        return json.loads(json_candidate)
    except json.JSONDecodeError:
        pass
    # Fallback to original heuristics if parsing fails.
    candidates = [
        json_candidate,
        _extract_json_object(text),
    ]
    candidates.append(_strip_reasoning_block(candidates[-1]))
    last_err: Optional[json.JSONDecodeError] = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            last_err = e
    if last_err is not None:
        raise last_err
    raise json.JSONDecodeError("Invalid JSON", text, 0)


def _recover_bottom_from_raw(raw: str) -> Optional[List[str]]:
    text = _strip_code_fences(raw)
    match = re.search(r'"bottom"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if not match:
        return None
    inner = match.group(1)
    ids = re.findall(r'"([^"]+)"', inner)
    return ids or None


def _normalize_bottom_selection(bottom: Any, ctx: BottomContext) -> List[str]:
    hand_ids = [c.instance_id for c in ctx.hand]
    id_by_card_id: Dict[str, Optional[str]] = {}
    for c in ctx.hand:
        if c.card_id not in id_by_card_id:
            id_by_card_id[c.card_id] = c.instance_id
        else:
            id_by_card_id[c.card_id] = None

    if bottom is None:
        raw_list: List[Any] = []
    elif isinstance(bottom, list):
        raw_list = list(bottom)
    elif isinstance(bottom, dict):
        raw_list = [bottom]
    else:
        raw_list = [bottom]

    normalized: List[str] = []
    for entry in raw_list:
        cid = None
        if isinstance(entry, dict):
            cid = entry.get("instance_id") or entry.get("id")
            if cid is None and "card_id" in entry:
                cid = id_by_card_id.get(entry.get("card_id"))
        elif isinstance(entry, int):
            if 0 <= entry < len(hand_ids):
                cid = hand_ids[entry]
        elif isinstance(entry, str):
            if entry in hand_ids:
                cid = entry
            elif entry.isdigit():
                idx = int(entry)
                if 0 <= idx < len(hand_ids):
                    cid = hand_ids[idx]
            else:
                cid = id_by_card_id.get(entry)
        if cid is None:
            continue
        if cid not in normalized:
            normalized.append(cid)

    if len(normalized) > ctx.bottoming_required:
        normalized = normalized[: ctx.bottoming_required]
    elif len(normalized) < ctx.bottoming_required:
        for cid in hand_ids:
            if cid not in normalized:
                normalized.append(cid)
                if len(normalized) >= ctx.bottoming_required:
                    break

    return normalized
