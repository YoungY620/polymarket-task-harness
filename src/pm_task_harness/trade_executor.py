from __future__ import annotations

import argparse
import json
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


@dataclass(frozen=True)
class TradeRequest:
    schema_version: int
    request_id: str
    created_at_utc: str
    expires_at_utc: str
    market_slug: str
    event_slug: str
    outcome: str
    token_id: str
    side: str
    amount_usd: float
    max_price: float | None
    source: str
    reason: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_request_id() -> str:
    return f"trade-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:10]}"


def request_from_plan(market: Any, plan: Any, *, source: str, reason: str, expires_at_utc: str) -> TradeRequest | None:
    if plan.action == "hold" or abs(float(plan.trade_notional)) < 1e-6:
        return None
    side = "BUY" if plan.trade_notional > 0 else "SELL"
    return TradeRequest(
        schema_version=1,
        request_id=new_request_id(),
        created_at_utc=utc_now_iso(),
        expires_at_utc=expires_at_utc,
        market_slug=market.market_slug,
        event_slug=market.event_slug,
        outcome=plan.outcome,
        token_id=plan.token_id,
        side=side,
        amount_usd=round(abs(float(plan.trade_notional)), 6),
        max_price=float(plan.market_price) if side == "BUY" else None,
        source=source,
        reason=reason,
    )


def write_request(inbox: Path, request: TradeRequest) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{request.request_id}.json"
    path.write_text(json.dumps(asdict(request), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def parse_iso(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def load_ledger(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "executed": [], "rejected": []}
    payload = read_json(path)
    payload.setdefault("schema_version", 1)
    payload.setdefault("executed", [])
    payload.setdefault("rejected", [])
    return payload


def save_ledger(path: Path, ledger: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")


def executed_buy_total(ledger: Dict[str, Any]) -> float:
    total = 0.0
    for row in ledger.get("executed", []):
        if row.get("side") == "BUY":
            total += float(row.get("amount_usd") or 0.0)
    return total


def validate_request(
    request: Dict[str, Any],
    ledger: Dict[str, Any],
    *,
    max_trade_usd: float,
    max_total_buy_usd: float,
    allowed_sources: set[str] | None = None,
) -> List[str]:
    errors: List[str] = []
    if request.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for field in ["request_id", "created_at_utc", "expires_at_utc", "market_slug", "event_slug", "outcome", "token_id", "side", "source"]:
        if not str(request.get(field) or "").strip():
            errors.append(f"{field} is required")
    if request.get("side") not in {"BUY", "SELL"}:
        errors.append("side must be BUY or SELL")
    if request.get("outcome") not in {"Yes", "No"}:
        errors.append("outcome must be Yes or No")
    try:
        amount = float(request.get("amount_usd"))
    except (TypeError, ValueError):
        amount = -1.0
    if amount <= 0:
        errors.append("amount_usd must be positive")
    if amount > max_trade_usd:
        errors.append(f"amount_usd exceeds max_trade_usd={max_trade_usd}")
    if request.get("side") == "BUY" and executed_buy_total(ledger) + amount > max_total_buy_usd:
        errors.append(f"BUY total would exceed max_total_buy_usd={max_total_buy_usd}")
    if request.get("side") == "BUY":
        try:
            max_price = float(request.get("max_price"))
        except (TypeError, ValueError):
            max_price = -1.0
        if max_price <= 0 or max_price >= 1:
            errors.append("BUY requests require max_price between 0 and 1")
    if allowed_sources is not None and request.get("source") not in allowed_sources:
        errors.append(f"source must be one of {sorted(allowed_sources)}")
    try:
        expires_at = parse_iso(str(request.get("expires_at_utc")), "expires_at_utc")
        if expires_at <= datetime.now(timezone.utc):
            errors.append("request is expired")
    except ValueError as exc:
        errors.append(str(exc))
    seen_ids = {row.get("request_id") for row in ledger.get("executed", [])}
    seen_ids.update(row.get("request_id") for row in ledger.get("rejected", []))
    if request.get("request_id") in seen_ids:
        errors.append("request_id was already processed")
    return errors


def run_execution_command(command: str, request: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
    env = dict(os.environ)
    env["PM_TRADE_REQUEST_JSON"] = json.dumps(request, ensure_ascii=False)
    result = subprocess.run(
        command,
        input=json.dumps(request, ensure_ascii=False),
        shell=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_seconds,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def process_request_file(args: argparse.Namespace, path: Path, ledger: Dict[str, Any]) -> None:
    request = read_json(path)
    errors = validate_request(
        request,
        ledger,
        max_trade_usd=args.max_trade_usd,
        max_total_buy_usd=args.max_total_buy_usd,
        allowed_sources=set(args.allowed_source) if args.allowed_source else None,
    )
    record = {
        "processed_at_utc": utc_now_iso(),
        "request_file": str(path),
        **request,
    }
    if errors:
        ledger.setdefault("rejected", []).append({**record, "errors": errors})
        return

    if args.dry_run:
        ledger.setdefault("executed", []).append({**record, "dry_run": True, "execution": None})
        return
    if not args.command:
        raise RuntimeError("--command is required unless --dry-run is set")
    execution = run_execution_command(args.command, request, args.timeout_seconds)
    if execution["returncode"] != 0:
        ledger.setdefault("rejected", []).append({**record, "errors": ["execution command failed"], "execution": execution})
        return
    ledger.setdefault("executed", []).append({**record, "dry_run": False, "execution": execution})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Privileged Polymarket trade-request executor")
    parser.add_argument("--inbox", default="runtime-artifacts/trade-requests")
    parser.add_argument("--ledger", default="runtime-artifacts/trade-execution-ledger.json")
    parser.add_argument("--glob", default="*.json")
    parser.add_argument("--max-trade-usd", type=float, default=10.0)
    parser.add_argument("--max-total-buy-usd", type=float, default=50.0)
    parser.add_argument("--allowed-source", action="append", default=["portfolio_loop"])
    parser.add_argument("--command", default=os.environ.get("PM_TRADE_EXECUTOR_COMMAND", ""))
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    inbox = Path(args.inbox)
    ledger_path = Path(args.ledger)
    ledger = load_ledger(ledger_path)
    for path in sorted(inbox.glob(args.glob)):
        process_request_file(args, path, ledger)
    save_ledger(ledger_path, ledger)
    print(f"processed requests -> {ledger_path}")


if __name__ == "__main__":
    main()
