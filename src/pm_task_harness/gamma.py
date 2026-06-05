from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List


GAMMA_API = "https://gamma-api.polymarket.com"
SORTS = ["volume24hr", "liquidity", "startDate", "competitive"]


def _fetch_json(url: str, retries: int = 3) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"user-agent": "polymarket-task-harness/0.1"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network dependent
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"Gamma request failed: {last_error}")


def _extract_markets(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    markets: List[Dict[str, Any]] = []
    for event in events:
        event_slug = event.get("slug") or ""
        event_title = event.get("title") or ""
        event_tags = event.get("tags") or []
        event_category = event.get("category") or ""
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            row = dict(market)
            row["_event_slug"] = event_slug
            row["_event_title"] = event_title
            row["event_slug"] = row.get("event_slug") or event_slug
            row["url"] = row.get("url") or f"https://polymarket.com/event/{event_slug}"
            row["tags"] = row.get("tags") or event_tags
            row["category_slug"] = row.get("category_slug") or row.get("category") or event_category
            markets.append(row)
    return markets


def fetch_gamma_markets(pages: int = 2, events_per_page: int = 50) -> Dict[str, Any]:
    seen = set()
    markets: List[Dict[str, Any]] = []
    for sort in SORTS:
        for page in range(pages):
            params = urllib.parse.urlencode(
                {
                    "active": "true",
                    "closed": "false",
                    "limit": min(events_per_page, 50),
                    "offset": page * events_per_page,
                    "order": sort,
                    "ascending": "false",
                }
            )
            events = _fetch_json(f"{GAMMA_API}/events?{params}")
            if not events:
                break
            for market in _extract_markets(events):
                key = market.get("id") or market.get("conditionId") or market.get("slug")
                if key in seen:
                    continue
                seen.add(key)
                markets.append(market)
            if len(events) < events_per_page:
                break
            time.sleep(0.2)
    return {
        "source": "gamma-events",
        "pages": pages,
        "events_per_page": events_per_page,
        "sorts": SORTS,
        "markets": markets,
    }

