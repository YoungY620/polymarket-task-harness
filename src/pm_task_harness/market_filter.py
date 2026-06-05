from __future__ import annotations

import json
import math
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .models import Market, Task


# Hard rejects are intentionally text-based: they describe market shapes that
# are outside this strategy, such as word-count props or short-term price bets.
# Positive selection should stay tag-based below, not keyword-based.
RANDOM_OR_ENTERTAINMENT_PATTERNS = [
    r"\b(say|says|mention|mentions|tweet|tweets|post|posts)\b.*\b(word|phrase|times?)\b",
    r"\bwhat will .* say\b",
    r"\b(up or down)\b",
    r"\b(host|podcast|interview|streamer|influencer)\b.*\b(say|mention)\b",
    r"\b(temperature|rain|snow) .* (day|week)\b",
]

SHORT_TERM_PRICE_PATTERNS = [
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|doge)\b.*\b(up or down|price|above|below)\b",
    r"\b(nasdaq|s&p|spy|qqq|tesla|nvidia)\b.*\b(up or down|above|below)\b",
]

DEFAULT_TAG_WEIGHTS: Mapping[str, float] = {
    "geopolitics": 1.6,
    "global-elections": 1.5,
    "world-elections": 1.5,
    "elections": 1.4,
    "politics": 1.3,
    "world": 1.1,
    "finance": 0.9,
    "economics": 0.9,
    "crypto": 0.25,
    "crypto-prices": 0.1,
    "sports": 0.1,
    "esports": 0.1,
    "pop-culture": 0.1,
}

DEFAULT_TYPE_WEIGHT = 0.4

# These are defensive penalties for obviously poor research domains. Keep this
# list narrow; preferred market types should be controlled by Polymarket tags
# and --tag-weights-json instead of growing another keyword ontology here.
BAD_TOPIC_KEYWORDS = [
    "alien",
    "aliens",
    "ufo",
    "extraterrestrial",
    "conspiracy",
    "nba",
    "nfl",
    "mlb",
    "nhl",
    "ufc",
    "champions league",
    "world cup winner",
    "grammy",
    "oscar",
    "box office",
]


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _as_float_list(value: Any) -> List[float]:
    out = []
    for item in _as_list(value):
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return out


def _as_str_list(value: Any) -> List[str]:
    return [str(item) for item in _as_list(value) if str(item)]


def _parse_days_to_end(end_date: str, now: datetime) -> float:
    # Missing or unparsable dates should not accidentally reject otherwise
    # researchable markets. Treat them as medium-horizon candidates.
    if not end_date:
        return 180.0
    try:
        end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
    except ValueError:
        return 180.0
    return (end - now).total_seconds() / 86400


def market_from_raw(raw: Dict[str, Any]) -> Market:
    # Gamma fields appear in several shapes depending on whether the row came
    # from an event expansion, market endpoint, or a saved task artifact.
    event_slug = str(raw.get("event_slug") or raw.get("_event_slug") or raw.get("eventSlug") or "")
    market_slug = str(raw.get("slug") or raw.get("market_slug") or raw.get("marketSlug") or "")
    question = str(raw.get("question") or raw.get("title") or "")
    tags = []
    for tag in raw.get("tags") or []:
        if isinstance(tag, dict):
            tags.append(str(tag.get("slug") or tag.get("label") or tag.get("name") or ""))
        else:
            tags.append(str(tag))
    return Market(
        id=str(raw.get("id") or raw.get("condition_id") or market_slug),
        event_slug=event_slug,
        market_slug=market_slug,
        question=question,
        description=str(raw.get("description") or ""),
        url=str(raw.get("url") or (f"https://polymarket.com/event/{event_slug}" if event_slug else "")),
        outcomes=_as_str_list(raw.get("outcomes")),
        outcome_prices=_as_float_list(raw.get("outcome_prices", raw.get("outcomePrices"))),
        clob_token_ids=_as_str_list(raw.get("clob_token_ids", raw.get("clobTokenIds"))),
        liquidity=float(raw.get("liquidity") or raw.get("liquidityNum") or 0),
        volume_24hr=float(raw.get("volume_24hr") or raw.get("volume24hr") or raw.get("volume24h") or 0),
        end_date=str(raw.get("end_date") or raw.get("endDate") or ""),
        category=str(raw.get("category_slug") or raw.get("category") or ""),
        tags=[tag for tag in tags if tag],
    )


def load_markets(payload: Any) -> List[Market]:
    if isinstance(payload, dict):
        rows = payload.get("markets") or payload.get("data") or []
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("market payload must be a list or an object with a markets array")
    return [market_from_raw(row) for row in rows if isinstance(row, dict)]


def _matches_any(text: str, patterns: Sequence[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _topic_hits(text: str, keywords: Sequence[str]) -> List[str]:
    lower = text.lower()
    return [keyword for keyword in keywords if keyword in lower]


def _dedupe_key(market: Market) -> str:
    # De-dupe by question text first because sibling events can expose the same
    # market under different slugs. This is only an intra-filter-run de-dupe;
    # history/cooldown belongs in a future portfolio or scheduler layer.
    normalized = re.sub(r"\W+", " ", market.question.lower()).strip()
    return normalized[:120] or market.event_slug or market.market_slug


def _market_type_weight(market: Market, tag_weights: Mapping[str, float]) -> Tuple[str, float]:
    # Preferred market type is derived from Polymarket metadata, not the market
    # question text. If selection philosophy changes, update this comment and
    # test_uses_polymarket_tags_not_topic_keywords_for_preferred_types together.
    candidates = [tag.lower() for tag in market.tags if tag]
    if market.category:
        candidates.append(market.category.lower())
    best_type = "other"
    best_weight = DEFAULT_TYPE_WEIGHT
    for tag in candidates:
        weight = tag_weights.get(tag)
        if weight is not None and (best_type == "other" or weight > best_weight):
            best_type = tag
            best_weight = weight
    return best_type, best_weight


def score_market(
    market: Market,
    now: datetime,
    tag_weights: Mapping[str, float] = DEFAULT_TAG_WEIGHTS,
) -> Tuple[float, str, List[str]]:
    text = " ".join([market.question, market.description, market.category, " ".join(market.tags)]).lower()

    # Quality gates first: rejected markets should never reach the weighted
    # sampler, no matter how common their tag is in the current Gamma snapshot.
    if _matches_any(text, RANDOM_OR_ENTERTAINMENT_PATTERNS):
        return -100.0, "rejected", ["rejected: random host/word-count style market"]
    if _matches_any(text, SHORT_TERM_PRICE_PATTERNS):
        return -100.0, "rejected", ["rejected: short-term price prediction"]
    if _topic_hits(text, BAD_TOPIC_KEYWORDS):
        return -20.0, "penalized", ["penalized: sports/entertainment topic"]
    if len(market.outcomes) < 2 or len(market.outcome_prices) < 2 or len(market.clob_token_ids) < 2:
        return -100.0, "rejected", ["rejected: missing executable outcome/token data"]
    if market.liquidity < 5_000:
        return -100.0, "rejected", ["rejected: liquidity below $5,000"]
    min_price = min(market.outcome_prices)
    max_price = max(market.outcome_prices)
    if min_price < 0.04 or max_price > 0.96:
        return -100.0, "rejected", ["rejected: nearly resolved price"]

    days = _parse_days_to_end(market.end_date, now)
    if days < 14:
        return -100.0, "rejected", ["rejected: expires too soon for careful research"]

    reasons: List[str] = []
    score = 0.0
    market_type, type_weight = _market_type_weight(market, tag_weights)

    # Low-priority tags are strategy exclusions in practice. They are kept as
    # weights rather than separate hard-coded branches so the CLI config can
    # re-enable them during experiments.
    if type_weight < DEFAULT_TYPE_WEIGHT:
        return -20.0, market_type, [f"penalized: low-priority Polymarket tag {market_type} ({type_weight:.2f})"]
    if type_weight > DEFAULT_TYPE_WEIGHT:
        score += 3.5 * type_weight
        reasons.append(f"tag-weighted type: {market_type} ({type_weight:.2f})")
    else:
        score -= 2.0
        reasons.append("no preferred Polymarket tag")

    if 30 <= days <= 540:
        score += 2.0
        reasons.append(f"researchable horizon: {days:.0f} days")
    elif days > 540:
        score += 0.5
        reasons.append(f"long horizon may tie up capital: {days:.0f} days")

    if "?" in market.question and len(market.question) >= 35:
        score += 1.0
        reasons.append("specific but nontrivial market question")
    if any(0.08 <= p <= 0.25 for p in market.outcome_prices):
        score += 1.2
        reasons.append("contains longshot side worth checking for No bias")

    score += min(math.log10(market.liquidity + 1) - 3.5, 2.5)
    score += min(math.log10(market.volume_24hr + 1) - 2.5, 1.5)
    return score, market_type, reasons


def select_tasks(
    markets: Iterable[Market],
    max_tasks: int = 3,
    min_score: float = 4.0,
    tag_weights: Mapping[str, float] = DEFAULT_TAG_WEIGHTS,
    seed: int | str | None = None,
) -> List[Task]:
    # Selection has two stages:
    # 1. score and de-dupe executable markets;
    # 2. sample across market types so broad buckets with many candidates get
    #    explored, while each bucket still contributes its strongest candidates.
    now = datetime.now(timezone.utc)
    best_by_key: Dict[str, Tuple[Market, float, str, List[str]]] = {}
    for market in markets:
        score, market_type, reasons = score_market(market, now, tag_weights=tag_weights)
        if score < min_score:
            continue
        key = _dedupe_key(market)
        existing = best_by_key.get(key)
        if existing is None or score > existing[1]:
            best_by_key[key] = (market, score, market_type, reasons)

    ranked = _weighted_type_order(best_by_key.values(), tag_weights=tag_weights, seed=seed)
    tasks: List[Task] = []
    for market, score, market_type, reasons in ranked[:max_tasks]:
        task_id = market.market_slug or market.id
        tasks.append(
            Task(
                task_id=task_id,
                market=market,
                score=round(score, 4),
                market_type=market_type,
                reasons=reasons,
                completion_standard={
                    "format": "strict_json",
                    "must_include": [
                        "decision",
                        "outcome",
                        "ai_prob",
                        "market_prob",
                        "edge",
                        "confidence",
                        "evidence_table",
                        "sources",
                    ],
                    "decision_values": ["buy_yes", "buy_no", "skip"],
                },
            )
        )
    return tasks


def _weighted_type_order(
    candidates: Iterable[Tuple[Market, float, str, List[str]]],
    tag_weights: Mapping[str, float],
    seed: int | str | None = None,
) -> List[Tuple[Market, float, str, List[str]]]:
    rng = random.Random(seed)
    buckets: Dict[str, List[Tuple[Market, float, str, List[str]]]] = {}
    for candidate in candidates:
        buckets.setdefault(candidate[2], []).append(candidate)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: item[1], reverse=True)

    ordered: List[Tuple[Market, float, str, List[str]]] = []
    while buckets:
        types = list(buckets)
        # The user's intended exploration rule: a type's draw probability is
        # proportional to tag_weight * remaining_candidate_count. After a type
        # is drawn, we take its highest-scored remaining market to keep quality
        # from collapsing into pure randomness.
        weights = [
            max(float(tag_weights.get(market_type, DEFAULT_TYPE_WEIGHT)), 0.0) * len(buckets[market_type])
            for market_type in types
        ]
        if sum(weights) <= 0:
            weights = [float(len(buckets[market_type])) for market_type in types]
        chosen_type = rng.choices(types, weights=weights, k=1)[0]
        ordered.append(buckets[chosen_type].pop(0))
        if not buckets[chosen_type]:
            del buckets[chosen_type]
    return ordered
