"""Baseline measurements (spec section 6.2).

Four conditions on one documented link pair: two added-delay settings x two
loss settings. Every condition runs once per seed, with everything else held
constant. Results go to ``evidence/measure/<suite>/``:

    results.csv       one row per run (failed probes and failed runs included)
    rtt_samples.csv   every successful probe's RTT
    summary.csv/.md   per-condition mean and spread across runs
    *.png             labeled plots (needs matplotlib)
    <cond>_seed<N>/   per-run configs, full log, meta.json

Usage (from the repo root):
    python tests/measure_baseline.py                  # run + aggregate + plot
    python tests/measure_baseline.py --aggregate-only # rebuild tables/plots
    python tests/measure_baseline.py --probes 20 --seeds 101   # quick smoke run

Measurement definitions
-----------------------
* Path: symmetric upper path 0 -> 1 -> 2 -> 4 -> 5 and back through the same
  routers. Host 0 is the only sender.
* Link pair: router 2's two outgoing links (2 -> 4 carries requests, 2 -> 1
  carries replies). The added delay and the loss probability are applied to
  both links; nothing else changes between conditions.
* RTT: monotonic-clock time at the source host from sending a probe until its
  matching reply is processed. It covers both directions and is not one-way
  delay. Only successful probes have an RTT.
* Success fraction: probes with a matching reply / probes sent.
* Timeout: a probe with no matching reply within the response timeout.
* Configured link loss is not the end-to-end failure probability: a probe
  crosses router 2's lossy links twice (request and reply), so the expected
  success fraction is (1 - p)^2.
"""

from __future__ import annotations

import argparse
import csv
import statistics as st
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "evidence"

SENDER, RECEIVER = 0, 5
LOSSY_ROUTER = 2  # neighbors [1, 4]: delays are given in that order

FIELDS = [
    "suite", "condition", "delay_ms", "loss", "seed", "probes", "interval_s",
    "timeout_s", "payload_bytes", "successes", "success_frac", "timeouts",
    "unresolved", "mean_rtt_ms", "stdev_rtt_ms", "min_rtt_ms", "max_rtt_ms",
    "loss_drops", "status", "error",
]


# ----------------------------------------------------------------------
# Conditions
# ----------------------------------------------------------------------
@dataclass
class Condition:
    label: str
    delay_ms: float
    loss: float
    extra_edits: dict = field(default_factory=dict)  # for extension suites


def _label(delay_ms: float, loss: float) -> str:
    return f"delay{delay_ms:g}ms_loss{loss * 100:g}pct"


BASELINE = [
    Condition(_label(d, p), d, p) for d in (10, 100) for p in (0.0, 0.05)
]

# Later: add a suite for the extension here, e.g. SUITES["extension"] = [...]
# with the same workload and constants.
SUITES = {"baseline": BASELINE}


# ----------------------------------------------------------------------
# Running
# ----------------------------------------------------------------------
def run_once(suite: str, cond: Condition, seed: int, probes: int,
             interval: float, timeout: float, payload_bytes: int) -> tuple[dict, list]:
    """Run one condition with one seed. Always returns a row, even on error."""
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from harness import (UPPER, link_settings, merge_edits, scenario,
                         send_probes, wait_for_probes)

    row = {
        "suite": suite, "condition": cond.label, "delay_ms": cond.delay_ms,
        "loss": cond.loss, "seed": seed, "probes": probes,
        "interval_s": interval, "timeout_s": timeout,
        "payload_bytes": payload_bytes, "successes": 0, "success_frac": "",
        "timeouts": 0, "unresolved": probes, "mean_rtt_ms": "",
        "stdev_rtt_ms": "", "min_rtt_ms": "", "max_rtt_ms": "",
        "loss_drops": "", "status": "ok", "error": "",
    }
    rtts_ms: list[float] = []
    edits = merge_edits(
        UPPER,
        link_settings(LOSSY_ROUTER, loss=cond.loss,
                      delays_ms=[cond.delay_ms, cond.delay_ms]),
        cond.extra_edits,
    )
    try:
        with scenario(f"measure/{suite}/{cond.label}_seed{seed}",
                      edits=edits, seed=seed) as net:
            sender = net.h[SENDER]
            send_probes(sender, RECEIVER, probes, "m", interval=interval,
                        timeout=timeout, payload="x" * payload_bytes)
            wait_for_probes(sender, limit=timeout + 2.0)
            rtts_ms = [r * 1000.0 for r in sender.rtt_samples()]
            timeouts = sum(r.status == "timeout" for r in sender.probe_results())
            row["loss_drops"] = sum(r.loss_drops for r in net.r.values())
        row["successes"] = len(rtts_ms)
        row["success_frac"] = len(rtts_ms) / probes
        row["timeouts"] = timeouts
        row["unresolved"] = probes - len(rtts_ms) - timeouts
        if rtts_ms:
            row["mean_rtt_ms"] = st.mean(rtts_ms)
            row["stdev_rtt_ms"] = st.stdev(rtts_ms) if len(rtts_ms) > 1 else 0.0
            row["min_rtt_ms"] = min(rtts_ms)
            row["max_rtt_ms"] = max(rtts_ms)
    except Exception as exc:  # keep the suite going; the error is recorded
        row["status"] = "error"
        row["error"] = repr(exc)
        rtts_ms = []
    return row, rtts_ms


def run_suite(suite: str, args) -> Path:
    suite_dir = EVIDENCE_DIR / "measure" / suite
    suite_dir.mkdir(parents=True, exist_ok=True)
    results = suite_dir / "results.csv"
    if results.exists():  # never overwrite earlier data
        results.rename(suite_dir / f"results_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    rtt_path = suite_dir / "rtt_samples.csv"

    conditions = SUITES[suite]
    total = len(conditions) * len(args.seeds)
    done = 0
    with open(results, "w", newline="") as rf, open(rtt_path, "w", newline="") as tf:
        writer = csv.DictWriter(rf, fieldnames=FIELDS)
        writer.writeheader()
        rtt_writer = csv.writer(tf)
        rtt_writer.writerow(["condition", "delay_ms", "loss", "seed", "rtt_ms"])
        # Seeds outside, conditions inside: slow drift hits all conditions alike.
        for seed in args.seeds:
            for cond in conditions:
                done += 1
                print(f"[{done}/{total}] {cond.label} seed={seed} ...", flush=True)
                row, rtts = run_once(suite, cond, seed, args.probes,
                                     args.interval, args.timeout,
                                     args.payload_bytes)
                writer.writerow(row)
                rf.flush()
                for rtt in rtts:
                    rtt_writer.writerow([cond.label, cond.delay_ms, cond.loss, seed, rtt])
                tf.flush()
                print(f"    status={row['status']} successes={row['successes']}/"
                      f"{row['probes']} timeouts={row['timeouts']} "
                      f"mean_rtt_ms={row['mean_rtt_ms']}", flush=True)
                time.sleep(0.5)  # let the fixed UDP ports free up
    return results


# ----------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------
def _num(value):
    return None if value in ("", None) else float(value)


def _spread(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "mean": st.mean(values),
        "stdev": st.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
        "n": len(values),
    }


def aggregate(results_csv: Path) -> list[dict]:
    """Mean and spread across runs, per condition. Failed probes are kept."""
    with open(results_csv, newline="") as fh:
        rows = list(csv.DictReader(fh))

    order: list[tuple] = []
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["condition"], float(row["delay_ms"]), float(row["loss"]))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    # Prediction anchor: lowest delay with zero loss.
    anchors = [(k, st.mean([_num(r["mean_rtt_ms"]) for r in groups[k]
                            if r["status"] == "ok" and _num(r["mean_rtt_ms"]) is not None]))
               for k in order if k[2] == 0.0
               and any(r["status"] == "ok" and _num(r["mean_rtt_ms"]) is not None
                       for r in groups[k])]
    anchor = min(anchors, key=lambda a: a[0][1], default=None)

    summaries = []
    for key in order:
        label, delay, loss = key
        runs = groups[key]
        ok = [r for r in runs if r["status"] == "ok"]
        success = [float(r["success_frac"]) for r in ok]
        rtt = [_num(r["mean_rtt_ms"]) for r in ok if _num(r["mean_rtt_ms"]) is not None]
        timeouts = [float(r["timeouts"]) for r in ok]
        predicted_rtt = None
        if anchor is not None:
            # Router 2 adds (delay - anchor delay) in each direction.
            predicted_rtt = anchor[1] + 2 * (delay - anchor[0][1])
        summaries.append({
            "condition": label, "delay_ms": delay, "loss": loss,
            "n_runs": len(ok), "n_error_runs": len(runs) - len(ok),
            "probes_per_run": int(runs[0]["probes"]),
            "success_frac": _spread(success),
            "mean_rtt_ms": _spread(rtt),
            "n_runs_with_rtt": len(rtt),
            "timeouts": _spread(timeouts),
            "predicted_success": (1 - loss) ** 2,
            "predicted_rtt_ms": predicted_rtt,
            "per_run": {"success_frac": success, "mean_rtt_ms": rtt,
                        "timeouts": timeouts},
        })
    return summaries


def _fmt(s: dict | None, digits: int = 3) -> str:
    if s is None:
        return "n/a"
    return (f"{s['mean']:.{digits}f} ± {s['stdev']:.{digits}f} "
            f"[{s['min']:.{digits}f}-{s['max']:.{digits}f}]")


def write_summary(summaries: list[dict], out_dir: Path) -> None:
    with open(out_dir / "summary.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["condition", "delay_ms", "loss", "n_runs", "n_error_runs",
                    "probes_per_run",
                    "success_mean", "success_sd", "success_min", "success_max",
                    "rtt_mean_ms", "rtt_sd_ms", "rtt_min_ms", "rtt_max_ms",
                    "n_runs_with_rtt",
                    "timeouts_mean", "timeouts_sd", "timeouts_min", "timeouts_max",
                    "predicted_success", "predicted_rtt_ms"])
        for s in summaries:
            def cols(m):
                return ([m["mean"], m["stdev"], m["min"], m["max"]]
                        if m else ["", "", "", ""])
            w.writerow([s["condition"], s["delay_ms"], s["loss"], s["n_runs"],
                        s["n_error_runs"], s["probes_per_run"],
                        *cols(s["success_frac"]), *cols(s["mean_rtt_ms"]),
                        s["n_runs_with_rtt"], *cols(s["timeouts"]),
                        s["predicted_success"],
                        "" if s["predicted_rtt_ms"] is None else s["predicted_rtt_ms"]])

    lines = [
        "| Delay (ms) | Loss | Runs | Success fraction | RTT of successful probes (ms) "
        "| Timeouts per run | Predicted success | Predicted RTT (ms) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        pred_rtt = "n/a" if s["predicted_rtt_ms"] is None else f"{s['predicted_rtt_ms']:.1f}"
        runs = f"{s['n_runs']}" + (f" (+{s['n_error_runs']} errored)" if s["n_error_runs"] else "")
        lines.append(
            f"| {s['delay_ms']:g} | {s['loss'] * 100:g}% | {runs} "
            f"| {_fmt(s['success_frac'])} | {_fmt(s['mean_rtt_ms'], 1)} "
            f"| {_fmt(s['timeouts'], 1)} | {s['predicted_success']:.3f} | {pred_rtt} |"
        )
    text = (
        "Each cell is `mean ± standard deviation [min-max]` across runs "
        "(one run per seed). RTT is the per-run mean over successful probes, "
        "measured at the source with a monotonic clock; it includes both "
        "directions.\n\n" + "\n".join(lines) + "\n"
    )
    (out_dir / "summary.md").write_text(text)
    print("\n" + text)


# ----------------------------------------------------------------------
# Plots
# ----------------------------------------------------------------------
def plot_all(summaries: list[dict], out_dir: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed; run `pip install matplotlib` "
              "and rerun with --aggregate-only to make the plots.")
        return

    delays = sorted({s["delay_ms"] for s in summaries})
    losses = sorted({s["loss"] for s in summaries})
    by_key = {(s["delay_ms"], s["loss"]): s for s in summaries}
    width = 0.8 / len(losses)

    def bar_chart(metric, per_run_key, ylabel, title, fname, predicted=None,
                  ymax=None, headroom=None):
        fig, ax = plt.subplots(figsize=(6.5, 4.2))
        for j, loss in enumerate(losses):
            xs, means, errs = [], [], []
            for i, delay in enumerate(delays):
                s = by_key.get((delay, loss))
                if s is None or s[metric] is None:
                    continue
                x = i + (j - (len(losses) - 1) / 2) * width
                xs.append(x)
                means.append(s[metric]["mean"])
                errs.append(s[metric]["stdev"])
                ax.scatter([x] * len(s["per_run"][per_run_key]),
                           s["per_run"][per_run_key], color="black", s=14, zorder=3)
                if predicted and s[predicted] is not None:
                    ax.scatter([x], [s[predicted]], marker="D", color="red",
                               s=34, zorder=4)
            ax.bar(xs, means, width * 0.92, yerr=errs, capsize=4,
                   label=f"loss {loss * 100:g}%")
        ax.set_xticks(range(len(delays)))
        ax.set_xticklabels([f"{d:g} ms" for d in delays])
        ax.set_xlabel("Added delay on router 2's links (each direction)")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        if ymax:
            ax.set_ylim(0, ymax)
        elif headroom:
            ax.set_ylim(0, ax.get_ylim()[1] * headroom)
        ax.legend(loc="upper center", ncol=len(losses), frameon=False)
        probes = summaries[0]["probes_per_run"]
        fig.text(0.01, 0.005,
                 f"bars: mean ± sd across runs; dots: individual runs"
                 f"{'; diamonds: prediction' if predicted else ''}; "
                 f"{probes} probes per run",
                 fontsize=7)
        fig.tight_layout(rect=(0, 0.03, 1, 1))
        fig.savefig(out_dir / fname, dpi=150)
        plt.close(fig)

    bar_chart("success_frac", "success_frac", "Fraction of probes answered",
              "Baseline: probes receiving a matching response",
              "baseline_success.png", predicted="predicted_success", ymax=1.3)
    bar_chart("mean_rtt_ms", "mean_rtt_ms", "RTT of successful probes (ms)",
              "Baseline: round-trip time vs. added delay and loss",
              "baseline_rtt.png", predicted="predicted_rtt_ms", headroom=1.25)
    bar_chart("timeouts", "timeouts", "Timeouts per run",
              "Baseline: probe timeouts", "baseline_timeouts.png")
    print(f"Plots written to {out_dir}")


# ----------------------------------------------------------------------
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--suite", default="baseline", choices=sorted(SUITES))
    ap.add_argument("--probes", type=int, default=100,
                    help="probes per run (spec minimum is 20)")
    ap.add_argument("--interval", type=float, default=0.05, help="seconds between probes")
    ap.add_argument("--timeout", type=float, default=2.0, help="per-probe response timeout")
    ap.add_argument("--payload-bytes", type=int, default=64)
    ap.add_argument("--seeds", type=int, nargs="+", default=[101, 102, 103])
    ap.add_argument("--aggregate-only", action="store_true",
                    help="skip running; rebuild summary and plots from results.csv")
    args = ap.parse_args(argv)

    suite_dir = EVIDENCE_DIR / "measure" / args.suite
    if args.aggregate_only:
        results = suite_dir / "results.csv"
    else:
        results = run_suite(args.suite, args)

    summaries = aggregate(results)
    write_summary(summaries, results.parent)
    plot_all(summaries, results.parent)
    errored = sum(s["n_error_runs"] for s in summaries)
    if errored:
        print(f"WARNING: {errored} run(s) errored; see the 'error' column in {results}")


if __name__ == "__main__":
    main()