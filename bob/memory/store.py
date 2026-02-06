from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from bob.memory.schema import MemoryCandidate, now_utc


class LTMStore(Protocol):
    """
    Long-term memory store interface.

    HARD RULE:
    - This store never decides *what* to store.
      It only persists *approved* items.
    """

    def upsert(self, *, candidate: MemoryCandidate, extra_payload: Optional[Dict[str, Any]] = None) -> str: ...

    def query(self, *, query_text: str, k: int = 8) -> List[Dict[str, Any]]: ...


@dataclass
class FileLTMStore:
    """
    Minimal, dependency-free LTM backend.

    - JSONL append-only storage
    - naive retrieval for early bring-up (substring matching)
    - stable ids via candidate fingerprint
    """

    path: str

    def __post_init__(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def upsert(self, *, candidate: MemoryCandidate, extra_payload: Optional[Dict[str, Any]] = None) -> str:
        fp = candidate.fingerprint()
        record = {
            "id": fp,
            "stored_at_utc": now_utc(),
            "candidate": candidate.to_dict(),
            "extra": dict(extra_payload or {}),
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return fp

    def query(self, *, query_text: str, k: int = 8) -> List[Dict[str, Any]]:
        q = (query_text or "").strip().lower()
        if not q:
            return []
        hits: List[Dict[str, Any]] = []
        if not os.path.exists(self.path):
            return hits

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    cand = (obj.get("candidate") or {})
                    text = str(cand.get("text") or "")
                    blob = (text + " " + " ".join(cand.get("tags") or [])).lower()
                    if q in blob:
                        hits.append(obj)
        except Exception:
            return hits

        # return most recent first
        hits.sort(key=lambda x: str(x.get("stored_at_utc") or ""), reverse=True)
        return hits[: max(1, int(k))]

