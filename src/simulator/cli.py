"""Run reproducible congestion control experiments from the command line."""

import argparse
import json
import math
import re
from collections.abc import Sequence
from pathlib import Path

from src.algorithm import AIMDController, RenoController
from src.telemetry import ExperimentLogger

from .link import Link
from .simulation import Simulation
from .traffic import TrafficGenerator


def _bandwidth(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)\s*([kmg]?)(?:bps|bit/s)?", value, re.I)
    if not match:
        raise argparse.ArgumentTypeError(
            "use bits/second, for example 10000000 or 10Mbps"
        )
    result = float(match[1]) * {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}[match[2].lower()]
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("bandwidth must be finite")
    return result


def _delay(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)\s*(ms|s)?", value, re.I)
    if not match:
        raise argparse.ArgumentTypeError(
            "use seconds or milliseconds, for example 0.02 or 20ms"
        )
    result = float(match[1]) / (1000 if (match[2] or "").lower() == "ms" else 1)
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("delay must be finite")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Parse link/flow configuration, run, and export CSV plus JSON results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flows", type=int, default=5)
    parser.add_argument("--duration", type=float, default=60, help="Simulated seconds")
    parser.add_argument("--bandwidth", type=_bandwidth, default=10_000_000)
    parser.add_argument(
        "--delay", type=_delay, default=0.02, help="One-way propagation delay"
    )
    parser.add_argument(
        "--buffer", type=int, default=100, help="Packets including service"
    )
    parser.add_argument("--algorithm", choices=("aimd", "reno"), default="aimd")
    parser.add_argument("--output", type=Path, default=Path("results"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--packet-size", type=int, default=1500)
    parser.add_argument(
        "--start-times", help="Comma-separated simulated seconds, one per flow"
    )
    parser.add_argument(
        "--cross-traffic", type=_bandwidth, default=0, help="Fixed background bits/s"
    )
    parser.add_argument(
        "--loss", type=float, default=0, help="Independent drop probability"
    )
    parser.add_argument(
        "--jitter", type=_delay, default=0, help="Maximum added one-way delay"
    )
    parser.add_argument("--ecn-threshold", type=float, default=0.5)
    parser.add_argument(
        "--plots", action="store_true", help="Also save three PNG plots"
    )
    args = parser.parse_args(argv)
    try:
        starts = (
            [float(value) for value in args.start_times.split(",")]
            if args.start_times
            else None
        )
        link = Link(args.bandwidth, args.delay, args.buffer, args.ecn_threshold)
        controller_type = AIMDController if args.algorithm == "aimd" else RenoController

        def controller_factory():
            return controller_type(
                initial_rtt=2 * args.delay + 8 * args.packet_size / args.bandwidth,
                mss=args.packet_size,
            )

        traffic = TrafficGenerator(
            args.flows,
            controller_factory,
            starts,
            args.cross_traffic / 8,
            args.packet_size,
        )
        simulation = Simulation(
            link,
            traffic,
            seed=args.seed,
            loss_probability=args.loss,
            jitter=args.jitter,
        )
        recorder = simulation.run(args.duration)
        metadata = {
            "algorithm": args.algorithm,
            "seed": args.seed,
            "bandwidth_bits_per_second": args.bandwidth,
            "propagation_delay_seconds": args.delay,
            "buffer_packets": args.buffer,
            "packet_size_bytes": args.packet_size,
            "start_times": [flow.start_time for flow in traffic.flows.values()],
            "cross_traffic_bits_per_second": args.cross_traffic,
            "loss_probability": args.loss,
            "jitter_seconds": args.jitter,
            "ecn_threshold": args.ecn_threshold,
        }
        logger = ExperimentLogger(recorder, metadata)
        csv_path = logger.write_csv(args.output / "run.csv")
        json_path = logger.write_json(args.output / "run.json")
        outputs = {"csv": str(csv_path), "json": str(json_path)}
        if args.plots:
            from src.telemetry.plot import plot_results

            outputs["plots"] = [str(path) for path in plot_results(csv_path)]
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps({"summary": recorder.summary(), "outputs": outputs}, indent=2))
    return 0
