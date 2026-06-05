from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .models import Task, ValidationResult


REQUIRED_FIELDS = [
    "decision",
    "outcome",
    "ai_prob",
    "market_prob",
    "edge",
    "confidence",
    "thesis",
    "evidence_table",
    "sources",
]


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    # Be tolerant at the harness boundary: agent CLIs often prepend reasoning
    # despite the prompt. The executable artifact is the extracted JSON object.
    # Tighten this only if downstream execution requires raw-output purity.
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(stripped[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def validate_agent_output(text: str, task: Task, min_edge_to_buy: float = 0.08) -> ValidationResult:
    parsed = extract_json_object(text)
    if parsed is None:
        return ValidationResult(False, ["output is not a JSON object"])

    errors: List[str] = []
    for field in REQUIRED_FIELDS:
        if field not in parsed:
            errors.append(f"missing field: {field}")

    decision = parsed.get("decision")
    if decision not in {"buy_yes", "buy_no", "skip"}:
        errors.append("decision must be one of buy_yes, buy_no, skip")

    outcome = parsed.get("outcome")
    if decision == "buy_yes" and str(outcome).lower() != "yes":
        errors.append("buy_yes decision must use outcome Yes")
    if decision == "buy_no" and str(outcome).lower() != "no":
        errors.append("buy_no decision must use outcome No")

    for field in ["ai_prob", "market_prob", "edge"]:
        value = parsed.get(field)
        if not isinstance(value, (int, float)):
            errors.append(f"{field} must be numeric")
    if isinstance(parsed.get("ai_prob"), (int, float)) and not 0 <= parsed["ai_prob"] <= 1:
        errors.append("ai_prob must be between 0 and 1")
    if isinstance(parsed.get("market_prob"), (int, float)) and not 0 <= parsed["market_prob"] <= 1:
        errors.append("market_prob must be between 0 and 1")
    if all(isinstance(parsed.get(field), (int, float)) for field in ["ai_prob", "market_prob", "edge"]):
        implied = round(float(parsed["ai_prob"]) - float(parsed["market_prob"]), 6)
        if abs(implied - float(parsed["edge"])) > 0.015:
            errors.append("edge must equal ai_prob - market_prob within 1.5 percentage points")
        if decision != "skip" and abs(float(parsed["edge"])) < min_edge_to_buy:
            errors.append(f"buy decisions require |edge| >= {min_edge_to_buy}")

    evidence = parsed.get("evidence_table")
    if not isinstance(evidence, list) or len(evidence) < 3:
        errors.append("evidence_table must contain at least 3 rows including base and final")
    sources = parsed.get("sources")
    if not isinstance(sources, list) or len(sources) < 2:
        errors.append("sources must contain at least 2 source objects")

    market = task.market
    # Token validation is what turns a research answer into something an
    # execution layer can safely map to the correct CLOB instrument.
    token_ids = {
        label.lower(): token
        for label, token in zip(market.outcomes, market.clob_token_ids)
    }
    if decision in {"buy_yes", "buy_no"}:
        wanted = "yes" if decision == "buy_yes" else "no"
        if wanted not in token_ids:
            errors.append(f"market does not expose a {wanted} token")
        if parsed.get("token_id") and parsed["token_id"] != token_ids.get(wanted):
            errors.append("token_id does not match chosen outcome")

    return ValidationResult(ok=not errors, errors=errors, parsed=parsed)


def validate_fair_value_output(text: str) -> ValidationResult:
    parsed = extract_json_object(text)
    if parsed is None:
        return ValidationResult(False, ["output is not a JSON object"])

    errors: List[str] = []
    yes_prob = parsed.get("yes_prob")
    if not isinstance(yes_prob, (int, float)):
        errors.append("yes_prob must be numeric")
    elif not 0 <= yes_prob <= 1:
        errors.append("yes_prob must be between 0 and 1")
    for field in ["confidence", "thesis", "evidence_table", "sources"]:
        if field not in parsed:
            errors.append(f"missing field: {field}")
    evidence = parsed.get("evidence_table")
    if not isinstance(evidence, list) or len(evidence) < 3:
        errors.append("evidence_table must contain at least 3 rows including base and final")
    sources = parsed.get("sources")
    if not isinstance(sources, list) or len(sources) < 2:
        errors.append("sources must contain at least 2 source objects")
    return ValidationResult(ok=not errors, errors=errors, parsed=parsed)
