"""Hop-limit enforcement and forwarding-error tests for Issue #33."""

import socket
import struct
import time

import pytest

from src.simulator.router import HEADER_FORMAT, Router


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


def _make_router(
    *,
    virtual_address: int,
    neighbors: list,
    forwarding_table: dict,
) -> Router:
    """Build a loss-free, zero-delay router on a free localhost port."""
    return Router(
        IP_ADDRESS="127.0.0.1",
        PORT=_free_udp_port(),
        virtual_address=virtual_address,
        bandwidth=1000,
        buffer_size=500,
        neighbors=neighbors,
        forwarding_table=forwarding_table,
        delays={neighbor: 0.0 for neighbor in neighbors},
        loss=0.0,
    )


@pytest.fixture
def quiet_router():
    """Unstarted router VA=1 forwarding dest 5 via neighbor 2.

    Datagrams are processed by calling ``_process_datagram`` directly on
    the test thread so capsys can capture the drop logs.
    """
    router = _make_router(
        virtual_address=1, neighbors=[2], forwarding_table={5: 2}
    )
    yield router
    router.shutdown()


# ----------------------------------------------------------------------
# Hop-limit enforcement
# ----------------------------------------------------------------------
def test_ttl_expired_packet_is_dropped_not_forwarded(quiet_router):
    """A received TTL of zero is counted as a drop and never forwarded."""
    router = quiet_router
    sent = []
    router._send_datagram = lambda datagram, end_destination: sent.append(
        (datagram, end_destination)
    )

    router._process_datagram(_pack_datagram(ttl=0, destination=5))

    assert router.ttl_expired_drops == 1
    assert sent == []


def test_ttl_expired_drop_log_identifies_packet(quiet_router, capsys):
    """The expired-TTL drop log names the router, id, and TTL."""
    router = quiet_router
    router._process_datagram(
        _pack_datagram(ttl=0, identifier=321, source=7, destination=5)
    )

    output = capsys.readouterr().out
    assert "Router 1 dropped an expired packet" in output
    assert "id=321" in output
    assert "src=7" in output
    assert "dest=5" in output
    assert "ttl=0" in output


def test_forwarding_loop_terminates_by_ttl():
    """A two-router forwarding loop ends through TTL expiration."""
    router_one = _make_router(
        virtual_address=1, neighbors=[2], forwarding_table={99: 2}
    )
    router_two = _make_router(
        virtual_address=2, neighbors=[1], forwarding_table={99: 1}
    )
    router_one.address_to_real_dest_map = {
        2: ("127.0.0.1", router_two.meta_port)
    }
    router_two.address_to_real_dest_map = {
        1: ("127.0.0.1", router_one.meta_port)
    }

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        router_one.start()
        router_two.start()

        client.sendto(
            _pack_datagram(ttl=6, destination=99, payload=b"loop"),
            ("127.0.0.1", router_one.meta_port),
        )

        def total_ttl_drops():
            return (
                router_one.ttl_expired_drops + router_two.ttl_expired_drops
            )

        assert _wait_until(lambda: total_ttl_drops() == 1, timeout=5.0)

        # The packet must not keep circulating after the TTL drop.
        time.sleep(0.3)
        assert total_ttl_drops() == 1
        assert router_one.no_route_drops == 0
        assert router_two.no_route_drops == 0

        for router in (router_one, router_two):
            assert router._receiver_thread.is_alive()
            assert router._forwarder_thread.is_alive()
    finally:
        client.close()
        router_one.shutdown()
        router_two.shutdown()


# ----------------------------------------------------------------------
# Forwarding error handling
# ----------------------------------------------------------------------
def test_no_route_drop_log_identifies_packet(quiet_router, capsys):
    """An unknown destination logs a no-route drop with packet details."""
    router = quiet_router
    router._process_datagram(
        _pack_datagram(ttl=9, identifier=55, source=3, destination=99)
    )

    assert router.no_route_drops == 1
    output = capsys.readouterr().out
    assert "no route to destination 99" in output
    assert "Router 1" in output
    assert "id=55" in output
    assert "src=3" in output
    assert "ttl=8" in output  # Decremented once before the routing lookup.


def test_invalid_next_hop_drop_log_identifies_packet(quiet_router, capsys):
    """A next hop outside the neighbor set is rejected with details."""
    router = quiet_router
    # Destination 7 routes to 9, which is not a configured neighbor.
    router.forwarding_table[7] = 9
    router._process_datagram(
        _pack_datagram(ttl=9, identifier=77, source=4, destination=7)
    )

    assert router.no_route_drops == 1
    output = capsys.readouterr().out
    assert "next hop 9 for destination 7" in output
    assert "not a configured neighbor" in output
    assert "id=77" in output
    assert "src=4" in output
    assert "ttl=8" in output
