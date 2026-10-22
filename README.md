# Network Flow Controller

**Mini Internet Project — Track C: Congestion Control**
Saint Louis University · Computer Networks

| Name | Role |
|---|---|
| Mains Mainuddin | Algorithm design & integration |
| Henry Morgan | Network simulator |
| Adrian Wehlen | Telemetry & evaluation |


## Status

| Area | Current functionality |
| --- | --- |
| Host communication | Echo requests/replies, matching message IDs, payload handling, finite timeouts, monotonic-clock RTT measurements |
| Router forwarding | Configurable static forwarding tables, neighbor-only forwarding, hop-limit decrement and expiry |
| Fault handling | Reject short/oversized packets, unknown destinations and non-neighbor next hops; continue running after malformed traffic |
| Network conditions | Configurable outbound-link delays, seeded packet loss, forced drops, logs identifying loss versus late replies |
| Core acceptance evidence | Automated tests for two-way multihop probes, upper/lower routes, TTL loops, invalid packets, and loss/delay |
| Core baseline | Four conditions measured: 10/100 ms added delay × 0%/5% loss, 3 seeds, 100 probes per run |
| Track C | AIMD/control-algorithm work exists, but fixed-rate bottleneck integration and comparison experiments remain to be completed |

**Important:** The presence of a Track C controller or CLI module does not demonstrate that it is integrated with the UDP core network. The old five-flow congestion CLI is not part of the core workflow described below, and no Track C experimental results are claimed.

## Requirements and setup

Use Python **3.10 or newer**. Run these commands from the **repository root**, where `src/`, `tests/`, and `requirements.txt` are located.

```bash
python -m venv .venv
```

Activate it:

- **Windows PowerShell:** `.venv\Scripts\Activate.ps1`
- **macOS/Linux:** `source .venv/bin/activate`

Then install dependencies:

```bash
python -m pip install -r requirements.txt
```

The supplied requirements list `matplotlib`, `numpy`, `pytest`, `pytest-cov`, and `ruff`. The project is run from its source tree; **`pip install -e .` is not required**.

## Network topology

The six virtual addresses use distinct real UDP ports on `127.0.0.1`:

| Device | Virtual address | UDP address |
| --- | ---: | --- |
| Host A | 0 | `127.0.0.1:60000` |
| Router A | 1 | `127.0.0.1:60001` |
| Router B | 2 | `127.0.0.1:60002` |
| Router C | 3 | `127.0.0.1:60003` |
| Router D | 4 | `127.0.0.1:60004` |
| Host B | 5 | `127.0.0.1:60005` |

The harness configures either of two routes (and their matching return routes):

```text
Upper: Host 0 → Router 1 → Router 2 → Router 4 → Host 5
Lower: Host 0 → Router 1 → Router 3 → Router 4 → Host 5
```

Each host sends to its entry router, never directly to the destination host's UDP port. Routers use the *virtual destination* to select a configured neighboring next hop. The harness's `UPPER` and `LOWER` presets change routing tables between runs.

Configuration files reside in:

- `src/simulator/router configurations/routerA.ini` through `routerD.ini`
- `src/simulator/host configurations/hostA.ini` and `hostB.ini`

Router `routing.delays` values are **milliseconds** (converted to seconds when loaded); loss probabilities are on a 0–1 scale. Router `buffer_size` is interpreted as **bytes** by the current source code.

### Packet layout

A packet is encoded in network byte order with `struct` format **`!BBBHHH`**:

| Field | Bytes |
| --- | ---: |
| TTL (hop limit) | 1 |
| Message type flags | 1 |
| Reserved | 1 |
| Message ID | 2 |
| Source virtual address | 2 |
| Destination virtual address | 2 |

The header totals **9 bytes**, followed by payload bytes. The protocol maximum is **1,200 bytes including the header** (up to 1,191 payload bytes). Echo request flag: `0x01`; echo reply flag: `0x02`. Each router drops an incoming TTL of 0; otherwise it decrements TTL before forwarding.

## Run the working core network

The simplest supported launcher is the **test harness** (`tests/harness.py`). It starts both hosts and all four routers, sends probes, verifies the results, produces structured evidence, and shuts down the sockets. Run scenarios **sequentially** because devices use fixed ports.

### Short recorded demonstration

From the repository root:

```bash
python -m pytest tests/test_scenarios.py::test_s1_multihop -q
```

This runs the network and checks **20 probes initiated by each host**, matching returned payloads, the upper multihop route, and no drops/timeouts for the zero-loss scenario. The harness writes `evidence/s1_multihop/`.


### Evidence directories

Each scenario generates:

```text
evidence/<scenario>/
    cfg/                # copied INI configuration used in that run
    run.log             # timestamped, per-device event trace
    meta.json           # configuration edits and recorded router seeds
    key_lines_*.txt     # selected trace/drop/timeout evidence
```

**Do not overwrite evidence you intend to submit:** `scenario()` recreates the directory with the same scenario name every time. `test_scenarios.py` and `test_evidence.py` both use `s1_multihop`; running one replaces the other's log and trace extracts.

## Baseline measurement and reproducibility

The completed core baseline sends probes from Host 0 to Host 5 over the **symmetric upper path**. Router 2 applies an additional delay and optional loss on two directed outgoing links: `2 → 4` carries forward requests, and `2 → 1` carries replies. Each probe traverses one of those affected links on its outbound leg and the other on its return leg.

Run the full measurements (the recorded dataset uses **100 probes/run**, seeds **101, 102, 103**, 0.05 s probe interval, 2 s timeout, 64-byte payload):

```bash
python tests/measure_baseline.py
```

Smaller reproducibility smoke run:

```bash
python tests/measure_baseline.py --probes 20 --seeds 101
```

Rebuild summaries and plots from an existing results file **without sending probes**:

```bash
python tests/measure_baseline.py --aggregate-only
```

Outputs are under `evidence/measure/baseline/`:

- `results.csv`: successes, timeouts, loss drops, RTT summaries, errors for every run
- `rtt_samples.csv`: all successful-probe RTT measurements
- `summary.csv` and `summary.md`: means, standard deviations, ranges and model predictions
- `baseline_success.png`, `baseline_rtt.png`, `baseline_timeouts.png`
- `<condition>_seed<seed>/`: exact configuration, device logs, and seed metadata

**Measured averages from the supplied baseline dataset** (three 100-probe runs per condition):

| Added link delay (each direction) | Configured loss | Mean matching replies | Mean RTT of successful probes | Mean timeouts/run |
| --- | --- | ---: | ---: | ---: |
| 10 ms | 0% | 100.00% | 155.76 ms | 0.00 |
| 10 ms | 5% | 90.00% | 154.67 ms | 10.00 |
| 100 ms | 0% | 100.00% | 346.63 ms | 0.00 |
| 100 ms | 5% | 90.33% | 344.40 ms | 9.67 |

RTT is a **round-trip** measurement at the sending host using `time.monotonic()`; it is not one-way link delay. Only successful probes contribute to RTT averages; unsuccessful probes remain in the success/timeout statistics. For independent 5% loss on one outbound and one return-direction link, predicted end-to-end probe success is `(1 - 0.05)^2 = 90.25%`.

Rerunning the full suite takes longer than the short demonstration. For a brief recording, **show the existing `summary.md` instead of rerunning baseline measurements**:

```bash
cat evidence/measure/baseline/summary.md
```

## Wireshark evidence

Start capture on the **loopback** interface while a core scenario runs. A Wireshark display filter for the project UDP ports is:

```text
udp && udp.port >= 60000 && udp.port <= 60005
```

Export only displayed packets via **File → Export Specified Packets → Displayed**. Real UDP endpoints use the ports above; the UDP payload begins with the custom 9-byte virtual-network header. Correlate `id`, virtual `src`/`dest`, and TTL against `run.log`. Injected drops before `sendto()` appear in the logs, not in Wireshark. A pre-recorded capture is included with the project evidence when available; use a small project-only `.pcapng` for submission.

## Track C extension: next phase

The selected extension is **one AIMD-paced sender compared with one fixed-rate sender**, each using the same network path and a simple capacity-limited router-to-router bottleneck. The required model has one packet in transmission at a time and **no waiting queue**; arrivals during an ongoing transmission count as logged overload drops. The AIMD sender will pace probes and update its rate from matching replies and timeouts, without inspecting router internals. The main experiment will disable unrelated random loss and compare delivery ratio, unique-payload goodput, overload drops and sender rate over time over repeated runs.

Controller/RTT estimation code has been developed, but the fixed-rate bottleneck and AIMD feedback/pacing **are not yet demonstrated as an integrated experiment**. The previous generic `src/simulator/cli.py` was built against different assumptions (including five flows and a 1,500-byte default packet) and is **not part of the supported core demonstration or current Track C run instructions**. Use the tested `tests/` harness above for the working Mini Internet. This README will need a new Track C command section after that experiment is integrated.

## Known constraints

- Network endpoints currently assume one computer (`127.0.0.1`) and the `60000 + virtual address` convention rather than a fully configurable neighbor-endpoint map.
- Router forwarding tables are manually configured (no automatic route discovery).
- Large application data is not fragmented; each UDP payload with the custom header must fit within 1,200 bytes.
- Some configuration errors need stricter validation: `router_builder.py` combines destination and forwarding lists with `zip`, which does not reject mismatched lengths.

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
