"""Probe tracking, RTT, and timeout tests for Issue #29."""

import socket
import struct
import time

import pytest

from src.simulator.host import (
    DEFAULT_PROBE_TIMEOUT_S,
    HEADER_FORMAT,
    Host,
)


def _free_udp_port() -> int:
    """Bind briefly to learn an unused UDP port on localhost."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def _pack_reply(
    *,
    identifier: int,
    source: int,
    destination: int,
    payload: bytes = b"pong",
    ttl: int = 64,
) -> bytes:
    """Build an echo-reply datagram for the host under test."""
    type_flags = Host.flags["reply"]
    header = struct.pack(
        HEADER_FORMAT,
        ttl,
        type_flags,
        0,
        identifier,
        source,
        destination,
    )
    return header + payload


@pytest.fixture
def host_and_peer():
    """Host that sends probes to a local peer acting as the entry router."""
    host_port = _free_udp_port()
    peer_port = _free_udp_port()
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", peer_port))
    peer.settimeout(2.0)

    host = Host(
        IP_ADDRESS="127.0.0.1",
        PORT=host_port,
        virtual_address=10,
        entry_router_address=1,
        probe_timeout=0.5,
    )
    host.entry_router_meta = ("127.0.0.1", peer_port)
    host.start()

    yield host, peer, host_port

    host.shutdown()
    peer.close()


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    """Poll until ``predicate`` is true or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _recv_probe(peer: socket.socket) -> tuple[dict, bytes, tuple]:
    """Receive one datagram at the peer and return header, payload, addr."""
    data, addr = peer.recvfrom(65535)
    # Use a temporary host helper for unpack only.
    helper = Host.__new__(Host)
    header, payload = Host._interpret_datagram(helper, data)
    return header, payload, addr


def test_successful_probe_records_positive_rtt(host_and_peer):
    """A timely reply yields success and a positive RTT sample."""
    host, peer, _ = host_and_peer
    identifier = host.send_probe(20, b"ping")

    header, payload, addr = _recv_probe(peer)
    assert header["identifier"] == identifier
    assert header["type_flags"] & Host.flags["echo"]
    peer.sendto(
        _pack_reply(
            identifier=identifier,
            source=20,
            destination=host.virtual_address,
            payload=payload,
        ),
        addr,
    )

    assert _wait_until(lambda: len(host.probe_results()) == 1)
    result = host.probe_results()[0]
    assert result.status == "success"
    assert result.identifier == identifier
    assert result.rtt is not None and result.rtt > 0
    assert host.rtt_samples() == (result.rtt,)
    assert identifier not in host.outstanding_probe_ids()


def test_missing_reply_times_out(host_and_peer):
    """Unanswered probes expire as timeout without an RTT sample."""
    host, peer, _ = host_and_peer
    host.probe_timeout = 0.15
    identifier = host.send_probe(21, b"gone")

    # Drain the outbound probe so the peer socket stays quiet.
    _recv_probe(peer)

    assert _wait_until(
        lambda: any(
            r.identifier == identifier and r.status == "timeout"
            for r in host.probe_results()
        ),
        timeout=1.0,
    )
    result = next(r for r in host.probe_results() if r.identifier == identifier)
    assert result.rtt is None
    assert host.rtt_samples() == ()
    assert identifier not in host.outstanding_probe_ids()


def test_delayed_reply_within_deadline_succeeds(host_and_peer):
    """A reply that arrives before the deadline still measures RTT."""
    host, peer, _ = host_and_peer
    identifier = host.send_probe(22, b"slow", timeout=0.4)

    header, payload, addr = _recv_probe(peer)
    time.sleep(0.1)
    peer.sendto(
        _pack_reply(
            identifier=identifier,
            source=22,
            destination=host.virtual_address,
            payload=payload,
        ),
        addr,
    )

    assert _wait_until(lambda: len(host.probe_results()) == 1)
    result = host.probe_results()[0]
    assert result.status == "success"
    assert result.rtt is not None and result.rtt >= 0.1


def test_reply_after_deadline_is_ignored_for_measurement(host_and_peer):
    """Late replies do not create a success RTT after timeout."""
    host, peer, _ = host_and_peer
    identifier = host.send_probe(23, b"late", timeout=0.1)

    header, payload, addr = _recv_probe(peer)
    assert _wait_until(
        lambda: any(r.identifier == identifier for r in host.probe_results()),
        timeout=1.0,
    )
    assert host.probe_results()[0].status == "timeout"

    peer.sendto(
        _pack_reply(
            identifier=identifier,
            source=23,
            destination=host.virtual_address,
            payload=payload,
        ),
        addr,
    )
    time.sleep(0.15)

    results = [r for r in host.probe_results() if r.identifier == identifier]
    assert len(results) == 1
    assert results[0].status == "timeout"
    assert host.rtt_samples() == ()


def test_duplicate_reply_does_not_add_second_rtt(host_and_peer):
    """A second reply for the same id does not corrupt measurements."""
    host, peer, _ = host_and_peer
    identifier = host.send_probe(24, b"dup")

    header, payload, addr = _recv_probe(peer)
    reply = _pack_reply(
        identifier=identifier,
        source=24,
        destination=host.virtual_address,
        payload=payload,
    )
    peer.sendto(reply, addr)
    assert _wait_until(lambda: len(host.probe_results()) == 1)
    peer.sendto(reply, addr)
    time.sleep(0.15)

    assert len(host.probe_results()) == 1
    assert len(host.rtt_samples()) == 1


def test_unmatched_identifier_is_ignored(host_and_peer):
    """A reply whose id was never probed does not create a result."""
    host, peer, host_port = host_and_peer
    peer.sendto(
        _pack_reply(
            identifier=999,
            source=25,
            destination=host.virtual_address,
        ),
        ("127.0.0.1", host_port),
    )
    time.sleep(0.2)
    assert host.probe_results() == ()
    assert host.rtt_samples() == ()


def test_reply_from_unexpected_source_is_ignored(host_and_peer):
    """Replies must come from the probed destination virtual address."""
    host, peer, _ = host_and_peer
    identifier = host.send_probe(26, b"src")

    header, payload, addr = _recv_probe(peer)
    peer.sendto(
        _pack_reply(
            identifier=identifier,
            source=99,
            destination=host.virtual_address,
            payload=payload,
        ),
        addr,
    )
    time.sleep(0.2)
    assert identifier in host.outstanding_probe_ids()
    assert host.rtt_samples() == ()

    # Legitimate reply still succeeds afterward.
    peer.sendto(
        _pack_reply(
            identifier=identifier,
            source=26,
            destination=host.virtual_address,
            payload=payload,
        ),
        addr,
    )
    assert _wait_until(lambda: len(host.probe_results()) == 1)
    assert host.probe_results()[0].status == "success"


def test_multiple_concurrent_outstanding_probes(host_and_peer):
    """Independent probes match their own replies and RTTs."""
    host, peer, _ = host_and_peer
    destinations = (30, 31, 32)
    ids = [host.send_probe(dest, f"p{dest}".encode()) for dest in destinations]

    pending = {}
    for _ in destinations:
        header, payload, addr = _recv_probe(peer)
        pending[header["identifier"]] = (header, payload, addr)

    assert set(pending) == set(ids)

    # Reply out of order.
    for identifier in reversed(ids):
        header, payload, addr = pending[identifier]
        peer.sendto(
            _pack_reply(
                identifier=identifier,
                source=header["destination"],
                destination=host.virtual_address,
                payload=payload,
            ),
            addr,
        )

    assert _wait_until(lambda: len(host.probe_results()) == 3)
    assert {r.identifier for r in host.probe_results()} == set(ids)
    assert all(r.status == "success" for r in host.probe_results())
    assert len(host.rtt_samples()) == 3
    assert host.outstanding_probe_ids() == set()


def test_identifier_reuse_blocked_while_outstanding(host_and_peer):
    """An id still waiting for a reply is not reused for a new probe."""
    host, peer, _ = host_and_peer
    first = host.send_probe(40, b"hold", timeout=2.0)
    _recv_probe(peer)

    # Force the allocator to wrap back to the outstanding id.
    with host._probe_lock:
        host._next_identifier = first

    second = host.send_probe(41, b"next", timeout=2.0)
    _recv_probe(peer)

    assert second != first
    assert first in host.outstanding_probe_ids()
    assert second in host.outstanding_probe_ids()


def test_sending_failure_cleans_pending_entry(host_and_peer):
    """A failed sendto must not leave a stuck outstanding probe."""
    host, peer, _ = host_and_peer
    host.shutdown()

    # Recreate a host with a closed socket path via monkeypatched send.
    host_port = _free_udp_port()
    host = Host(
        IP_ADDRESS="127.0.0.1",
        PORT=host_port,
        virtual_address=11,
        entry_router_address=1,
        probe_timeout=0.5,
    )
    host.entry_router_meta = ("127.0.0.1", _free_udp_port())

    def boom(datagram, end_destination):
        raise OSError("simulated send failure")

    host._send_datagram = boom  # type: ignore[method-assign]

    with pytest.raises(OSError, match="simulated send failure"):
        host.send_probe(50, b"fail")

    assert host.outstanding_probe_ids() == set()
    assert host.probe_results() == ()
    host.shutdown()


def test_configurable_timeout_behavior():
    """Constructor probe_timeout controls the default deadline."""
    with pytest.raises(ValueError):
        Host("127.0.0.1", _free_udp_port(), 1, 1, probe_timeout=0)
    with pytest.raises(ValueError):
        Host("127.0.0.1", _free_udp_port(), 1, 1, probe_timeout=-1)
    with pytest.raises(ValueError):
        Host("127.0.0.1", _free_udp_port(), 1, 1, probe_timeout=float("inf"))

    host_port = _free_udp_port()
    peer_port = _free_udp_port()
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", peer_port))
    peer.settimeout(1.0)

    host = Host(
        IP_ADDRESS="127.0.0.1",
        PORT=host_port,
        virtual_address=12,
        entry_router_address=1,
        probe_timeout=0.12,
    )
    assert host.probe_timeout == pytest.approx(0.12)
    assert DEFAULT_PROBE_TIMEOUT_S > 0
    host.entry_router_meta = ("127.0.0.1", peer_port)
    host.start()
    try:
        started = time.monotonic()
        identifier = host.send_probe(60, b"cfg")
        _recv_probe(peer)
        assert _wait_until(
            lambda: any(r.identifier == identifier for r in host.probe_results()),
            timeout=1.0,
        )
        elapsed = time.monotonic() - started
        result = host.probe_results()[0]
        assert result.status == "timeout"
        # Deadline-driven expiry should complete near 0.12s, not ~1s late.
        assert elapsed < 0.5
    finally:
        host.shutdown()
        peer.close()


def test_echo_cli_path_uses_send_probe(monkeypatch, host_and_peer):
    """The interactive echo command delegates to send_probe."""
    host, peer, _ = host_and_peer
    calls = []

    def tracking_send_probe(destination, payload, timeout=None):
        calls.append((destination, payload, timeout))
        return 7

    host.send_probe = tracking_send_probe  # type: ignore[method-assign]
    inputs = iter(["echo 5 hello-probe", "quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    host.operation_loop()

    assert calls == [(5, "hello-probe", None)]
