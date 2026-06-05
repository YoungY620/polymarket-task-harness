from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Market:
    id: str
    event_slug: str
    market_slug: str
    question: str
    description: str
    url: str
    outcomes: List[str]
    outcome_prices: List[float]
    clob_token_ids: List[str]
    liquidity: float
    volume_24hr: float
    end_date: str
    category: str
    tags: List[str]


@dataclass(frozen=True)
class Task:
    task_id: str
    market: Market
    score: float
    market_type: str
    reasons: List[str]
    completion_standard: Dict[str, Any]


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: List[str]
    parsed: Optional[Dict[str, Any]] = None
