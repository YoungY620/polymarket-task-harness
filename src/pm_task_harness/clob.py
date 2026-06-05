from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


CLOB_API = "https://clob.polymarket.com"


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class BookSnapshot:
    token_id: str
    timestamp: str
    best_bid: Optional[float]
    best_ask: Optional[float]
    min_order_size: Optional[float]
    bids: List[BookLevel]
    asks: List[BookLevel]


def _level(row: Dict[str, Any]) -> Optional[BookLevel]:
    try:
        price = float(row.get("price"))
        size = float(row.get("size"))
    except (TypeError, ValueError):
        return None
    if price <= 0 or size <= 0:
        return None
    return BookLevel(price=price, size=size)


def fetch_orderbook(token_id: str) -> BookSnapshot:
    qs = urllib.parse.urlencode({"token_id": token_id})
    req = urllib.request.Request(f"{CLOB_API}/book?{qs}", headers={"user-agent": "polymarket-task-harness/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    bids = [_level(row) for row in payload.get("bids") or []]
    asks = [_level(row) for row in payload.get("asks") or []]
    # Normalize orderbook order at the boundary; Gamma/CLOB snapshots have not
    # always arrived in the order the estimator expects.
    clean_bids = sorted([level for level in bids if level], key=lambda item: item.price, reverse=True)
    clean_asks = sorted([level for level in asks if level], key=lambda item: item.price)
    try:
        min_order_size = float(payload.get("min_order_size"))
    except (TypeError, ValueError):
        min_order_size = None
    return BookSnapshot(
        token_id=token_id,
        timestamp=str(payload.get("timestamp") or ""),
        best_bid=clean_bids[0].price if clean_bids else None,
        best_ask=clean_asks[0].price if clean_asks else None,
        min_order_size=min_order_size,
        bids=clean_bids,
        asks=clean_asks,
    )


def estimate_buy_profit(book: BookSnapshot, ai_prob: float, max_slippage_pct: float = 0.04) -> Dict[str, Any]:
    if book.best_ask is None:
        return {
            "best_ask": None,
            "edge_at_best_ask": None,
            "positive_edge_depth_usd": 0.0,
            "max_expected_profit_usd": 0.0,
            "levels_used": 0,
        }
    price_ceiling = min(ai_prob, book.best_ask * (1 + max_slippage_pct))
    notional = 0.0
    profit = 0.0
    levels_used = 0
    worst_price = book.best_ask
    for level in book.asks:
        # Only consume ask levels that still have positive expected value under
        # the supplied subjective probability and the slippage guardrail.
        if level.price > price_ceiling + 1e-12:
            break
        level_notional = level.price * level.size
        level_profit = (ai_prob - level.price) * level.size
        if level_profit <= 0:
            break
        notional += level_notional
        profit += level_profit
        worst_price = level.price
        levels_used += 1
    return {
        "best_ask": book.best_ask,
        "best_bid": book.best_bid,
        "edge_at_best_ask": ai_prob - book.best_ask,
        "price_ceiling": price_ceiling,
        "worst_price_used": worst_price if levels_used else None,
        "positive_edge_depth_usd": round(notional, 4),
        "max_expected_profit_usd": round(profit, 4),
        "levels_used": levels_used,
        "min_order_size": book.min_order_size,
        "book_timestamp": book.timestamp,
    }
