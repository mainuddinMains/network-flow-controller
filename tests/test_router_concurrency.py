"""Concurrency and shutdown tests for Issue #28."""

import socket
import struct
import threading
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
    """Build a wire datagram matching the router header format."""
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


def _make_router(
    *,
    port: int,
    neighbors: list[int],
    forwarding_table: dict[int, int],
    delays: dict[int, float],
    loss: float = 0.0,
    inbound_queue_size: int = 32,
    virtual_address: int = 1,
) -> Router:
    """Construct a router bound to ``port`` for isolated unit tests."""
    return Router(
        IP_ADDRESS="127.0.0.1",
        PORT=port,
        virtual_address=virtual_address,
        bandwidth=1000,
        buffer_size=65535,
        neighbors=neighbors,
        forwarding_table=forwarding_table,
        delays=delays,
        loss=loss,
        inbound_queue_size=inbound_queue_size,
    )


@pytest.fixture
def peer_and_router():
    """Router VA=1 forwarding dest 5 via neighbor 2; peer captures sends."""
    router_port = _free_udp_port()
    peer_port = _free_udp_port()
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", peer_port))
    peer.settimeout(2.0)

    router = _make_router(
        port=router_port,
        neighbors=[2],
        forwarding_table={5: 2},
        delays={2: 0.2},
        loss=0.0,
    )
    router.address_to_real_dest_map = {2: ("127.0.0.1", peer_port)}
    router.start()

    yield router, peer, router_port

    router.shutdown()
    peer.close()


def test_second_packet_is_received_while_first_is_delayed(peer_and_router):
    """Receiver must enqueue packet 2 before packet 1 finishes delayed send."""
    router, peer, router_port = peer_and_router
    enqueue_times: list[float] = []
    send_windows: list[tuple[float, float]] = []

    original_put = router._inbound.put_nowait

    def timed_put(item):
        enqueue_times.append(time.perf_counter())
        return original_put(item)

    router._inbound.put_nowait = timed_put  # type: ignore[method-assign]

    original_send = router._send_datagram

    def timed_send(datagram, end_destination):
        started = time.perf_counter()
        original_send(datagram, end_destination)
        send_windows.append((started, time.perf_counter()))

    router._send_datagram = timed_send  # type: ignore[method-assign]

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.sendto(
            _pack_datagram(identifier=1, payload=b"first"),
            ("127.0.0.1", router_port),
        )
        time.sleep(0.05)
        client.sendto(
            _pack_datagram(identifier=2, payload=b"second"),
            ("127.0.0.1", router_port),
        )

        # Wait until both delayed forwards complete.
        deadline = time.perf_counter() + 3.0
        while len(send_windows) < 2 and time.perf_counter() < deadline:
            time.sleep(0.02)
    finally:
        client.close()

    assert len(enqueue_times) >= 2
    assert len(send_windows) == 2
    # Packet 2 was enqueued while packet 1 was still inside _send_datagram.
    assert enqueue_times[1] < send_windows[0][1]

    # Drain peer so the next test fixture teardown is quiet.
    try:
        while True:
            peer.recvfrom(65535)
    except socket.timeout:
        pass


def test_packets_reach_correct_next_hop_without_duplication(peer_and_router):
    """Each datagram is forwarded once to the configured next hop."""
    router, peer, router_port = peer_and_router
    # Remove delay so the test finishes quickly.
    router.delays[2] = 0.0

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for identifier, payload in ((10, b"alpha"), (11, b"beta")):
            client.sendto(
                _pack_datagram(identifier=identifier, payload=payload),
                ("127.0.0.1", router_port),
            )

        received = []
        deadline = time.perf_counter() + 2.0
        while len(received) < 2 and time.perf_counter() < deadline:
            try:
                data, addr = peer.recvfrom(65535)
            except socket.timeout:
                break
            header, _ = router._interpret_datagram(data)
            received.append((header["identifier"], addr, data))
    finally:
        client.close()

    assert len(received) == 2
    identifiers = [item[0] for item in received]
    assert identifiers.count(10) == 1
    assert identifiers.count(11) == 1
    assert all(addr[0] == "127.0.0.1" for _, addr, _ in received)


def test_controlled_no_loss_burst_is_delivered(peer_and_router):
    """With loss disabled, a short burst is forwarded without app drops."""
    router, peer, router_port = peer_and_router
    router.delays[2] = 0.0
    router.loss_chance = 0.0

    count = 8
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for identifier in range(count):
            client.sendto(
                _pack_datagram(identifier=identifier, payload=b"x"),
                ("127.0.0.1", router_port),
            )

        got = []
        deadline = time.perf_counter() + 3.0
        while len(got) < count and time.perf_counter() < deadline:
            try:
                data, _ = peer.recvfrom(65535)
            except socket.timeout:
                break
            header, _ = router._interpret_datagram(data)
            got.append(header["identifier"])
    finally:
        client.close()

    assert sorted(got) == list(range(count))
    assert router.overflow_drops == 0


def test_router_workers_and_socket_shut_down_cleanly():
    """shutdown joins workers and releases the UDP bind port."""
    port = _free_udp_port()
    router = _make_router(
        port=port,
        neighbors=[2],
        forwarding_table={5: 2},
        delays={2: 0.0},
        loss=0.0,
    )
    router.start()
    assert router._receiver_thread is not None
    assert router._forwarder_thread is not None
    assert router._receiver_thread.is_alive()
    assert router._forwarder_thread.is_alive()

    router.shutdown(join_timeout=2.0)

    assert not router._receiver_thread.is_alive()
    assert not router._forwarder_thread.is_alive()

    # Port must be reusable after a clean shutdown.
    rebound = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        rebound.bind(("127.0.0.1", port))
    finally:
        rebound.close()


def test_inbound_queue_overflow_drops_explicitly():
    """A full inbound queue drops new datagrams and counts overflow_drops."""
    router_port = _free_udp_port()
    router = _make_router(
        port=router_port,
        neighbors=[2],
        forwarding_table={5: 2},
        delays={2: 0.0},
        loss=0.0,
        inbound_queue_size=1,
    )
    # Do not start the forwarder: fill the queue from the receiver only.
    router._stop_event.clear()
    receiver = threading.Thread(target=router.listening_loop, daemon=True)
    receiver.start()

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for identifier in range(5):
            client.sendto(
                _pack_datagram(identifier=identifier, payload=b"z"),
                ("127.0.0.1", router_port),
            )
            time.sleep(0.05)

        deadline = time.perf_counter() + 2.0
        while router.overflow_drops < 1 and time.perf_counter() < deadline:
            time.sleep(0.02)
    finally:
        client.close()
        router.shutdown()

    assert router.overflow_drops >= 1
    assert router._inbound.qsize() <= 1
