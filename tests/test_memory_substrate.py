import os
import tempfile
import unittest

from bob.memory.approval import ApprovalLedger, apply_approval_decisions
from bob.memory.schema import MemoryCandidate
from bob.memory.store import FileLTMStore
from bob.memory.parse import parse_memory_candidates_from_think


class TestMemorySubstrate(unittest.TestCase):
    def setUp(self):
        print(f"\n[TEST] {self.__class__.__name__}.{self._testMethodName}")

    def test_candidate_validation_and_fingerprint_stable(self):
        print("[STEP] MemoryCandidate validates fields and produces stable fingerprint")
        c = MemoryCandidate.from_obj(
            {
                "text": "User prefers concise answers.",
                "type": "preference",
                "tags": ["style", "concise", "Style"],
                "ttl_days": None,
                "source": "user_said",
                "why_store": "Improves response formatting.",
            }
        )
        fp1 = c.fingerprint()
        fp2 = c.fingerprint()
        self.assertEqual(fp1, fp2, msg="Fingerprint should be deterministic/stable.")
        self.assertEqual(c.tags, ["style", "concise"], msg="Tags should be de-duped case-insensitively.")

    def test_approval_ledger_and_apply_decisions(self):
        print("[STEP] Apply decisions returns approved candidates and appends ledger records")
        c = MemoryCandidate.from_obj(
            {
                "text": "User is learning Magic timing rules.",
                "type": "mtg_profile",
                "tags": ["mtg", "timing"],
                "ttl_days": 90,
                "source": "assistant_inferred",
                "why_store": "Tailor tutoring to timing.",
            }
        )
        with tempfile.TemporaryDirectory() as td:
            ledger_path = os.path.join(td, "approvals.jsonl")
            ledger = ApprovalLedger(ledger_path)
            approved = apply_approval_decisions(
                candidates=[c],
                decisions=[{"candidate_fingerprint": c.fingerprint(), "approved": True}],
                reviewer="brad",
                ledger=ledger,
            )
            self.assertEqual(len(approved), 1)
            self.assertTrue(os.path.exists(ledger_path))

    def test_file_ltm_store_upsert_and_query(self):
        print("[STEP] FileLTMStore persists approved items and supports naive retrieval")
        c = MemoryCandidate.from_obj(
            {
                "text": "User prefers being called Brad.",
                "type": "preference",
                "tags": ["name"],
                "ttl_days": None,
                "source": "user_said",
                "why_store": "Use correct name.",
            }
        )
        with tempfile.TemporaryDirectory() as td:
            store_path = os.path.join(td, "ltm.jsonl")
            store = FileLTMStore(store_path)
            mem_id = store.upsert(candidate=c, extra_payload={"reviewer": "brad"})
            self.assertEqual(mem_id, c.fingerprint())

            hits = store.query(query_text="Brad", k=5)
            self.assertGreaterEqual(len(hits), 1)

    def test_parse_candidates_from_think(self):
        print("[STEP] parse_memory_candidates_from_think extracts JSON bullets")
        think = """SITUATION:
- x

MEMORY CANDIDATES:
- {"text":"Remember this","type":"fact","tags":["x"],"ttl_days":null,"source":"user_said","why_store":"test"}
"""
        cands = parse_memory_candidates_from_think(think)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].text, "Remember this")


if __name__ == "__main__":
    unittest.main()
