"""Render throughput, bottleneck queue depth, and fairness from CSV results."""

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Sequence


def plot_results(
    input_path: str | Path, output_dir: str | Path | None = None
) -> list[Path]:
    """Save three labelled PNG plots using a noninteractive Matplotlib backend."""
    source = Path(input_path)
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"time", "flow_id", "throughput", "queue_depth", "fairness"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                "CSV must contain time, flow_id, throughput, queue_depth, fairness"
            )
        rows = list(reader)
    if not rows:
        raise ValueError("CSV contains no observations")
    parsed = []
    for number, row in enumerate(rows, start=2):
        try:
            values = {key: float(row[key]) for key in required if key != "flow_id"}
            flow_id = int(row["flow_id"])
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"CSV row {number} contains missing or invalid numbers"
            ) from error
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError("plot values must be finite")
        parsed.append({**values, "flow_id": flow_id})
    parsed.sort(key=lambda row: row["time"])
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    destination = Path(output_dir) if output_dir else source.parent / "plots"
    destination.mkdir(parents=True, exist_ok=True)
    outputs = []
    grouped = defaultdict(list)
    for row in parsed:
        grouped[row["flow_id"]].append(row)
    fig, ax = plt.subplots()
    for flow_id, observations in sorted(grouped.items()):
        ax.plot(
            [row["time"] for row in observations],
            [row["throughput"] for row in observations],
            label=f"Flow {flow_id}",
        )
    ax.set(
        title="Per-flow throughput",
        xlabel="Simulated time (s)",
        ylabel="Throughput (bytes/s)",
    )
    ax.legend()
    fig.tight_layout()
    outputs.append(destination / "throughput.png")
    fig.savefig(outputs[-1])
    plt.close(fig)
    for key, title, ylabel, name in (
        ("queue_depth", "Bottleneck queue depth", "Queue depth (packets)", "queue.png"),
        ("fairness", "Jain's fairness index", "Fairness", "fairness.png"),
    ):
        fig, ax = plt.subplots()
        ax.plot(
            [row["time"] for row in parsed], [row[key] for row in parsed], label=title
        )
        ax.set(title=title, xlabel="Simulated time (s)", ylabel=ylabel)
        if key == "fairness":
            ax.set_ylim(0, 1.05)
        ax.legend()
        fig.tight_layout()
        outputs.append(destination / name)
        fig.savefig(outputs[-1])
        plt.close(fig)
    return outputs


def main(argv: Sequence[str] | None = None) -> int:
    """Parse standalone plotting arguments and report invalid inputs cleanly."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument(
        "--output", type=Path, help="Defaults to the input's plots/ directory"
    )
    args = parser.parse_args(argv)
    try:
        outputs = plot_results(args.input, args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
