"""Window control with per-round feedback and RFC 6298 RTT estimation."""

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import Enum


class State(str, Enum):
    """Congestion control states exposed to observers."""

    SLOW_START = "SLOW_START"
    CONGESTION_AVOIDANCE = "CONGESTION_AVOIDANCE"
    FAST_RECOVERY = "FAST_RECOVERY"


class CongestionController(ABC):
    """Maintain a window in packets and return a sending rate in bytes/second.

    Feedback contains RTT in seconds, loss and ECN flags, and acknowledged bytes.
    Optional duplicate_acks, recovery_complete, and timeout fields describe
    recovery signals. The controller never changes the supplied mapping.
    """

    def __init__(
        self,
        cwnd: float = 10.0,
        ssthresh: float = 64.0,
        initial_rtt: float = 0.04,
        mss: int = 1500,
    ) -> None:
        """Initialize a positive RTT, packet size, and window of at least one."""
        for name, value in (("cwnd", cwnd), ("ssthresh", ssthresh)):
            if not math.isfinite(value) or value < 1:
                raise ValueError(f"{name} must be finite and at least one packet")
        if not math.isfinite(initial_rtt) or initial_rtt <= 0:
            raise ValueError("initial_rtt must be finite and positive")
        if not isinstance(mss, int) or isinstance(mss, bool) or mss <= 0:
            raise ValueError("mss must be a positive integer")
        self.cwnd = float(cwnd)
        self.ssthresh = float(ssthresh)
        self.initial_rtt = initial_rtt
        self.mss = mss
        self.srtt: float | None = None
        self.rttvar: float | None = None
        self.state = State.CONGESTION_AVOIDANCE
        self.transitions: list[tuple[State, State]] = []

    @property
    def rtt_estimate(self) -> float:
        """Return the smoothed RTT, or the initial estimate before a sample."""
        return self.initial_rtt if self.srtt is None else self.srtt

    @property
    def send_rate(self) -> float:
        """Return the window-paced sending rate in bytes per second."""
        return self.cwnd * self.mss / self.rtt_estimate

    @property
    def retransmission_timeout(self) -> float:
        """Return the RFC 6298 timeout with a one-second lower bound."""
        if self.srtt is None or self.rttvar is None:
            return 1.0
        return max(1.0, self.srtt + max(0.001, 4 * self.rttvar))

    def update_rtt(self, sample: float) -> None:
        """Update deviation before SRTT using beta=1/4 and alpha=1/8."""
        if not math.isfinite(sample) or sample <= 0:
            raise ValueError("RTT samples must be finite and positive")
        if self.srtt is None:
            self.srtt = sample
            self.rttvar = sample / 2
        else:
            assert self.rttvar is not None
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - sample)
            self.srtt = 0.875 * self.srtt + 0.125 * sample

    def _transition(self, state: State) -> None:
        if state != self.state:
            self.transitions.append((self.state, state))
            self.state = state

    @abstractmethod
    def step(self, feedback: Mapping[str, object]) -> float:
        """Apply one round of feedback and return a sending rate."""


class AIMDController(CongestionController):
    """Increase by one packet per successful RTT and halve on congestion."""

    def step(self, feedback: Mapping[str, object]) -> float:
        """React once to aggregated loss/ECN, respecting the one-packet floor."""
        sample = feedback.get("rtt")
        if sample is not None and feedback.get("rtt_sample_valid", True):
            self.update_rtt(float(sample))
        if feedback.get("loss") or feedback.get("ecn"):
            self.cwnd = max(1.0, self.cwnd / 2)
            self.ssthresh = max(2.0, self.cwnd)
        elif int(feedback.get("bytes_acked", self.mss)) > 0:
            self.cwnd += 1
        return self.send_rate


class RenoController(CongestionController):
    """Add slow start, fast recovery, and timeout response to AIMD.

    This is a per-RTT educational model. Duplicate ACK counts are new signals
    since the previous feedback, and recovery_complete explicitly ends recovery.
    """

    def __init__(
        self,
        cwnd: float = 10.0,
        ssthresh: float = 64.0,
        initial_rtt: float = 0.04,
        mss: int = 1500,
    ) -> None:
        """Start in slow start unless the initial window reaches its threshold."""
        super().__init__(cwnd, ssthresh, initial_rtt, mss)
        self._duplicate_acks = 0
        if self.cwnd < self.ssthresh:
            self.state = State.SLOW_START

    def step(self, feedback: Mapping[str, object]) -> float:
        """Apply slow start, congestion avoidance, or recovery signals."""
        sample = feedback.get("rtt")
        if sample is not None and feedback.get("rtt_sample_valid", True):
            self.update_rtt(float(sample))
        duplicate_acks = int(feedback.get("duplicate_acks", 0))
        if duplicate_acks < 0:
            raise ValueError("duplicate_acks cannot be negative")
        if feedback.get("ack_advanced") or (
            not duplicate_acks and int(feedback.get("bytes_acked", 0)) > 0
        ):
            self._duplicate_acks = 0
        self._duplicate_acks += duplicate_acks
        if feedback.get("timeout"):
            self.ssthresh = max(2.0, self.cwnd / 2)
            self.cwnd = 1.0
            self._duplicate_acks = 0
            self._transition(State.SLOW_START)
        elif self.state == State.FAST_RECOVERY:
            if feedback.get("recovery_complete"):
                self.cwnd = self.ssthresh
                self._duplicate_acks = 0
                self._transition(State.CONGESTION_AVOIDANCE)
            else:
                self.cwnd += duplicate_acks
        elif self._duplicate_acks >= 3:
            self.ssthresh = max(2.0, self.cwnd / 2)
            self.cwnd = self.ssthresh + self._duplicate_acks
            self._transition(State.FAST_RECOVERY)
        elif feedback.get("loss") or feedback.get("ecn"):
            self.cwnd = max(1.0, self.cwnd / 2)
            self.ssthresh = max(2.0, self.cwnd)
            self._transition(State.CONGESTION_AVOIDANCE)
        elif int(feedback.get("bytes_acked", self.mss)) > 0:
            if self.state == State.SLOW_START:
                self.cwnd = min(self.ssthresh, 2 * self.cwnd)
                if self.cwnd >= self.ssthresh:
                    self._transition(State.CONGESTION_AVOIDANCE)
            else:
                self.cwnd += 1
        return self.send_rate
