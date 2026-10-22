"""Evidence-gathering harness for the mini internet simulator.

Put this file in ``tests/`` next to the scenario tests. Scenarios run one at
a time because every device binds a fixed UDP port.

Typical use::

    from harness import scenario, UPPER, send_probes, wait_for_probes

    with scenario("s1_multihop", edits=UPPER) as net:
        send_probes(net.h[0], 5, 20, "A-probe")
        wait_for_probes(net.h[0])
"""

from __future__ import annotations

import configparser
import contextlib
import json
import logging
import re
import shutil
import socket
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from src.simulator import host_builder, router_builder

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_CONFIG_DIR = REPO_ROOT / "src" / "simulator" / "router configurations"
HOST_CONFIG_DIR = REPO_ROOT / "src" / "simulator" / "host configurations"
EVIDENCE_DIR = REPO_ROOT / "evidence"

LOCAL_HOST = "127.0.0.1"
BASE_PORT = 60000  # device with virtual address N listens on BASE_PORT + N

# Virtual address -> config file name (A-D are routers 1-4; hosts are 0 and 5).
ROUTER_FILES = {1: "routerA.ini", 2: "routerB.ini", 3: "routerC.ini", 4: "routerD.ini"}
HOST_FILES = {0: "hostA.ini", 5: "hostB.ini"}


# ----------------------------------------------------------------------
# Config variants
# ----------------------------------------------------------------------
def merge_edits(*edit_dicts: dict) -> dict:
    """Combine several ``{filename: {(section, key): value}}`` dicts.

    Later dicts override earlier ones for the same file and key.
    """
    merged: dict = {}
    for edits in edit_dicts:
        for fname, keys in edits.items():
            merged.setdefault(fname, {}).update(keys)
    return merged


def forwarding(router: int, table: dict[int, int]) -> dict:
    """Replace a router's ``destinations`` / ``local_forwarding`` lists.

    ``table`` maps destination -> next hop, e.g. ``{5: 2, 0: 0}``.
    """
    return {
        ROUTER_FILES[router]: {
            ("routing", "destinations"): list(table.keys()),
            ("routing", "local_forwarding"): list(table.values()),
        }
    }


def link_settings(router: int, loss: float | None = None,
                  delays_ms: list[float] | None = None) -> dict:
    """Set a router's ``loss_chance`` and/or per-neighbor ``delays`` (ms).

    ``delays_ms`` must follow the order of that router's ``neighbors`` list.
    """
    keys: dict = {}
    if loss is not None:
        keys[("loss", "loss_chance")] = loss
    if delays_ms is not None:
        keys[("routing", "delays")] = list(delays_ms)
    return {ROUTER_FILES[router]: keys}


# Symmetric paths: request and reply use the same routers.
# Stock router 4 sends 0 -> 3 (lower), so the upper preset overrides it.
UPPER = merge_edits(forwarding(1, {5: 2, 0: 0}), forwarding(4, {5: 5, 0: 2}))
LOWER = merge_edits(forwarding(1, {5: 3, 0: 0}), forwarding(4, {5: 5, 0: 3}))

# Applied to every scenario first, so a leftover loss_chance in a stock .ini
# can never break a deterministic test. Scenario edits override it.
NO_LOSS = merge_edits(*[link_settings(r, loss=0) for r in ROUTER_FILES])


def write_variant(cfg_out: Path, edits: dict | None = None) -> Path:
    """Copy every stock .ini into ``cfg_out`` with ``edits`` applied."""
    edits = edits or {}
    cfg_out.mkdir(parents=True, exist_ok=True)
    sources = [*ROUTER_CONFIG_DIR.glob("*.ini"), *HOST_CONFIG_DIR.glob("*.ini")]
    for src in sources:
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(src)
        for (section, key), value in edits.get(src.name, {}).items():
            if not cp.has_section(section):
                cp.add_section(section)
            if isinstance(value, list):
                cp[section][key] = "\n".join(str(v) for v in value)
            else:
                cp[section][key] = str(value)
        with open(cfg_out / src.name, "w") as fh:
            cp.write(fh)
    return cfg_out


# ----------------------------------------------------------------------
# Network lifecycle
# ----------------------------------------------------------------------
@dataclass
class Network:
    """Running devices, indexed by virtual address (``net.r[2]``, ``net.h[0]``)."""

    r: dict
    h: dict
    out: Path | None = None
    notes: dict = field(default_factory=dict)


@contextlib.contextmanager
def network(cfg_dir: Path, seed: int | None = None):
    """Build, start, yield, and always tear down all routers and hosts.

    If ``seed`` is given, router ``a`` is seeded with ``seed + a`` so the
    routers draw different random numbers but the run is reproducible.
    Requires ``Router.set_seed``.
    """
    routers: dict = {}
    hosts: dict = {}
    try:
        for addr, fname in ROUTER_FILES.items():
            router = router_builder.build_router_from_ini(cfg_dir / fname)
            if seed is not None:
                router.set_seed(seed + addr)
            routers[addr] = router
        for addr, fname in HOST_FILES.items():
            hosts[addr] = host_builder.build_host_from_ini(cfg_dir / fname)

        for router in routers.values():
            router.start()
        for host in hosts.values():
            host.start()
        yield Network(r=routers, h=hosts)
    finally:
        for host in hosts.values():
            host.shutdown()
        for router in routers.values():
            router.shutdown()


# ----------------------------------------------------------------------
# Logging
#
# The scenario log must look exactly like netlog's own output, e.g.
#   2026-10-21 15:19:41,288 | run=58fcf924 | INFO | netlog.host-5 | event=send ...
# netlog's formatter sits on the per-device loggers, which only exist once a
# device is built, so the formatter is resolved lazily on the first record.
# ----------------------------------------------------------------------
_LINE_RE = re.compile(
    r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \| run=\S+ \| [A-Z]+ \| netlog\.\S+ \| event="
)
_FALLBACK_RUN_ID = uuid.uuid4().hex[:8]


def _is_capture_handler(handler: logging.Handler) -> bool:
    """True for pytest's own capture handlers, whose format has no run id."""
    return ("pytest" in type(handler).__module__
            or "LogCapture" in type(handler).__name__)


def _netlog_loggers():
    yield logging.getLogger("netlog")
    for name, lg in list(logging.root.manager.loggerDict.items()):
        if name.startswith("netlog.") and isinstance(lg, logging.Logger):
            yield lg
    yield logging.getLogger()


def _renders_run_id(handler: logging.Handler) -> bool:
    """True if ``handler`` formats a netlog record with a ``run=`` field."""
    record = logging.LogRecord(
        "netlog.probe", logging.INFO, __file__, 0, "event=probe", None, None
    )
    try:
        if not handler.filter(record):
            return False
        return "run=" in handler.format(record)
    except Exception:
        return False


def _template_handler() -> logging.Handler | None:
    """Find netlog's real handler (the one that prints the run id)."""
    for lg in _netlog_loggers():
        for handler in lg.handlers:
            if _is_capture_handler(handler):
                continue
            if isinstance(handler.formatter, _LazyNetlogFormatter):
                continue
            if _renders_run_id(handler):
                return handler
    return None


def _discover_run_id() -> str | None:
    try:
        import src.telemetry.netlog as nl
    except Exception:
        return None
    for name, value in vars(nl).items():
        lowered = name.lower()
        if "run" in lowered and "id" in lowered:
            if callable(value):
                try:
                    value = value()
                except Exception:
                    continue
            if isinstance(value, str) and value:
                return value
    return None


class _LazyNetlogFormatter(logging.Formatter):
    """Delegate to netlog's formatter once its handler exists."""

    def __init__(self):
        super().__init__()
        self._template: logging.Handler | None = None
        run_id = _discover_run_id() or _FALLBACK_RUN_ID
        self._fallback = logging.Formatter(
            f"%(asctime)s | run={run_id} | %(levelname)s | %(name)s | %(message)s"
        )

    def format(self, record: logging.LogRecord) -> str:
        if self._template is None:
            self._template = _template_handler()
        if self._template is not None:
            # Run its filters first: they may attach the run id to the record.
            self._template.filter(record)
            return self._template.formatter.format(record)
        return self._fallback.format(record)


def attach_log(path: Path) -> logging.Handler:
    """Write all ``netlog.*`` records to ``path`` in netlog's own format."""
    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_LazyNetlogFormatter())
    logger = logging.getLogger("netlog")
    if logger.level == logging.NOTSET or logger.level > logging.INFO:
        logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler


def _check_log_format(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        print(f"WARNING: {path} is empty; check netlog logger propagation")
        return
    bad = [ln for ln in lines if not _LINE_RE.match(ln)]
    if bad:
        print(f"WARNING: {len(bad)} of {len(lines)} lines in {path} lack the "
              f"timestamp/run-id format. First: {bad[0]}")


@contextlib.contextmanager
def scenario(name: str, edits: dict | None = None, seed: int | None = None):
    """Run one evidence scenario in ``evidence/<name>/``.

    Creates ``cfg/`` (the exact configs used), ``run.log`` (all device
    events, in netlog's format) and ``meta.json`` (seeds and edits), then
    yields a ``Network`` whose ``.out`` is the scenario folder. Router loss
    is forced to 0 unless ``edits`` sets it.
    """
    edits = merge_edits(NO_LOSS, edits or {})
    out = EVIDENCE_DIR / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    cfg = write_variant(out / "cfg", edits)
    log_path = out / "run.log"
    handler = attach_log(log_path)
    net = None
    try:
        with network(cfg, seed) as net:
            net.out = out
            yield net
    finally:
        meta = {
            "scenario": name,
            "requested_seed": seed,
            "router_seeds": {a: r.seed for a, r in (net.r.items() if net else [])},
            "edits": {f: {f"{s}.{k}": v for (s, k), v in keys.items()}
                      for f, keys in edits.items()},
        }
        (out / "meta.json").write_text(json.dumps(meta, indent=2))
        handler.flush()
        logging.getLogger("netlog").removeHandler(handler)
        handler.close()
        _check_log_format(log_path)


# ----------------------------------------------------------------------
# Traffic helpers
# ----------------------------------------------------------------------
def send_probes(host, destination: int, count: int, label: str,
                interval: float = 0.02, timeout: float | None = None,
                ttl: int | None = None, payload: str | None = None) -> list[int]:
    """Send ``count`` tracked probes. Payload is ``<label>-<i>`` unless given."""
    ids = []
    for i in range(count):
        kwargs = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if ttl is not None:
            kwargs["ttl"] = ttl
        ids.append(host.send_probe(
            destination, payload if payload is not None else f"{label}-{i}",
            **kwargs))
        time.sleep(interval)
    return ids


def wait_until(condition, limit: float = 3.0, step: float = 0.02) -> bool:
    """Poll ``condition()`` until it is true or ``limit`` seconds pass."""
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(step)
    return bool(condition())


def wait_for_probes(*hosts, limit: float = 5.0) -> bool:
    """Block until no probe is outstanding on any host, or ``limit`` passes."""
    return wait_until(lambda: not any(h.outstanding_probe_ids() for h in hosts),
                      limit=limit)


def send_raw(data: bytes, port: int, from_socket: socket.socket | None = None) -> None:
    """Send arbitrary bytes to ``127.0.0.1:port``.

    Pass a host's ``socket`` as ``from_socket`` to send from that host's bound
    port, which looks tidier in Wireshark.
    """
    if from_socket is not None:
        from_socket.sendto(data, (LOCAL_HOST, port))
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(data, (LOCAL_HOST, port))
    finally:
        sock.close()


# ----------------------------------------------------------------------
# Evidence extraction
# ----------------------------------------------------------------------
def grep_log(out: Path, *needles: str, name: str | None = None) -> list[str]:
    """Return log lines containing ALL ``needles``; optionally save them.

    Example: ``grep_log(out, "id=7", "type=echo", "src=0", "dest=5",
    name="trace_echo_7")`` writes ``key_lines_trace_echo_7.txt``.
    """
    lines = (out / "run.log").read_text(encoding="utf-8").splitlines()
    hits = [ln for ln in lines if all(n in ln for n in needles)]
    if name:
        (out / f"key_lines_{name}.txt").write_text("\n".join(hits) + "\n")
    return hits


def device_events(out: Path, device: str, *needles: str,
                  name: str | None = None) -> list[str]:
    """Like ``grep_log`` but only for one device, e.g. ``"router-1"``."""
    return grep_log(out, f"| netlog.{device} |", *needles, name=name)


def count_events(out: Path, *needles: str) -> int:
    """Count log lines containing all ``needles``."""
    return len(grep_log(out, *needles))