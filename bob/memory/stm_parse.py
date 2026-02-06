from __future__ import annotations

import re
from typing import Optional


_SECTION_RE = re.compile(r"^\s*STM\s+QUERY\s*:\s*$", re.IGNORECASE | re.MULTILINE)


def parse_stm_query_from_think(think_text: str) -> Optional[str]:
    """
    Extract STM query text from THINK output.

    Expected format:
    STM QUERY:
    - NONE
    - or 1–3 bullet fragments describing what to retrieve from STM
    """
    text = think_text or ""
    m = _SECTION_RE.search(text)
    if not m:
        return None

    tail = text[m.end():]
    lines = tail.splitlines()

    parts: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # stop at next section header
        if re.match(r"^[A-Z][A-Z _/]{2,}:\s*$", line):
            break

        if line.upper().startswith("- NONE"):
            return None

        if line.startswith("-"):
            payload = line[1:].strip()
            if payload:
                parts.append(payload)
            continue

        # allow continuation lines
        if parts:
            parts.append(line)

    if not parts:
        return None

    return " | ".join(parts)
