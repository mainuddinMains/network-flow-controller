"""End-to-end acceptance checks for five competing flows and result exports."""

import csv
import json
import time

import pytest

from src.algorithm import AIMDController, RenoController
from src.simulator import Link, Simulation, TrafficGenerator
from src.telemetry import ExperimentLogger


@pytest.mark.parametrize("controller_type", [AIMDController, RenoController])
def test_five_flow_simulation_is_fair_and_exports_results(tmp_path, controller_type):
    """Meet issue #17's fairness, nonstarvation, output, and runtime requirements."""
    started = time.perf_counter()
    recorder = Simulation(
        Link(bandwidth=10_000_000, propagation_delay=0.02, buffer_size=100),
        TrafficGenerator(5, controller_type),
        seed=0,
    ).run(30)
    logger = ExperimentLogger(recorder)
    csv_path = logger.write_csv(tmp_path / "run.csv")
    json_path = logger.write_json(tmp_path / "run.json")
    summary = recorder.summary()
    assert time.perf_counter() - started < 30
    assert summary["fairness"] >= 0.9
    assert all(flow["throughput"] > 0 for flow in summary["flows"].values())
    assert summary["total_throughput_bytes_per_second"] <= 10_000_000 / 8
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    payload = json.loads(json_path.read_text())
    assert rows
    assert len(payload["flows"]) == 5
    for flow_id, metrics in summary["flows"].items():
        recorded_bytes = sum(
            int(row["bytes_acked"]) for row in rows if int(row["flow_id"]) == flow_id
        )
        assert recorded_bytes == metrics["bytes_acked"]
        assert (
            recorded_bytes == payload["summary"]["flows"][str(flow_id)]["bytes_acked"]
        )
