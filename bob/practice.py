from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from bob.config import BobConfig
from bob.memory.schema import MemoryCandidate, now_utc


@dataclass
class PracticeResult:
    candidates: List[MemoryCandidate]
    skipped: int


def _load_recent_turns(path: str, limit: int = 200) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for line in lines[-max(1, int(limit)) :]:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _load_decided_fingerprints(path: str) -> set[str]:
    decided: set[str] = set()
    if not os.path.exists(path):
        return decided
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                fp = str(obj.get("candidate_fingerprint") or "").strip()
                if fp:
                    decided.add(fp)
    except Exception:
        return decided
    return decided


def _load_existing_ltm_fingerprints(path: str) -> set[str]:
    fps: set[str] = set()
    if not os.path.exists(path):
        return fps
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                cand = (obj.get("candidate") or {}) if isinstance(obj, dict) else {}
                fp = str(cand.get("fingerprint") or "").strip()
                if fp:
                    fps.add(fp)
    except Exception:
        return fps
    return fps


def _load_existing_practice_fingerprints(path: str) -> set[str]:
    fps: set[str] = set()
    if not os.path.exists(path):
        return fps
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                fp = str(obj.get("fingerprint") or "").strip()
                if fp:
                    fps.add(fp)
    except Exception:
        return fps
    return fps


def _split_sentences(text: str) -> List[str]:
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _mtg_context(text: str) -> bool:
    if not text:
        return False
    blob = text.lower()
    keywords = [
        "mtg",
        "magic",
        "deck",
        "sideboard",
        "mulligan",
        "combat",
        "stack",
        "mana",
        "draft",
        "sealed",
        "standard",
        "modern",
        "commander",
        "edh",
    ]
    return any(k in blob for k in keywords)


def _classify_sentence(sent: str) -> Optional[Dict[str, Any]]:
    if not sent:
        return None

    s = sent.strip()
    lower = s.lower()
    mtg = _mtg_context(s)

    pref_markers = ["i like", "i love", "i prefer", "please", "don't", "do not", "never", "always"]
    learn_markers = ["i want to learn", "help me learn", "practice", "i want to get better", "i'm learning"]
    struggle_markers = ["i struggle", "i keep forgetting", "i misplay", "i mess up", "i make mistakes"]

    if any(m in lower for m in pref_markers):
        return {
            "type": "preference",
            "text": f"User preference: {s}",
            "tags": ["practice"] + (["mtg"] if mtg else []),
            "source": "user_said",
        }

    if mtg and any(m in lower for m in struggle_markers):
        return {
            "type": "mtg_lesson",
            "text": f"User reports a recurring issue: {s}",
            "tags": ["practice", "mtg"],
            "source": "user_said",
        }

    if mtg and any(m in lower for m in learn_markers):
        return {
            "type": "mtg_profile",
            "text": f"User wants to practice/learn: {s}",
            "tags": ["practice", "mtg"],
            "source": "user_said",
        }

    return None


def _candidate_from_sentence(sent: str, *, turn_ref: str) -> Optional[MemoryCandidate]:
    base = _classify_sentence(sent)
    if not base:
        return None

    obj = {
        "text": base["text"],
        "type": base["type"],
        "tags": base["tags"],
        "ttl_days": 180,
        "source": base["source"],
        "why_store": f"Practice extraction from {turn_ref}",
    }
    try:
        return MemoryCandidate.from_obj(obj)
    except Exception:
        return None


def run_practice(
    cfg: BobConfig,
    *,
    max_turns: int = 200,
    max_candidates: int = 8,
    write_file: bool = True,
) -> PracticeResult:
    turns = _load_recent_turns(cfg.log_file, limit=max_turns)
    decided = _load_decided_fingerprints(cfg.approval_ledger_file)
    existing_ltm = _load_existing_ltm_fingerprints(cfg.ltm_file)
    practice_path = getattr(cfg, "practice_candidates_file", "./runtime/practice_candidates.jsonl")
    existing_practice = _load_existing_practice_fingerprints(practice_path)

    out: List[MemoryCandidate] = []
    skipped = 0

    for t in reversed(turns):
        if len(out) >= max(1, int(max_candidates)):
            break
        user_input = str(t.get("user_input") or "").strip()
        final_output = str(t.get("final_output") or "").strip()
        if not user_input or not final_output:
            continue
        turn_ref = f"turn {t.get('turn_number')} session {t.get('session_id')}"
        for sent in _split_sentences(user_input):
            cand = _candidate_from_sentence(sent, turn_ref=turn_ref)
            if not cand:
                continue
            fp = cand.fingerprint()
            if fp in decided or fp in existing_ltm or fp in existing_practice:
                skipped += 1
                continue
            out.append(cand)
            if len(out) >= max(1, int(max_candidates)):
                break

    if write_file and out:
        os.makedirs(os.path.dirname(practice_path), exist_ok=True)
        with open(practice_path, "a", encoding="utf-8") as f:
            for c in out:
                record = c.to_dict()
                record["practice_ts_utc"] = now_utc()
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return PracticeResult(candidates=out, skipped=skipped)


def load_practice_candidates(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except Exception:
        return []
    return rows


def main() -> None:
    from bob.config import load_config

    cfg = load_config()
    res = run_practice(cfg)
    print(f"[practice] candidates={len(res.candidates)} skipped={res.skipped}")


if __name__ == "__main__":
    main()
