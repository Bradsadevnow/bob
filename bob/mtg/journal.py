from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from bob.runtime.logging import now_utc


@dataclass
class GameJournal:
    """
    Logs-only game journal (append-only JSONL).
    """

    journal_path: str

    def __post_init__(self) -> None:
        os.makedirs(os.path.dirname(self.journal_path), exist_ok=True)

    def append(self, event: Dict[str, Any]) -> None:
        row = {"ts_utc": now_utc(), **event}
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_game_summary(path: str, *, summary: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

