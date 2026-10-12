# Network Flow Controller

**Mini Internet Project — Track C: Congestion Control**
Saint Louis University · Computer Networks

| Name | Role |
|---|---|
| Mains Mainuddin | Algorithm design & integration |
| Henry Morgan | Network simulator |
| Adrian Wehlen | Telemetry & evaluation |

Run reproducible packet-level experiments with independent flows sharing one
finite FIFO bottleneck. The AIMD baseline and Reno-style controller adapt their
windows to loss and ECN. Telemetry exports throughput, RTT, loss, queue depth,
and Jain's fairness to CSV, JSON, and optional PNG plots.

## Setup

Python 3.10 or newer is required. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Run an experiment

```bash
python -m src.simulator --flows 5 --duration 60 --bandwidth 10Mbps --algorithm aimd --plots
```

Duration is simulated time; the program does not sleep for that duration.
`python -m src` provides the same interface. Run both commands from the repository
root with the virtual environment activated.

| Flag | Meaning | Default |
|---|---|---|
| `--flows` | Independent controlled flows | `5` |
| `--duration` | Simulated seconds | `60` |
| `--bandwidth` | Bits/second; accepts `10Mbps`, `100Kbps`, or a number | `10Mbps` |
| `--delay` | One-way propagation delay; accepts `20ms` or seconds | `20ms` |
| `--buffer` | Packets awaiting departure, including the packet in service | `100` |
| `--algorithm` | `aimd` or `reno` | `aimd` |
| `--output` | Destination for `run.csv` and `run.json` | `results/` |
| `--seed` | Initial pacing, random loss, and jitter seed | `0` |
| `--packet-size` | Payload bytes and controller MSS | `1500` |
| `--start-times` | Comma-separated start times, one per flow | All zero |
| `--cross-traffic` | Fixed background bits/second on the same link | `0` |
| `--loss` | Independent drop probability, in addition to buffer drops | `0` |
| `--jitter` | Maximum extra forward propagation delay | `0` |
| `--ecn-threshold` | Mark above this fraction of buffer occupancy | `0.5` |
| `--plots` | Save throughput, queue, and fairness PNGs | Off |

Use separate output directories to preserve experiments:

```bash
python -m src.simulator --duration 30 --algorithm aimd --seed 0 --output results/aimd
python -m src.simulator --duration 30 --algorithm reno --seed 0 --output results/reno
python -m src.simulator --flows 3 --duration 30 --start-times 0,5,10 --cross-traffic 1Mbps --loss 0.01 --jitter 2ms --output results/staggered
```

The CLI prints a JSON summary. CSV contains one observation per feedback
interval per flow, including the final partial interval. JSON includes the same
series grouped by flow ID, summary statistics, units, and configuration. Reusing
an output directory replaces its run files.

Plot an existing CSV:

```bash
python -m src.telemetry.plot --input results/aimd/run.csv
```

The noninteractive plots are saved beside the CSV in `plots/`.

## Units and metrics

- Control uses seconds, packets, and bytes/second.
- Exported throughput is newly acknowledged unique payload bytes/second; RTT
  is milliseconds; loss is a percentage of attempted transmissions.
- Multiply throughput by eight to report bits/second.
- Time-series fairness compares the latest rate of each active flow.
- Summary fairness compares cumulative rates over each flow's own active
  duration. Flows starting after the experiment ends are excluded from fairness
  and have zero delivered bytes.
- Retries do not count payload twice. Background traffic competes for capacity
  but is excluded from controlled-flow telemetry.

## Tests and CI

```bash
ruff check src/ tests/
pytest tests/ -v
python -m pytest tests/ --cov=src --cov-report=term-missing
```

Pytest includes the repository root in its import path. CI installs dependencies
using the selected interpreter, then runs lint and coverage tests on pushes and
pull requests to main.

Behavior tests cover controllers, RTT estimation, recovery, queue service,
drops, ECN, immutable feedback, reproducibility, staggered flows, cross-traffic,
observer isolation, exports, plotting, and invalid CLI arguments. Integration
tests run five flows for 30 simulated seconds with each controller, require
fairness >= 0.9 and positive throughput for every flow, check matching nonempty
CSV/JSON, and require execution under 30 wall-clock seconds.

Follow [CLAUDE.md](CLAUDE.md) before creating or modifying test files.

## Structure

```text
src/
├── algorithm/controller.py   # Controllers, RTT estimation, state transitions
├── simulator/
│   ├── link.py               # Packets, FIFO service, ECN
│   ├── traffic.py            # Independent flows and traffic sources
│   ├── simulation.py         # Event scheduler, ACKs, per-RTT feedback
│   └── cli.py                # Configuration and entry point
└── telemetry/
    ├── metrics.py            # Observer, fairness, CSV/JSON
    └── plot.py               # Standalone PNG generation
tests/                       # Behavior and integration tests
docs/                        # Design, issue mapping, and measured results
```

Controllers receive immutable feedback every estimated RTT. Telemetry only
observes feedback and cannot change controller or simulator state. All random
events use a local seeded RNG.

This educational model uses one bottleneck, aggregate per-RTT feedback,
individual packet delivery ACKs, and ideal per-round drop detection. It does not
implement a full TCP stack, cumulative-ACK retransmission timers, ACK link
serialization, or a multi-hop topology. Results apply to these assumptions.

See [algorithm design](docs/algorithm-design.md),
[issue solutions](docs/issue-solutions.md), and
[evaluation results](docs/evaluation.md).

## License

See [LICENSE](LICENSE).
