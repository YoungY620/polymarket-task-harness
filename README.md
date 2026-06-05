# Polymarket Task Harness

Static-filter-first Polymarket research harness.

The design is intentionally small:

1. A no-AI market filter selects a tiny, de-duplicated set of research tasks.
2. Each task is sent to one agent session with a fixed goal, skill, and completion standard.
3. A static validation loop checks whether the agent returned executable JSON.
4. If validation fails, the harness resumes the same agent session with the validation errors.

## Quick Start

```bash
PYTHONPATH=src python3 harness.py discover --pages 2 --out runtime-artifacts/markets.json
PYTHONPATH=src python3 harness.py filter --markets runtime-artifacts/markets.json --out runtime-artifacts/tasks.json --max-tasks 3
PYTHONPATH=src python3 harness.py run --tasks runtime-artifacts/tasks.json --provider codex --dry-run
```

Remove `--dry-run` to call the configured agent CLI.

Supported providers:

- `codex`
- `kimi`

## Output

Each task gets its own directory under `runtime-artifacts/run/<task_id>/`:

- `prompt.txt`
- `agent-output-0.txt`
- `repair-N.txt`
- `agent-output-N.txt`
- `validation.json`
- `decision.json` when validation passes

## Live Request Boundary

`loop --live` does not open a wallet or submit orders. It writes short-lived
trade request JSON files into an inbox. Run the executor separately in the
environment that has the wallet login/session:

```bash
PYTHONPATH=src python3 harness.py loop --live --iterations 1 --trade-request-inbox runtime-artifacts/trade-requests
PYTHONPATH=src python3 trade_executor.py --dry-run --inbox runtime-artifacts/trade-requests --max-total-buy-usd 50
```

For real execution, provide a command that consumes the request JSON on stdin
and performs the signed Polymarket order. The executor also exposes the same
JSON as `PM_TRADE_REQUEST_JSON`:

```bash
PYTHONPATH=src python3 trade_executor.py \
  --inbox runtime-artifacts/trade-requests \
  --ledger runtime-artifacts/trade-execution-ledger.json \
  --max-trade-usd 10 \
  --max-total-buy-usd 50 \
  --command "/path/to/privileged-polymarket-order-adapter"
```

The executor rejects expired requests, duplicate request ids, unsupported
sources, buys over the per-trade cap, and buys that would exceed the total
configured budget.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s test -v
```
