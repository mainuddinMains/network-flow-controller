# Network Flow Controller

**Mini Internet Project — Track C: Congestion Control**
Saint Louis University · Computer Networks

| Name | Role |
|---|---|
| Mains Mainuddin | Algorithm design & integration |
| Henry Morgan | Network simulator |
| Adrian Wehlen | Telemetry & evaluation |

---

## Overview

This project implements a **network flow controller** that enforces congestion control policies across a simulated multi-node network topology. The goal of Track C is to design, implement, and evaluate a congestion control algorithm that adapts sending rates in response to observed network conditions (queue depth, packet loss, RTT), demonstrating measurable improvements in throughput-fairness trade-offs compared to a baseline.

The controller is structured as three cooperating modules:

```
src/
├── algorithm/    # Congestion control logic (AIMD, BBR-style, or custom)
├── simulator/    # Packet-level network emulator (topology, queues, links)
└── telemetry/    # Metrics collection, logging, and result export
tests/            # Unit and integration tests
docs/             # Design documents and experiment results
```

---

## Modules

### `src/algorithm/`
Implements the congestion control algorithm. Responsible for:
- Maintaining per-flow state (congestion window, slow-start threshold, RTT estimates)
- Reacting to loss signals and ECN marks from the simulator
- Exposing a `step(feedback) -> send_rate` interface consumed by the simulator

### `src/simulator/`
Packet-level network emulator. Responsible for:
- Modelling links with configurable bandwidth, propagation delay, and buffer size
- Scheduling packet arrivals, drops, and ECN marks
- Running multiple concurrent flows and delivering per-RTT feedback to the algorithm

### `src/telemetry/`
Metrics collection and result export. Responsible for:
- Recording per-flow throughput, RTT, loss rate, and fairness (Jain's index) over time
- Writing structured logs (CSV/JSON) for offline analysis
- Generating summary statistics for the project report

---

## Requirements

- Python 3.10+
- Dependencies listed in `requirements.txt` (populated as the project grows)

Suggested packages (add to `requirements.txt` as used):

```
matplotlib       # plotting results
numpy            # numerical helpers
pytest           # test runner
pytest-cov       # coverage reports
```

---

## Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd network-flow-controller
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate.bat     # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Verify the environment

```bash
python -c "import src.algorithm, src.simulator, src.telemetry; print('OK')"
```

---

## Running the Simulation

> Entry point will be added once core modules are implemented.

Planned invocation:

```bash
python -m src.simulator --flows 5 --duration 60 --bandwidth 10Mbps --algorithm aimd
```

Key flags (subject to change):

| Flag | Description | Default |
|---|---|---|
| `--flows` | Number of concurrent TCP-like flows | `5` |
| `--duration` | Simulation wall-time in seconds | `60` |
| `--bandwidth` | Bottleneck link capacity | `10Mbps` |
| `--delay` | One-way propagation delay | `20ms` |
| `--buffer` | Router buffer size (packets) | `100` |
| `--algorithm` | Congestion control variant (`aimd`, `bbr`, `custom`) | `aimd` |
| `--output` | Directory for telemetry output | `./results/` |

---

## Running Tests

```bash
pytest tests/ -v
```

To include coverage:

```bash
pytest tests/ --cov=src --cov-report=term-missing
```

---

## Project Structure

```
network-flow-controller/
├── src/
│   ├── algorithm/
│   │   └── __init__.py
│   ├── simulator/
│   │   └── __init__.py
│   └── telemetry/
│       └── __init__.py
├── tests/
├── docs/
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Evaluation Metrics

The controller will be evaluated on the following criteria from the Mini Internet rubric:

| Metric | Description |
|---|---|
| **Throughput** | Total bits delivered per second across all flows |
| **Fairness** | Jain's fairness index across concurrent flows |
| **RTT stability** | Variance in measured round-trip time |
| **Loss rate** | Fraction of packets dropped at the bottleneck |
| **Convergence time** | Time to reach steady-state after a new flow joins |

---

## Design Notes

- The simulator and algorithm communicate through a clean feedback interface so that different congestion control strategies can be swapped in without changing the simulator.
- Telemetry is write-only from the simulator's perspective; the algorithm never reads telemetry state, keeping the control loop deterministic and reproducible.
- All random events (packet loss, jitter) are seeded so experiments are repeatable.

---

## License

See [LICENSE](LICENSE).
