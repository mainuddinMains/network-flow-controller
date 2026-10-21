"""Configurable packet loss and nonblocking delay tests for Issue #34."""

import socket
import struct
import time
from pathlib import Path

import pytest

from src.simulator.router import HEADER_FORMAT, Router
from src.simulator.router_builder import build_router_from_ini


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
    link_loss: dict[int, float] | None = None,
    seed: int | None = 1,
    virtual_address: int = 1,
) -> Router:
    """Construct a router bound to ``port`` for isolated loss/delay tests."""
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
        link_loss=link_loss,
        seed=seed,
    )


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` seconds elapse."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _recv_identifier(peer: socket.socket, timeout: float):
    """Return the identifier of the next datagram, or None on timeout."""
    peer.settimeout(timeout)
    try:
        data, _ = peer.recvfrom(65535)
    except socket.timeout:
        return None
    # HEADER_FORMAT is "!BBBHHH": identifier is the first 16-bit field.
    (identifier,) = struct.unpack_from("!H", data, offset=3)
    return identifier


@pytest.fixture
def forward_peer():
    """Router VA=1 forwarding dest 5 via neighbor 2, with a capturing peer."""
    router_port = _free_udp_port()
    peer_port = _free_udp_port()
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", peer_port))
    peer.settimeout(2.0)

    router = _make_router(
        port=router_port,
        neighbors=[2],
        forwarding_table={5: 2},
        delays={2: 0.0},
        loss=0.0,
        seed=1,
    )
    router.address_to_real_dest_map = {2: ("127.0.0.1", peer_port)}
    router.start()

    yield router, peer, router_port

    router.shutdown()
    peer.close()


def test_zero_loss_delivers_every_packet(forward_peer):
    """A 0% loss configuration introduces no intentional drops."""
    router, peer, router_port = forward_peer
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    count = 8
    try:
        for identifier in range(count):
            client.sendto(
                _pack_datagram(identifier=identifier, payload=b"x"),
                ("127.0.0.1", router_port),
            )
        got = []
        deadline = time.perf_counter() + 2.0
        while len(got) < count and time.perf_counter() < deadline:
            identifier = _recv_identifier(peer, 0.1)
            if identifier is not None:
                got.append(identifier)
    finally:
        client.close()

    assert sorted(got) == list(range(count))
    assert router.loss_drops == 0


def test_forced_drop_drops_every_packet(forward_peer):
    """loss=1.0 is a deterministic forced drop of every forwarded packet."""
    router, peer, router_port = forward_peer
    router.loss_chance = 1.0
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    count = 5
    try:
        for identifier in range(count):
            client.sendto(
                _pack_datagram(identifier=identifier, type_flags=identifier % 2),
                ("127.0.0.1", router_port),
            )
        assert _wait_until(lambda: router.loss_drops >= count)
        assert _recv_identifier(peer, 0.2) is None
    finally:
        client.close()

    assert router.loss_drops == count


def test_same_seed_reproduces_loss_decisions():
    """Two routers with the same seed drop the same packets at loss=0.5."""

    def pattern(seed: int) -> tuple[str, ...]:
        router_port = _free_udp_port()
        peer_port = _free_udp_port()
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(("127.0.0.1", peer_port))
        router = _make_router(
            port=router_port,
            neighbors=[2],
            forwarding_table={5: 2},
            delays={2: 0.0},
            loss=0.5,
            seed=seed,
        )
        router.address_to_real_dest_map = {2: ("127.0.0.1", peer_port)}
        router.start()
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        decisions = []
        try:
            for identifier in range(24):
                drops_before = router.loss_drops
                client.sendto(
                    _pack_datagram(identifier=identifier),
                    ("127.0.0.1", router_port),
                )
                decided = False
                deadline = time.perf_counter() + 2.0
                while time.perf_counter() < deadline:
                    if router.loss_drops > drops_before:
                        decisions.append("drop")
                        decided = True
                        break
                    identifier_seen = _recv_identifier(peer, 0.02)
                    if identifier_seen == identifier:
                        decisions.append("send")
                        decided = True
                        break
                if not decided:
                    raise AssertionError(
                        f"seed {seed} packet {identifier} was not decided"
                    )
        finally:
            client.close()
            router.shutdown()
            peer.close()
        return tuple(decisions)

    first = pattern(12345)
    second = pattern(12345)
    other = pattern(999)
    assert first == second
    assert "drop" in first
    assert "send" in first
    assert first != other


def test_directed_link_loss_does_not_affect_other_links():
    """Loss on one next hop drops only packets forwarded on that link."""
    router_port = _free_udp_port()
    lossy_port = _free_udp_port()
    clean_port = _free_udp_port()
    lossy = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    clean = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    lossy.bind(("127.0.0.1", lossy_port))
    clean.bind(("127.0.0.1", clean_port))
    router = _make_router(
        port=router_port,
        neighbors=[2, 3],
        forwarding_table={5: 2, 6: 3},
        delays={2: 0.0, 3: 0.0},
        loss=0.0,
        link_loss={2: 1.0, 3: 0.0},
        seed=4,
    )
    router.address_to_real_dest_map = {
        2: ("127.0.0.1", lossy_port),
        3: ("127.0.0.1", clean_port),
    }
    router.start()
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.sendto(
            _pack_datagram(identifier=1, destination=5, type_flags=0),
            ("127.0.0.1", router_port),
        )
        client.sendto(
            _pack_datagram(identifier=2, destination=6, type_flags=1),
            ("127.0.0.1", router_port),
        )
        assert _wait_until(lambda: router.loss_drops >= 1)
        assert _recv_identifier(clean, 1.0) == 2
        assert _recv_identifier(lossy, 0.2) is None
    finally:
        client.close()
        router.shutdown()
        lossy.close()
        clean.close()

    assert router.loss_drops == 1


def test_bidirectional_pair_loss_is_per_direction():
    """Each direction of a router pair keeps its own loss probability."""
    port_a = _free_udp_port()
    port_b = _free_udp_port()
    router_a = _make_router(
        port=port_a,
        neighbors=[2],
        forwarding_table={2: 2},
        delays={2: 0.0},
        loss=0.0,
        link_loss={2: 1.0},
        seed=5,
        virtual_address=1,
    )
    router_b = _make_router(
        port=port_b,
        neighbors=[1],
        forwarding_table={1: 1},
        delays={1: 0.0},
        loss=0.0,
        link_loss={1: 0.0},
        seed=5,
        virtual_address=2,
    )
    router_a.address_to_real_dest_map = {2: ("127.0.0.1", port_b)}
    router_b.address_to_real_dest_map = {1: ("127.0.0.1", port_a)}
    kept_a: list[int] = []
    kept_b: list[int] = []

    def _keep_a(_payload, header) -> None:
        kept_a.append(header["identifier"])

    def _keep_b(_payload, header) -> None:
        kept_b.append(header["identifier"])

    router_a._keep_datagram = _keep_a  # type: ignore[method-assign]
    router_b._keep_datagram = _keep_b  # type: ignore[method-assign]
    router_a.start()
    router_b.start()
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.sendto(
            _pack_datagram(identifier=10, destination=2),
            ("127.0.0.1", port_a),
        )
        client.sendto(
            _pack_datagram(identifier=11, destination=1),
            ("127.0.0.1", port_b),
        )
        assert _wait_until(lambda: router_a.loss_drops >= 1)
        assert _wait_until(lambda: 11 in kept_a)
    finally:
        client.close()
        router_a.shutdown()
        router_b.shutdown()

    assert router_a.loss_drops == 1
    assert router_b.loss_drops == 0
    assert kept_b == []
    assert kept_a == [11]


def test_configured_delay_postpones_delivery(forward_peer):
    """A configured link delay holds the packet until the delay elapses."""
    router, peer, router_port = forward_peer
    delay = 0.3
    router.delays[2] = delay
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        started = time.perf_counter()
        client.sendto(
            _pack_datagram(identifier=7, type_flags=1),
            ("127.0.0.1", router_port),
        )
        assert _recv_identifier(peer, 1.5) == 7
        elapsed = time.perf_counter() - started
    finally:
        client.close()

    assert router.delayed_sends == 1
    assert elapsed >= delay * 0.85


def test_delayed_packet_does_not_block_other_link():
    """A delayed link must not hold up a packet forwarded on another link."""
    router_port = _free_udp_port()
    slow_port = _free_udp_port()
    fast_port = _free_udp_port()
    slow = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fast = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    slow.bind(("127.0.0.1", slow_port))
    fast.bind(("127.0.0.1", fast_port))
    delay = 0.4
    router = _make_router(
        port=router_port,
        neighbors=[2, 3],
        forwarding_table={5: 2, 6: 3},
        delays={2: delay, 3: 0.0},
        loss=0.0,
        seed=8,
    )
    router.address_to_real_dest_map = {
        2: ("127.0.0.1", slow_port),
        3: ("127.0.0.1", fast_port),
    }
    router.start()
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        started = time.perf_counter()
        client.sendto(
            _pack_datagram(identifier=1, destination=5, type_flags=0),
            ("127.0.0.1", router_port),
        )
        client.sendto(
            _pack_datagram(identifier=2, destination=6, type_flags=1),
            ("127.0.0.1", router_port),
        )
        assert _recv_identifier(fast, 1.0) == 2
        fast_elapsed = time.perf_counter() - started
        assert _recv_identifier(slow, 1.5) == 1
        slow_elapsed = time.perf_counter() - started
    finally:
        client.close()
        router.shutdown()
        slow.close()
        fast.close()

    assert fast_elapsed + 0.2 < slow_elapsed
    assert slow_elapsed >= delay * 0.85
    assert router.delayed_sends == 1


def test_schedule_send_returns_before_the_delay_elapses(forward_peer):
    """Scheduling a delayed packet must not sleep on the caller."""
    router, peer, _router_port = forward_peer
    delay = 0.3
    router.delays[2] = delay
    datagram = _pack_datagram(identifier=3)
    started = time.perf_counter()
    router._schedule_send(datagram, 5)
    scheduled = time.perf_counter() - started

    assert scheduled < 0.05
    assert _recv_identifier(peer, 1.5) == 3
    assert time.perf_counter() - started >= delay * 0.85


def test_drop_and_delay_logs_are_distinguishable(forward_peer):
    """Injected drops and delayed delivery are logged as different events."""
    router, _peer, router_port = forward_peer
    router.loss_chance = 1.0
    router.delays[2] = 0.05
    messages: list[str] = []

    def capture(message: str) -> None:
        messages.append(message)

    router._log = capture  # type: ignore[method-assign]
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.sendto(
            _pack_datagram(identifier=4),
            ("127.0.0.1", router_port),
        )
        assert _wait_until(lambda: router.loss_drops >= 1)
    finally:
        client.close()

    combined = "\n".join(messages)
    assert "delayed a packet" in combined
    assert "injected random loss" in combined
    assert router.delayed_sends == 1
    assert router.loss_drops == 1


def test_builder_reads_link_loss_and_seed(tmp_path: Path):
    """INI link_loss and seed configure the directed links and the RNG."""
    port = _free_udp_port()
    config_path = tmp_path / "router.ini"
    config_path.write_text(
        "\n".join(
            [
                "[metadata]",
                "HOST = 127.0.0.1",
                f"PORT = {port}",
                "",
                "[host]",
                "virtual_address = 1",
                "bandwidth = 1000",
                "buffer_size = 500",
                "",
                "[routing]",
                "neighbors = 2",
                "    3",
                "destinations = 5",
                "    6",
                "local_forwarding = 2",
                "    3",
                "delays = 15",
                "    0",
                "",
                "[loss]",
                "loss_chance = 0.25",
                "link_loss = 1.0",
                "    0.0",
                "seed = 7",
                "",
            ]
        ),
        encoding="utf-8",
    )

    router = build_router_from_ini(config_path)
    try:
        assert router.loss_chance == 0.25
        assert router.link_loss == {2: 1.0, 3: 0.0}
        assert router.delays[2] == pytest.approx(0.015)
        assert router.delays[3] == 0.0
        assert router.seed == 7
    finally:
        router.shutdown()


def test_builder_without_seed_records_a_generated_seed(tmp_path: Path):
    """Omitting the seed still records the generated seed on the router."""
    port = _free_udp_port()
    config_path = tmp_path / "router.ini"
    config_path.write_text(
        "\n".join(
            [
                "[metadata]",
                "HOST = 127.0.0.1",
                f"PORT = {port}",
                "",
                "[host]",
                "virtual_address = 4",
                "bandwidth = 1000",
                "buffer_size = 500",
                "",
                "[routing]",
                "neighbors = 2",
                "destinations = 5",
                "local_forwarding = 2",
                "delays = 0",
                "",
                "[loss]",
                "loss_chance = 0",
                "",
            ]
        ),
        encoding="utf-8",
    )

    router = build_router_from_ini(config_path)
    try:
        assert isinstance(router.seed, int)
        assert router.link_loss == {}
    finally:
        router.shutdown()
