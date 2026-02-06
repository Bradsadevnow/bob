import json
import os
import tempfile
import unittest

from bob.runtime.logging import JsonlLogger
from bob.runtime.state import StateStore


class TestStateAndLogging(unittest.TestCase):
    def setUp(self):
        print(f"\n\n[TEST] {self.__class__.__name__}.{self._testMethodName}")

    def test_state_store_init_and_commit(self):
        print("[STEP] StateStore initializes new state.json (identity + continuity empty)")
        with tempfile.TemporaryDirectory() as td:
            state_path = os.path.join(td, "state.json")
            store = StateStore(state_path, system_id="bob", display_name="Bob")

            snap1 = store.snapshot()
            self.assertEqual(snap1["identity"]["system_id"], "bob")
            self.assertEqual(snap1["identity"]["display_name"], "Bob")
            self.assertIn("agent_name", snap1["identity"])
            self.assertIn("affect_state", snap1)
            self.assertEqual(snap1["continuity"]["active_context"], [])
            self.assertEqual(snap1["continuity"]["open_threads"], [])
            self.assertIn("integrity", snap1["continuity"])
            self.assertTrue(os.path.exists(state_path))

            print("[STEP] StateStore.commit updates only active_context/open_threads and increments turn_counter")
            store.commit(active_context=["x"], open_threads=["t1"])
            snap2 = store.snapshot()
            self.assertEqual(
                snap2["continuity"]["active_context"],
                ["x"],
                msg="Failure suggests StateStore.commit did not persist active_context correctly.",
            )
            self.assertEqual(
                snap2["continuity"]["open_threads"],
                ["t1"],
                msg="Failure suggests StateStore.commit did not persist open_threads correctly.",
            )
            self.assertGreaterEqual(
                snap2["meta"]["turn_counter"],
                1,
                msg="Failure suggests turn_counter is not incrementing on commit.",
            )

    def test_jsonl_logger_appends_valid_json(self):
        print("[STEP] JsonlLogger.append writes one valid JSON object per line")
        with tempfile.TemporaryDirectory() as td:
            log_path = os.path.join(td, "turns.jsonl")
            logger = JsonlLogger(log_path)
            logger.append({"a": 1, "b": "x"})

            with open(log_path, "r", encoding="utf-8") as f:
                line = f.readline()
            obj = json.loads(line)
            self.assertEqual(obj["a"], 1, msg="Failure suggests JSONL write/read mismatch for key 'a'.")
            self.assertEqual(obj["b"], "x", msg="Failure suggests JSONL write/read mismatch for key 'b'.")


if __name__ == "__main__":
    unittest.main()
