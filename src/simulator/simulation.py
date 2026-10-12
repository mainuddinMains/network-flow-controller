"""Discrete event scheduling, packet reception, and per-RTT feedback."""

import heapq
import itertools
import math
import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from src.algorithm import State

from .link import Link, Packet
from .traffic import Flow, TrafficGenerator

if TYPE_CHECKING:
    from src.telemetry import MetricsRecorder


@dataclass(order=True)
class _Event:
    time: float
    order: int
    kind: str = field(compare=False)
    payload: object = field(compare=False)


class Scheduler:
    """Process paced packets and ACKs against one FIFO bottleneck link."""

    def __init__(
        self,
        link: Link,
        seed: int = 0,
        loss_probability: float = 0.0,
        jitter: float = 0.0,
    ) -> None:
        """Seed random loss, extra one-way jitter, and initial pacing phases."""
        if not math.isfinite(loss_probability) or not 0 <= loss_probability <= 1:
            raise ValueError("loss_probability must be between zero and one")
        if not math.isfinite(jitter) or jitter < 0:
            raise ValueError("jitter must be finite and nonnegative")
        self.link = link
        self.rng = random.Random(seed)
        self.loss_probability = loss_probability
        self.jitter = jitter
        self.now = 0.0
        self._events: list[_Event] = []
        self._counter = itertools.count()
        self._event_log: list[Mapping[str, object]] = []
        self._traffic: TrafficGenerator | None = None
        self._started = False

    @property
    def event_log(self) -> tuple[Mapping[str, object], ...]:
        """Return read-only drop and ECN events with timestamps."""
        return tuple(self._event_log)

    def _schedule(self, time: float, kind: str, payload: object) -> None:
        heapq.heappush(self._events, _Event(time, next(self._counter), kind, payload))

    def _log(self, kind: str, packet: Packet, **details: object) -> None:
        self._event_log.append(
            MappingProxyType(
                {
                    "time": self.now,
                    "kind": kind,
                    "flow_id": packet.flow_id,
                    "sequence": packet.sequence,
                    **details,
                }
            )
        )

    def submit(self, packet: Packet, now: float) -> bool:
        """Enqueue a packet and schedule reception, returning False on a drop."""
        if not math.isfinite(now) or now < self.now:
            raise ValueError("scheduler time cannot move backwards")
        self.now = now
        random_drop = self.rng.random() < self.loss_probability
        transmission = None if random_drop else self.link.enqueue(packet, now)
        if transmission is None:
            self._log("drop", packet, reason="random" if random_drop else "buffer")
            if self._traffic is not None and packet.flow_id in self._traffic.flows:
                flow = self._traffic.flows[packet.flow_id]
                flow.dropped += 1
                flow.lost.add(packet.sequence)
            return False
        if transmission.ecn:
            self._log("ecn", packet)
        extra_delay = self.rng.uniform(0, self.jitter) if self.jitter else 0.0
        arrival = transmission.departure_at + self.link.propagation_delay + extra_delay
        self._schedule(arrival, "receive", (packet, transmission.ecn))
        return True

    def _send(self, flow: Flow) -> None:
        packet = None
        while flow.retries:
            sequence = flow.retries.popleft()
            flow.queued_retries.discard(sequence)
            if sequence in flow.outstanding:
                packet = Packet(
                    flow.flow_id,
                    sequence,
                    self.now,
                    flow.packet_size,
                    True,
                )
                flow.retransmitted.add(sequence)
                break
        if packet is None and len(flow.outstanding) < max(1, int(flow.controller.cwnd)):
            packet = Packet(
                flow.flow_id,
                flow.next_sequence,
                self.now,
                flow.packet_size,
            )
            flow.next_sequence += 1
        if packet is not None:
            flow.outstanding[packet.sequence] = packet
            flow.sent += 1
            self.submit(packet, self.now)
        interval = flow.packet_size / flow.controller.send_rate
        self._schedule(self.now + interval, "send", flow)

    def _receive(self, packet: Packet, ecn: bool) -> None:
        assert self._traffic is not None
        if packet.flow_id not in self._traffic.flows:
            return
        flow = self._traffic.flows[packet.flow_id]
        is_new = packet.sequence not in flow.received
        if is_new:
            flow.received.add(packet.sequence)
            flow.receiver_pending.add(packet.sequence)
        duplicate_ack = packet.sequence > flow.next_expected
        while flow.next_expected in flow.receiver_pending:
            flow.receiver_pending.remove(flow.next_expected)
            flow.next_expected += 1
        self._schedule(
            self.now + self.link.propagation_delay,
            "ack",
            (packet, ecn, is_new, duplicate_ack, flow.next_expected),
        )

    def _ack(
        self,
        packet: Packet,
        ecn: bool,
        is_new: bool,
        duplicate_ack: bool,
        next_expected: int,
    ) -> None:
        assert self._traffic is not None
        flow = self._traffic.flows[packet.flow_id]
        flow.outstanding.pop(packet.sequence, None)
        flow.lost.discard(packet.sequence)
        if is_new:
            flow.bytes_acked += packet.size
        if packet.sequence not in flow.retransmitted:
            flow.rtt_samples.append(self.now - packet.sent_at)
        flow.ecn |= ecn
        flow.duplicate_acks += int(duplicate_ack)
        flow.ack_expected = max(flow.ack_expected, next_expected)

    def _feedback(
        self,
        flow: Flow,
        recorder: "MetricsRecorder",
        update_controller: bool = True,
    ) -> None:
        interval = self.now - flow.last_feedback
        if interval <= 0:
            return
        timed_out = {
            sequence
            for sequence, packet in flow.outstanding.items()
            if self.now - packet.sent_at >= flow.controller.retransmission_timeout
        }
        for sequence in sorted(flow.lost | timed_out):
            if sequence in flow.outstanding and sequence not in flow.queued_retries:
                flow.retries.append(sequence)
                flow.queued_retries.add(sequence)
        rtt = (
            sum(flow.rtt_samples) / len(flow.rtt_samples)
            if flow.rtt_samples
            else flow.controller.rtt_estimate
        )
        feedback = MappingProxyType(
            {
                "rtt": rtt,
                "rtt_sample_valid": bool(flow.rtt_samples),
                "loss": flow.dropped > 0,
                "ecn": flow.ecn,
                "bytes_acked": flow.bytes_acked,
                "packets_sent": flow.sent,
                "packets_dropped": flow.dropped,
                "duplicate_acks": flow.duplicate_acks,
                "ack_advanced": flow.ack_expected > flow.last_expected,
                "timeout": bool(timed_out),
                "recovery_complete": (
                    flow.recovery_until is not None
                    and flow.ack_expected > flow.recovery_until
                ),
            }
        )
        if update_controller:
            old_state = flow.controller.state
            flow.controller.step(feedback)
            if flow.controller.state == State.FAST_RECOVERY:
                if old_state != State.FAST_RECOVERY:
                    flow.recovery_until = flow.next_sequence - 1
            else:
                flow.recovery_until = None
        recorder.record(
            self.now,
            flow.flow_id,
            feedback,
            interval=interval,
            queue_depth=self.link.queue_depth(self.now),
            cwnd=flow.controller.cwnd,
            state=flow.controller.state.value,
        )
        flow.last_feedback = self.now
        flow.last_expected = flow.ack_expected
        flow.sent = flow.dropped = flow.bytes_acked = flow.duplicate_acks = 0
        flow.ecn = False
        flow.rtt_samples.clear()
        if update_controller:
            self._schedule(self.now + flow.controller.rtt_estimate, "feedback", flow)

    def run(
        self,
        traffic: TrafficGenerator,
        duration: float,
        recorder: "MetricsRecorder | None" = None,
    ) -> "MetricsRecorder":
        """Run once for simulated seconds and return the observing recorder."""
        from src.telemetry import MetricsRecorder

        if self._started:
            raise ValueError("create a fresh scheduler for each experiment")
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("duration must be finite and positive")
        if self._events or self.now != 0:
            raise ValueError("run requires a fresh scheduler without submitted packets")
        self._started = True
        self._traffic = traffic
        recorder = recorder if recorder is not None else MetricsRecorder()
        phase = 8 * traffic.packet_size / self.link.bandwidth
        for flow in traffic.flows.values():
            recorder.register(flow.flow_id, flow.start_time)
            self._schedule(flow.start_time + self.rng.random() * phase, "send", flow)
            self._schedule(
                flow.start_time + flow.controller.initial_rtt,
                "feedback",
                flow,
            )
        if traffic.cross_traffic_rate:
            self._schedule(0.0, "background", 0)
        while self._events and self._events[0].time <= duration:
            event = heapq.heappop(self._events)
            self.now = event.time
            if event.kind == "send":
                self._send(event.payload)
            elif event.kind == "receive":
                self._receive(*event.payload)
            elif event.kind == "ack":
                self._ack(*event.payload)
            elif event.kind == "feedback":
                self._feedback(event.payload, recorder)
            else:
                self.submit(
                    Packet(-1, event.payload, self.now, traffic.packet_size), self.now
                )
                self._schedule(
                    self.now + traffic.packet_size / traffic.cross_traffic_rate,
                    "background",
                    event.payload + 1,
                )
        self.now = duration
        for flow in traffic.flows.values():
            if flow.start_time < duration:
                self._feedback(flow, recorder, update_controller=False)
        recorder.finish(duration)
        return recorder


class Simulation:
    """Connect traffic, a bottleneck scheduler, and a read-only observer."""

    def __init__(
        self,
        link: Link,
        traffic: TrafficGenerator,
        recorder: "MetricsRecorder | None" = None,
        seed: int = 0,
        loss_probability: float = 0.0,
        jitter: float = 0.0,
    ) -> None:
        """Construct one reproducible experiment without running it."""
        self.traffic = traffic
        self.recorder = recorder
        self.scheduler = Scheduler(link, seed, loss_probability, jitter)

    def run(self, duration: float = 60.0) -> "MetricsRecorder":
        """Run for simulated seconds and return collected metrics."""
        self.recorder = self.scheduler.run(self.traffic, duration, self.recorder)
        return self.recorder
