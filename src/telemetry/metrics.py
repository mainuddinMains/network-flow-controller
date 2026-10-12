"""Compute metrics from immutable feedback without changing simulation state."""

import csv
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

FIELDS = (
    "time",
    "flow_id",
    "throughput",
    "rtt",
    "loss",
    "fairness",
    "queue_depth",
    "cwnd",
    "state",
    "bytes_acked",
    "packets_sent",
    "packets_dropped",
    "interval",
)
UNITS = {
    "time": "seconds",
    "throughput": "bytes/second",
    "rtt": "milliseconds",
    "loss": "percent",
    "queue_depth": "packets",
    "cwnd": "packets",
}


def jains_fairness(throughputs: Sequence[float]) -> float:
    """Return Jain's index; empty or all-zero inputs return zero by convention."""
    values = [float(value) for value in throughputs]
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("throughputs must be finite and nonnegative")
    if not values or max(values) == 0:
        return 0.0
    scale = max(values)
    normalized = [value / scale for value in values]
    return min(
        1.0,
        math.fsum(normalized) ** 2
        / (len(normalized) * math.fsum(value * value for value in normalized)),
    )


class MetricsRecorder:
    """Observe per-RTT feedback and keep detached per-flow time series."""

    def __init__(self) -> None:
        """Initialize empty observations and cumulative packet counters."""
        self._starts: dict[int, float] = {}
        self._latest: dict[int, dict[str, object]] = {}
        self._history: list[Mapping[str, object]] = []
        self._totals: dict[int, dict[str, int]] = {}
        self._duration: float | None = None

    def register(self, flow_id: int, start_time: float = 0.0) -> None:
        """Register a flow before observing it, including its activation time."""
        if flow_id in self._starts:
            raise ValueError("flow is already registered")
        if not math.isfinite(start_time) or start_time < 0:
            raise ValueError("start_time must be finite and nonnegative")
        self._starts[flow_id] = start_time
        self._totals[flow_id] = dict(bytes_acked=0, packets_sent=0, packets_dropped=0)

    @property
    def history(self) -> tuple[Mapping[str, object], ...]:
        """Return immutable rows so observers cannot change recorded results."""
        return tuple(self._history)

    def record(
        self,
        time: float,
        flow_id: int,
        feedback: Mapping[str, object],
        *,
        interval: float,
        queue_depth: int,
        cwnd: float,
        state: str,
    ) -> None:
        """Compute throughput, RTT in ms, loss percentage, and current fairness."""
        if self._duration is not None:
            raise ValueError("cannot record after finish")
        if flow_id not in self._starts:
            raise ValueError("register the flow before recording")
        if not math.isfinite(time) or time < self._starts[flow_id]:
            raise ValueError("time must be finite and at or after flow start")
        if self._history and time < self._history[-1]["time"]:
            raise ValueError("observation time cannot move backwards")
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("interval must be finite and positive")
        counts = {key: int(feedback.get(key, 0)) for key in self._totals[flow_id]}
        if any(value < 0 for value in counts.values()):
            raise ValueError("packet counters cannot be negative")
        if counts["packets_dropped"] > counts["packets_sent"]:
            raise ValueError("dropped packets cannot exceed sent packets")
        rtt = float(feedback["rtt"])
        if not math.isfinite(rtt) or rtt <= 0:
            raise ValueError("rtt must be finite and positive")
        row = {
            "time": time,
            "flow_id": flow_id,
            "throughput": counts["bytes_acked"] / interval,
            "rtt": rtt * 1000,
            "loss": 100 * counts["packets_dropped"] / max(1, counts["packets_sent"]),
            "fairness": 0.0,
            "queue_depth": queue_depth,
            "cwnd": cwnd,
            "state": state,
            **counts,
            "interval": interval,
        }
        self._latest[flow_id] = row
        rates = [
            self._latest.get(key, {}).get("throughput", 0.0)
            for key, start in self._starts.items()
            if start <= time
        ]
        row["fairness"] = jains_fairness(rates)
        for key, value in counts.items():
            self._totals[flow_id][key] += value
        self._history.append(MappingProxyType(row.copy()))

    def snapshot(self) -> dict[int, dict[str, object]]:
        """Return detached current observations keyed by flow ID."""
        return {flow_id: row.copy() for flow_id, row in self._latest.items()}

    def finish(self, duration: float) -> None:
        """Set the experiment end time for cumulative throughput statistics."""
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("duration must be finite and positive")
        if self._history and duration < self._history[-1]["time"]:
            raise ValueError("duration cannot precede the last observation")
        if self._duration is not None:
            raise ValueError("recorder is already finished")
        self._duration = duration

    def summary(self) -> dict[str, object]:
        """Summarize acknowledged bytes, fairness, loss, and RTT variability."""
        duration = self._duration or (
            float(self._history[-1]["time"]) if self._history else 0.0
        )
        flows = {}
        active_rates = []
        for flow_id, totals in self._totals.items():
            elapsed = max(0.0, duration - self._starts[flow_id])
            throughput = totals["bytes_acked"] / elapsed if elapsed else 0.0
            samples = [
                float(row["rtt"]) for row in self._history if row["flow_id"] == flow_id
            ]
            flows[flow_id] = {
                **totals,
                "throughput": throughput,
                "rtt_mean_ms": statistics.fmean(samples) if samples else 0.0,
                "rtt_variance_ms2": statistics.pvariance(samples) if samples else 0.0,
                "loss_rate_percent": (
                    100 * totals["packets_dropped"] / max(1, totals["packets_sent"])
                ),
            }
            if elapsed:
                active_rates.append(throughput)
        sent = sum(item["packets_sent"] for item in self._totals.values())
        dropped = sum(item["packets_dropped"] for item in self._totals.values())
        delivered = sum(item["bytes_acked"] for item in self._totals.values())
        return {
            "duration": duration,
            "flows": flows,
            "fairness": jains_fairness(active_rates),
            "total_throughput_bytes_per_second": delivered / duration
            if duration
            else 0,
            "loss_rate_percent": 100 * dropped / max(1, sent),
        }


class ExperimentLogger:
    """Export CSV and JSON from one captured recorder snapshot."""

    def __init__(
        self,
        recorder: MetricsRecorder,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Capture both time-series rows and summary for consistent exports."""
        self._rows = [dict(row) for row in recorder.history]
        self._summary = recorder.summary()
        self._metadata = dict(metadata or {})

    def write_csv(self, path: str | Path = "results/run.csv") -> Path:
        """Write one row per feedback per flow, creating parent directories."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(self._rows)
        return destination

    def write_json(self, path: str | Path = "results/run.json") -> Path:
        """Write time-series arrays grouped by flow ID alongside units and summary."""
        flows = {
            str(flow_id): {key: [] for key in FIELDS if key != "flow_id"}
            for flow_id in self._summary["flows"]
        }
        for row in self._rows:
            series = flows[str(row["flow_id"])]
            for key in series:
                series[key].append(row[key])
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "units": UNITS,
                    "metadata": self._metadata,
                    "summary": self._summary,
                    "flows": flows,
                },
                handle,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
        return destination
