"""Deterministic acceptance scenarios (spec section 6.1, scenarios 1-5).

Each test writes ``evidence/<scenario>/`` with the configs used, the full
log in netlog's format, ``meta.json`` and ``key_lines_*.txt`` extracts.
Random-loss experiments live in ``test_random_loss.py``.
"""

import re

import pytest

from harness import (
    BASE_PORT, LOWER, UPPER, count_events, device_events, forwarding,
    grep_log, merge_edits, scenario, send_probes, send_raw, wait_for_probes,
    wait_until,
)


def _forward_hops(out, router):
    lines = device_events(out, f"router-{router}", "event=forward")
    return {int(re.search(r"next_hop=(\d+)", ln).group(1)) for ln in lines}


# ----------------------------------------------------------------------
# 1. Multihop communication
# ----------------------------------------------------------------------
def test_s1_multihop():
    with scenario("s1_multihop", edits=UPPER) as net:
        a, b = net.h[0], net.h[5]
        send_probes(a, 5, 20, "A-probe")
        send_probes(b, 0, 20, "B-probe")
        assert wait_for_probes(a, b)
        assert len(a.rtt_samples()) == 20
        assert len(b.rtt_samples()) == 20
        # Matching payloads: each host's storage holds its own 20 probes
        # echoed back (it also stores the other host's requests).
        own_a = sorted(p.decode() for p in a.storage if p.startswith(b"A-probe"))
        own_b = sorted(p.decode() for p in b.storage if p.startswith(b"B-probe"))
        assert own_a == sorted(f"A-probe-{i}" for i in range(20))
        assert own_b == sorted(f"B-probe-{i}" for i in range(20))
        out = net.out

    assert device_events(out, "router-3", "event=receive") == []
    for r in (1, 2, 4):
        assert device_events(out, f"router-{r}", "event=receive")
    assert not grep_log(out, "event=drop")
    assert not grep_log(out, "event=timeout")

    grep_log(out, "id=7", "type=echo", "src=0", "dest=5", name="trace_echo")
    grep_log(out, "id=7", "type=reply", "src=5", "dest=0", name="trace_reply")


# ----------------------------------------------------------------------
# 2. Configurable paths
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "name, edits, unused_router, used_router",
    [("s2_upper", UPPER, 3, 2), ("s2_lower", LOWER, 2, 3)],
)
def test_s2_paths(name, edits, unused_router, used_router):
    with scenario(name, edits=edits) as net:
        a, b = net.h[0], net.h[5]
        send_probes(a, 5, 5, "A-probe")
        send_probes(b, 0, 5, "B-probe")
        assert wait_for_probes(a, b)
        assert len(a.rtt_samples()) == 5
        assert len(b.rtt_samples()) == 5
        out = net.out
        neighbors = {addr: set(r.neighboring_sockets) for addr, r in net.r.items()}

    assert device_events(out, f"router-{unused_router}", "event=receive") == []
    assert device_events(out, f"router-{used_router}", "event=receive")

    # Every transmission goes only to a configured neighbor.
    for router, hops in ((r, _forward_hops(out, r)) for r in (1, 2, 3, 4)):
        assert hops <= neighbors[router], (router, hops, neighbors[router])

    grep_log(out, "event=forward", name="forwards")


def test_s2b_non_neighbor_next_hop():
    """Router 1 is told to reach host 5 via router 4, which is not a neighbor."""
    edits = merge_edits(UPPER, forwarding(1, {5: 4, 0: 0}))
    with scenario("s2b_non_neighbor", edits=edits) as net:
        a = net.h[0]
        a.send_probe(5, "bad-hop", timeout=0.5)
        assert wait_for_probes(a, limit=3.0)
        assert a.probe_results()[0].status == "timeout"
        assert net.r[1].no_route_drops == 1
        out = net.out

    assert len(device_events(out, "router-1", "reason=next_hop_not_neighbor")) == 1
    assert device_events(out, "router-2", "event=receive") == []
    assert device_events(out, "router-3", "event=receive") == []

    grep_log(out, "reason=next_hop_not_neighbor", name="drop")
    device_events(out, "host-0", "event=timeout", name="host_timeout")


# ----------------------------------------------------------------------
# 3. Hop-limit enforcement
# ----------------------------------------------------------------------
def test_s3_hop_limit():
    """Routers 1 and 2 point host 5's traffic at each other: a forwarding loop."""
    edits = merge_edits(UPPER, forwarding(1, {5: 2, 0: 0}), forwarding(2, {5: 1, 0: 1}))
    with scenario("s3_hop_limit", edits=edits) as net:
        a = net.h[0]
        a.send_probe(5, "loop", ttl=8, timeout=1.0)
        assert wait_for_probes(a, limit=4.0)
        assert a.probe_results()[0].status == "timeout"
        assert net.r[1].ttl_expired_drops == 1
        assert net.r[2].ttl_expired_drops == 0
        out = net.out

    # TTL 8 arrives at router 1, 7 at router 2, ... down to TTL 0 at router 1.
    assert len(device_events(out, "router-1", "event=receive", "type=echo")) == 5
    assert len(device_events(out, "router-2", "event=receive", "type=echo")) == 4
    assert len(grep_log(out, "reason=ttl_expired")) == 1
    assert device_events(out, "router-3", "event=receive") == []
    assert device_events(out, "router-4", "event=receive") == []

    grep_log(out, "type=echo", "event=receive", name="loop_trace")
    grep_log(out, "reason=ttl_expired", name="ttl_drop")
    device_events(out, "host-0", "event=timeout", name="host_timeout")


# ----------------------------------------------------------------------
# 4. Error handling
# ----------------------------------------------------------------------
def test_s4_errors():
    with scenario("s4_errors", edits=UPPER) as net:
        a = net.h[0]

        # Unknown destination, which is also the "missing response" case.
        a.send_probe(9, "nowhere", timeout=0.5)

        # Invalid packets: one shorter than the header, one over 1200 bytes.
        send_raw(b"\x01\x02", BASE_PORT + 1)
        send_raw(b"\x00" * 1300, BASE_PORT + 1)
        send_raw(b"\x01\x02", BASE_PORT + 5)  # same fault, aimed at a host

        assert wait_until(lambda: net.r[1].malformed_drops == 2
                          and net.h[5].malformed_drops == 1)
        assert wait_for_probes(a, limit=3.0)
        assert a.probe_results()[0].status == "timeout"
        assert net.r[1].no_route_drops == 1

        # Everything is still running: a valid probe now succeeds.
        a.send_probe(5, "still-alive")
        assert wait_for_probes(a, limit=3.0)
        assert len(a.rtt_samples()) == 1
        out = net.out

    assert len(device_events(out, "router-1", "reason=no_route")) == 1
    assert len(device_events(out, "router-1", "reason=malformed_too_short")) == 1
    assert len(device_events(out, "router-1", "reason=malformed_oversized")) == 1
    assert len(device_events(out, "host-5", "reason=malformed_too_short")) == 1
    assert len(device_events(out, "host-0", "reason=probe_timeout")) == 1

    grep_log(out, "event=drop", name="drops")
    device_events(out, "host-0", "event=timeout", name="host_timeout")
    device_events(out, "host-5", "payload=still-alive", name="recovery")


# ----------------------------------------------------------------------
# 5. Loss and delay (deterministic parts)
# ----------------------------------------------------------------------
def test_s5a_forced_drop():
    with scenario("s5a_forced_drop", edits=UPPER) as net:
        a = net.h[0]
        # Drop only the first echo that router 2 tries to send toward host 5.
        net.r[2].drop_once(lambda h: bool(h["type_flags"] & 1) and h["destination"] == 5)
        send_probes(a, 5, 3, "A-probe", interval=0.05, timeout=1.0)
        assert wait_for_probes(a, limit=4.0)
        statuses = sorted(r.status for r in a.probe_results())
        assert statuses == ["success", "success", "timeout"]
        assert net.r[2].forced_drops == 1
        assert sum(r.loss_drops for r in net.r.values()) == 0
        out = net.out

    assert len(device_events(out, "router-2", "reason=forced_drop")) == 1
    assert not grep_log(out, "reason=injected_loss")
    assert len(device_events(out, "host-0", "reason=probe_timeout")) == 1

    grep_log(out, "reason=forced_drop", name="forced_drop")
    device_events(out, "host-0", "event=timeout", name="host_timeout")


def test_s5b_late_reply():
    """A reply that arrives after its probe timed out is not an injected drop."""
    with scenario("s5b_late_reply", edits=UPPER) as net:
        a = net.h[0]
        a.send_probe(5, "late", timeout=0.05)  # shorter than the ~0.12 s round trip
        assert wait_until(lambda: count_events(
            net.out, "| netlog.host-0 |", "reason=late_after_timeout") >= 1)
        assert a.probe_results()[0].status == "timeout"
        out = net.out

    host_lines = device_events(out, "host-0")
    idx_timeout = next(i for i, ln in enumerate(host_lines) if "reason=probe_timeout" in ln)
    idx_receive = next(i for i, ln in enumerate(host_lines)
                       if "event=receive" in ln and "type=reply" in ln)
    idx_late = next(i for i, ln in enumerate(host_lines) if "reason=late_after_timeout" in ln)
    assert idx_timeout < idx_receive < idx_late

    # No router dropped anything: the reply was only late.
    assert count_events(out, "| netlog.router-", "event=drop") == 0

    device_events(out, "host-0", name="host0_full")
    grep_log(out, "reason=late_after_timeout", name="late_reply")
