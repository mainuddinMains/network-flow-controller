# CLAUDE.md

## Project

This is a Python project for the Mini Internet Project, **Track C: Congestion Control**, at Saint Louis University. All implementation targets Python 3.10+.

## Code Style

- All public functions, classes, and modules must include standard Python docstrings (PEP 257).
- No third-party docstring formats (NumPy-style, Google-style) — use plain reStructuredText or bare summary-only docstrings.

## Module Responsibilities

| Package | Purpose |
|---|---|
| `src/algorithm/` | Core congestion control logic — window management, rate adaptation, loss response |
| `src/simulator/` | Network simulation — links, queues, packet scheduling, topology |
| `src/telemetry/` | Logging and metrics — throughput, RTT, loss rate, fairness, result export |

Do not place logic in the wrong module. For example, metric collection belongs in `src/telemetry/`, not in `src/algorithm/` or `src/simulator/`.

## Tests

**Do not modify any file under `tests/` without first asking the user for confirmation.**

New test files may be proposed but must be reviewed before being written.
