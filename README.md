# Network Flow Controller

**Mini Internet Project — Track C: Congestion Control**
Saint Louis University · Computer Networks

| Name | Role |
|---|---|
| Mains Mainuddin | Algorithm design & integration |
| Henry Morgan | Network simulator |
| Adrian Wehlen | Telemetry & evaluation |

## Setup

Python 3.10 or newer is required. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

## Reproducible Commands

**Run a 5-flow simulation (AIMD, 30 simulated seconds, 10 Mbps link):**

```bash
python -m src.simulator \
  --flows 5 --duration 30 --bandwidth 10Mbps --algorithm aimd \
  --output results/run1
```

Switch `--algorithm aimd` to `--algorithm reno` for TCP Reno.
Results are written to `results/run1/run.csv` and `results/run1/run.json`.

**Run all tests:**

```bash
pytest tests/ -q
# Expected: 129 passed in ~60s
```

**Lint:**

```bash
ruff check src/ tests/
```

## Algorithm

```python
from src.algorithm import AIMDController, RenoController

controller = AIMDController(cwnd=10, initial_rtt=0.04, mss=1500)
rate = controller.step({
    "rtt": 0.04, "loss": False, "ecn": False, "bytes_acked": 15000,
    "packets_sent": 10, "packets_dropped": 0,
})
# cwnd is now 11 packets; rate is bytes/second.
```

`CongestionController` is an abstract base class. Both `AIMDController` and
`RenoController` (with slow start, fast recovery, and RFC 6298 RTT estimation)
are implemented.

## Architecture

| Package | Responsibility |
|---|---|
| `src/algorithm/` | Congestion controllers: AIMD, Reno, RFC 6298 RTT |
| `src/simulator/` | Discrete-event simulation: Link, TrafficGenerator, Scheduler, Router, Host |
| `src/telemetry/` | Metrics, Jain's fairness index, ExperimentLogger |
| `src/integration.py` | Orchestration wiring algorithm ↔ simulator ↔ telemetry |

## Documentation

- [Algorithm design](docs/algorithm-design.md)
- [Integration contract](docs/integration-contract.md)
- [Progress report](docs/progress-report.md)
- [Mainuddin's individual statement](docs/individual-statement-mainuddin.md)

## License

See [LICENSE](LICENSE).
