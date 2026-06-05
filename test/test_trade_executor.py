import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from pm_task_harness.market_filter import load_markets
from pm_task_harness.portfolio import choose_new_market_plan
from pm_task_harness.trade_executor import (
    load_ledger,
    process_request_file,
    request_from_plan,
    validate_request,
)


def request_payload(**overrides):
    payload = {
        "schema_version": 1,
        "request_id": "trade-test",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "expires_at_utc": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        "market_slug": "iran",
        "event_slug": "iran-event",
        "outcome": "No",
        "token_id": "no-token",
        "side": "BUY",
        "amount_usd": 9.0,
        "max_price": 0.84,
        "source": "portfolio_loop",
        "reason": "new_market",
    }
    payload.update(overrides)
    return payload


class TradeExecutorTest(unittest.TestCase):
    def test_rejects_request_that_exceeds_total_buy_budget(self):
        ledger = {"executed": [{"request_id": "old", "side": "BUY", "amount_usd": 45.0}], "rejected": []}
        errors = validate_request(
            request_payload(amount_usd=6.0),
            ledger,
            max_trade_usd=10.0,
            max_total_buy_usd=50.0,
            allowed_sources={"portfolio_loop"},
        )
        self.assertIn("BUY total would exceed max_total_buy_usd=50.0", errors)

    def test_rejects_duplicate_request_id(self):
        ledger = {"executed": [{"request_id": "trade-test"}], "rejected": []}
        errors = validate_request(
            request_payload(),
            ledger,
            max_trade_usd=10.0,
            max_total_buy_usd=50.0,
            allowed_sources={"portfolio_loop"},
        )
        self.assertIn("request_id was already processed", errors)

    def test_dry_run_records_valid_request_as_executed_without_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_file = root / "inbox" / "trade-test.json"
            request_file.parent.mkdir()
            request_file.write_text(json.dumps(request_payload()), encoding="utf-8")
            ledger = load_ledger(root / "ledger.json")
            args = SimpleNamespace(
                max_trade_usd=10.0,
                max_total_buy_usd=50.0,
                allowed_source=["portfolio_loop"],
                dry_run=True,
                command="",
                timeout_seconds=1,
            )
            process_request_file(args, request_file, ledger)
            self.assertEqual(len(ledger["executed"]), 1)
            self.assertTrue(ledger["executed"][0]["dry_run"])

    def test_builds_request_from_rebalance_plan(self):
        market = load_markets(
            [
                {
                    "event_slug": "iran-event",
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
        plan = choose_new_market_plan(market, yes_prob=0.05, nav=1000)
        request = request_from_plan(
            market,
            plan,
            source="portfolio_loop",
            reason="new_market",
            expires_at_utc=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
        )
        self.assertIsNotNone(request)
        self.assertEqual(request.side, "BUY")
        self.assertEqual(request.token_id, "no-token")


if __name__ == "__main__":
    unittest.main()
