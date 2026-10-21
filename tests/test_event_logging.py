"""Packet event logging required by issue #31."""

import logging
import random
import socket
import struct
import threading

import pytest

from src.simulator.host import Host
from src.simulator.router import HEADER_FORMAT, Router
from src.telemetry.netlog import RUN_ID, format_event, get_device_logger

_ECHO = 0b0000_0001
_REPLY = 0b0000_0010


class _Capture(logging.Handler):
    """Collect log records without changing packet handling."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def captured():
    """Attach a capture handler to the shared network-event logger."""
    handler = _Capture()
    root = logging.getLogger("netlog")
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)


def _messages(handler, event):
    """Return captured messages for one event type."""
    needle = f"event={event} "
    return [
        record.getMessage()
        for record in handler.records
        if record.getMessage().startswith(needle)
        or f" {needle}" in record.getMessage()
    ]


def _formatted(record):
    """Render a record with the process log format."""
    root = logging.getLogger("netlog")
    for handler in root.handlers:
        if handler.formatter is not None:
            return handler.format(record)
    raise AssertionError("network logger has no formatter")


def _pack(
    *,
    ttl=64,
    type_flags=0,
    identifier=1,
    source=1,
    destination=5,
    payload=b"ping",
):
    """Build one wire datagram."""
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


def _router(loss=0.0):
    """Router VA 3 forwarding destination 5 via neighbor 4, with no delay."""
    router = Router(
        IP_ADDRESS="127.0.0.1",
        PORT=0,
        virtual_address=3,
        bandwidth=1000,
        buffer_size=4096,
        neighbors=[4],
        forwarding_table={5: 4},
        delays={4: 0.0},
        loss=loss,
        seed=1,
    )
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", 0))
    peer.settimeout(0.3)
    router.address_to_real_dest_map = {4: peer.getsockname()}
    router.start()
    return router, peer


def test_forward_log_has_required_fields_and_same_ttl(captured):
    """A forwarded echo is logged once and leaves with a decremented TTL."""
    router, peer = _router()
    try:
        router._process_datagram(
            _pack(ttl=8, type_flags=_ECHO, identifier=17, source=1, destination=5)
        )
        data, _ = peer.recvfrom(2048)
    finally:
        peer.close()
        router.shutdown()

    assert struct.unpack_from(HEADER_FORMAT, data)[0] == 7
    message = _messages(captured, "forward")[-1]
    for field in (
        "id=17",
        "type=echo",
        "src=1",
        "dest=5",
        "next_hop=4",
        "ttl=7",
    ):
        assert field in message

    record = captured.records[-1]
    rendered = _formatted(record)
    assert f"run={RUN_ID}" in rendered
    assert "netlog.router-3" in rendered
    assert rendered.split(" | ")[0][:4].isdigit()


def test_drop_reasons_cover_ttl_unknown_destination_and_loss(captured):
    """Drops name TTL expiry, an unknown destination, and injected loss."""
    state = random.getstate()
    router, peer = _router(loss=1.0)
    try:
        router._process_datagram(
            _pack(ttl=0, identifier=3, source=1, destination=5)
        )
        router._process_datagram(
            _pack(ttl=4, identifier=4, source=1, destination=9)
        )
        router._process_datagram(
            _pack(ttl=4, identifier=5, source=1, destination=5)
        )
        with pytest.raises(socket.timeout):
            peer.recvfrom(2048)
    finally:
        random.setstate(state)
        peer.close()
        router.shutdown()

    drops = _messages(captured, "drop")
    assert any("reason=ttl_expired" in line and "ttl=0" in line for line in drops)
    assert any("reason=no_route" in line and "dest=9" in line for line in drops)
    assert any(
        "reason=injected_loss" in line and "next_hop=4" in line and "ttl=3" in line
        for line in drops
    )


def test_probe_and_echo_reply_share_an_identifier(captured):
    """One probe id is visible on the router hop and on the echo reply."""
    router, peer = _router()
    host = Host("127.0.0.1", 0, 5, entry_router_address=3)
    reply_sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reply_sink.bind(("127.0.0.1", 0))
    reply_sink.settimeout(0.3)
    host.entry_router_meta = reply_sink.getsockname()
    try:
        router._process_datagram(
            _pack(
                ttl=8,
                type_flags=_ECHO,
                identifier=21,
                source=1,
                destination=5,
            )
        )
        forwarded, _ = peer.recvfrom(2048)
        host._handle_datagram(forwarded)
        reply, _ = reply_sink.recvfrom(2048)
    finally:
        reply_sink.close()
        peer.close()
        host.shutdown()
        router.shutdown()

    ttl, flags, _, identifier, source, destination = struct.unpack_from(
        HEADER_FORMAT, reply
    )
    assert (ttl, flags, identifier, source, destination) == (64, _REPLY, 21, 5, 1)

    text = " ".join(record.getMessage() for record in captured.records)
    assert "event=receive" in text and "id=21" in text and "type=echo" in text
    assert "event=forward" in text and "next_hop=4" in text
    assert "event=send" in text and "type=reply" in text and "id=21" in text
    devices = {_formatted(record) for record in captured.records}
    assert any("netlog.router-3" in line for line in devices)
    assert any("netlog.host-5" in line for line in devices)
    assert all(f"run={RUN_ID}" in line for line in devices)


def test_host_probe_timeout_names_the_probe(captured):
    """A probe that never returns is a timeout for that id and destination."""
    host = Host("127.0.0.1", 0, 1, entry_router_address=2)
    try:
        identifier = host.send_probe(5, "hi", timeout=0.2)
        host._expire_probes(now=host._outstanding[identifier].deadline)
    finally:
        host.shutdown()

    sent = _messages(captured, "send")[-1]
    assert f"id={identifier}" in sent
    assert "type=echo" in sent
    assert "src=1" in sent
    assert "dest=5" in sent
    assert "next_hop=2" in sent
    assert "ttl=64" in sent

    timed_out = _messages(captured, "timeout")[-1]
    assert "reason=probe_timeout" in timed_out
    assert f"id={identifier}" in timed_out
    assert "dest=5" in timed_out


def test_concurrent_records_stay_on_one_line(captured):
    """Worker threads cannot splice two events into one record."""
    logger = get_device_logger("router-9")

    def worker(tag):
        for number in range(40):
            logger.info(format_event("forward", id=number, type=tag, src=1, dest=2))

    threads = [
        threading.Thread(target=worker, args=("echo",)),
        threading.Thread(target=worker, args=("reply",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    messages = [record.getMessage() for record in captured.records]
    assert len(messages) >= 80
    for message in messages:
        assert "\n" not in message
        assert message.count("event=") == 1
