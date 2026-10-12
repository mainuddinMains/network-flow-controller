"""Fairness, observer isolation, structured exports, and plotting behavior."""

import csv
import json

import pytest

from src.telemetry import ExperimentLogger, MetricsRecorder, jains_fairness
from src.telemetry.plot import plot_results


@pytest.mark.parametrize(
    "values, expected",
    [
        ([10, 10, 10], 1),
        ([1, 3], 0.8),
        ([100], 1),
        ([0, 0], 0),
        ([], 0),
        ([1e300, 1e300], 1),
        ([0, 10], 0.5),
    ],
)
def test_fairness_handles_equal_unequal_and_zero_rates(values, expected):
    """Check the documented zero convention and stable large-value arithmetic."""
    assert jains_fairness(values) == pytest.approx(expected)


@pytest.mark.parametrize("values", [[-1, 2], [float("nan")], [float("inf")]])
def test_fairness_rejects_invalid_rates(values):
    """Reject undefined or negative throughput measurements."""
    with pytest.raises(ValueError):
        jains_fairness(values)


def _record(recorder, flow_id=0, time=1, bytes_acked=3000):
    feedback = {
        "rtt": 0.1,
        "bytes_acked": bytes_acked,
        "packets_sent": 10,
        "packets_dropped": 1,
    }
    original = feedback.copy()
    recorder.record(
        time,
        flow_id,
        feedback,
        interval=1,
        queue_depth=4,
        cwnd=10,
        state="CONGESTION_AVOIDANCE",
    )
    assert feedback == original


def test_metrics_units_counters_and_snapshot_isolation():
    """Compute units correctly and prevent snapshots from editing stored rows."""
    recorder = MetricsRecorder()
    recorder.register(0)
    _record(recorder)
    snapshot = recorder.snapshot()
    assert snapshot[0]["throughput"] == 3000
    assert snapshot[0]["rtt"] == 100
    assert snapshot[0]["loss"] == 10
    snapshot[0]["throughput"] = 0
    assert recorder.snapshot()[0]["throughput"] == 3000
    with pytest.raises(TypeError):
        recorder.history[0]["throughput"] = 0
    recorder.finish(1)
    assert recorder.summary()["fairness"] == 1
    assert recorder.summary()["flows"][0]["bytes_acked"] == 3000


def test_summary_uses_active_time_for_staggered_flow_rates():
    """Keep cumulative rates meaningful for flows with different start times."""
    recorder = MetricsRecorder()
    recorder.register(0)
    recorder.register(1, 1)
    _record(recorder, 0, time=2, bytes_acked=6000)
    _record(recorder, 1, time=2, bytes_acked=3000)
    recorder.finish(2)
    summary = recorder.summary()
    assert summary["flows"][0]["throughput"] == 3000
    assert summary["flows"][1]["throughput"] == 3000
    assert summary["fairness"] == 1
    assert summary["total_throughput_bytes_per_second"] == 4500


def test_invalid_observation_lifecycle_is_rejected():
    """Reject unregistered, backward, and post-finish observations."""
    recorder = MetricsRecorder()
    with pytest.raises(ValueError):
        _record(recorder)
    recorder.register(0)
    with pytest.raises(ValueError):
        recorder.register(0)
    _record(recorder)
    with pytest.raises(ValueError):
        _record(recorder, time=0.5)
    with pytest.raises(ValueError):
        recorder.finish(0.5)
    recorder.finish(1)
    with pytest.raises(ValueError):
        _record(recorder, time=2)
    with pytest.raises(ValueError):
        recorder.finish(2)


def test_csv_and_json_match_one_captured_snapshot(tmp_path):
    """Export valid files with consistent counters even if collection continues."""
    recorder = MetricsRecorder()
    recorder.register(0)
    _record(recorder)
    logger = ExperimentLogger(recorder, {"seed": 123})
    _record(recorder, time=2)
    csv_path = logger.write_csv(tmp_path / "nested" / "run.csv")
    json_path = logger.write_json(tmp_path / "nested" / "run.json")
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    payload = json.loads(json_path.read_text())
    assert len(rows) == 1
    assert payload["flows"]["0"]["throughput"] == [float(rows[0]["throughput"])]
    assert payload["summary"]["flows"]["0"]["bytes_acked"] == 3000
    assert payload["metadata"]["seed"] == 123
    assert payload["units"]["rtt"] == "milliseconds"


def test_empty_export_contains_schema_and_zero_summary(tmp_path):
    """Write parseable empty output without inventing observations."""
    logger = ExperimentLogger(MetricsRecorder())
    csv_path = logger.write_csv(tmp_path / "empty.csv")
    json_path = logger.write_json(tmp_path / "empty.json")
    assert csv_path.read_text().startswith("time,flow_id,throughput,rtt,loss,fairness")
    assert json.loads(json_path.read_text())["summary"]["fairness"] == 0


def test_plotting_produces_three_pngs(tmp_path, monkeypatch):
    """Generate actual image artifacts from exported telemetry."""
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    recorder = MetricsRecorder()
    recorder.register(0)
    _record(recorder)
    _record(recorder, time=2)
    csv_path = ExperimentLogger(recorder).write_csv(tmp_path / "run.csv")
    outputs = plot_results(csv_path)
    assert {path.name for path in outputs} == {
        "throughput.png",
        "queue.png",
        "fairness.png",
    }
    assert all(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in outputs)


@pytest.mark.parametrize(
    "contents",
    [
        "time,flow_id\n1,0\n",
        "time,flow_id,throughput,queue_depth,fairness\n",
        "time,flow_id,throughput,queue_depth,fairness\nnan,0,1,0,1\n",
        "time,flow_id,throughput,queue_depth,fairness\n1,0\n",
        "time,flow_id,throughput,queue_depth,fairness\n1,invalid,1,0,1\n",
    ],
)
def test_plotting_rejects_invalid_csv(tmp_path, contents):
    """Report incomplete, empty, and nonfinite telemetry inputs."""
    source = tmp_path / "bad.csv"
    source.write_text(contents)
    with pytest.raises(ValueError):
        plot_results(source)
