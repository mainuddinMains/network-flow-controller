"""Synthetic-feedback unit tests for MetricsRecorder (#12)."""

import math

import pytest

from src.telemetry import MetricsRecorder


def _feedback(
    *,
    rtt=0.1,
    bytes_acked=3000,
    packets_sent=10,
    packets_dropped=1,
):
    """Build a mutable feedback dict for record() tests."""
    return {
        "rtt": rtt,
        "bytes_acked": bytes_acked,
        "packets_sent": packets_sent,
        "packets_dropped": packets_dropped,
    }


def test_record_computes_throughput_rtt_ms_and_loss_percent():
    """Verify throughput bytes/s, RTT in ms, and loss percentage units."""
    recorder = MetricsRecorder()
    recorder.record(1.0, 0, _feedback(), interval=1.0)
    snap = recorder.snapshot()[0]
    assert snap["throughput"] == 3000
    assert snap["rtt"] == 100
    assert snap["loss"] == 10


def test_multi_flow_snapshot_and_per_flow_history():
    """Track independent flows and expose snapshot keyed by flow ID."""
    recorder = MetricsRecorder()
    recorder.record(
        1.0, 0, _feedback(bytes_acked=1000, packets_dropped=0), interval=1.0
    )
    recorder.record(
        1.5, 1, _feedback(bytes_acked=2000, packets_dropped=2), interval=0.5
    )
    recorder.record(
        2.0, 0, _feedback(bytes_acked=4000, packets_dropped=0), interval=1.0
    )

    snap = recorder.snapshot()
    assert set(snap) == {0, 1}
    assert snap[0]["throughput"] == 4000
    assert snap[1]["throughput"] == 4000
    assert snap[1]["loss"] == 20

    assert len(recorder.history) == 3
    assert [row["flow_id"] for row in recorder.history] == [0, 1, 0]
    flow0 = [row for row in recorder.history if row["flow_id"] == 0]
    assert len(flow0) == 2
    assert flow0[0]["throughput"] == 1000
    assert flow0[1]["throughput"] == 4000


def test_snapshot_isolation_does_not_mutate_stored_rows():
    """Mutating a snapshot must not change the next snapshot or history."""
    recorder = MetricsRecorder()
    recorder.record(1.0, 0, _feedback(), interval=1.0)
    snap = recorder.snapshot()
    snap[0]["throughput"] = 0
    assert recorder.snapshot()[0]["throughput"] == 3000
    assert recorder.history[0]["throughput"] == 3000


def test_history_rows_are_immutable():
    """Stored history rows reject in-place assignment."""
    recorder = MetricsRecorder()
    recorder.record(1.0, 0, _feedback(), interval=1.0)
    with pytest.raises(TypeError):
        recorder.history[0]["throughput"] = 0


def test_record_does_not_mutate_feedback():
    """record() must leave the caller's feedback mapping unchanged."""
    recorder = MetricsRecorder()
    feedback = _feedback()
    original = feedback.copy()
    recorder.record(1.0, 0, feedback, interval=1.0)
    assert feedback == original


@pytest.mark.parametrize(
    "kwargs, interval",
    [
        ({}, 0),
        ({}, -1.0),
        ({}, float("nan")),
        ({}, float("inf")),
        ({"rtt": 0}, 1.0),
        ({"rtt": -0.1}, 1.0),
        ({"rtt": float("nan")}, 1.0),
        ({"rtt": float("inf")}, 1.0),
        ({"bytes_acked": -1}, 1.0),
        ({"packets_sent": -1}, 1.0),
        ({"packets_dropped": -1}, 1.0),
        ({"packets_sent": 5, "packets_dropped": 6}, 1.0),
    ],
)
def test_record_rejects_invalid_interval_and_feedback(kwargs, interval):
    """Reject non-positive interval and invalid feedback counters or RTT."""
    recorder = MetricsRecorder()
    feedback = _feedback(**kwargs)
    with pytest.raises(ValueError):
        recorder.record(1.0, 0, feedback, interval=interval)


def test_record_rejects_non_finite_time():
    """Reject non-finite observation times."""
    recorder = MetricsRecorder()
    with pytest.raises(ValueError):
        recorder.record(float("nan"), 0, _feedback(), interval=1.0)
    with pytest.raises(ValueError):
        recorder.record(float("inf"), 0, _feedback(), interval=1.0)


def test_record_rejects_missing_feedback_fields():
    """Reject feedback that omits required keys."""
    recorder = MetricsRecorder()
    for key in ("rtt", "bytes_acked", "packets_sent", "packets_dropped"):
        feedback = _feedback()
        del feedback[key]
        with pytest.raises(ValueError, match="missing required fields"):
            recorder.record(1.0, 0, feedback, interval=1.0)


def test_zero_packets_sent_uses_loss_denominator_of_one():
    """Loss percent uses max(1, packets_sent) when nothing was sent."""
    recorder = MetricsRecorder()
    recorder.record(
        1.0,
        0,
        _feedback(bytes_acked=0, packets_sent=0, packets_dropped=0),
        interval=1.0,
    )
    assert recorder.snapshot()[0]["loss"] == 0.0
    assert math.isfinite(recorder.snapshot()[0]["loss"])
