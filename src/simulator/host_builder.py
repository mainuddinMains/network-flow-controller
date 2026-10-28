"""Build Host instances from INI configuration files."""

import configparser
from pathlib import Path

from src.simulator.host import DEFAULT_PROBE_TIMEOUT_S, Host


def build_host_from_ini(file_path: Path) -> Host:
    """Load host settings from ``file_path`` and return a configured Host.

    Optional ``host.probe_timeout`` (seconds) overrides the default probe
    response timeout when present.
    """
    print()

    reader = configparser.ConfigParser()
    reader.read(file_path)

    probe_timeout = DEFAULT_PROBE_TIMEOUT_S
    if reader.has_option("host", "probe_timeout"):
        probe_timeout = float(reader["host"]["probe_timeout"])

    return Host(
        IP_ADDRESS=reader["metadata"]["HOST"],
        PORT=int(reader["metadata"]["PORT"]),
        virtual_address=int(reader["host"]["virtual_address"]),
        entry_router_address=int(reader["routing"]["entry"]),
        probe_timeout=probe_timeout,
    )
