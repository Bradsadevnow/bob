import json
import os
import tempfile
import unittest

from bob.config import BobConfig, ModelConfig
from bob.runtime.logging import JsonlLogger
from bob.runtime.orchestrator import Orchestrator
from bob.runtime.state import StateStore
from bob.runtime.testing import FakeChatClient, FakeLLMResponsePlan


class TestOrchestratorWithFakeLLM(unittest.TestCase):
    def setUp(self):
        print(f"\n[TEST] {self.__class__.__name__}.{self._testMethodName}")

    def test_orchestrator_runs_and_logs(self):
        print("[STEP] Build isolated temp runtime dir (state.json + turns.jsonl)")
        with tempfile.TemporaryDirectory() as td:
            state_path = os.path.join(td, "state.json")
            log_path = os.path.join(td, "turns.jsonl")

            print("[STEP] Create Orchestrator with FakeChatClient (no network/model deps)")
            cfg = BobConfig(
                system_id="bob",
                display_name="Bob",
                local=ModelConfig(base_url="http://example.invalid/v1", api_key="x", model="x"),
                mtg_remote=ModelConfig(base_url="http://example.invalid/v1", api_key="x", model="x"),
                route_mtg_to_remote=True,
                runtime_dir=td,
                log_file=log_path,
                state_file=state_path,
            )

            plan = FakeLLMResponsePlan(respond="abc")
            fake = FakeChatClient(plan=plan)

            orch = Orchestrator(
                cfg,
                local_llm=fake,
                mtg_llm=fake,
                state_store=StateStore(state_path, system_id="bob", display_name="Bob"),
                logger=JsonlLogger(log_path),
            )
            session = orch.new_session()

            print("[STEP] Run one full turn (THINK → RESPOND); assert streamed output matches RESPOND plan")
            out = "".join(list(orch.run_turn_stream(session=session, user_input="hi")))
            self.assertEqual(out, "abc")

            print("[STEP] Verify turn log exists and contains expected fields (think + state snapshots)")
            self.assertTrue(os.path.exists(log_path))
            with open(log_path, "r", encoding="utf-8") as f:
                row = json.loads(f.readline())

            self.assertEqual(
                row["user_input"],
                "hi",
                msg="Failure suggests the logger or orchestrator isn't recording user_input correctly.",
            )
            self.assertEqual(
                row["final_output"],
                "abc",
                msg="Failure suggests RESPOND streaming aggregation or logging is broken.",
            )
            self.assertEqual(
                row["think"],
                plan.think,
                msg="Failure suggests THINK output was not recorded or fake LLM routing is wrong.",
            )
            self.assertIsInstance(
                row["state_before"],
                dict,
                msg="Failure suggests state_before snapshot wasn't included in the turn record.",
            )
            self.assertIsInstance(
                row["state_after"],
                dict,
                msg="Failure suggests state_after snapshot wasn't included in the turn record.",
            )
            self.assertIsInstance(
                row.get("memory_candidates"),
                list,
                msg="Failure suggests memory_candidates field missing or wrong type in turn record.",
            )
            self.assertGreaterEqual(
                row["state_after"]["meta"]["turn_counter"],
                row["state_before"]["meta"]["turn_counter"],
                msg="Failure suggests StateStore.commit wasn't called or turn_counter didn't increment.",
            )


if __name__ == "__main__":
    unittest.main()
