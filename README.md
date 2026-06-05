# Polymarket Task Harness

Static-filter-first Polymarket research harness.

The design is intentionally small:

1. A no-AI market filter selects a tiny, de-duplicated set of research tasks.
2. Each task is sent to one agent session with a fixed goal, skill, and completion standard.
3. A static validation loop checks whether the agent returned executable JSON.
4. If validation fails, the harness resumes the same agent session with the validation errors.

## Report-Only Contract

This harness is a read-only research and reporting tool. It must not sign
wallet messages, access OKX/onchainos wallet sessions, place orders, cancel
orders, transfer funds, or submit approvals.

For the report flow, positions are synced from the public Polymarket API wallet
address only:

```text
0xfffbAA1616CE86d4a62e614e92ca6565198FC2F3
```

The intended report flow calls the agent once and writes a Chinese report. The
report must begin with a concise action section that says what to do, links the
market, labels it as an existing position or a new position, and gives the
buy/sell direction and amount. This flow must not require private keys, seed
phrases, wallet OTPs, trading API secrets, or any credential capable of trading.

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

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s test -v
```
