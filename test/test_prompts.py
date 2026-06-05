import tempfile
import unittest
from pathlib import Path

from pm_task_harness.market_filter import load_markets, select_tasks
from pm_task_harness.prompts import (
    build_chinese_report_prompt,
    build_fair_value_prompt,
    build_repair_prompt,
    build_task_prompt,
)


def make_task():
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
    return select_tasks([market])[0]


class PromptTest(unittest.TestCase):
    def test_task_prompt_starts_with_explicit_slash_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skill.md"
            skill.write_text("skill body", encoding="utf-8")
            prompt = build_task_prompt(make_task(), skill)
        self.assertTrue(prompt.startswith("/goal "))
        self.assertIn("# GOAL", prompt)
        self.assertIn("# SKILL", prompt)
        self.assertIn("# MARKET_TASK", prompt)

    def test_repair_prompt_starts_with_explicit_slash_goal(self):
        prompt = build_repair_prompt("{}", ["missing field: decision"])
        self.assertTrue(prompt.startswith("/goal "))
        self.assertIn("missing field: decision", prompt)

    def test_fair_value_prompt_keeps_trading_math_in_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skill.md"
            skill.write_text("skill body", encoding="utf-8")
            prompt = build_fair_value_prompt(make_task(), skill, purpose="position_review")
        self.assertIn('"yes_prob"', prompt)
        self.assertIn("do not decide", prompt.lower())
        self.assertNotIn('"decision"', prompt)

    def test_chinese_report_prompt_requires_operation_opening_and_one_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skill.md"
            skill.write_text("skill body", encoding="utf-8")
            prompt = build_chinese_report_prompt(
                position_address="0xabc",
                positions=[],
                new_tasks=[make_task()],
                skill_path=skill,
            )
        self.assertIn("# 操作建议", prompt)
        self.assertIn("只读分析", prompt)
        self.assertIn("不要声称已经下单", prompt)


if __name__ == "__main__":
    unittest.main()
