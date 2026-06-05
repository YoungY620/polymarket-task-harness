from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

from .agent_runner import AgentRunner
from .clob import estimate_buy_profit, fetch_orderbook
from .gamma import fetch_gamma_markets
from .market_filter import DEFAULT_TAG_WEIGHTS, load_markets, select_tasks
from .portfolio import (
    apply_rebalance,
    calculate_rebalance_plan,
    choose_new_market_plan,
    commit_portfolio,
    ensure_git_repo,
    load_portfolio,
    portfolio_nav,
    position_market,
    save_portfolio,
)
from .positions import fetch_user_positions, position_market_slug
from .prompts import build_chinese_report_prompt, build_fair_value_prompt, build_repair_prompt, build_task_prompt
from .report_policy import DEFAULT_POSITION_ADDRESS
from .validator import validate_agent_output, validate_fair_value_output


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILL = ROOT / "skills" / "polymarket_research_skill.md"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _task_to_json(task) -> dict:
    data = asdict(task)
    return data


def _load_tag_weights(path: str | None) -> dict[str, float]:
    # Tag weights are externalized so strategy experiments do not require code
    # edits. The keys are Polymarket tag slugs, not our own keyword categories.
    if not path:
        return dict(DEFAULT_TAG_WEIGHTS)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("--tag-weights-json must point to an object like {'geopolitics': 1.6}")
    weights: dict[str, float] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise ValueError("tag weight keys must be tag slugs")
        try:
            weights[key.lower()] = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"tag weight for {key!r} must be numeric") from exc
    return weights


def cmd_discover(args: argparse.Namespace) -> None:
    payload = fetch_gamma_markets(pages=args.pages, events_per_page=args.events_per_page)
    _write_json(Path(args.out), payload)
    print(f"wrote {len(payload['markets'])} raw markets to {args.out}")


def cmd_filter(args: argparse.Namespace) -> None:
    payload = json.loads(Path(args.markets).read_text(encoding="utf-8"))
    tasks = select_tasks(
        load_markets(payload),
        max_tasks=args.max_tasks,
        min_score=args.min_score,
        tag_weights=_load_tag_weights(args.tag_weights_json),
        seed=args.seed,
    )
    _write_json(Path(args.out), {"tasks": [_task_to_json(task) for task in tasks]})
    print(f"selected {len(tasks)} task(s) -> {args.out}")


def _load_tasks(path: Path):
    # Rehydrate task artifacts through the same Market parser used by discovery
    # output. This keeps run-time behavior aligned with filter-time behavior.
    payload = json.loads(path.read_text(encoding="utf-8"))
    markets = []
    for row in payload.get("tasks", []):
        market = row.get("market", {})
        market["slug"] = market.get("market_slug")
        market["event_slug"] = market.get("event_slug")
        markets.append(market)
    selected = select_tasks(load_markets(markets), max_tasks=len(markets), min_score=-999)
    by_slug = {task.market.market_slug: task for task in selected}
    tasks = []
    for row in payload.get("tasks", []):
        task = by_slug.get(row.get("market", {}).get("market_slug"))
        if task:
            tasks.append(task)
    return tasks


def cmd_run(args: argparse.Namespace) -> None:
    tasks = _load_tasks(Path(args.tasks))
    out_dir = Path(args.out_dir)
    skill = Path(args.skill)
    summary: List[dict] = []

    for task in tasks:
        task_dir = out_dir / task.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        prompt = build_task_prompt(task, skill)
        (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        if args.dry_run:
            summary.append({"task_id": task.task_id, "status": "dry_run", "prompt": str(task_dir / "prompt.txt")})
            continue

        runner = AgentRunner(args.provider, task_dir, model=args.model, timeout_seconds=args.task_timeout_seconds)
        output_path = task_dir / "agent-output-0.txt"
        started_at = datetime.now(timezone.utc)
        result = runner.run(prompt, output_path, resume=False)
        ended_at = datetime.now(timezone.utc)
        validation = validate_agent_output(result.output, task)

        # Repair loops intentionally resume the same agent session. The goal is
        # format repair in the original context, not a fresh research attempt.
        repair_index = 0
        while not validation.ok and repair_index < args.max_repairs:
            repair_index += 1
            repair_prompt = build_repair_prompt(result.output, validation.errors)
            (task_dir / f"repair-{repair_index}.txt").write_text(repair_prompt, encoding="utf-8")
            result = runner.run(repair_prompt, task_dir / f"agent-output-{repair_index}.txt", resume=True)
            validation = validate_agent_output(result.output, task)

        if validation.ok:
            _write_json(task_dir / "decision.json", validation.parsed)
        timing = {
            "started_at_utc": started_at.isoformat(),
            "ended_at_utc": ended_at.isoformat(),
            "duration_seconds": round((ended_at - started_at).total_seconds(), 3),
            "provider": args.provider,
            "returncode": result.returncode,
        }
        _write_json(task_dir / "timing.json", timing)
        _write_json(
            task_dir / "validation.json",
            {"ok": validation.ok, "errors": validation.errors, "repairs": repair_index},
        )
        summary.append(
            {
                "task_id": task.task_id,
                "ok": validation.ok,
                "errors": validation.errors,
                "repairs": repair_index,
                "dir": str(task_dir),
            }
        )

    _write_json(out_dir / "summary.json", {"tasks": summary})
    print(f"run summary -> {out_dir / 'summary.json'}")


def _task_from_market(market, reason: str):
    return select_tasks([market], max_tasks=1, min_score=-999)[0]


def _run_fair_value_review(args: argparse.Namespace, task, purpose: str, task_dir: Path):
    prompt = build_fair_value_prompt(task, Path(args.skill), purpose=purpose)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    if args.dry_run:
        return None

    runner = AgentRunner(args.provider, task_dir, model=args.model, timeout_seconds=args.task_timeout_seconds)
    result = runner.run(prompt, task_dir / "agent-output-0.txt", resume=False)
    validation = validate_fair_value_output(result.output)
    repair_index = 0
    while not validation.ok and repair_index < args.max_repairs:
        repair_index += 1
        repair_prompt = build_repair_prompt(result.output, validation.errors)
        (task_dir / f"repair-{repair_index}.txt").write_text(repair_prompt, encoding="utf-8")
        result = runner.run(repair_prompt, task_dir / f"agent-output-{repair_index}.txt", resume=True)
        validation = validate_fair_value_output(result.output)
    _write_json(task_dir / "validation.json", {"ok": validation.ok, "errors": validation.errors, "repairs": repair_index})
    if validation.ok:
        _write_json(task_dir / "fair-value.json", validation.parsed)
        return validation.parsed
    return None


def cmd_loop(args: argparse.Namespace) -> None:
    if args.live:
        raise RuntimeError("live portfolio sync/execution is not implemented yet; omit --live for paper mode")

    portfolio_path = Path(args.portfolio)
    ensure_git_repo(Path.cwd())
    iteration = 0
    while args.iterations == 0 or iteration < args.iterations:
        iteration += 1
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        loop_dir = Path(args.out_dir) / f"loop-{run_id}"
        portfolio = load_portfolio(portfolio_path)

        raw = fetch_gamma_markets(pages=args.pages, events_per_page=args.events_per_page)
        _write_json(loop_dir / "markets.json", raw)
        current_markets = load_markets(raw)
        by_slug = {market.market_slug: market for market in current_markets}

        for position in list(portfolio.get("positions", [])):
            market = by_slug.get(position.get("market_slug")) or position_market(position)
            task = _task_from_market(market, "position_review")
            fair = _run_fair_value_review(args, task, "position_review", loop_dir / "positions" / task.task_id)
            if fair is None:
                continue
            plan = calculate_rebalance_plan(
                market,
                str(position.get("outcome")),
                float(fair["yes_prob"]),
                float(position.get("shares") or 0.0),
                portfolio_nav(portfolio),
                kelly_fraction=args.kelly_fraction,
                max_market_fraction=args.max_market_fraction,
            )
            apply_rebalance(portfolio, market, plan, source="position_review")

        held_slugs = {position.get("market_slug") for position in portfolio.get("positions", [])}
        candidates = [market for market in current_markets if market.market_slug not in held_slugs]
        new_tasks = select_tasks(
            candidates,
            max_tasks=args.new_markets,
            min_score=args.min_score,
            tag_weights=_load_tag_weights(args.tag_weights_json),
            seed=f"{args.seed or ''}-{run_id}",
        )
        _write_json(loop_dir / "new-tasks.json", {"tasks": [_task_to_json(task) for task in new_tasks]})
        for task in new_tasks:
            fair = _run_fair_value_review(args, task, "new_market", loop_dir / "new" / task.task_id)
            if fair is None:
                continue
            plan = choose_new_market_plan(
                task.market,
                float(fair["yes_prob"]),
                portfolio_nav(portfolio),
                kelly_fraction=args.kelly_fraction,
                max_market_fraction=args.max_market_fraction,
                min_new_edge=args.min_new_edge,
            )
            apply_rebalance(portfolio, task.market, plan, source="new_market")

        save_portfolio(portfolio_path, portfolio)
        committed = commit_portfolio(
            portfolio_path,
            f"Update paper portfolio {run_id}",
            repo_dir=Path.cwd(),
            branch=args.portfolio_branch,
        )
        _write_json(
            loop_dir / "summary.json",
            {
                "portfolio": str(portfolio_path),
                "committed": committed,
                "live": args.live,
            },
        )
        print(f"loop {iteration} complete -> {loop_dir / 'summary.json'}")
        if args.iterations == 0 or iteration < args.iterations:
            time.sleep(args.sleep_seconds)


def cmd_report(args: argparse.Namespace) -> None:
    iteration = 0
    while args.iterations == 0 or iteration < args.iterations:
        iteration += 1
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_dir = Path(args.out_dir) / f"report-{run_id}"
        report_dir.mkdir(parents=True, exist_ok=True)

        positions = fetch_user_positions(args.position_address)
        _write_json(report_dir / "positions.json", {"address": args.position_address, "positions": positions})

        raw = fetch_gamma_markets(pages=args.pages, events_per_page=args.events_per_page)
        _write_json(report_dir / "markets.json", raw)
        held_slugs = {position_market_slug(position) for position in positions}
        markets = [market for market in load_markets(raw) if market.market_slug not in held_slugs]
        new_tasks = select_tasks(
            markets,
            max_tasks=args.new_markets,
            min_score=args.min_score,
            tag_weights=_load_tag_weights(args.tag_weights_json),
            seed=f"{args.seed or ''}-{run_id}",
        )
        _write_json(report_dir / "new-tasks.json", {"tasks": [_task_to_json(task) for task in new_tasks]})

        prompt = build_chinese_report_prompt(
            position_address=args.position_address,
            positions=positions,
            new_tasks=new_tasks,
            skill_path=Path(args.skill),
        )
        (report_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        report_path = report_dir / "report.md"
        started_at = datetime.now(timezone.utc)
        if args.dry_run:
            report_path.write_text("# 操作建议\n\n dry-run: 未调用 agent。\n", encoding="utf-8")
            command: list[str] = []
            returncode = 0
        else:
            runner = AgentRunner(args.provider, report_dir, model=args.model, timeout_seconds=args.task_timeout_seconds)
            result = runner.run(prompt, report_path, resume=False)
            command = result.command
            returncode = result.returncode
        ended_at = datetime.now(timezone.utc)
        _write_json(
            report_dir / "summary.json",
            {
                "position_address": args.position_address,
                "positions_count": len(positions),
                "new_tasks_count": len(new_tasks),
                "report": str(report_path),
                "started_at_utc": started_at.isoformat(),
                "ended_at_utc": ended_at.isoformat(),
                "duration_seconds": round((ended_at - started_at).total_seconds(), 3),
                "provider": args.provider,
                "returncode": returncode,
                "agent_calls": 0 if args.dry_run else 1,
                "command": command,
            },
        )
        print(f"report {iteration} complete -> {report_path}")
        if args.iterations == 0 or iteration < args.iterations:
            time.sleep(args.sleep_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal static-filter-first Polymarket agent harness")
    sub = parser.add_subparsers(required=True)

    discover = sub.add_parser("discover", help="fetch raw markets from Polymarket Gamma")
    discover.add_argument("--pages", type=int, default=2)
    discover.add_argument("--events-per-page", type=int, default=50)
    discover.add_argument("--out", default="runtime-artifacts/markets.json")
    discover.set_defaults(func=cmd_discover)

    filt = sub.add_parser("filter", help="select non-duplicated research tasks from market JSON")
    filt.add_argument("--markets", required=True)
    filt.add_argument("--out", default="runtime-artifacts/tasks.json")
    filt.add_argument("--max-tasks", type=int, default=3)
    filt.add_argument("--min-score", type=float, default=4.0)
    filt.add_argument("--seed", default=None, help="optional seed for reproducible weighted sampling")
    filt.add_argument(
        "--tag-weights-json",
        default=None,
        help="optional JSON object mapping Polymarket tag slugs to selection weights",
    )
    filt.set_defaults(func=cmd_filter)

    run = sub.add_parser("run", help="run selected tasks through an agent and static repair loop")
    run.add_argument("--tasks", required=True)
    run.add_argument("--provider", choices=["codex", "kimi"], default="codex")
    run.add_argument("--model", default=None)
    run.add_argument("--skill", default=str(DEFAULT_SKILL))
    run.add_argument("--out-dir", default="runtime-artifacts/run")
    run.add_argument("--max-repairs", type=int, default=2)
    run.add_argument("--task-timeout-seconds", type=int, default=300)
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_run)

    loop = sub.add_parser("loop", help="run portfolio review plus new-market research in a sleep loop")
    loop.add_argument("--portfolio", default="runtime-artifacts/portfolio.json")
    loop.add_argument("--portfolio-branch", default="paper-portfolio")
    loop.add_argument("--out-dir", default="runtime-artifacts")
    loop.add_argument("--iterations", type=int, default=1, help="0 means run forever")
    loop.add_argument("--sleep-seconds", type=float, default=300)
    loop.add_argument("--pages", type=int, default=2)
    loop.add_argument("--events-per-page", type=int, default=50)
    loop.add_argument("--new-markets", type=int, default=1)
    loop.add_argument("--min-score", type=float, default=4.0)
    loop.add_argument("--seed", default=None)
    loop.add_argument("--tag-weights-json", default=None)
    loop.add_argument("--provider", choices=["codex", "kimi"], default="codex")
    loop.add_argument("--model", default=None)
    loop.add_argument("--skill", default=str(DEFAULT_SKILL))
    loop.add_argument("--max-repairs", type=int, default=2)
    loop.add_argument("--task-timeout-seconds", type=int, default=300)
    loop.add_argument("--kelly-fraction", type=float, default=0.25)
    loop.add_argument("--max-market-fraction", type=float, default=0.05)
    loop.add_argument("--min-new-edge", type=float, default=0.08)
    loop.add_argument("--live", action="store_true")
    loop.add_argument("--dry-run", action="store_true")
    loop.set_defaults(func=cmd_loop)

    report = sub.add_parser("report", help="sync public positions and write one Chinese read-only report per cycle")
    report.add_argument("--position-address", default=DEFAULT_POSITION_ADDRESS)
    report.add_argument("--out-dir", default="runtime-artifacts/reports")
    report.add_argument("--iterations", type=int, default=1, help="0 means run forever")
    report.add_argument("--sleep-seconds", type=float, default=3600)
    report.add_argument("--pages", type=int, default=2)
    report.add_argument("--events-per-page", type=int, default=50)
    report.add_argument("--new-markets", type=int, default=1)
    report.add_argument("--min-score", type=float, default=4.0)
    report.add_argument("--seed", default=None)
    report.add_argument("--tag-weights-json", default=None)
    report.add_argument("--provider", choices=["codex", "kimi"], default="codex")
    report.add_argument("--model", default=None)
    report.add_argument("--skill", default=str(DEFAULT_SKILL))
    report.add_argument("--task-timeout-seconds", type=int, default=900)
    report.add_argument("--dry-run", action="store_true")
    report.set_defaults(func=cmd_report)

    evaluate = sub.add_parser("evaluate", help="reprice decisions against current CLOB orderbook")
    evaluate.add_argument("--run-dir", required=True)
    evaluate.add_argument("--out", default=None)
    evaluate.add_argument("--max-slippage-pct", type=float, default=0.04)
    evaluate.set_defaults(func=cmd_evaluate)
    return parser


def _read_json_if_exists(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def cmd_evaluate(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir)
    rows = []
    for decision_path in sorted(run_dir.glob("*/decision.json")):
        task_dir = decision_path.parent
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        timing = _read_json_if_exists(task_dir / "timing.json") or {}
        if not timing:
            # Older interrupted runs may have decisions but no timing.json.
            # File mtimes are only a fallback for latency analysis, not an
            # execution-grade clock.
            prompt_path = task_dir / "prompt.txt"
            output_path = task_dir / "agent-output-0.txt"
            start_iso = _mtime_iso(prompt_path)
            end_iso = _mtime_iso(output_path)
            duration = None
            if start_iso and end_iso:
                duration = datetime.fromisoformat(end_iso).timestamp() - datetime.fromisoformat(start_iso).timestamp()
            timing = {
                "started_at_utc": start_iso,
                "ended_at_utc": end_iso,
                "duration_seconds": round(duration, 3) if duration is not None else None,
                "source": "file_mtime_estimate",
            }
        row = {
            "task_id": task_dir.name,
            "decision": decision.get("decision"),
            "outcome": decision.get("outcome"),
            "ai_prob": decision.get("ai_prob"),
            "reported_market_prob": decision.get("market_prob"),
            "reported_edge": decision.get("edge"),
            "timing": timing,
        }
        if decision.get("decision") in {"buy_yes", "buy_no"} and decision.get("token_id"):
            # This is an EV/depth estimate under the agent's subjective
            # probability, not risk-free arbitrage and not an execution report.
            book = fetch_orderbook(str(decision["token_id"]))
            live = estimate_buy_profit(book, float(decision["ai_prob"]), max_slippage_pct=args.max_slippage_pct)
            row["live_orderbook"] = live
            row["live_edge_after_latency"] = live["edge_at_best_ask"]
            row["edge_decay_vs_reported"] = (
                float(decision["edge"]) - float(live["edge_at_best_ask"])
                if live["edge_at_best_ask"] is not None and isinstance(decision.get("edge"), (int, float))
                else None
            )
            row["is_still_above_8pct_edge"] = (
                live["edge_at_best_ask"] is not None and live["edge_at_best_ask"] >= 0.08
            )
        rows.append(row)

    out = {
        "run_dir": str(run_dir),
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "max_slippage_pct": args.max_slippage_pct,
        "decisions": rows,
    }
    out_path = Path(args.out) if args.out else run_dir / "execution-evaluation.json"
    _write_json(out_path, out)
    print(f"evaluation -> {out_path}")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
