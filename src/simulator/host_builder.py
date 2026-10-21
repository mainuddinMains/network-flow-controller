import configparser
from pathlib import Path

from src.simulator.host import Host


def build_host_from_ini(file_path: Path) -> Host:
    print()

    reader = configparser.ConfigParser()

    reader.read(file_path)

    built_link = Host(
        IP_ADDRESS=reader['metadata']['HOST'],
        PORT=int(reader['metadata']['PORT']),
        virtual_address=int(reader['host']['virtual_address']),
        entry_router_address=int(reader['routing']['entry']),
    )

    return built_link
