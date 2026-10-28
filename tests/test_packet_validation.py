"""Malformed-packet validation tests for Issue #30."""

import socket
import struct
import time

import pytest

from src.simulator.host import Host
from src.simulator.router import (
    HEADER_FORMAT,
    HEADER_SIZE,
    MAX_PACKET_SIZE_B,
    Router,
)


def _free_udp_port() -> int:
    """Bind briefly to learn an unused UDP port on localhost."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def _pack_datagram(
    *,
    ttl=64,
    type_flags=0,
    reserve=0,
    identifier=1,
    source=0,
    destination=5,
    payload=b"hello",
) -> bytes:
    """Build a wire datagram matching the shared header format."""
    header = struct.pack(
        HEADER_FORMAT,
        ttl,
        type_flags,
        reserve,
        identifier,
        source,
        destination,
    )
    return header + payload


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    """Poll until ``predicate`` is true or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def router_and_peer():
    """Router VA=1 forwarding dest 5 via neighbor 2; peer captures sends."""
    router_port = _free_udp_port()
    peer_port = _free_udp_port()
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", peer_port))
    peer.settimeout(2.0)

    router = Router(
        IP_ADDRESS="127.0.0.1",
        PORT=router_port,
        virtual_address=1,
        bandwidth=1000,
        buffer_size=500,
        neighbors=[2],
        forwarding_table={5: 2},
        delays={2: 0.0},
        loss=0.0,
    )
    router.address_to_real_dest_map = {2: ("127.0.0.1", peer_port)}
    router.start()

    yield router, peer, router_port

    router.shutdown()
    peer.close()


@pytest.fixture
def host_and_peer():
    """Host whose entry-router endpoint is a local capture socket."""
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


def _assert_forwarded(peer: socket.socket, expected_payload: bytes) -> bytes:
    """Receive one datagram at the peer and assert its payload matches."""
    data, _ = peer.recvfrom(65535)
    assert data[HEADER_SIZE:] == expected_payload
    return data


# ----------------------------------------------------------------------
# Router validation
# ----------------------------------------------------------------------
def test_router_rejects_empty_and_truncated_then_processes_valid(
    router_and_peer,
):
    """Short datagrams are dropped with a count; valid traffic continues."""
    router, peer, router_port = router_and_peer
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.sendto(b"", ("127.0.0.1", router_port))
        client.sendto(b"abc", ("127.0.0.1", router_port))
        assert _wait_until(lambda: router.malformed_drops == 2)

        assert router._receiver_thread.is_alive()
        assert router._forwarder_thread.is_alive()

        client.sendto(
            _pack_datagram(payload=b"still-works"),
            ("127.0.0.1", router_port),
        )
        _assert_forwarded(peer, b"still-works")
    finally:
        client.close()


@pytest.mark.parametrize("oversize", [MAX_PACKET_SIZE_B + 1, 5000])
def test_router_rejects_oversized_and_receiver_survives(
    router_and_peer, oversize
):
    """Oversized datagrams are rejected without killing the receiver."""
    router, peer, router_port = router_and_peer
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        big = _pack_datagram(payload=b"x" * (oversize - HEADER_SIZE))
        assert len(big) == oversize
        client.sendto(big, ("127.0.0.1", router_port))
        assert _wait_until(lambda: router.malformed_drops == 1)

        assert router._receiver_thread.is_alive()

        client.sendto(
            _pack_datagram(payload=b"after-oversize"),
            ("127.0.0.1", router_port),
        )
        _assert_forwarded(peer, b"after-oversize")
    finally:
        client.close()


def test_router_accepts_exactly_1200_bytes(router_and_peer):
    """A datagram at the 1200-byte limit is forwarded intact."""
    router, peer, router_port = router_and_peer
    payload = b"y" * (MAX_PACKET_SIZE_B - HEADER_SIZE)
    packet = _pack_datagram(payload=payload)
    assert len(packet) == MAX_PACKET_SIZE_B

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.sendto(packet, ("127.0.0.1", router_port))
        forwarded = _assert_forwarded(peer, payload)
        assert len(forwarded) == MAX_PACKET_SIZE_B
        assert router.malformed_drops == 0
    finally:
        client.close()


def test_router_forwards_binary_payload_unmodified(router_and_peer):
    """Non-UTF-8 payload bytes transit the router byte-for-byte."""
    router, peer, router_port = router_and_peer
    payload = b"\xff\xfe\x80\x00\x01binary"

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.sendto(
            _pack_datagram(ttl=64, payload=payload),
            ("127.0.0.1", router_port),
        )
        forwarded = _assert_forwarded(peer, payload)
        header = struct.unpack_from(HEADER_FORMAT, forwarded, offset=0)
        assert header[0] == 63  # TTL decremented by the forward hop.
    finally:
        client.close()


def test_router_drops_unknown_destination_and_missing_next_hop(
    router_and_peer,
):
    """No-route packets are dropped explicitly and threads stay alive."""
    router, peer, router_port = router_and_peer
    # Destination 7 routes to next hop 9, which is not a configured neighbor.
    router.forwarding_table[7] = 9

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.sendto(
            _pack_datagram(destination=99),
            ("127.0.0.1", router_port),
        )
        client.sendto(
            _pack_datagram(destination=7),
            ("127.0.0.1", router_port),
        )
        assert _wait_until(lambda: router.no_route_drops == 2)
        assert router.malformed_drops == 0
        assert router._receiver_thread.is_alive()
        assert router._forwarder_thread.is_alive()

        client.sendto(
            _pack_datagram(destination=5, payload=b"routed"),
            ("127.0.0.1", router_port),
        )
        _assert_forwarded(peer, b"routed")
    finally:
        client.close()


def test_router_survives_send_to_unreachable_neighbor(router_and_peer):
    """ICMP port-unreachable resets must not stop the receiver."""
    router, peer, router_port = router_and_peer
    # Neighbor 3 maps to a port with no listener bound.
    router.neighboring_sockets.append(3)
    router.address_to_real_dest_map[3] = ("127.0.0.1", _free_udp_port())
    router.forwarding_table[6] = 3

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.sendto(
            _pack_datagram(destination=6, payload=b"into-void"),
            ("127.0.0.1", router_port),
        )
        time.sleep(0.3)  # Give Windows time to surface the ICMP reset.

        client.sendto(
            _pack_datagram(destination=5, payload=b"survivor"),
            ("127.0.0.1", router_port),
        )
        _assert_forwarded(peer, b"survivor")
        assert router._receiver_thread.is_alive()
    finally:
        client.close()


# ----------------------------------------------------------------------
# Host validation
# ----------------------------------------------------------------------
def test_host_rejects_short_and_oversized_then_probes_normally(
    host_and_peer,
):
    """Malformed inbound datagrams are counted; probing still works."""
    host, peer, host_port = host_and_peer
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.sendto(b"ab", ("127.0.0.1", host_port))
        client.sendto(b"z" * 1500, ("127.0.0.1", host_port))
        assert _wait_until(lambda: host.malformed_drops == 2)
        assert host._listener_thread.is_alive()

        # A full probe round-trip still succeeds after the rejects.
        identifier = host.send_probe(20, b"ping")
        data, addr = peer.recvfrom(65535)
        reply_header = struct.pack(
            HEADER_FORMAT,
            64,
            Host.flags["reply"],
            0,
            identifier,
            20,
            host.virtual_address,
        )
        peer.sendto(reply_header + b"pong", addr)

        assert _wait_until(lambda: len(host.probe_results()) == 1)
        assert host.probe_results()[0].status == "success"
    finally:
        client.close()


def test_host_wakeup_datagram_is_not_counted_malformed(host_and_peer):
    """The internal wakeup datagram stays silent and uncounted."""
    host, peer, host_port = host_and_peer
    host.send_probe(21, b"x", timeout=0.2)  # Triggers a wakeup datagram.
    peer.recvfrom(65535)  # Drain the probe itself.
    assert _wait_until(
        lambda: any(r.status == "timeout" for r in host.probe_results()),
        timeout=1.0,
    )
    assert host.malformed_drops == 0


def test_host_oversized_probe_is_refused_without_pending_state(
    host_and_peer,
):
    """An oversized probe raises ValueError and leaves no tracking entry."""
    host, peer, _ = host_and_peer
    with pytest.raises(ValueError, match="1200-byte protocol maximum"):
        host.send_probe(30, b"x" * (MAX_PACKET_SIZE_B + 1))

    assert host.outstanding_probe_ids() == set()
    assert host.probe_results() == ()

    # A maximum-size probe is still allowed.
    identifier = host.send_probe(
        30, b"x" * (MAX_PACKET_SIZE_B - HEADER_SIZE), timeout=1.0
    )
    data, _ = peer.recvfrom(65535)
    assert len(data) == MAX_PACKET_SIZE_B
    assert identifier in host.outstanding_probe_ids()


def test_host_create_datagram_enforces_max_size():
    """Direct packet construction refuses oversized datagrams."""
    port = _free_udp_port()
    host = Host(
        IP_ADDRESS="127.0.0.1",
        PORT=port,
        virtual_address=11,
        entry_router_address=1,
    )
    try:
        with pytest.raises(ValueError, match="1200-byte protocol maximum"):
            host._create_datagram(
                64, 0, 0, 1, 11, 5, b"q" * (MAX_PACKET_SIZE_B + 100)
            )
    finally:
        host.shutdown()


def test_router_create_datagram_enforces_max_size():
    """Router packet construction refuses oversized datagrams."""
    port = _free_udp_port()
    router = Router(
        IP_ADDRESS="127.0.0.1",
        PORT=port,
        virtual_address=2,
        bandwidth=1000,
        buffer_size=500,
        neighbors=[3],
        forwarding_table={5: 3},
        delays={3: 0.0},
        loss=0.0,
    )
    try:
        with pytest.raises(ValueError, match="1200-byte protocol maximum"):
            router._create_datagram(
                64, 0, 0, 1, 2, 5, b"q" * (MAX_PACKET_SIZE_B + 100)
            )
    finally:
        router.shutdown()
