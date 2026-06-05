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

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s test -v
```
