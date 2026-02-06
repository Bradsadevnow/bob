from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Literal, Optional

MemoryType = Literal[
    "preference",
    "fact",
    "procedure",
    "project_decision",
    "mtg_profile",
    "mtg_lesson",
]

MemorySource = Literal["user_said", "assistant_inferred", "tool_output"]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_str_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return [values]
    if isinstance(values, list):
        out: list[str] = []
        for v in values:
            if v is None:
                continue
            out.append(str(v).strip())
        return [x for x in out if x]
    return [str(values).strip()] if str(values).strip() else []


def _normalize_tags(tags: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        s = (t or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _candidate_fingerprint(text: str, mem_type: str, tags: list[str]) -> str:
    """
    Stable id used for:
    - dedupe
    - approval ledger linkage
    """
    norm = {
        "text": (text or "").strip(),
        "type": (mem_type or "").strip(),
        "tags": [t.lower() for t in tags],
    }
    blob = json.dumps(norm, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryCandidate:
    text: str
    type: MemoryType
    tags: list[str]
    ttl_days: Optional[int]
    source: MemorySource
    why_store: str

    @staticmethod
    def from_obj(obj: Dict[str, Any]) -> "MemoryCandidate":
        text = str(obj.get("text") or "").strip()
        if not text:
            raise ValueError("MemoryCandidate.text must be non-empty")

        mem_type = str(obj.get("type") or "").strip()
        allowed_types = {
            "preference",
            "fact",
            "procedure",
            "project_decision",
            "mtg_profile",
            "mtg_lesson",
        }
        if mem_type not in allowed_types:
            raise ValueError(f"MemoryCandidate.type invalid: {mem_type!r}")

        tags = _normalize_tags(_as_str_list(obj.get("tags")))

        ttl_days_raw = obj.get("ttl_days")
        ttl_days: Optional[int]
        if ttl_days_raw is None or ttl_days_raw == "":
            ttl_days = None
        else:
            ttl_days = int(ttl_days_raw)
            if ttl_days <= 0:
                raise ValueError("MemoryCandidate.ttl_days must be > 0 when set")

        source = str(obj.get("source") or "").strip()
        allowed_sources = {"user_said", "assistant_inferred", "tool_output"}
        if source not in allowed_sources:
            raise ValueError(f"MemoryCandidate.source invalid: {source!r}")

        why_store = str(obj.get("why_store") or "").strip()
        if not why_store:
            raise ValueError("MemoryCandidate.why_store must be non-empty")

        return MemoryCandidate(
            text=text,
            type=mem_type,  # type: ignore[arg-type]
            tags=tags,
            ttl_days=ttl_days,
            source=source,  # type: ignore[arg-type]
            why_store=why_store,
        )

    def fingerprint(self) -> str:
        return _candidate_fingerprint(self.text, self.type, self.tags)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "type": self.type,
            "tags": list(self.tags),
            "ttl_days": self.ttl_days,
            "source": self.source,
            "why_store": self.why_store,
            "fingerprint": self.fingerprint(),
        }


@dataclass(frozen=True)
class MemoryApprovalDecision:
    candidate_fingerprint: str
    approved: bool
    reviewer: str
    decided_at_utc: str
    edited: Optional[Dict[str, Any]] = None  # edited candidate dict
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_fingerprint": self.candidate_fingerprint,
            "approved": self.approved,
            "reviewer": self.reviewer,
            "decided_at_utc": self.decided_at_utc,
            "edited": self.edited,
            "note": self.note,
        }

