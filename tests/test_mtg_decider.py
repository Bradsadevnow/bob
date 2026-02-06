import unittest

from bob.mtg.decider import MtgActionDecider

from mtg_core.actions import ActionType
from mtg_core.aibase import VisibleState, ZonesView


class _FixedChat:
    def __init__(self, text: str):
        self.text = text

    def chat_text(self, *, messages, temperature, max_tokens, timeout_s):  # noqa: ANN001
        return self.text


def _dummy_visible() -> VisibleState:
    return VisibleState(
        turn_number=1,
        active_player_id="P1",
        phase="MAIN1",
        priority_holder_id="P1",
        life_totals={"P1": 20, "P2": 20},
        zones=ZonesView(
            battlefield=[],
            stack=[],
            graveyards={"P1": [], "P2": []},
            exile={},
            hand=[],
            library_size=33,
        ),
        card_db={},
        available_mana={"generic": 0, "colored": {}},
        lands_played_this_turn=0,
        stack=[],
        combat_attackers=[],
        combat_blockers={},
        combat_attackers_declared=False,
        combat_blockers_declared=False,
        game_over=False,
        winner_id=None,
        end_reason=None,
    )


class TestMtgDecider(unittest.TestCase):
    def test_valid_pass_priority(self):
        chat = _FixedChat('{"type":"PASS_PRIORITY","object_id":null,"targets":null,"payload":null,"reasoning":"ok"}')
        d = MtgActionDecider(chat)
        schema = {"allowed_actions": ["PASS_PRIORITY", "SCOOP"]}
        decision = d.decide(visible=_dummy_visible(), action_schema=schema, player_id="P1")
        self.assertEqual(decision.action.type, ActionType.PASS_PRIORITY)
        self.assertIsNone(decision.error)

    def test_invalid_json_falls_back(self):
        chat = _FixedChat("not json")
        d = MtgActionDecider(chat)
        schema = {"allowed_actions": ["PASS_PRIORITY", "SCOOP"]}
        decision = d.decide(visible=_dummy_visible(), action_schema=schema, player_id="P1")
        self.assertEqual(decision.action.type, ActionType.PASS_PRIORITY)
        self.assertIsNotNone(decision.error)

    def test_disallowed_action_falls_back(self):
        chat = _FixedChat('{"type":"CAST_SPELL","object_id":"x","targets":null,"payload":null,"reasoning":"no"}')
        d = MtgActionDecider(chat)
        schema = {"allowed_actions": ["PASS_PRIORITY", "SCOOP"]}
        decision = d.decide(visible=_dummy_visible(), action_schema=schema, player_id="P1")
        self.assertEqual(decision.action.type, ActionType.PASS_PRIORITY)
        self.assertIsNotNone(decision.error)


if __name__ == "__main__":
    unittest.main()

