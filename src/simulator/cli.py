"""Configure experiments against the team's simulator and telemetry interfaces."""

import argparse
import json
import math
import re
from collections.abc import Sequence
from pathlib import Path

from src.algorithm import AIMDController, RenoController
from src.integration import IntegrationPendingError, Simulation, load_module_api


def _positive_int(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def _positive_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return result


def _fraction(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise argparse.ArgumentTypeError("must be between zero and one")
    return result


def _bandwidth(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)\s*([kmg]?)(?:bps|bit/s)?", value, re.I)
    if not match:
        raise argparse.ArgumentTypeError("use bits/second, for example 10Mbps")
    result = float(match[1]) * {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}[match[2].lower()]
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("bandwidth must be finite")
    return result


def _capacity(value: str) -> float:
    result = _bandwidth(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("link bandwidth must be positive")
    return result


def _delay(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)\s*(ms|s)?", value, re.I)
    if not match:
        raise argparse.ArgumentTypeError(
            "use seconds or milliseconds, for example 20ms"
        )
    result = float(match[1]) / (1000 if (match[2] or "").lower() == "ms" else 1)
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("delay must be finite")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Validate arguments and delegate packet and metric work to teammate APIs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flows", type=_positive_int, default=5)
    parser.add_argument(
        "--duration", type=_positive_float, default=60, help="Simulated seconds"
    )
    parser.add_argument("--bandwidth", type=_capacity, default=10_000_000)
    parser.add_argument(
        "--delay", type=_delay, default=0.02, help="One-way propagation delay"
    )
    parser.add_argument("--buffer", type=_positive_int, default=100)
    parser.add_argument("--algorithm", choices=("aimd", "reno"), default="aimd")
    parser.add_argument("--output", type=Path, default=Path("results"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--packet-size", type=_positive_int, default=1500)
    parser.add_argument(
        "--start-times", help="Comma-separated start times, one per flow"
    )
    parser.add_argument(
        "--cross-traffic", type=_bandwidth, default=0, help="Fixed background bits/s"
    )
    parser.add_argument("--loss", type=_fraction, default=0)
    parser.add_argument("--jitter", type=_delay, default=0)
    parser.add_argument("--ecn-threshold", type=_fraction, default=0.5)
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args(argv)
    try:
        starts = (
            [float(value) for value in args.start_times.split(",")]
            if args.start_times is not None
            else None
        )
        if starts is not None:
            if len(starts) != args.flows:
                raise ValueError("start-times must contain one value for each flow")
            if any(not math.isfinite(value) or value < 0 for value in starts):
                raise ValueError("start times must be finite and nonnegative")
        api = load_module_api()
        link = api.link(args.bandwidth, args.delay, args.buffer, args.ecn_threshold)
        controller_type = AIMDController if args.algorithm == "aimd" else RenoController

        def controller_factory():
            return controller_type(
                initial_rtt=2 * args.delay + 8 * args.packet_size / args.bandwidth,
                mss=args.packet_size,
            )

        traffic = api.traffic_generator(
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
        logger = api.logger(recorder, metadata)
        csv_path = logger.write_csv(args.output / "run.csv")
        json_path = logger.write_json(args.output / "run.json")
        outputs = {"csv": str(csv_path), "json": str(json_path)}
        if args.plots:
            from src.telemetry.plot import plot_results

            outputs["plots"] = [str(path) for path in plot_results(csv_path)]
    except (IntegrationPendingError, ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps({"summary": recorder.summary(), "outputs": outputs}, indent=2))
    return 0
