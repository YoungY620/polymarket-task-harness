from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Optional

from .market_filter import market_from_raw
from .models import Market


@dataclass(frozen=True)
class RebalancePlan:
    action: str
    outcome: str
    token_id: str
    fair_prob: float
    market_price: float
    edge: float
    kelly_fraction: float
    target_notional: float
    current_notional: float
    trade_notional: float
    target_shares: float
    trade_shares: float


def load_portfolio(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "usdc": 0.0, "positions": [], "trade_log": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("schema_version", 1)
    payload.setdefault("usdc", 0.0)
    payload.setdefault("positions", [])
    payload.setdefault("trade_log", [])
    return payload


def save_portfolio(path: Path, portfolio: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    portfolio["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(portfolio, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_git_repo(repo_dir: Path) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, stdout=subprocess.PIPE, text=True)


def _git_env(index_file: str | None = None) -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "portfolio-loop")
    env.setdefault("GIT_AUTHOR_EMAIL", "portfolio-loop@local")
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    if index_file:
        env["GIT_INDEX_FILE"] = index_file
    return env


def _git(repo_dir: Path, args: List[str], *, env: Dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        env=env,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def commit_portfolio(path: Path, message: str, repo_dir: Path | None = None, branch: str = "paper-portfolio") -> bool:
    repo_dir = repo_dir or Path.cwd()
    ensure_git_repo(repo_dir)

    rel_path = path.resolve().relative_to(repo_dir.resolve()).as_posix()
    branch_ref = f"refs/heads/{branch}"
    branch_exists = _git(repo_dir, ["show-ref", "--verify", "--quiet", branch_ref], check=False).returncode == 0
    current_blob = _git(repo_dir, ["hash-object", "-w", str(path.resolve())]).stdout.strip()
    old_blob = _git(repo_dir, ["rev-parse", f"{branch}:{rel_path}"], check=False) if branch_exists else None
    if old_blob is not None and old_blob.returncode == 0 and old_blob.stdout.strip() == current_blob:
        return False

    if not branch_exists:
        with TemporaryDirectory(prefix="portfolio-index-") as tmp:
            env = _git_env(str(Path(tmp) / "index"))
            head = _git(repo_dir, ["rev-parse", "--verify", "HEAD"], check=False)
            parent = head.stdout.strip() if head.returncode == 0 else ""
            if parent:
                _git(repo_dir, ["read-tree", "HEAD"], env=env)
            _git(repo_dir, ["update-index", "--add", "--cacheinfo", "100644", current_blob, rel_path], env=env)
            tree = _git(repo_dir, ["write-tree"], env=env).stdout.strip()
            commit_args = ["commit-tree", tree, "-m", message]
            if parent:
                commit_args[2:2] = ["-p", parent]
            commit = _git(repo_dir, commit_args, env=env).stdout.strip()
            _git(repo_dir, ["update-ref", branch_ref, commit])
        return True

    current_bytes = path.read_bytes()
    with TemporaryDirectory(prefix="portfolio-worktree-") as tmp:
        worktree = Path(tmp) / "branch"
        try:
            _git(repo_dir, ["worktree", "add", "--force", "--detach", str(worktree), branch])
            target = worktree / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(current_bytes)
            _git(worktree, ["add", rel_path])
            commit = _git(worktree, ["commit", "-m", message], env=_git_env(), check=False)
            if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr).lower():
                raise RuntimeError(commit.stderr or commit.stdout)
            new_head = _git(worktree, ["rev-parse", "HEAD"]).stdout.strip()
            _git(repo_dir, ["update-ref", branch_ref, new_head])
        finally:
            _git(repo_dir, ["worktree", "remove", "--force", str(worktree)], check=False)
    return True


def market_price_for_outcome(market: Market, outcome: str) -> tuple[float, str]:
    wanted = outcome.lower()
    for label, price, token_id in zip(market.outcomes, market.outcome_prices, market.clob_token_ids):
        if label.lower() == wanted:
            return float(price), token_id
    raise ValueError(f"market does not expose outcome {outcome!r}")


def outcome_probability(yes_prob: float, outcome: str) -> float:
    return yes_prob if outcome.lower() == "yes" else 1.0 - yes_prob


def portfolio_nav(portfolio: Dict[str, Any]) -> float:
    nav = float(portfolio.get("usdc") or 0.0)
    for position in portfolio.get("positions", []):
        nav += float(position.get("shares") or 0.0) * float(position.get("last_price") or position.get("avg_price") or 0.0)
    return nav


def calculate_rebalance_plan(
    market: Market,
    outcome: str,
    yes_prob: float,
    current_shares: float,
    nav: float,
    *,
    kelly_fraction: float = 0.25,
    max_market_fraction: float = 0.05,
    min_new_edge: float = 0.08,
    is_new: bool = False,
) -> RebalancePlan:
    market_price, token_id = market_price_for_outcome(market, outcome)
    fair_prob = outcome_probability(yes_prob, outcome)
    edge = fair_prob - market_price
    raw_kelly = max(0.0, edge / max(1.0 - market_price, 1e-9))
    effective_kelly = min(raw_kelly * kelly_fraction, max_market_fraction)
    if is_new and edge < min_new_edge:
        effective_kelly = 0.0
    target_notional = max(nav, 0.0) * effective_kelly
    current_notional = current_shares * market_price
    trade_notional = target_notional - current_notional
    target_shares = target_notional / market_price if market_price > 0 else 0.0
    trade_shares = target_shares - current_shares
    if abs(trade_notional) < 1e-6:
        action = "hold"
    elif trade_notional > 0:
        action = "buy"
    elif target_notional <= 1e-6:
        action = "sell_all"
    else:
        action = "sell"
    return RebalancePlan(
        action=action,
        outcome=outcome,
        token_id=token_id,
        fair_prob=round(fair_prob, 6),
        market_price=round(market_price, 6),
        edge=round(edge, 6),
        kelly_fraction=round(effective_kelly, 6),
        target_notional=round(target_notional, 6),
        current_notional=round(current_notional, 6),
        trade_notional=round(trade_notional, 6),
        target_shares=round(target_shares, 6),
        trade_shares=round(trade_shares, 6),
    )


def choose_new_market_plan(
    market: Market,
    yes_prob: float,
    nav: float,
    *,
    kelly_fraction: float = 0.25,
    max_market_fraction: float = 0.05,
    min_new_edge: float = 0.08,
) -> RebalancePlan:
    yes_plan = calculate_rebalance_plan(
        market,
        "Yes",
        yes_prob,
        0.0,
        nav,
        kelly_fraction=kelly_fraction,
        max_market_fraction=max_market_fraction,
        min_new_edge=min_new_edge,
        is_new=True,
    )
    no_plan = calculate_rebalance_plan(
        market,
        "No",
        yes_prob,
        0.0,
        nav,
        kelly_fraction=kelly_fraction,
        max_market_fraction=max_market_fraction,
        min_new_edge=min_new_edge,
        is_new=True,
    )
    return max([yes_plan, no_plan], key=lambda plan: plan.edge)


def apply_rebalance(portfolio: Dict[str, Any], market: Market, plan: RebalancePlan, source: str) -> None:
    positions: List[Dict[str, Any]] = portfolio.setdefault("positions", [])
    match: Optional[Dict[str, Any]] = None
    for position in positions:
        if position.get("market_slug") == market.market_slug and position.get("outcome") == plan.outcome:
            match = position
            break

    if match is None and plan.target_shares > 0:
        match = {
            "market_slug": market.market_slug,
            "event_slug": market.event_slug,
            "outcome": plan.outcome,
            "token_id": plan.token_id,
            "shares": 0.0,
            "avg_price": plan.market_price,
            "market": asdict(market),
        }
        positions.append(match)
    if match is None:
        return

    old_shares = float(match.get("shares") or 0.0)
    portfolio["usdc"] = round(float(portfolio.get("usdc") or 0.0) - plan.trade_notional, 6)
    match["shares"] = plan.target_shares
    match["last_price"] = plan.market_price
    match["last_fair_prob"] = plan.fair_prob
    match["last_edge"] = plan.edge
    match["last_reviewed_at_utc"] = datetime.now(timezone.utc).isoformat()
    match["market"] = asdict(market)
    if old_shares <= 0 and plan.target_shares > 0:
        match["avg_price"] = plan.market_price
    if plan.target_shares <= 1e-6:
        positions.remove(match)

    portfolio.setdefault("trade_log", []).append(
        {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "market_slug": market.market_slug,
            **asdict(plan),
        }
    )


def position_market(position: Dict[str, Any]) -> Market:
    return market_from_raw(position.get("market") or position)
