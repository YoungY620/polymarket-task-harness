from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List


DATA_API = "https://data-api.polymarket.com"


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
    raise RuntimeError(f"Polymarket Data API request failed: {last_error}")


def fetch_user_positions(position_address: str) -> List[Dict[str, Any]]:
    params = urllib.parse.urlencode({"user": position_address})
    payload = _fetch_json(f"{DATA_API}/positions?{params}")
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("positions") or []
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def position_market_slug(position: Dict[str, Any]) -> str:
    for key in ["marketSlug", "market_slug", "slug"]:
        value = position.get(key)
        if value:
            return str(value)
    market = position.get("market")
    if isinstance(market, dict):
        return str(market.get("slug") or market.get("market_slug") or market.get("marketSlug") or "")
    return ""
