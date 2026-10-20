# Network Flow Controller

**Mini Internet Project — Track C: Congestion Control**
Saint Louis University · Computer Networks

| Name | Role |
|---|---|
| Mains Mainuddin | Algorithm design & integration |
| Henry Morgan | Network simulator |
| Adrian Wehlen | Telemetry & evaluation |

## Current contribution

This branch contains Mains Mainuddin's algorithm work and integration wiring.
The simulator and telemetry implementations remain the responsibilities of
Henry and Adrian.

- Implemented: controller interface (#4), AIMD (#6), RTT estimation (#7), and
  the Reno-style slow-start/congestion-avoidance/recovery state machine (#8).
- Prepared: the CLI and integration orchestration (#16), plus the five-flow
  acceptance tests (#17). End-to-end completion awaits teammate APIs.
- Fixed: pytest's source import path and interpreter-specific CI commands.

The full earlier implementation is preserved on
[codex/full-implementation-reference](https://github.com/mainuddinMains/network-flow-controller/tree/codex/full-implementation-reference)
for optional reference. It is not included in this PR's proposed simulator or
telemetry changes.

## Setup

Python 3.10 or newer is required. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Algorithm use

```python
from src.algorithm import AIMDController

controller = AIMDController(cwnd=10, initial_rtt=0.1)
rate = controller.step({
    "rtt": 0.1, "loss": False, "ecn": False, "bytes_acked": 1500,
})
# cwnd is now 11 packets; rate is bytes/second.
```

Use `RenoController` for the modeled state machine. Windows are in packets,
control RTT is in seconds, and the returned sending rate is bytes/second.

## Integration status

CLI help works without the teammate implementations:

```bash
python -m src.simulator --help
python -m src --help
```

Once the shared interfaces are available, the intended command is:

```bash
python -m src.simulator --flows 5 --duration 30 --bandwidth 10Mbps --algorithm aimd --output results/run1
```

Until then, the CLI reports the missing interfaces and creates no experiment
output. `--plots` delegates to Adrian's plotting API; this branch does not
implement plotting.

The integration wrapper targets the proposed public API described in
[the integration contract](docs/integration-contract.md). Confirm/adapt those
interfaces with Henry and Adrian as their implementations arrive.

## Validation

```bash
ruff check src/ tests/
python -m pytest tests/ -v -rs --cov=src --cov-report=term-missing
```

Controller tests exercise real algorithm behavior. CLI tests use recording
doubles to verify argument handling and delegation; those doubles do not
implement network scheduling or metrics. Two real five-flow integration cases
skip with an explicit dependency reason while the teammate APIs are absent.
They will exercise the real implementations once the APIs are exposed.

The original import tests are unchanged. Follow [CLAUDE.md](CLAUDE.md) before
creating or modifying test files.

## Documentation

- [Algorithm design](docs/algorithm-design.md)
- [Integration contract and pending work](docs/integration-contract.md)
- [Issue scope and ownership](docs/issue-solutions.md)

## License

See [LICENSE](LICENSE).
