"""Validate the CLI and integration wiring without implementing teammate logic."""

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import src.simulator as simulator
import src.telemetry as telemetry
from src.algorithm import RenoController
from src.integration import (
    IntegrationPendingError,
    load_module_api,
    missing_dependencies,
)
from src.simulator.cli import main


@pytest.fixture
def fake_teammate_api(monkeypatch):
    """Provide recording doubles solely to check integration delegation."""
    calls = {}

    class Link:
        """Capture link configuration without simulating any packets."""

        def __init__(self, bandwidth, delay, buffer, threshold):
            """Capture the delegated constructor arguments."""
            calls["link"] = (bandwidth, delay, buffer, threshold)

    class TrafficGenerator:
        """Capture controllers and activation configuration."""

        def __init__(self, flows, factory, starts, cross_traffic, packet_size):
            """Create real controllers but leave traffic generation to teammates."""
            controllers = [factory() for _ in range(flows)]
            calls["controllers"] = controllers
            calls["traffic"] = (starts, cross_traffic, packet_size)
            self.flows = {
                key: SimpleNamespace(start_time=(starts or [0] * flows)[key])
                for key in range(flows)
            }

    class MetricsRecorder:
        """Return a minimal result for wiring checks, not measured telemetry."""

        def summary(self):
            """Expose the duration passed to the scheduler."""
            return {"duration": calls["duration"], "flows": {}}

    class Scheduler:
        """Capture orchestration calls without network behavior."""

        def __init__(self, link, seed, loss_probability, jitter):
            """Record scheduler options."""
            calls["scheduler"] = (link, seed, loss_probability, jitter)

        def run(self, traffic, duration, recorder):
            """Return the supplied observer and record the duration."""
            calls["duration"] = duration
            calls["observer"] = recorder
            return recorder

    class ExperimentLogger:
        """Capture export delegation without calculating any metrics."""

        def __init__(self, recorder, metadata=None):
            """Capture metadata and the exact recorder from scheduling."""
            self.recorder = recorder
            self.metadata = metadata or {}
            calls["logger_recorder"] = recorder

        def write_csv(self, path):
            """Write a marker file to check destination handling."""
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("time,flow_id\n")
            return path

        def write_json(self, path):
            """Write captured configuration for assertions."""
            path.write_text(
                json.dumps(
                    {
                        "metadata": self.metadata,
                        "summary": self.recorder.summary(),
                    }
                )
            )
            return path

    for name, value in (
        ("Link", Link),
        ("TrafficGenerator", TrafficGenerator),
        ("Scheduler", Scheduler),
    ):
        monkeypatch.setattr(simulator, name, value, raising=False)
    for name, value in (
        ("MetricsRecorder", MetricsRecorder),
        ("ExperimentLogger", ExperimentLogger),
    ):
        monkeypatch.setattr(telemetry, name, value, raising=False)
    return calls


@pytest.mark.parametrize(
    "args",
    [
        ["--flows", "0"],
        ["--duration", "-1"],
        ["--duration", "nan"],
        ["--bandwidth", "0"],
        ["--bandwidth", "fast"],
        ["--delay", "-1ms"],
        ["--buffer", "0"],
        ["--loss", "1.1"],
        ["--jitter", "bad"],
        ["--flows", "2", "--start-times", "0"],
        ["--packet-size", "0"],
        ["--algorithm", "bbr"],
        ["--ecn-threshold", "2"],
        ["--start-times", "0,0,0,0,nan"],
    ],
)
def test_cli_rejects_invalid_configuration(args, tmp_path, capsys):
    """Validate bad input before attempting to load unfinished modules."""
    with pytest.raises(SystemExit) as error:
        main([*args, "--output", str(tmp_path / "results")])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert "error:" in message
    assert "Awaiting teammate" not in message
    assert not (tmp_path / "results").exists()


def test_cli_delegates_units_and_configuration(tmp_path, capsys, fake_teammate_api):
    """Pass validated settings, controllers, observer, and exports to the APIs."""
    assert (
        main(
            [
                "--flows",
                "2",
                "--duration",
                "0.2",
                "--bandwidth",
                "2Mbps",
                "--delay",
                "10ms",
                "--algorithm",
                "reno",
                "--seed",
                "42",
                "--cross-traffic",
                "100Kbps",
                "--jitter",
                "1ms",
                "--loss",
                "0.01",
                "--start-times",
                "0,0.05",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    stdout = json.loads(capsys.readouterr().out)
    payload = json.loads((tmp_path / "run.json").read_text())
    assert fake_teammate_api["link"] == (2_000_000, 0.01, 100, 0.5)
    assert fake_teammate_api["traffic"] == ([0, 0.05], 12_500, 1500)
    assert all(
        isinstance(controller, RenoController)
        for controller in fake_teammate_api["controllers"]
    )
    assert fake_teammate_api["scheduler"][1:] == (42, 0.01, 0.001)
    assert fake_teammate_api["observer"] is fake_teammate_api["logger_recorder"]
    assert payload["metadata"]["seed"] == 42
    assert payload["metadata"]["start_times"] == [0, 0.05]
    assert stdout["summary"]["duration"] == 0.2


@pytest.mark.parametrize("module", ["src", "src.simulator"])
def test_entry_point_help_does_not_require_teammate_implementations(module):
    """Expose CLI help even while collaborator modules are unfinished."""
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--flows" in result.stdout
    assert "--algorithm" in result.stdout


def test_missing_interfaces_are_reported_without_creating_output(
    tmp_path,
    capsys,
    monkeypatch,
):
    """Report integration dependencies clearly instead of fabricating results."""
    for module, names in (
        (simulator, ("Link", "TrafficGenerator", "Scheduler")),
        (telemetry, ("MetricsRecorder", "ExperimentLogger")),
    ):
        for name in names:
            monkeypatch.delattr(module, name, raising=False)
    assert "src.simulator.Link" in missing_dependencies()
    with pytest.raises(IntegrationPendingError):
        load_module_api()
    with pytest.raises(SystemExit) as error:
        main(["--duration", "0.1", "--output", str(tmp_path / "results")])
    assert error.value.code == 2
    assert "Awaiting teammate implementations" in capsys.readouterr().err
    assert not (tmp_path / "results").exists()


def test_plot_option_delegates_to_telemetry(
    tmp_path,
    monkeypatch,
    capsys,
    fake_teammate_api,
):
    """Check optional plotting delegation without implementing plotting."""
    plot_module = ModuleType("src.telemetry.plot")
    inputs = []

    def plot_results(input_path):
        inputs.append(input_path)
        return [
            tmp_path / name for name in ("throughput.png", "queue.png", "fairness.png")
        ]

    plot_module.plot_results = plot_results
    monkeypatch.setitem(sys.modules, "src.telemetry.plot", plot_module)
    main(["--duration", "0.1", "--plots", "--output", str(tmp_path)])
    outputs = json.loads(capsys.readouterr().out)["outputs"]["plots"]
    assert inputs == [tmp_path / "run.csv"]
    assert len(outputs) == 3
