# Algorithm Design

**Owner: Mains Mainuddin — issues #4, #6, #7, and #8**

## Controller interface

`CongestionController` is an abstract interface. Each flow needs its own instance.
The controller maintains window and RTT state, reads a mapping, and returns a
paced rate without changing the input:

```text
send_rate = cwnd * mss / rtt_estimate
```

The window and threshold are in packets, MSS is payload bytes, RTT is in seconds,
and send rate is bytes/second. Initialization rejects invalid/nonfinite windows,
RTTs, and packet sizes.

## AIMD baseline

`AIMDController` adds one packet per successful feedback round. Loss or ECN
halves the window once per round, with a one-packet floor. No acknowledged
payload means no additive growth. The baseline exposes congestion avoidance.

## RTT estimates

The first valid sample R initializes SRTT to R and RTTVAR to R/2. Later samples
update deviation using the previous SRTT before updating SRTT:

```text
rttvar = 0.75 * rttvar + 0.25 * abs(old_srtt - R)
srtt   = 0.875 * old_srtt + 0.125 * R
rto    = max(1.0, srtt + max(0.001, 4 * rttvar))
```

These constants and update order follow
[RFC 6298](https://www.rfc-editor.org/rfc/rfc6298.html). A positive initial RTT
provides pacing before measurements arrive. A feedback RTT explicitly marked
as a fallback does not update the estimator.

## Reno-style state machine

`RenoController` provides a per-RTT educational model of the concepts in
[RFC 5681](https://www.rfc-editor.org/rfc/rfc5681.html), not a complete TCP stack.

- Slow start doubles the window per successful RTT up to the threshold.
- Congestion avoidance adds one packet per successful RTT.
- Loss or ECN without recovery signals halves the window once.
- Three duplicate ACKs enter fast recovery, set the threshold to at least half
  the prior window, and inflate the window for duplicate ACKs.
- Explicit recovery completion deflates to the threshold and returns to
  congestion avoidance.
- Timeout restarts slow start at one packet.

Duplicate ACK signals can accumulate across rounds. Advancing ACK progress
resets the count. State changes are retained as old/new pairs in `transitions`.

## Feedback expected from the simulator

| Field | Meaning |
|---|---|
| `rtt` | Positive RTT in seconds |
| `loss` | Congestion loss signal |
| `ecn` | Congestion marking signal |
| `bytes_acked` | Newly acknowledged payload bytes |
| `rtt_sample_valid` | Optional; false means RTT is only a fallback |
| `duplicate_acks` | Optional; new duplicate ACK signals |
| `ack_advanced` | Optional; cumulative ACK progress |
| `timeout` | Optional timeout signal |
| `recovery_complete` | Optional end-of-recovery signal |

The four original fields remain supported. Explicit recovery fields are proposed
for coordination with Henry; the controller does not synthesize those signals.
Feedback may be immutable, and the controller does not read telemetry state.

## Validation and integration

`tests/test_controller_behavior.py` verifies growth, halving, floors, RTT
initialization/EWMA/convergence, immutable inputs, threshold transitions,
duplicate-ACK recovery, timeout, and invalid inputs.

Simulation and telemetry work are outside this contribution. The integration
contract and pending end-to-end validation are documented in
[integration-contract.md](integration-contract.md).
