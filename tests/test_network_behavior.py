"""Packet serialization, loss, reproducibility, and observer boundary checks."""

import pytest

from src.algorithm import AIMDController
from src.simulator import Link, Packet, Scheduler, Simulation, TrafficGenerator
from src.telemetry import MetricsRecorder


def test_link_serializes_and_tail_drops():
    """Verify FIFO service time, finite capacity, and space after departure."""
    link = Link(bandwidth=8000, propagation_delay=0.1, buffer_size=2)
    first = link.enqueue(Packet(0, 0, 0, 1000), 0)
    second = link.enqueue(Packet(1, 0, 0, 1000), 0)
    assert first.departure_at == 1
    assert second.service_start == 1
    assert second.departure_at == 2
    assert link.enqueue(Packet(2, 0, 0, 1000), 0) is None
    assert link.dequeue(1) == (first,)
    assert link.queue_depth(1) == 1
    assert link.enqueue(Packet(2, 0, 1, 1000), 1).departure_at == 3


def test_ecn_marks_only_above_threshold():
    """Expose ECN when occupancy exceeds half of the configured capacity."""
    link = Link(bandwidth=8000, buffer_size=4)
    marks = [link.enqueue(Packet(0, index, 0, 1000), 0).ecn for index in range(4)]
    assert marks == [False, False, True, True]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bandwidth": 0},
        {"bandwidth": float("inf")},
        {"propagation_delay": -1},
        {"buffer_size": 0},
        {"ecn_threshold": 1.1},
    ],
)
def test_invalid_link_configuration_is_rejected(kwargs):
    """Reject physical configurations with undefined service or queue sizes."""
    with pytest.raises(ValueError):
        Link(**kwargs)


def test_link_cannot_go_backwards_in_time():
    """Prevent later queue observations from being silently undone."""
    link = Link()
    link.queue_depth(1)
    with pytest.raises(ValueError):
        link.dequeue(0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"size": 0},
        {"sequence": -1},
        {"sent_at": float("nan")},
    ],
)
def test_invalid_packets_are_rejected(kwargs):
    """Reject malformed packet metadata before enqueueing."""
    values = {"flow_id": 0, "sequence": 0, "sent_at": 0}
    values.update(kwargs)
    with pytest.raises(ValueError):
        Packet(**values)


def test_scheduler_records_drops_and_ecn_without_mutable_log_entries():
    """Retain timestamped events while protecting them from observer mutation."""
    scheduler = Scheduler(Link(buffer_size=1, ecn_threshold=0))
    assert scheduler.submit(Packet(0, 0, 0), 0)
    assert not scheduler.submit(Packet(1, 0, 0), 0)
    assert [event["kind"] for event in scheduler.event_log] == ["ecn", "drop"]
    assert scheduler.event_log[-1]["reason"] == "buffer"
    with pytest.raises(TypeError):
        scheduler.event_log[-1]["time"] = 100
    with pytest.raises(ValueError):
        scheduler.submit(Packet(0, 1, 0), -1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"flows": 0},
        {"packet_size": 0},
        {"cross_traffic_rate": -1},
        {"flows": 2, "start_times": [0]},
        {"start_times": [0, 0, 0, 0, -1]},
    ],
)
def test_invalid_traffic_configuration_is_rejected(kwargs):
    """Reject invalid flow counts, schedules, packet sizes, and rates."""
    with pytest.raises(ValueError):
        TrafficGenerator(**kwargs)


def test_flow_controllers_are_independent():
    """Ensure a factory cannot accidentally share congestion state across flows."""
    traffic = TrafficGenerator(3)
    assert len({id(flow.controller) for flow in traffic.flows.values()}) == 3
    shared = AIMDController()
    with pytest.raises(ValueError):
        TrafficGenerator(2, controller_factory=lambda: shared)
    with pytest.raises(ValueError):
        TrafficGenerator(controller_factory=lambda: None)
    with pytest.raises(ValueError):
        TrafficGenerator(controller_factory=lambda: AIMDController(mss=1000))


def test_simulation_delivers_immutable_per_round_feedback():
    """Let controllers read congestion signals while preventing feedback edits."""
    seen = []

    class CheckingController(AIMDController):
        """Check the simulator's feedback boundary before normal control."""

        def step(self, feedback):
            """Assert that required feedback is present and cannot be changed."""
            assert {"rtt", "loss", "ecn", "bytes_acked"} <= feedback.keys()
            with pytest.raises(TypeError):
                feedback["loss"] = True
            seen.append(dict(feedback))
            return super().step(feedback)

    Simulation(Link(), TrafficGenerator(1, CheckingController)).run(0.3)
    assert len(seen) > 1
    assert any(feedback["bytes_acked"] > 0 for feedback in seen)


def test_seeded_loss_and_jitter_are_reproducible():
    """Repeat the same experiment exactly without using global random state."""

    def run(seed):
        simulation = Simulation(
            Link(buffer_size=10),
            TrafficGenerator(3),
            seed=seed,
            loss_probability=0.05,
            jitter=0.001,
        )
        return simulation.run(1).history, simulation.scheduler.event_log

    assert run(42) == run(42)
    assert run(42) != run(43)


def test_staggered_and_inactive_flows_have_correct_activation_times():
    """Prevent a future flow from sending or producing earlier observations."""
    traffic = TrafficGenerator(3, start_times=[0, 0.5, 2])
    recorder = Simulation(Link(), traffic).run(1)
    assert all(row["time"] >= 0.5 for row in recorder.history if row["flow_id"] == 1)
    assert any(
        row["bytes_acked"] > 0 for row in recorder.history if row["flow_id"] == 1
    )
    assert traffic.flows[2].next_sequence == 0
    assert recorder.summary()["flows"][2]["bytes_acked"] == 0


def test_retries_do_not_count_payload_twice_and_capacity_is_respected():
    """Bound delivered bytes by unique payloads and physical link capacity."""
    traffic = TrafficGenerator(3)
    simulation = Simulation(
        Link(bandwidth=1_000_000, buffer_size=5),
        traffic,
        loss_probability=0.05,
        seed=12,
    )
    summary = simulation.run(2).summary()
    assert summary["loss_rate_percent"] > 0
    assert sum(flow["bytes_acked"] for flow in summary["flows"].values()) <= 250_000
    for flow_id, metrics in summary["flows"].items():
        assert metrics["bytes_acked"] <= traffic.flows[flow_id].next_sequence * 1500


def test_background_traffic_consumes_capacity():
    """Compete for the same link without appearing as a controlled data flow."""
    baseline = Simulation(Link(bandwidth=1_000_000), TrafficGenerator(2)).run(2)
    background = Simulation(
        Link(bandwidth=1_000_000),
        TrafficGenerator(2, cross_traffic_rate=100_000),
    ).run(2)
    assert len(background.summary()["flows"]) == 2
    assert (
        background.summary()["total_throughput_bytes_per_second"]
        < baseline.summary()["total_throughput_bytes_per_second"]
    )


def test_observer_does_not_change_the_simulation():
    """Use an alternate recorder without changing packet or controller results."""

    class CountingRecorder(MetricsRecorder):
        """Count observations without accessing mutable simulator state."""

        def __init__(self):
            """Initialize the observation count."""
            super().__init__()
            self.calls = 0

        def record(self, *args, **kwargs):
            """Delegate metric computation and count the call."""
            self.calls += 1
            super().record(*args, **kwargs)

    baseline = Simulation(Link(), TrafficGenerator(2), seed=7).run(0.5)
    observer = CountingRecorder()
    observed = Simulation(Link(), TrafficGenerator(2), recorder=observer, seed=7).run(
        0.5
    )
    assert observed is observer
    assert observer.calls > 0
    assert baseline.history == observed.history


def test_total_loss_and_invalid_repeated_runs_are_handled():
    """Avoid undefined metrics when every packet drops and reject reused state."""
    simulation = Simulation(Link(), TrafficGenerator(2), loss_probability=1)
    summary = simulation.run(0.1).summary()
    assert summary["fairness"] == 0
    assert summary["loss_rate_percent"] == 100
    with pytest.raises(ValueError):
        simulation.run(0.1)
    with pytest.raises(ValueError):
        Simulation(Link(), TrafficGenerator()).run(0)
    with pytest.raises(ValueError):
        Scheduler(Link(), jitter=-1)
    with pytest.raises(ValueError):
        Scheduler(Link(), loss_probability=2)


def test_feedback_waits_for_ack_return_propagation():
    """Do not expose receiver progress before the ACK has reached the sender."""
    observations = []

    class ObservingController(AIMDController):
        """Capture feedback for a path with a long return propagation delay."""

        def step(self, feedback):
            """Record progress signals, then apply the baseline policy."""
            observations.append(dict(feedback))
            return super().step(feedback)

    traffic = TrafficGenerator(1, lambda: ObservingController(initial_rtt=1))
    Simulation(Link(propagation_delay=0.6), traffic).run(2.1)
    assert observations[0]["bytes_acked"] == 0
    assert not observations[0]["ack_advanced"]
    assert observations[1]["ack_advanced"]


def test_ambiguous_original_ack_is_not_an_rtt_sample():
    """Exclude original ACK timing after the same sequence was retransmitted."""
    controller = AIMDController(cwnd=1, initial_rtt=1.2)
    traffic = TrafficGenerator(1, lambda: controller)
    recorder = Simulation(Link(propagation_delay=0.6), traffic).run(2.5)
    assert traffic.flows[0].retransmitted
    assert recorder.summary()["flows"][0]["bytes_acked"] > 0
    assert controller.srtt is None
