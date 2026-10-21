"""Per-flow throughput, RTT, and loss recording from immutable feedback."""

import math
from collections.abc import Mapping
from types import MappingProxyType

_REQUIRED_FEEDBACK = ("rtt", "bytes_acked", "packets_sent", "packets_dropped")


class MetricsRecorder:
    """Observe per-interval feedback and keep detached per-flow time series.

    The recorder is a read-only observer: it never mutates the feedback mapping
    or any simulator or algorithm state. Flows are created on the first
    successful ``record`` call for each flow ID.
    """

    def __init__(self) -> None:
        """Initialize empty per-flow observation storage."""
        self._series: dict[int, list[Mapping[str, object]]] = {}
        self._history: list[Mapping[str, object]] = []
        self._latest: dict[int, dict[str, object]] = {}

    @property
    def history(self) -> tuple[Mapping[str, object], ...]:
        """Return a flat, immutable view of all recorded rows in append order."""
        return tuple(self._history)

    def record(
        self,
        time: float,
        flow_id: int,
        feedback: Mapping[str, object],
        *,
        interval: float,
    ) -> None:
        """Append one observation derived from immutable feedback.

        :param time: Observation time in seconds.
        :param flow_id: Flow identifier.
        :param feedback: Mapping with ``rtt`` (seconds), ``bytes_acked``,
            ``packets_sent``, and ``packets_dropped``.
        :param interval: Observation interval in seconds; used for throughput.
        :raises ValueError: if time, interval, or feedback values are invalid.
        """
        if not math.isfinite(time):
            raise ValueError("time must be finite")
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("interval must be finite and positive")
        missing = [key for key in _REQUIRED_FEEDBACK if key not in feedback]
        if missing:
            raise ValueError(
                "feedback missing required fields: " + ", ".join(missing)
            )

        rtt = float(feedback["rtt"])
        if not math.isfinite(rtt) or rtt <= 0:
            raise ValueError("rtt must be finite and positive")

        bytes_acked = int(feedback["bytes_acked"])
        packets_sent = int(feedback["packets_sent"])
        packets_dropped = int(feedback["packets_dropped"])
        if bytes_acked < 0 or packets_sent < 0 or packets_dropped < 0:
            raise ValueError("packet counters cannot be negative")
        if packets_dropped > packets_sent:
            raise ValueError("dropped packets cannot exceed sent packets")

        row = {
            "time": time,
            "flow_id": flow_id,
            "throughput": bytes_acked / interval,
            "rtt": rtt * 1000,
            "loss": 100 * packets_dropped / max(1, packets_sent),
            "bytes_acked": bytes_acked,
            "packets_sent": packets_sent,
            "packets_dropped": packets_dropped,
            "interval": interval,
        }
        stored = MappingProxyType(row.copy())
        self._latest[flow_id] = row
        self._series.setdefault(flow_id, []).append(stored)
        self._history.append(stored)

    def snapshot(self) -> dict[int, dict[str, object]]:
        """Return detached current observations keyed by flow ID."""
        return {flow_id: row.copy() for flow_id, row in self._latest.items()}
