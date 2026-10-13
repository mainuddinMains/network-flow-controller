import socket
import struct

# Use one universal local hosting address, as we assume that our mini internet is run on one device.
LOCAL_HOST = "127.0.0.1"

# Universal header format
HEADER_FORMAT = "!B"
class Router:
    def __init__(self, IP_ADDRESS, PORT, virtual_address, bandwidth, buffer_size, neighbors : list, forwarding_table : dict):
        # Meta configurations to use sockets.
        self.meta_ip = LOCAL_HOST       # Host address to use real world socket infrastructure.
        self.meta_port = PORT           # Host port num to use real world socket infrastructure.

        # Router configurations
        self.bandwidth = bandwidth      # In Mbps
        self.buffer_size = buffer_size  # In MB.

        self.virtual_address = virtual_address  # An integer.

        self.neighboring_sockets = neighbors.copy() # As a list of virtual addresses.
        self.forwarding_table = forwarding_table.copy() # As a dictionary of packet_destination : next router.

        # Socket setup.
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.meta_ip, self.meta_port))

    def __interpret_datagram__(self):
        pass

    def __create_datagram__(self):
        pass

    def __send_datagram__(self):
        pass

    def __operation_loop__(self):
        pass




