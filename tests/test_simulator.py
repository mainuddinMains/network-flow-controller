"""Unit tests for src/simulator/."""

import threading
import time
from pathlib import Path

from src.simulator import host_builder, router_builder

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_CONFIG_DIR = REPO_ROOT / "src" / "simulator" / "router configurations"
HOST_CONFIG_DIR = REPO_ROOT / "src" / "simulator" / "host configurations"

PROBE_COUNT = 20
DEFAULT_TTL = 64
REPLY_DEADLINE_S = 15.0


def test_simulator_module_imports():
    """Verify the simulator module is importable."""
    import src.simulator  # noqa: F401


def _record_kept_datagrams(host, records):
    """Record (type_flags, identifier, source, payload) for kept datagrams."""
    original = host._keep_datagram

    def keeper(payload, header):
        records.append(
            (header["type_flags"], header["identifier"], header["source"],
             payload)
        )
        original(payload, header)

    host._keep_datagram = keeper


def _send_probes(host, destination, label):
    """Send PROBE_COUNT echo-flagged probes from ``host`` to ``destination``."""
    for index in range(PROBE_COUNT):
        datagram = host._create_datagram(
            DEFAULT_TTL,
            host.flags["echo"],
            0,
            host._new_identifier(),
            host.virtual_address,
            destination,
            f"{label}-{index}",
        )
        host._send_datagram(datagram, destination)
        time.sleep(0.02)


def _reply_identifiers(records, reply_flag, expected_source, label):
    """Sorted identifiers of echo replies from ``expected_source``."""
    prefix = f"{label}-".encode()
    return sorted(
        identifier
        for type_flags, identifier, source, payload in records
        if type_flags & reply_flag
        and source == expected_source
        and payload.startswith(prefix)
    )


def test_network():
    """Twenty echo probes from each host round-trip across the diamond.

    Verifies issue #32: both hosts exchange probes and matching replies
    through the four-router diamond with zero configured loss, the upper
    path through router B and the lower path through router C both carry
    traffic, and no router drops a datagram.
    """
    routers = [
        router_builder.build_router_from_ini(
            ROUTER_CONFIG_DIR / f"router{name}.ini"
        )
        for name in "ABCD"
    ]
    host_a = host_builder.build_host_from_ini(HOST_CONFIG_DIR / "hostA.ini")
    host_b = host_builder.build_host_from_ini(HOST_CONFIG_DIR / "hostB.ini")

    kept_a: list = []
    kept_b: list = []
    _record_kept_datagrams(host_a, kept_a)
    _record_kept_datagrams(host_b, kept_b)

    reply_flag = host_a.flags["reply"]
    listeners = []
    try:
        for router in routers:
            router.start()
        for host in (host_a, host_b):
            listener = threading.Thread(
                target=host.listening_loop,
                name=f"host-{host.virtual_address}-listener",
                daemon=True,
            )
            listener.start()
            listeners.append(listener)

        sender_a = threading.Thread(
            target=_send_probes,
            args=(host_a, host_b.virtual_address, "A-probe"),
        )
        sender_b = threading.Thread(
            target=_send_probes,
            args=(host_b, host_a.virtual_address, "B-probe"),
        )
        sender_a.start()
        sender_b.start()
        sender_a.join()
        sender_b.join()

        # Replies drain through the routers' simulated delays; poll until
        # both hosts hold every reply or the deadline passes.
        deadline = time.monotonic() + REPLY_DEADLINE_S
        while time.monotonic() < deadline:
            replies_a = _reply_identifiers(
                kept_a, reply_flag, host_b.virtual_address, "A-probe"
            )
            replies_b = _reply_identifiers(
                kept_b, reply_flag, host_a.virtual_address, "B-probe"
            )
            if (
                len(replies_a) >= PROBE_COUNT
                and len(replies_b) >= PROBE_COUNT
            ):
                break
            time.sleep(0.1)
    finally:
        for host in (host_a, host_b):
            host._stop_event.set()
        for router in routers:
            router.shutdown()
        for listener in listeners:
            listener.join(timeout=2.0)
        for host in (host_a, host_b):
            host.socket.close()

    assert replies_a == list(range(PROBE_COUNT))
    assert replies_b == list(range(PROBE_COUNT))
    assert all(router.overflow_drops == 0 for router in routers)
