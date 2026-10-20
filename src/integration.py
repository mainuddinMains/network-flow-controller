"""Orchestration contracts for the teammate-owned simulator and telemetry."""

import importlib
import math
from collections.abc import Callable
from dataclasses import dataclass


class IntegrationPendingError(RuntimeError):
    """Signal that required teammate interfaces have not been delivered yet."""


@dataclass(frozen=True)
class ModuleAPI:
    """Factory interfaces used by orchestration, without their implementation."""

    link: Callable[..., object]
    traffic_generator: Callable[..., object]
    scheduler: Callable[..., object]
    recorder: Callable[..., object]
    logger: Callable[..., object]


_REQUIRED = {
    "src.simulator": ("Link", "TrafficGenerator", "Scheduler"),
    "src.telemetry": ("MetricsRecorder", "ExperimentLogger"),
}


def missing_dependencies() -> tuple[str, ...]:
    """List required public APIs that the teammate packages do not expose."""
    missing = []
    for module_name, names in _REQUIRED.items():
        module = importlib.import_module(module_name)
        missing.extend(
            f"{module_name}.{name}" for name in names if not hasattr(module, name)
        )
    return tuple(missing)


def load_module_api() -> ModuleAPI:
    """Load collaborator factories, or report that integration is pending."""
    missing = missing_dependencies()
    if missing:
        raise IntegrationPendingError(
            "Awaiting teammate implementations: " + ", ".join(missing)
        )
    simulator = importlib.import_module("src.simulator")
    telemetry = importlib.import_module("src.telemetry")
    return ModuleAPI(
        simulator.Link,
        simulator.TrafficGenerator,
        simulator.Scheduler,
        telemetry.MetricsRecorder,
        telemetry.ExperimentLogger,
    )


class Simulation:
    """Connect collaborator factories without implementing packets or metrics."""

    def __init__(
        self,
        link: object,
        traffic: object,
        recorder: object | None = None,
        seed: int = 0,
        loss_probability: float = 0.0,
        jitter: float = 0.0,
    ) -> None:
        """Delegate scheduling and observations to the teammate implementations."""
        api = load_module_api()
        self.traffic = traffic
        self.recorder = recorder if recorder is not None else api.recorder()
        self.scheduler = api.scheduler(link, seed, loss_probability, jitter)

    def run(self, duration: float = 60.0) -> object:
        """Run the supplied scheduler and return its observing recorder."""
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("duration must be finite and positive")
        self.recorder = self.scheduler.run(self.traffic, duration, self.recorder)
        return self.recorder
