"""Build Router instances from INI configuration files."""

import configparser
from pathlib import Path

from src.simulator.router import Router


def _parse_int_list(value: str) -> list[int]:
    """Split a multi-line or space-separated config value into integers."""
    return [int(part) for part in value.split() if part]


def _parse_float_list(value: str) -> list[float]:
    """Split a multi-line or space-separated config value into floats."""
    return [float(part) for part in value.split() if part]


def build_router_from_ini(file_path: Path) -> Router:
    """Load router settings from ``file_path`` and return a configured Router.

    Delay values in the INI ``routing.delays`` field are milliseconds and are
    converted to seconds for ``Router``.
    """
    print()

    reader = configparser.ConfigParser()
    reader.read(file_path)

    neighbor_list = _parse_int_list(reader["routing"]["neighbors"])
    destinations = _parse_int_list(reader["routing"]["destinations"])
    local_forwards = _parse_int_list(reader["routing"]["local_forwarding"])
    forwarding_table = dict(zip(destinations, local_forwards))

    delay_ms = _parse_int_list(reader["routing"]["delays"])
    if len(delay_ms) != len(neighbor_list):
        raise ValueError(
            "routing.delays must provide one millisecond value per neighbor"
        )
    delays = {
        neighbor: milliseconds / 1000.0
        for neighbor, milliseconds in zip(neighbor_list, delay_ms)
    }

    # Optional per-link loss overrides: one probability per neighbor, in the
    # same order as ``routing.neighbors``. Omitted means the router-wide
    # ``loss.loss_chance`` applies to every link.
    link_loss: dict[int, float] = {}
    raw_link_loss = reader["loss"].get("link_loss")
    if raw_link_loss:
        link_loss_values = _parse_float_list(raw_link_loss)
        if len(link_loss_values) != len(neighbor_list):
            raise ValueError(
                "loss.link_loss must provide one probability per neighbor"
            )
        link_loss = dict(zip(neighbor_list, link_loss_values))

    # Optional seed for reproducible loss decisions. Omitted means the router
    # generates one and logs it at startup.
    raw_seed = reader["loss"].get("seed")
    seed = int(raw_seed) if raw_seed else None

    return Router(
        IP_ADDRESS=reader["metadata"]["HOST"],
        PORT=int(reader["metadata"]["PORT"]),
        virtual_address=int(reader["host"]["virtual_address"]),
        bandwidth=float(reader["host"]["bandwidth"]),
        buffer_size=int(reader["host"]["buffer_size"]),
        neighbors=neighbor_list,
        forwarding_table=forwarding_table,
        delays=delays,
        loss=float(reader["loss"]["loss_chance"]),
        link_loss=link_loss,
        seed=seed,
    )
