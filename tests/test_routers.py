"""Router configuration tests for the diamond topology (issues #25, #32)."""

import socket
import struct
from pathlib import Path

from src.simulator import router_builder
from src.simulator.router import HEADER_FORMAT, HEADER_SIZE

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_CONFIG_DIR = REPO_ROOT / "src" / "simulator" / "router configurations"

# Host B's real UDP endpoint; a plain socket stands in for the host here.
HOST_B_PORT = 60005
ROUTER_A_PORT = 60001


def _build_diamond_routers():
    """Build routers A-D from the supplied INI configuration files."""
    return [
        router_builder.build_router_from_ini(
            ROUTER_CONFIG_DIR / f"router{name}.ini"
        )
        for name in "ABCD"
    ]


def test_router_configs_load_into_consistent_forwarding_state():
    """Each INI yields unique addresses and neighbor-only next hops."""
    routers = _build_diamond_routers()
    try:
        assert [router.virtual_address for router in routers] == [1, 2, 3, 4]
        assert [router.meta_port for router in routers] == [
            60001, 60002, 60003, 60004,
        ]
        for router in routers:
            # Every forwarding next hop must be a configured neighbor.
            assert set(router.forwarding_table.values()) <= set(
                router.neighboring_sockets
            )
            # Every neighbor must have a configured propagation delay.
            assert set(router.delays) == set(router.neighboring_sockets)
    finally:
        for router in routers:
            router.shutdown()


def test_routers():
    """A datagram entering router A reaches host B's endpoint via B and D."""
    routers = _build_diamond_routers()

    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", HOST_B_PORT))
    receiver.settimeout(5.0)

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        for router in routers:
            router.start()

        header = struct.pack(HEADER_FORMAT, 100, 0, 0, 1, 0, 5)
        client.sendto(header + b"Test payload.", ("127.0.0.1", ROUTER_A_PORT))

        data, _ = receiver.recvfrom(65535)
    finally:
        client.close()
        for router in routers:
            router.shutdown()
        receiver.close()

    ttl, _, _, identifier, source, destination = struct.unpack_from(
        HEADER_FORMAT, data
    )
    assert source == 0
    assert destination == 5
    assert identifier == 1
    # Decremented once by each forwarding router: A, B, and D.
    assert ttl == 97
    assert data[HEADER_SIZE:] == b"Test payload."
