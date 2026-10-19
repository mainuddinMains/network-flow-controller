import socket
import struct

# Use one universal local hosting address, as we assume that our mini internet is run on one device.
LOCAL_HOST = "127.0.0.1"

# Max size of a packet that the router can receive, in bytes.
# MAX_PACKET_SIZE_B = 1024

# Max amount of bytes a payload can be, saving space for overhead.
# MAX_PAYLOAD_SIZE_B = 900

# Universal header format
HEADER_FORMAT = "!BBBHHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

class Router:
    def __init__(self, IP_ADDRESS, PORT, virtual_address, bandwidth, buffer_size, neighbors : list, forwarding_table : dict):
        """
        :param IP_ADDRESS:
        :param PORT:
        :param virtual_address:
        :param  bandwidth:
        :param buffer_size:
        :param neighbors:
        :param forwarding_table:
        """

        # Meta configurations to use sockets.
        self.meta_ip = IP_ADDRESS       # Host address to use real world socket infrastructure.
        self.meta_port = int(PORT)           # Host port num to use real world socket infrastructure.

        # Router configurations
        self.bandwidth = float(bandwidth)      # In Mbps
        self.buffer_size = int(buffer_size)  # In bytes.

        self.virtual_address = int(virtual_address)  # An integer.

        self.neighboring_sockets = neighbors.copy() # As a list of virtual addresses.
        self.forwarding_table = forwarding_table.copy() # As a dictionary of packet_destination : next router.

        # Socket setup.
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.meta_ip, self.meta_port))
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.buffer_size)

        # POTENTIALLY TEMPORARY LOGIC
        # This hardcodes usage of the local host address.
        # This also hardcodes the port convention of 60000+virtual_address
        self.address_to_real_dest_map = {neighbor: (LOCAL_HOST, 60000 + neighbor) for neighbor in self.neighboring_sockets}

        print("Router initialized.\n"
              f"     meta_ip={self.meta_ip}\n"
              f"     meta_port={self.meta_port}\n"
              f"     address={self.virtual_address}\n"
              f"     neighbors={self.neighboring_sockets}\n"
              f"     forwarding_table={self.forwarding_table}\n"
              f"     bandwidth={self.bandwidth}\n"
              f"     buffer_size={self.buffer_size}\n")


    def _interpret_datagram(self, datagram):
        """
        :param datagram: The datagram to unpack.
        :return: Header dictionary and a payload.

        Unpacks a datagram into its header and the payload.
        """
        (TTL,
         type_flags,
         identifier,
         reserve,
         source,
         destination) = struct.unpack_from(HEADER_FORMAT, datagram, offset=0)

        header = {"TTL" : TTL,
                  "type_flags": type_flags,
                  "identifier": identifier,
                  "reserve": reserve,
                  "source": source,
                  "destination": destination}

        payload = datagram[HEADER_SIZE:]

        return header, payload

    def _create_datagram(self, TTL, type_flags, reserve, identifier, source, destination, payload):
        """
        :param TTL: Time to live. 0-255
        :param type_flags: 8 bit value.
        :param reserve: Reserve space. Arbitrary. 0-255
        :param identifier: Some packet-level identifier. 0 - 65,535
        :param source: Source virtual IP address. 0 - 65,535
        :param destination: Destination virtual IP address. 0 - 65,535
        :param payload:
        """

        header_bytes = struct.pack(HEADER_FORMAT,
                                   TTL,
                                   type_flags,
                                   reserve,
                                   identifier,
                                   source,
                                   destination)

        payload_bytes = str(payload).encode('utf-8')

        # if len(payload_bytes) > MAX_PACKET_SIZE_B:
        #     print(f"Error from router {self.virtual_address}: Payload exceeds {MAX_PAYLOAD_SIZE_B} bytes ({len(payload_bytes)})")

        datagram = header_bytes + payload_bytes

        print(f"Router {self.virtual_address} created a packet.\n     bytes={len(datagram)}")

        return datagram

    def _send_datagram(self, datagram, end_destination):
        forward = self.forwarding_table[end_destination]
        dest = self.address_to_real_dest_map[forward]

        "Here is where transmission delay can be simulated."

        print(f"Router {self.virtual_address} is sending a packet.\n"
              f"     dest={end_destination}\n"
              f"     forward={forward}\n"
              f"     forward_real={dest}\n")
        self.socket.sendto(datagram, dest)

    def _keep_datagram(self, payload):
        """
        :param payload: The datagram to keep and act on.

        Currently a dummy function that just logs the payload, as no functionality as been implemented that would require
        a router to keep a datagram.
        """

        payload_results = payload.decode('utf-8')

        print(f"Router {self.virtual_address} has been given a packet to keep.\n"
              f"     len_payload={len(payload)}\n"
              f"     payload={(payload_results[:30]) + ('...' if len(payload_results) > 30 else '')}\n")

    def operation_loop(self):
        while True:
            print(f"Router {self.virtual_address} is awaiting a packet.\n")
            data, _ = self.socket.recvfrom(self.buffer_size)

            head, payload = self._interpret_datagram(data)

            print(f"Router {self.virtual_address} received packet.\n"
                  f"     src={head['source']}\n"
                  f"     dest={head['destination']}\n"
                  f"     size_head={len(head)}\n"
                  f"     size_payload={len(payload)}\n")

            if head["destination"] == self.virtual_address:
                self._keep_datagram(payload)
                continue

            "Insert any other needed logic here. I.e., responding to certain flags."

            if head["TTL"] <= 0:
                continue

            datagram = self._create_datagram(head["TTL"] - 1,
                                  head["type_flags"],
                                  head["reserve"],
                                  head["identifier"],
                                  head["source"],
                                  head["destination"],
                                  payload.decode('utf-8'))

            self._send_datagram(datagram, head["destination"])










