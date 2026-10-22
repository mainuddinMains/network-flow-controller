from harness import (scenario, UPPER, send_probes, wait_for_probes,
                     grep_log, count_events)

def test_s1_multihop():
    with scenario("s1_multihop", edits=UPPER) as net:
        a, b = net.h[0], net.h[5]
        send_probes(a, 5, 20, "A-probe")
        send_probes(b, 0, 20, "B-probe")
        assert wait_for_probes(a, b)
        assert len(a.rtt_samples()) == 20
        assert len(b.rtt_samples()) == 20
        out = net.out

    # Symmetric upper path: router 3 must never see a packet.
    assert count_events(out, "netlog.router-3", "event=receive") == 0
    assert count_events(out, "netlog.router-2", "event=receive") > 0

    # Traced exchange for the report.
    grep_log(out, "id=7", "type=echo", "src=0", "dest=5", name="trace_echo")
    grep_log(out, "id=7", "type=reply", "src=5", "dest=0", name="trace_reply")