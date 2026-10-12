# Integration contract and pending work

**Owner: Mains Mainuddin — issues #16 and #17**

The CLI and orchestration wrapper are prepared for integration with Henry's
simulator and Adrian's telemetry. These are proposed interfaces for coordination;
adapt the wiring to the team's agreed API when the implementations arrive.

## Required public interfaces

| Package | Interface | Required behavior |
|---|---|---|
| src.simulator | Link | Configure bandwidth bits/s, one-way delay seconds, buffer packets, ECN fraction |
| src.simulator | TrafficGenerator | Configure flows, independent controller factory, starts, background bytes/s, packet bytes; expose flows with start_time |
| src.simulator | Scheduler | Configure link, seed, loss probability, jitter; run(traffic, duration, recorder) returns the observer |
| src.telemetry | MetricsRecorder | Observe the simulator without changing state; expose summary() |
| src.telemetry | ExperimentLogger | Capture recorder and optional metadata; write_csv(path) and write_json(path) return paths |
| src.telemetry.plot | plot_results | Optional plotting delegation; return output paths for an input CSV |

`src.integration.Simulation` supplies only orchestration: instantiate
the observer and scheduler, pass the traffic and duration to the scheduler, and
return its recorder. It does not schedule packets or calculate metrics.

The integration readiness check lists missing public factories. It does not
replace them with fallback simulation or telemetry implementations. CLI help and
input validation work independently; running a valid experiment reports pending
dependencies until they are available.

## Output contract used by the acceptance test

- summary.duration: simulated seconds.
- summary.fairness: final Jain's index.
- summary.total_throughput_bytes_per_second: total delivered rate.
- summary.flows: mapping by flow ID with throughput and bytes_acked.
- CSV: nonempty rows with flow_id and bytes_acked (and planned time-series metrics).
- JSON: summary.flows keyed by string flow ID with matching bytes_acked.

Confirm the distinction between interval and cumulative throughput/fairness with
Adrian before final integration.

## Completion checklist

- [ ] Henry exposes the link, traffic generator, and scheduler APIs.
- [ ] Adrian exposes the recorder and logger APIs.
- [ ] Confirm the constructor, feedback, observer, and output contracts together.
- [ ] Adapt the integration wiring to the agreed implementations.
- [ ] Run the CLI with five flows for 30 simulated seconds.
- [ ] Run both real controller cases in tests/test_integration.py without skips.
- [ ] Require fairness >= 0.9, positive throughput for every flow, matching nonempty
  CSV/JSON, capacity compliance, and execution under 30 wall-clock seconds.
- [ ] Validate optional plotting when Adrian's plotting API is ready.

Issues #16 and #17 remain pending until this checklist is satisfied. Recording
doubles in CLI unit tests verify delegation only; they do not satisfy end-to-end
acceptance.
