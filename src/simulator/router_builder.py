import configparser
from pathlib import Path

from src.simulator.router import Router


def build_router_from_ini(file_path: Path) -> Router:
    print()

    reader = configparser.ConfigParser()

    reader.read(file_path)

    # Pre-parsing of multi-line entries
    # Neighbor list
    neighbor_list = [
        int(neighbor.strip())
        for neighbor in reader['routing']['neighbors']
        if neighbor.strip()
    ]

    # Forwarding table
    destinations = [
        int(dest.strip())
        for dest in reader['routing']['destinations']
        if dest.strip()
    ]
    local_forwards = [
        int(dest.strip())
        for dest in reader['routing']['local_forwarding']
        if dest.strip()
    ]
    forwarding_table = dict(zip(destinations, local_forwards))

    built_link = Router(
        IP_ADDRESS=reader['metadata']['HOST'],
        PORT=int(reader['metadata']['PORT']),
        virtual_address=int(reader['host']['virtual_address']),
        bandwidth=float(reader['host']['bandwidth']),
        buffer_size=int(reader['host']['buffer_size']),
        neighbors=neighbor_list,
        forwarding_table=forwarding_table,
    )

    return built_link
