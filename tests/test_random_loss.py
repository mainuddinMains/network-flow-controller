"""Random-loss experiment (scenario 5c). Kept apart from the deterministic tests.

Loss is drawn from seeded generators; the seeds are recorded in
``evidence/<scenario>/meta.json`` and in the routers' ``seed`` log events.
"""

from harness import (
    UPPER, count_events, device_events, grep_log, link_settings, merge_edits,
    scenario, send_probes, wait_for_probes,
)

SEED = 101
PROBES = 50


def test_s5c_random_loss_and_delay():
    # Router 2: 10% loss on both of its links, 20 ms toward router 1 and
    # 60 ms toward router 4 (neighbors are [1, 4]).
    edits = merge_edits(UPPER, link_settings(2, loss=0.10, delays_ms=[20, 60]))
    with scenario("s5c_random_loss", edits=edits, seed=SEED) as net:
        a = net.h[0]
        send_probes(a, 5, PROBES, "A-probe", interval=0.02, timeout=1.0)
        assert wait_for_probes(a, limit=5.0)
        successes = len(a.rtt_samples())
        timeouts = sum(r.status == "timeout" for r in a.probe_results())
        drops = sum(r.loss_drops for r in net.r.values())
        out = net.out

    # Every probe resolves, and every lost packet costs exactly one probe.
    assert successes + timeouts == PROBES
    assert drops >= 1
    assert timeouts == drops
    assert count_events(out, "reason=injected_loss") == drops
    # Replies were never merely late: the timeout is far above the round trip.
    assert count_events(out, "reason=late_after_timeout") == 0
    # The configured delays were applied.
    assert device_events(out, "router-2", "event=delay", "next_hop=4", "delay_s=0.06")

    grep_log(out, "reason=injected_loss", name="injected_loss")
    device_events(out, "router-2", "event=delay", name="router2_delay")
    device_events(out, "host-0", "event=timeout", name="host_timeout")
