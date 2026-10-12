"""Behavioral checks for congestion response, RTT estimates, and recovery."""

from types import MappingProxyType

import pytest

from src.algorithm import AIMDController, CongestionController, RenoController, State


def test_base_controller_is_abstract():
    """Prevent a base controller with no congestion policy from being used."""
    with pytest.raises(TypeError):
        CongestionController()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cwnd": 0},
        {"cwnd": float("inf")},
        {"ssthresh": -1},
        {"initial_rtt": 0},
        {"initial_rtt": float("nan")},
        {"mss": 0},
    ],
)
def test_invalid_initial_state_is_rejected(kwargs):
    """Reject invalid windows, packet sizes, and RTTs before sending."""
    with pytest.raises(ValueError):
        AIMDController(**kwargs)


def test_aimd_grows_and_cuts_once_per_round():
    """Grow one packet per successful round and halve once for loss plus ECN."""
    controller = AIMDController(cwnd=10, initial_rtt=0.1)
    for _ in range(5):
        rate = controller.step({"rtt": 0.1, "bytes_acked": 1500})
    assert controller.cwnd == 15
    assert rate == 225_000
    controller.step({"rtt": 0.1, "loss": True, "ecn": True})
    assert controller.cwnd == 7.5
    for _ in range(10):
        controller.step({"loss": True})
    assert controller.cwnd == 1


def test_no_acknowledgments_do_not_grow_window():
    """Avoid increasing the window while a flow makes no progress."""
    controller = AIMDController()
    controller.step({"bytes_acked": 0})
    assert controller.cwnd == 10


def test_feedback_is_not_modified():
    """Accept immutable feedback and preserve ordinary caller dictionaries."""
    feedback = {"rtt": 0.1, "loss": False, "ecn": False, "bytes_acked": 1500}
    original = feedback.copy()
    AIMDController().step(MappingProxyType(feedback))
    RenoController().step(feedback)
    assert feedback == original


def test_rtt_estimator_uses_previous_srtt_for_deviation():
    """Check first-sample initialization and the RFC 6298 update order."""
    controller = AIMDController()
    assert controller.retransmission_timeout == 1
    controller.update_rtt(0.1)
    assert controller.srtt == 0.1
    assert controller.rttvar == 0.05
    controller.update_rtt(0.2)
    assert controller.srtt == pytest.approx(0.1125)
    assert controller.rttvar == pytest.approx(0.0625)
    assert controller.retransmission_timeout == 1
    for _ in range(100):
        controller.update_rtt(0.2)
    assert controller.rtt_estimate == pytest.approx(0.2, abs=1e-6)


def test_missing_rtt_sample_does_not_update_estimator():
    """Do not turn fallback RTTs into fabricated measurements."""
    controller = AIMDController()
    controller.step({"rtt": 0.2, "rtt_sample_valid": False, "bytes_acked": 0})
    assert controller.srtt is None
    assert controller.rtt_estimate == 0.04


@pytest.mark.parametrize("sample", [0, -1, float("nan"), float("inf")])
def test_invalid_rtt_samples_are_rejected(sample):
    """Avoid undefined rates and estimates for invalid samples."""
    with pytest.raises(ValueError):
        AIMDController().update_rtt(sample)


def test_reno_slow_start_then_congestion_avoidance():
    """Double per RTT until the threshold, then grow by one packet."""
    controller = RenoController(cwnd=2, ssthresh=8)
    assert controller.state == State.SLOW_START
    controller.step({"bytes_acked": 1500})
    assert controller.cwnd == 4
    controller.step({"bytes_acked": 1500})
    assert controller.cwnd == 8
    assert controller.state == State.CONGESTION_AVOIDANCE
    controller.step({"bytes_acked": 1500})
    assert controller.cwnd == 9
    assert controller.transitions == [(State.SLOW_START, State.CONGESTION_AVOIDANCE)]


def test_reno_fast_recovery_waits_for_explicit_completion():
    """Recognize three duplicate ACKs across rounds and deflate after recovery."""
    controller = RenoController(cwnd=20, ssthresh=10)
    for _ in range(3):
        controller.step({"duplicate_acks": 1, "bytes_acked": 0})
    assert controller.state == State.FAST_RECOVERY
    assert controller.ssthresh == 10
    assert controller.cwnd == 13
    controller.step({"duplicate_acks": 2, "bytes_acked": 0})
    assert controller.cwnd == 15
    controller.step({"bytes_acked": 1500, "recovery_complete": True})
    assert controller.state == State.CONGESTION_AVOIDANCE
    assert controller.cwnd == 10


def test_reno_timeout_restarts_slow_start():
    """Reduce to one packet after timeout rather than entering fast recovery."""
    controller = RenoController(cwnd=20, ssthresh=10)
    controller.step({"timeout": True, "loss": True, "duplicate_acks": 3})
    assert controller.cwnd == 1
    assert controller.ssthresh == 10
    assert controller.state == State.SLOW_START


def test_reno_congestion_without_duplicate_acks_halves_window():
    """Handle ECN and explicit loss when fast recovery signals are unavailable."""
    controller = RenoController(cwnd=20)
    controller.step({"loss": True})
    assert controller.cwnd == 10
    assert controller.state == State.CONGESTION_AVOIDANCE
    controller.step({"ecn": True})
    assert controller.cwnd == 5
    with pytest.raises(ValueError):
        controller.step({"duplicate_acks": -1})
