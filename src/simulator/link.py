"""Network link parameters for discrete-event congestion-control simulation."""

import math


class Link:
    """Immutable description of a bottleneck network link.

    :param bandwidth: Link capacity in bits per second.
    :param propagation_delay: One-way propagation delay in seconds.
    :param buffer_size: Maximum packets the link queue can hold.
    :param ecn_threshold: Queue fill fraction at which ECN marking begins.
    """

    def __init__(
        self,
        bandwidth: float,
        propagation_delay: float,
        buffer_size: int,
        ecn_threshold: float = 0.5,
    ) -> None:
        """Validate and store link parameters."""
        if not math.isfinite(bandwidth) or bandwidth <= 0:
            raise ValueError("bandwidth must be finite and positive")
        if not math.isfinite(propagation_delay) or propagation_delay <= 0:
            raise ValueError("propagation_delay must be finite and positive")
        if not isinstance(buffer_size, int) or isinstance(buffer_size, bool) or buffer_size < 1:
            raise ValueError("buffer_size must be a positive integer")
        if not math.isfinite(ecn_threshold) or not 0.0 <= ecn_threshold <= 1.0:
            raise ValueError("ecn_threshold must be between 0 and 1")

        self.bandwidth = float(bandwidth)
        self.propagation_delay = float(propagation_delay)
        self.buffer_size = int(buffer_size)
        self.ecn_threshold = float(ecn_threshold)
