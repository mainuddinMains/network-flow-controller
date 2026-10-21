"""Build Router instances from INI configuration files."""

import configparser
from pathlib import Path

from src.simulator.router import Router


def _parse_int_list(value: str) -> list[int]:
    """Split a multi-line or space-separated config value into integers."""
    return [int(part) for part in value.split() if part]


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
    )
