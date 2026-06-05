import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from pm_task_harness.market_filter import load_markets
from pm_task_harness.portfolio import (
    apply_rebalance,
    calculate_rebalance_plan,
    choose_new_market_plan,
    commit_portfolio,
    portfolio_nav,
)


def market():
    return load_markets(
        [
            {
                "id": "iran",
                "event_slug": "iran",
                "slug": "iran",
                "question": "Will the U.S. invade Iran before 2027?",
                "outcomes": ["Yes", "No"],
                "outcome_prices": [0.16, 0.84],
                "clob_token_ids": ["yes-token", "no-token"],
                "liquidity": 100000,
                "volume_24hr": 50000,
                "end_date": "2026-12-31T00:00:00Z",
                "tags": [{"slug": "geopolitics"}],
            }
        ]
    )[0]


class PortfolioTest(unittest.TestCase):
    def test_existing_position_sells_when_fair_prob_falls_below_price(self):
        m = market()
        plan = calculate_rebalance_plan(m, "No", yes_prob=0.3, current_shares=100, nav=1000)
        self.assertEqual(plan.action, "sell_all")
        self.assertEqual(plan.target_shares, 0)

    def test_existing_position_adds_when_fair_prob_and_edge_rise(self):
        m = market()
        plan = calculate_rebalance_plan(m, "No", yes_prob=0.05, current_shares=10, nav=1000)
        self.assertEqual(plan.action, "buy")
        self.assertGreater(plan.target_shares, 10)

    def test_new_market_chooses_best_side_only_when_edge_passes_threshold(self):
        m = market()
        buy_no = choose_new_market_plan(m, yes_prob=0.05, nav=1000)
        self.assertEqual(buy_no.action, "buy")
        self.assertEqual(buy_no.outcome, "No")

        skip = choose_new_market_plan(m, yes_prob=0.2, nav=1000)
        self.assertEqual(skip.action, "hold")
        self.assertEqual(skip.target_notional, 0)

    def test_apply_rebalance_updates_cash_and_position_json(self):
        m = market()
        portfolio = {"usdc": 1000.0, "positions": [], "trade_log": []}
        plan = choose_new_market_plan(m, yes_prob=0.05, nav=portfolio_nav(portfolio))
        apply_rebalance(portfolio, m, plan, source="new_market")
        self.assertLess(portfolio["usdc"], 1000)
        self.assertEqual(len(portfolio["positions"]), 1)
        self.assertEqual(portfolio["positions"][0]["outcome"], "No")
        self.assertEqual(len(portfolio["trade_log"]), 1)

    def test_commit_portfolio_writes_current_file_to_separate_branch_without_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            portfolio_path = repo / "portfolio.json"
            portfolio_path.write_text(json.dumps({"usdc": 1}), encoding="utf-8")

            self.assertTrue(commit_portfolio(portfolio_path, "first", repo_dir=repo, branch="paper-portfolio"))
            current_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=repo,
                stdout=subprocess.PIPE,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertNotEqual(current_branch, "paper-portfolio")

            portfolio_path.write_text(json.dumps({"usdc": 2}), encoding="utf-8")
            self.assertTrue(commit_portfolio(portfolio_path, "second", repo_dir=repo, branch="paper-portfolio"))
            saved = subprocess.run(
                ["git", "show", "paper-portfolio:portfolio.json"],
                cwd=repo,
                stdout=subprocess.PIPE,
                text=True,
                check=True,
            ).stdout
            self.assertEqual(json.loads(saved)["usdc"], 2)


if __name__ == "__main__":
    unittest.main()
