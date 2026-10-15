"""Fairness metrics for concurrent flows."""


def jains_fairness(throughputs: list[float]) -> float:
    """Return Jain's fairness index for a set of per-flow throughputs.

    The index is (sum(x))^2 / (n * sum(x^2)). It equals 1.0 when every flow
    receives the same throughput and approaches 1/n as one flow dominates.
    All-zero input is treated as perfectly fair and returns 1.0.

    :raises ValueError: if ``throughputs`` is empty or contains a negative value.
    """
    if not throughputs:
        raise ValueError("throughputs must contain at least one flow")
    if any(x < 0 for x in throughputs):
        raise ValueError("throughputs must be non-negative")
    sum_sq = sum(x * x for x in throughputs)
    if sum_sq == 0:
        return 1.0
    total = sum(throughputs)
    return (total * total) / (len(throughputs) * sum_sq)
