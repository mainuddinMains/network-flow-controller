"""A finite FIFO link with serialization delay and ECN marking."""

import math
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Packet:
    """An immutable payload identified by flow and sequence number."""

    flow_id: int
    sequence: int
    sent_at: float
    size: int = 1500
    retransmission: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.size, int) or self.size <= 0:
            raise ValueError("packet size must be a positive integer")
        if not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("sequence must be a nonnegative integer")
        if not math.isfinite(self.sent_at) or self.sent_at < 0:
            raise ValueError("sent_at must be finite and nonnegative")


@dataclass(frozen=True)
class Transmission:
    """The service times and congestion mark of an accepted packet."""

    packet: Packet
    enqueued_at: float
    service_start: float
    departure_at: float
    ecn: bool


class Link:
    """Serialize packets at bandwidth bits/second with a bounded FIFO.

    propagation_delay is one-way in seconds. buffer_size counts all accepted
    packets still awaiting departure, including the packet being serialized.
    """

    def __init__(
        self,
        bandwidth: float = 10_000_000,
        propagation_delay: float = 0.02,
        buffer_size: int = 100,
        ecn_threshold: float = 0.5,
    ) -> None:
        """Configure finite capacity, nonnegative delay, and the ECN fraction."""
        if not math.isfinite(bandwidth) or bandwidth <= 0:
            raise ValueError("bandwidth must be finite and positive")
        if not math.isfinite(propagation_delay) or propagation_delay < 0:
            raise ValueError("propagation_delay must be finite and nonnegative")
        if not isinstance(buffer_size, int) or buffer_size < 1:
            raise ValueError("buffer_size must be a positive integer")
        if not math.isfinite(ecn_threshold) or not 0 <= ecn_threshold <= 1:
            raise ValueError("ecn_threshold must be between zero and one")
        self.bandwidth = float(bandwidth)
        self.propagation_delay = propagation_delay
        self.buffer_size = buffer_size
        self.ecn_threshold = ecn_threshold
        self._queue: deque[Transmission] = deque()
        self._last_time = 0.0

    def dequeue(self, now: float) -> tuple[Transmission, ...]:
        """Remove and return all packets whose serialization has finished."""
        if not math.isfinite(now) or now < self._last_time:
            raise ValueError("link time must be finite and cannot move backwards")
        self._last_time = now
        departed = []
        while self._queue and self._queue[0].departure_at <= now:
            departed.append(self._queue.popleft())
        return tuple(departed)

    def queue_depth(self, now: float) -> int:
        """Return the number of packets still awaiting departure at now."""
        self.dequeue(now)
        return len(self._queue)

    def enqueue(self, packet: Packet, now: float) -> Transmission | None:
        """Schedule a packet, or return None when the buffer is full."""
        self.dequeue(now)
        if len(self._queue) >= self.buffer_size:
            return None
        service_start = max(now, self._queue[-1].departure_at) if self._queue else now
        transmission = Transmission(
            packet=packet,
            enqueued_at=now,
            service_start=service_start,
            departure_at=service_start + 8 * packet.size / self.bandwidth,
            ecn=len(self._queue) + 1 > self.ecn_threshold * self.buffer_size,
        )
        self._queue.append(transmission)
        return transmission
