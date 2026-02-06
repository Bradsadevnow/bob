import unittest

from bob.memory.stm_parse import parse_stm_query_from_think


class TestSTMParse(unittest.TestCase):
    def test_parse_stm_query(self):
        think = """SITUATION:
- x

STM QUERY:
- cards: bolt bird
- tempo pressure

TOOL REQUESTS:
- NONE
"""
        q = parse_stm_query_from_think(think)
        self.assertIsNotNone(q)
        self.assertIn("bolt", q)
        self.assertIn("tempo", q)

    def test_parse_none(self):
        think = """SITUATION:
- x

STM QUERY:
- NONE

TOOL REQUESTS:
- NONE
"""
        q = parse_stm_query_from_think(think)
        self.assertIsNone(q)


if __name__ == "__main__":
    unittest.main()
