from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from bob.memory.schema import MemoryCandidate


_SECTION_RE = re.compile(r"^\s*MEMORY\s+CANDIDATES\s*:\s*$", re.IGNORECASE | re.MULTILINE)


def parse_memory_candidates_from_think(think_text: str, *, limit: int = 2) -> List[MemoryCandidate]:
    """
    Extract MemoryCandidate objects from the THINK output.

    Expected format (from prompt):

    MEMORY CANDIDATES:
    - NONE
    - or up to 2 bullets, each as compact JSON:
      {"text":"...","type":"preference|...","tags":["..."],"ttl_days":null,"source":"...","why_store":"..."}
    """
    text = think_text or ""
    m = _SECTION_RE.search(text)
    if not m:
        return []

    tail = text[m.end() :]
    lines = tail.splitlines()

    out: List[MemoryCandidate] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # stop if we hit another obvious section header
        if re.match(r"^[A-Z][A-Z _/]{2,}:\s*$", line):
            break

        if line.upper().startswith("- NONE"):
            break

        if not line.startswith("-"):
            continue

        payload = line[1:].strip()
        if not payload.startswith("{"):
            continue

        try:
            obj = json.loads(payload)
        except Exception:
            continue

        if not isinstance(obj, dict):
            continue

        try:
            cand = MemoryCandidate.from_obj(obj)
        except Exception:
            continue

        out.append(cand)
        if len(out) >= max(1, int(limit)):
            break

    return out
