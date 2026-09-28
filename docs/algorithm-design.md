# Algorithm Design

**Track C: Congestion Control — Saint Louis University**

## Algorithm Choice

<!-- Document the chosen algorithm here (AIMD, BBR-style, or custom) and the rationale. -->

## State Variables

| Variable | Description |
|---|---|
| `cwnd` | Congestion window (packets) |
| `ssthresh` | Slow-start threshold |
| `srtt` | Smoothed round-trip time estimate |
| `rttvar` | RTT variance |

## State Machine

<!-- Diagram or description of SLOW_START → CONGESTION_AVOIDANCE → FAST_RECOVERY transitions. -->

## Feedback Interface

The algorithm receives a `feedback` dict from the simulator each RTT:

| Key | Type | Description |
|---|---|---|
| `rtt` | float | Measured RTT in seconds |
| `loss` | bool | True if packet loss detected |
| `ecn` | bool | True if ECN mark received |
| `bytes_acked` | int | Bytes acknowledged this RTT |

## Evaluation Plan

<!-- How will the algorithm be evaluated against the rubric metrics? -->
