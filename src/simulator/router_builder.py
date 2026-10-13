import configparser
from pathlib import Path
from src.simulator.router import Router


def build_router_from_ini(file_path : Path) -> Router:
    reader = configparser.ConfigParser()

    reader.read(file_path)

    # Pre-parsing of multi-line entries
    # Neighbor list
    neighbor_list = [neighbor.strip() for neighbor in reader['routing']['neighbors'] if neighbor.strip()]

    # Forwarding table
    destinations = [dest.strip() for dest in reader['routing']['destinations'] if dest.strip()]
    local_forwards=[dest.strip() for dest in reader['routing']['local_forwarding'] if dest.strip()]
    forwarding_table = dict(zip(destinations,local_forwards))

    built_link = Router(
        IP_ADDRESS=reader['metadata']['HOST'],
        PORT=reader['metadata']['PORT'],
        virtual_address=reader['host']['virtual_address'],
        bandwidth=reader['host']['bandwidth'],
        buffer_size=reader['host']['buffer_size'],
        neighbors=neighbor_list,
        forwarding_table=forwarding_table,
    )

    return built_link
