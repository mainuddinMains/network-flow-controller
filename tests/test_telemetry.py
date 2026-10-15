"""Unit tests for src/telemetry/."""

import pytest

from src.telemetry import jains_fairness


def test_telemetry_module_imports():
    """Verify the telemetry module is importable."""
    import src.telemetry  # noqa: F401


def test_jains_fairness_equal_throughputs_is_one():
    """Equal throughputs are perfectly fair."""
    assert jains_fairness([5.0, 5.0, 5.0, 5.0]) == pytest.approx(1.0)


def test_jains_fairness_unequal_throughputs_below_one():
    """Unequal throughputs score below 1.0."""
    assert jains_fairness([1.0, 2.0, 3.0]) == pytest.approx(36 / 42)


def test_jains_fairness_one_flow_hogging_is_one_over_n():
    """A single active flow among n yields the minimum index 1/n."""
    assert jains_fairness([10.0, 0.0, 0.0, 0.0]) == pytest.approx(0.25)


def test_jains_fairness_single_flow_is_one():
    """A single flow is trivially fair."""
    assert jains_fairness([7.5]) == pytest.approx(1.0)


def test_jains_fairness_all_zero_is_one():
    """All-zero throughputs are treated as equal shares."""
    assert jains_fairness([0.0, 0.0, 0.0]) == 1.0


def test_jains_fairness_empty_raises():
    """An empty flow list is rejected."""
    with pytest.raises(ValueError):
        jains_fairness([])


def test_jains_fairness_negative_raises():
    """Negative throughput is rejected."""
    with pytest.raises(ValueError):
        jains_fairness([1.0, -1.0])
