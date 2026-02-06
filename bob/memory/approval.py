from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from bob.memory.schema import MemoryApprovalDecision, MemoryCandidate, now_utc


@dataclass
class ApprovalLedger:
    """
    Append-only approval ledger.

    Purpose:
    - prove what was approved/denied and when
    - support dedupe / re-review
    """

    path: str

    def __post_init__(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def append(self, decision: MemoryApprovalDecision) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(decision.to_dict(), ensure_ascii=False) + "\n")


def apply_approval_decisions(
    *,
    candidates: Iterable[MemoryCandidate],
    decisions: Iterable[Dict[str, Any]],
    reviewer: str,
    ledger: Optional[ApprovalLedger] = None,
) -> List[MemoryCandidate]:
    """
    Applies a set of approval decisions to candidates.

    - decisions are external inputs (UI/CLI) and may contain edits
    - approved (possibly edited) candidates are returned for committing to LTM
    - all decisions are optionally recorded to a ledger
    """
    cand_by_fp = {c.fingerprint(): c for c in candidates}
    approved: List[MemoryCandidate] = []

    for d in decisions:
        fp = str(d.get("candidate_fingerprint") or "").strip()
        if not fp or fp not in cand_by_fp:
            continue

        ok = bool(d.get("approved"))
        edited = d.get("edited")
        note = (d.get("note") or None) if isinstance(d.get("note"), str) else None

        if ok:
            if isinstance(edited, dict):
                approved.append(MemoryCandidate.from_obj(edited))
            else:
                approved.append(cand_by_fp[fp])

        if ledger is not None:
            ledger.append(
                MemoryApprovalDecision(
                    candidate_fingerprint=fp,
                    approved=ok,
                    reviewer=reviewer,
                    decided_at_utc=now_utc(),
                    edited=edited if isinstance(edited, dict) else None,
                    note=note,
                )
            )

    return approved

