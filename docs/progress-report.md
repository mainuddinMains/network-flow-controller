# Progress Report — Track C: Congestion Control

**Course:** Computer Networks | **University:** Saint Louis University
**Date:** 2026-10-20
**Team:** Mainuddin Sarker, Adrian Wehlen, H. Morgan

**Commit checkpoint:** `73522d1` (branch: `codex/implement-congestion-control-issues`)

**Reproducible commands:**
```bash
git clone <repo-url> && cd network-flow-controller
python3 -m venv .venv && source .venv/bin/activate && pip install -e .
pytest tests/ -q
# Expected: 106 passed, 2 skipped, 0 failed
```

---

## Common-Network Acceptance Evidence & Baseline Results

The simulator runs end-to-end over UDP on `127.0.0.1`. Static routing and
bidirectional communication between hosts have been verified (#32). Packet
validation enforces a 1,200-byte MTU; malformed and oversized datagrams are
rejected without crashing the receiver (#30, #49). TTL enforcement prevents
forwarding loops (#33). Probe-based RTT measurement is functional in the host
layer (#29).

**Test suite (checkpoint `73522d1`):** 106 passing, 2 skipped, 0 failing.

---

## Current Architecture

| Module | Status | Key Components |
|---|---|---|
| `src/algorithm/` | Complete | `CongestionController` (ABC), `AIMDController`, `RenoController` with RFC 6298 RTT estimation, slow start, fast recovery |
| `src/simulator/` | Core complete | `Router` (concurrent recv/forward via threads + bounded queue), `Host` + `HostBuilder`, `RouterBuilder` |
| `src/telemetry/` | Complete | `MetricsRecorder` (per-flow throughput, RTT, loss), `fairness.py` (Jain's Fairness Index) |
| `src/integration.py` | Complete | Wires algorithm ↔ simulator ↔ telemetry |

The router uses a two-thread design (receiver → bounded inbound queue →
forwarder) to prevent a slow send from stalling new arrivals. The congestion
controller is a pluggable abstract base class; both AIMD and Reno variants are
implemented per-RTT.

---

## Unresolved Defects

1. **Router config test skipped** (issue #25, `tests/test_routers.py`): Marked
   `xfail` pending a `.ini` config file format fix.
2. **Global random seed** (`router.py:11`): Loss simulation uses
   `random.seed(42)` — should be replaced with a logged random seed before
   extension experiments for reproducibility.

---

## Updated Schedule

| Milestone | Target | Status |
|---|---|---|
| Core simulator (routing, hosts, validation) | Week 6 | Done |
| Congestion controllers (AIMD, Reno) | Week 7 | Done |
| Telemetry (metrics, fairness) | Week 7 | Done |
| SO_RCVBUF / packet boundary fix (#49) | Week 8 | Done |
| Extension: multi-flow fairness experiment | Week 9 | Planned |
| Extension: BBR/CUBIC comparison | Week 10 | Planned |
| Final report | Week 12 | Pending |

---

## Confirmed Extension & Experimental Plan

**Extension:** Multi-flow fairness under competing Reno/AIMD flows, with
Jain's Fairness Index as the primary metric. Secondary experiment: add a
CUBIC-inspired controller and compare convergence time and throughput under
simulated bottleneck links with configurable loss rates (0%, 1%, 5%).

**Experimental setup:** 3–5 concurrent flows sharing a single bottleneck
router; vary bandwidth (1–10 Mbps) and RTT asymmetry; record per-interval
throughput and loss from `MetricsRecorder`.

---

## Individual Contributions

| Member | Contributions |
|---|---|
| **Adrian Wehlen** | Router concurrent architecture (#28), host probe/RTT (#29), packet validation (#30), bidirectional routing fix (#32), TTL enforcement (#33), Jain's fairness index, CI fixes |
| **H. Morgan** | `Router` initial implementation, `Host`/`HostBuilder`, router buffer, `.ini` config parsing, datagram format |
| **Mainuddin Sarker** | `CongestionController` ABC, `AIMDController`, `RenoController`, RFC 6298 RTT estimation, `MetricsRecorder`, `integration.py`, SO_RCVBUF packet boundary fix (#49), CI/linting fixes |
