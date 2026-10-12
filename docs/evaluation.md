# Evaluation results

These are reproducible observations of the educational bottleneck model. They
are not measurements of a production TCP implementation or a real network.

## Configuration

The baseline comparison uses five simultaneously starting flows, 30 simulated
seconds, a 10 Mbps link, 20 ms one-way propagation, a 100-packet buffer, 1500-byte
payloads, ECN above 50% occupancy, seed 0, and no added random loss or jitter.
The CLI initializes pacing RTT to 41.2 ms. The integration tests also exercise
the public API with its default initial RTT of 40 ms.

```bash
python -m src.simulator --flows 5 --duration 30 --bandwidth 10Mbps --algorithm aimd --seed 0 --output results/aimd
python -m src.simulator --flows 5 --duration 30 --bandwidth 10Mbps --algorithm reno --seed 0 --output results/reno
python -m src.telemetry.plot --input results/aimd/run.csv
```

## Observed results

| Run | Controlled-flow throughput (Mbps) | Summary Jain fairness | Dropped attempts (%) | Lowest flow rate (bytes/s) |
|---|---:|---:|---:|---:|
| AIMD baseline | 7.7248 | 0.998785 | 0.000000 | 187150 |
| Reno model | 7.6104 | 0.999306 | 0.514446 | 183250 |

Both algorithms meet the required fairness threshold of 0.9 and keep every flow
active. Reno has slightly higher fairness here, but lower throughput and more
startup drops. All reported Reno drops occur in feedback intervals between
0.1773 and 0.2397 simulated seconds. Slow-start bursts contribute to this tradeoff;
these results do not establish that Reno always improves the AIMD baseline.

Throughput is the sum of unique payload bytes acknowledged by the end time,
divided by experiment duration and converted to bits/second. Summary fairness
compares each flow's rate over its active duration. Outstanding packets at the
cutoff do not count as acknowledged bytes. Buffer capacity includes the packet
being serialized, and reverse ACK bandwidth is not modeled.

## Staggered traffic with loss and cross-traffic

```bash
python -m src.simulator --flows 3 --duration 30 --start-times 0,5,10 --cross-traffic 1Mbps --loss 0.01 --jitter 2ms --output results/staggered
```

Observed controlled throughput is **6.9244 Mbps**, fairness is **0.989833**, and
controlled transmission loss is **0.927454%**. All three flows deliver payload.
Their cumulative active-duration rates are 306950, 393420, and 346125 bytes/s.
The realized random loss percentage can differ from the configured probability.
The 1 Mbps background source shares the FIFO but is excluded from flow metrics.

## Validation

At implementation validation, all **91 tests passed** with **97% source
coverage**. Ruff and the Git whitespace check passed. The original tests were
left unchanged; new behavior tests were added with user approval.

The suite tests the fixed pytest import path, abstract controller interface,
numeric RTT updates, congestion response, state transitions, physical FIFO
service, buffer drops, ECN, immutable feedback, ACK return delay, rejection of
ambiguous retransmission RTT samples, seeded reproducibility, observer
isolation, telemetry units, CSV/JSON consistency, plotting, and CLI input errors.

The five-flow integration test uses both controllers for 30 simulated seconds,
requires summary fairness >= 0.9 and positive throughput for every flow, bounds
delivery by link capacity, verifies nonempty consistent CSV/JSON, and enforces
less than 30 wall-clock seconds per experiment. The full suite also builds real
PNG plots; initial font-cache construction can add setup time to the overall
suite without affecting the integration-run limit.

```bash
ruff check src/ tests/
python -m pytest tests/ -v --cov=src --cov-report=term-missing
```

The experiment JSON files retain configuration and units, allowing reruns of
these comparisons. To establish broader performance conclusions, compare more
seeds and link/delay/buffer settings. Convergence time after a joining flow is
not automatically summarized; use the staggered time series to study it.
