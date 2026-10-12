"""Command-line configuration, error reporting, and entry-point compatibility."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.simulator.cli import main
from src.telemetry.plot import main as plot_main


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
    ],
)
def test_cli_rejects_invalid_configuration(args, tmp_path, capsys):
    """Fail before writing artifacts for invalid numeric or unsupported options."""
    with pytest.raises(SystemExit) as error:
        main([*args, "--output", str(tmp_path / "results")])
    assert error.value.code == 2
    assert "error:" in capsys.readouterr().err
    assert not (tmp_path / "results").exists()


def test_cli_parses_units_and_records_reproducible_configuration(tmp_path, capsys):
    """Preserve the exact experimental settings alongside valid telemetry."""
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
    assert payload["metadata"]["bandwidth_bits_per_second"] == 2_000_000
    assert payload["metadata"]["propagation_delay_seconds"] == 0.01
    assert payload["metadata"]["start_times"] == [0, 0.05]
    assert payload["metadata"]["seed"] == 42
    assert stdout["summary"]["duration"] == 0.2
    assert (tmp_path / "run.csv").stat().st_size > 0


@pytest.mark.parametrize("module", ["src", "src.simulator"])
def test_both_entry_points_run_from_the_repository(module, tmp_path):
    """Support the documented invocation and the issue's root-package entry."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            module,
            "--flows",
            "1",
            "--duration",
            "0.1",
            "--output",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["summary"]["flows"]["0"]["bytes_acked"] > 0


def test_cli_plot_option_exports_images(tmp_path, monkeypatch, capsys):
    """Make optional plot generation available directly from the simulator."""
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    main(["--flows", "1", "--duration", "0.1", "--plots", "--output", str(tmp_path)])
    outputs = json.loads(capsys.readouterr().out)["outputs"]["plots"]
    assert len(outputs) == 3
    assert all(Path(path).stat().st_size > 0 for path in outputs)


def test_plot_cli_reports_missing_input(tmp_path, capsys):
    """Return an argument error for an absent CSV rather than a traceback."""
    with pytest.raises(SystemExit) as error:
        plot_main(["--input", str(tmp_path / "missing.csv")])
    assert error.value.code == 2
    assert "error:" in capsys.readouterr().err
