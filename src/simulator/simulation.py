"""Discrete-event congestion-control scheduler for a single bottleneck link."""

import heapq
import math
import random


class Scheduler:
    """Drive a per-RTT congestion-control simulation over a shared bottleneck.

    Receiving and forwarding are modelled as a leaky-bucket queue. Each flow
    fires an event every RTT; events are processed in simulation-time order via
    a min-heap. The link queue is drained at the configured bandwidth between
    consecutive events.

    :param link: A ``Link`` describing the bottleneck.
    :param seed: RNG seed for reproducible loss and jitter.
    :param loss_probability: Per-packet random loss probability (0–1).
    :param jitter: Standard deviation of Gaussian RTT noise in seconds.
    """

    def __init__(
        self,
        link: object,
        seed: int = 0,
        loss_probability: float = 0.0,
        jitter: float = 0.0,
    ) -> None:
        """Validate scheduler parameters."""
        if not math.isfinite(loss_probability) or not 0.0 <= loss_probability <= 1.0:
            raise ValueError("loss_probability must be between 0 and 1")
        if not math.isfinite(jitter) or jitter < 0.0:
            raise ValueError("jitter must be finite and non-negative")

        self.link = link
        self.seed = int(seed)
        self.loss_probability = float(loss_probability)
        self.jitter = float(jitter)

    def run(self, traffic: object, duration: float, recorder: object) -> object:
        """Simulate all flows for ``duration`` seconds and return the recorder.

        :param traffic: A ``TrafficGenerator`` whose flows are to be simulated.
        :param duration: Simulated wall-clock seconds to run.
        :param recorder: A ``MetricsRecorder`` that observes each RTT event.
        :returns: The same ``recorder`` after all events have been processed.
        """
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("duration must be finite and positive")

        rng = random.Random(self.seed)
        link = self.link
        mss = traffic.packet_bytes

        # Link drain rate in packets per second.
        drain_pps = link.bandwidth / (mss * 8)
        background_pps = traffic.background_bytes_per_second / mss
        effective_drain_pps = max(0.0, drain_pps - background_pps)

        # Shared queue state (fractional packets for smooth drain accounting).
        queue_depth = 0.0
        last_event_time = 0.0

        # Min-heap of (event_time, flow_id).
        events: list[tuple[float, int]] = []
        for flow_id, flow in traffic.flows.items():
            if flow.start_time < duration:
                heapq.heappush(events, (flow.start_time, flow_id))

        while events:
            event_time, flow_id = heapq.heappop(events)
            if event_time >= duration:
                break

            flow = traffic.flows[flow_id]
            controller = flow.controller

            # Drain shared queue since the last processed event.
            elapsed = event_time - last_event_time
            queue_depth = max(0.0, queue_depth - effective_drain_pps * elapsed)
            last_event_time = event_time

            # RTT = propagation round-trip + queuing delay at current depth.
            base_rtt = 2.0 * link.propagation_delay
            queuing_delay = queue_depth / drain_pps if drain_pps > 0 else 0.0
            rtt = base_rtt + queuing_delay
            if self.jitter > 0.0:
                rtt = max(base_rtt * 0.5, rtt + rng.gauss(0.0, self.jitter))

            # Packets this flow injects this RTT.
            packets_to_send = max(1, int(controller.cwnd))

            # ECN: mark if queue was already above threshold before this flow.
            ecn = queue_depth > link.ecn_threshold * link.buffer_size

            # Tail-drop: admit only what fits in the buffer.
            new_depth = queue_depth + packets_to_send
            if new_depth > link.buffer_size:
                dropped_buffer = int(new_depth - link.buffer_size)
                queue_depth = float(link.buffer_size)
            else:
                dropped_buffer = 0
                queue_depth = new_depth
            packets_admitted = packets_to_send - dropped_buffer

            # Apportion link capacity proportionally among active flows so that
            # total bytes_acked never exceeds physical link capacity.
            active_cwnds = [
                max(1, int(f.controller.cwnd))
                for f in traffic.flows.values()
                if f.start_time <= event_time
            ]
            total_cwnd = sum(active_cwnds) or 1
            capacity_per_rtt = int(effective_drain_pps * rtt)
            flow_capacity = int(int(controller.cwnd) / total_cwnd * capacity_per_rtt)

            # Deliverable this RTT: bounded by both admitted and flow capacity.
            deliverable = min(packets_admitted, flow_capacity)

            # Random loss applied only to deliverable packets.
            random_drops = 0
            if self.loss_probability > 0.0 and deliverable > 0:
                random_drops = sum(
                    1 for _ in range(deliverable)
                    if rng.random() < self.loss_probability
                )

            packets_through = deliverable - random_drops
            bytes_acked = max(0, packets_through) * mss
            total_dropped = dropped_buffer + random_drops

            # Reno recovery signals: trigger fast recovery on buffer overflow;
            # clear it on a clean RTT.
            dup_acks = 0
            recovery_complete = False
            ack_advanced = True
            if dropped_buffer > 0 and not flow.in_recovery:
                dup_acks = 3
                flow.in_recovery = True
                ack_advanced = False
            elif flow.in_recovery:
                if total_dropped == 0:
                    recovery_complete = True
                    flow.in_recovery = False
                else:
                    ack_advanced = False

            feedback = {
                "rtt": rtt,
                "loss": total_dropped > 0,
                "ecn": ecn,
                "bytes_acked": bytes_acked,
                "packets_sent": packets_to_send,
                "packets_dropped": total_dropped,
                "duplicate_acks": dup_acks,
                "recovery_complete": recovery_complete,
                "rtt_sample_valid": True,
                "ack_advanced": ack_advanced,
            }

            recorder.record(event_time, flow_id, feedback, interval=rtt)
            controller.step(feedback)

            next_time = event_time + rtt
            if next_time < duration:
                heapq.heappush(events, (next_time, flow_id))

        return recorder
