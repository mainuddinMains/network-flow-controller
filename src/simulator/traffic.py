"""Independent flow state and configurable traffic sources."""

import math
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from src.algorithm import AIMDController, CongestionController

from .link import Packet


@dataclass
class Flow:
    """A paced flow's packet bookkeeping and per-round raw observations."""

    flow_id: int
    controller: CongestionController
    start_time: float
    packet_size: int
    next_sequence: int = 0
    next_expected: int = 0
    ack_expected: int = 0
    last_expected: int = 0
    last_feedback: float = 0.0
    sent: int = 0
    dropped: int = 0
    bytes_acked: int = 0
    duplicate_acks: int = 0
    ecn: bool = False
    recovery_until: int | None = None
    rtt_samples: list[float] = field(default_factory=list)
    outstanding: dict[int, Packet] = field(default_factory=dict)
    received: set[int] = field(default_factory=set)
    retransmitted: set[int] = field(default_factory=set)
    receiver_pending: set[int] = field(default_factory=set)
    lost: set[int] = field(default_factory=set)
    retries: deque[int] = field(default_factory=deque)
    queued_retries: set[int] = field(default_factory=set)


class TrafficGenerator:
    """Create independent controllers with simultaneous or staggered starts."""

    def __init__(
        self,
        flows: int = 5,
        controller_factory: Callable[[], CongestionController] | None = None,
        start_times: Sequence[float] | None = None,
        cross_traffic_rate: float = 0.0,
        packet_size: int = 1500,
    ) -> None:
        """Configure start times and background traffic in bytes per second."""
        if not isinstance(flows, int) or flows < 1:
            raise ValueError("flows must be a positive integer")
        if not isinstance(packet_size, int) or packet_size < 1:
            raise ValueError("packet_size must be a positive integer")
        if not math.isfinite(cross_traffic_rate) or cross_traffic_rate < 0:
            raise ValueError("cross_traffic_rate must be finite and nonnegative")
        starts = list(start_times) if start_times is not None else [0.0] * flows
        if len(starts) != flows:
            raise ValueError("start_times must contain one value for each flow")
        if any(not math.isfinite(time) or time < 0 for time in starts):
            raise ValueError("start times must be finite and nonnegative")
        factory = controller_factory or (lambda: AIMDController(mss=packet_size))
        self.flows = {}
        controller_ids = set()
        for flow_id, start_time in enumerate(starts):
            controller = factory()
            if not isinstance(controller, CongestionController):
                raise ValueError("controller_factory must return a controller")
            if id(controller) in controller_ids:
                raise ValueError("each flow must have an independent controller")
            if controller.mss != packet_size:
                raise ValueError("controller MSS must equal the traffic packet size")
            controller_ids.add(id(controller))
            self.flows[flow_id] = Flow(
                flow_id,
                controller,
                start_time,
                packet_size,
                last_feedback=start_time,
            )
        self.cross_traffic_rate = cross_traffic_rate
        self.packet_size = packet_size
