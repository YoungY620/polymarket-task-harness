import json
import unittest

from pm_task_harness.market_filter import load_markets, select_tasks
from pm_task_harness.validator import validate_agent_output, validate_fair_value_output


class ValidatorTest(unittest.TestCase):
    def setUp(self):
        market = load_markets(
            [
                {
                    "id": "m1",
                    "event_slug": "iran",
                    "slug": "iran",
                    "question": "Will the US and Iran reach a nuclear agreement before December 31?",
                    "outcomes": ["Yes", "No"],
                    "outcome_prices": [0.3, 0.7],
                    "clob_token_ids": ["yes-token", "no-token"],
                    "liquidity": 100000,
                    "volume_24hr": 50000,
                    "end_date": "2026-12-31T00:00:00Z",
                    "category_slug": "politics",
                }
            ]
        )[0]
        self.task = select_tasks([market])[0]

    def test_accepts_executable_json(self):
        payload = {
            "decision": "buy_no",
            "outcome": "No",
            "token_id": "no-token",
            "ai_prob": 0.82,
            "market_prob": 0.7,
            "edge": 0.12,
            "confidence": "medium",
            "thesis": "Rules and evidence favor No.",
            "evidence_table": [{"step": "base"}, {"step": "evidence"}, {"step": "final"}],
            "sources": [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}],
        }
        result = validate_agent_output(json.dumps(payload), self.task)
        self.assertTrue(result.ok, result.errors)

    def test_rejects_bad_json_and_small_edge_buy(self):
        self.assertFalse(validate_agent_output("not json", self.task).ok)
        payload = {
            "decision": "buy_yes",
            "outcome": "Yes",
            "token_id": "yes-token",
            "ai_prob": 0.34,
            "market_prob": 0.3,
            "edge": 0.04,
            "confidence": "low",
            "thesis": "Too small.",
            "evidence_table": [{"step": "base"}, {"step": "evidence"}, {"step": "final"}],
            "sources": [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}],
        }
        result = validate_agent_output(json.dumps(payload), self.task)
        self.assertFalse(result.ok)
        self.assertTrue(any("edge" in error for error in result.errors))

    def test_accepts_fair_value_probability_without_trade_decision(self):
        payload = {
            "yes_prob": 0.18,
            "confidence": "medium",
            "thesis": "Current evidence puts Yes below market.",
            "evidence_table": [{"step": "base"}, {"step": "evidence"}, {"step": "final"}],
            "sources": [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}],
        }
        result = validate_fair_value_output(json.dumps(payload))
        self.assertTrue(result.ok, result.errors)


if __name__ == "__main__":
    unittest.main()
