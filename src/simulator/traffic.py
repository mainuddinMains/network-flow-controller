"""Traffic generator for multiple concurrent congestion-controlled flows."""

import math
from collections.abc import Callable


class Flow:
    """A single flow driven by its own CongestionController instance.

    :param flow_id: Unique integer identifier for this flow.
    :param controller: The congestion controller governing this flow's window.
    :param start_time: Simulated time in seconds when the flow begins sending.
    :param packet_bytes: Payload size in bytes (MSS equivalent).
    """

    def __init__(
        self,
        flow_id: int,
        controller: object,
        start_time: float,
        packet_bytes: int,
    ) -> None:
        """Store flow parameters and initialize recovery state."""
        self.flow_id = flow_id
        self.controller = controller
        self.start_time = float(start_time)
        self.packet_bytes = int(packet_bytes)
        self.in_recovery: bool = False


class TrafficGenerator:
    """Spawn N independent flows each driven by its own CongestionController.

    :param n_flows: Number of flows to create.
    :param controller_factory: No-argument callable that returns a new controller.
    :param start_times: Per-flow start times in seconds; defaults to zero for all.
    :param background_bytes_per_second: Fixed cross-traffic rate in bytes per second.
    :param packet_bytes: Packet payload size in bytes used by all flows.
    """

    def __init__(
        self,
        n_flows: int,
        controller_factory: Callable,
        start_times: list[float] | None = None,
        background_bytes_per_second: float = 0.0,
        packet_bytes: int = 1500,
    ) -> None:
        """Validate parameters and create one Flow per requested flow."""
        if not isinstance(n_flows, int) or isinstance(n_flows, bool) or n_flows < 1:
            raise ValueError("n_flows must be a positive integer")
        if not isinstance(packet_bytes, int) or isinstance(packet_bytes, bool) or packet_bytes < 1:
            raise ValueError("packet_bytes must be a positive integer")
        if not math.isfinite(background_bytes_per_second) or background_bytes_per_second < 0:
            raise ValueError("background_bytes_per_second must be finite and non-negative")

        if start_times is None:
            start_times = [0.0] * n_flows
        elif len(start_times) != n_flows:
            raise ValueError("start_times must contain one entry per flow")

        self.background_bytes_per_second = float(background_bytes_per_second)
        self.packet_bytes = int(packet_bytes)

        self.flows: dict[int, Flow] = {
            i: Flow(
                flow_id=i,
                controller=controller_factory(),
                start_time=float(start_times[i]),
                packet_bytes=self.packet_bytes,
            )
            for i in range(n_flows)
        }
