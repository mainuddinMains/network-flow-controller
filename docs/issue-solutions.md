# Issue solutions

This maps the GitHub backlog to the local implementation. Remote issue closure
should follow review and merge; this document does not change issue states or
milestone dates.

## CI and setup

The CI failure was an import-path problem: the pytest executable did not
include the repository root, so imports of src failed. Setting
`pythonpath = ["."]` in pyproject.toml fixes both direct and module invocation.
CI uses `python -m pip` and `python -m pytest` to use the selected interpreter.
Git ignores the local environment, caches, coverage, and generated results.

## Issue map

| Issue | Implementation | Main verification |
|---|---|---|
| #4 — Controller interface | Abstract CongestionController; validated initial state; immutable mapping input; bytes/second output | Abstract interface, validation, feedback isolation |
| #6 — AIMD | One-packet additive growth; one halving per loss/ECN round; one-packet floor | Growth, combined loss/ECN, no-ACK behavior |
| #7 — RTT | First-sample initialization; alpha 1/8 and beta 1/4; deviation updated first; timeout property | Numerical samples, convergence, invalid/missing samples |
| #8 — State machine | Reno slow start, congestion avoidance, duplicate-ACK recovery, timeout restart | Thresholds, duplicate ACKs across rounds, completion, timeout |
| #5 — Link | Immutable packets, finite FIFO, service delay, propagation delay, ECN | Exact serialization, overflow drop, threshold boundary |
| #9 — Scheduler | Event heap for sends/receives/ACKs/feedback; timestamped drop/ECN events; seeded randomness | Event logs, physical capacity bound, reproducibility |
| #10 — Traffic | Independent controllers, tagged sequences, staggered starts, paced window-limited sends, cross-traffic | Independent state, activation, competing traffic |
| #11 — Feedback | Immutable per-round original fields plus explicit recovery signals | Read-only boundary and repeated controller feedback |
| #12 — Recorder | Detached snapshots, immutable series, throughput/RTT/loss/window/queue observations | Units, counters, observer isolation |
| #13 — Fairness | Stable Jain calculation with documented zero convention; interval and summary fairness | Equal/unequal rates, one flow, zeros, invalid/huge values |
| #14 — Logging | CSV and per-flow JSON from the same captured recorder snapshot; automatic directories | Parsing, consistent counters, snapshot stability |
| #15 — Plotting | Standalone noninteractive throughput, queue, and fairness PNGs with labels/legends/titles | Actual PNGs and invalid CSV handling |
| #16 — Integration / CLI | Both package entry points delegate to one CLI; all modules wired; validated flags; exports | Both commands, units/configuration, errors, optional plots |
| #17 — Integration test | Five flows for 30 simulated seconds, both algorithms; fairness >= 0.9; no starvation; matching outputs; runtime < 30 s | tests/test_integration.py |

Main source files:
`src/algorithm/controller.py`, `src/simulator/link.py`,
`src/simulator/traffic.py`, `src/simulator/simulation.py`,
`src/simulator/cli.py`, `src/telemetry/metrics.py`, and
`src/telemetry/plot.py`.

Epics #1, #2, and #3 aggregate the algorithm, simulator, and telemetry work above.
Their children are implemented locally and can be completed after review and merge.

## Corrections to the original plan

- #16 requested src/__main__.py but required python -m src.simulator. Both entry
  points now work, preserving the documented command.
- The original feedback could not express triple duplicate ACKs. Optional
  explicit recovery fields make that behavior possible.
- RTT seconds convert to milliseconds in telemetry. Rate/throughput use
  bytes/second; link bandwidth uses bits/second.
- Branches sharing a commit need no repair; feature commits make them diverge.
- Foundation tasks #4 and #5 are implemented and tested. The overdue milestone
  can be completed after review/merge; future dates require a team schedule.

## Validation

The user approved new behavior test files before creation. Existing import tests
and tests/conftest.py remain unchanged.

```bash
source .venv/bin/activate
ruff check src/ tests/
python -m pytest tests/ -v --cov=src --cov-report=term-missing
python -m src.simulator --flows 5 --duration 30 --algorithm aimd --plots --output results/demo
python -m src.telemetry.plot --input results/demo/run.csv
```

See [evaluation.md](evaluation.md) for measured results and
[algorithm-design.md](algorithm-design.md) for assumptions and interfaces.
