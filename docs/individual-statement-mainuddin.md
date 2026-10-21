# Individual Contribution Statement — Mainuddin Sarker

**Project:** Mini Internet — Track C: Congestion Control
**University:** Saint Louis University
**Date:** 2026-10-20

---

## Project Setup and Infrastructure

- Initialized the repository with the modular `src/algorithm/`, `src/simulator/`,
  and `src/telemetry/` package structure.
- Wrote the project `README.md` and `CLAUDE.md` with module responsibilities and
  coding conventions.
- Configured the GitHub Actions CI workflow, PR template, and project dependencies
  (`pyproject.toml`).
- Produced the algorithm design document (`docs/algorithm-design.md`) and
  integration contract (`docs/integration-contract.md`).

## Congestion Control Algorithm (src/algorithm/)

- Designed and implemented `CongestionController`, an abstract base class that
  exposes a pluggable `step(feedback)` interface, RFC 6298 smoothed-RTT
  estimation (`update_rtt`), and derived properties for send rate and
  retransmission timeout.
- Implemented `AIMDController`: additive increase by one packet per RTT, halve
  on loss or ECN signal.
- Implemented `RenoController`: slow start with exponential growth, congestion
  avoidance with linear growth, fast recovery triggered at three duplicate ACKs,
  and timeout reset — all driven by per-RTT feedback.

## Telemetry (src/telemetry/)

- Implemented `MetricsRecorder`: a read-only observer that records per-flow
  throughput, RTT, and loss rate from immutable feedback mappings, with full
  time-series history.

## Integration and CLI (src/integration.py, src/simulator/cli.py)

- Wrote `src/integration.py`, which wires the algorithm, simulator, and telemetry
  layers together under a single simulation loop.
- Implemented the simulator CLI (`src/simulator/cli.py`) for launching and
  configuring simulation runs from the command line.

## Bug Fix — SO_RCVBUF Packet Boundary (Issue #49)

- Diagnosed and fixed a macOS-specific bug where `setsockopt(SO_RCVBUF, 500)`
  caused the OS kernel to silently discard any datagram larger than 500 bytes
  before `recvfrom` could see it. The fix clamps `SO_RCVBUF` to
  `max(buffer_size, UDP_MAX_SIZE)`, separating the simulation's in-memory queue
  concept from the OS socket buffer. This resolved three failing packet boundary
  tests (`test_packet_validation.py`). Commit: `73522d1`.

## CI and Lint Maintenance

- Fixed a broken `telemetry.__init__` import introduced after PR #18 merged (#22).
- Resolved all E501 and I001 lint violations in `router.py`, `router_builder.py`,
  and `test_routers.py` (#23, #24).
- Marked `test_routers` as `xfail` with a clear issue reference while the `.ini`
  config format is under repair (#25, #26).

---

## Tests Written

| File | Coverage |
|---|---|
| `tests/test_controller_behavior.py` | AIMD and Reno state transitions, RTT estimation, edge cases |
| `tests/test_cli_behavior.py` | CLI argument parsing and simulation launch |
| `tests/test_integration.py` | End-to-end algorithm ↔ simulator ↔ telemetry loop |
