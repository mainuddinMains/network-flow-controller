import socket
import struct
import threading

# Universal header format
HEADER_FORMAT = "!BBBHHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# Use one universal local hosting address, as we assume that our mini internet
# is run on one device.
LOCAL_HOST = "127.0.0.1"

UDP_MAX_SIZE = 65535
DEFAULT_TTL = 64


class Host:
    flags = {
        "echo": 0b0000_0001,
        "reply": 0b0000_0010
    }

    def __init__(self, IP_ADDRESS, PORT, virtual_address, entry_router_address):
        self.meta_ip = IP_ADDRESS
        self.meta_port = PORT
        self.virtual_address = virtual_address

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.meta_ip, self.meta_port))
        # Lets the listening thread wake up periodically to check for shutdown.
        self.socket.settimeout(1.0)

        self.entry_router_address = entry_router_address
        self.entry_router_meta = (LOCAL_HOST, 60000 + entry_router_address)

        self.storage = []

        # Threading state
        self._stop_event = threading.Event()
        self._storage_lock = threading.Lock()
        self._print_lock = threading.Lock()
        self._next_identifier = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _log(self, message):
        """Thread-safe print so output from both threads doesn't interleave."""
        with self._print_lock:
            print(message)

    def _new_identifier(self):
        identifier = self._next_identifier
        self._next_identifier = (self._next_identifier + 1) & 0xFFFF
        return identifier

    # ------------------------------------------------------------------
    # Datagram handling
    # ------------------------------------------------------------------
    def _interpret_datagram(self, datagram):
        """
        :param datagram: The datagram to unpack.
        :return: Header dictionary and a payload.

        Unpacks a datagram into its header and the payload.
        """
        # Order matches HEADER_FORMAT ("!BBBHHH") and _create_datagram's pack.
        (TTL,
         type_flags,
         reserve,
         identifier,
         source,
         destination) = struct.unpack_from(HEADER_FORMAT, datagram, offset=0)

        header = {"TTL": TTL,
                  "type_flags": type_flags,
                  "identifier": identifier,
                  "reserve": reserve,
                  "source": source,
                  "destination": destination}

        payload = datagram[HEADER_SIZE:]

        return header, payload

    def _create_datagram(
        self, TTL, type_flags, reserve, identifier, source, destination, payload
    ):
        """
        :param TTL: Time to live. 0-255
        :param type_flags: 8 bit value.
        :param reserve: Reserve space. Arbitrary. 0-255
        :param identifier: Some packet-level identifier. 0 - 65,535
        :param source: Source virtual IP address. 0 - 65,535
        :param destination: Destination virtual IP address. 0 - 65,535
        :param payload: str or bytes.
        """

        header_bytes = struct.pack(HEADER_FORMAT,
                                   TTL,
                                   type_flags,
                                   reserve,
                                   identifier,
                                   source,
                                   destination)

        # str(bytes) would give "b'...'", so only stringify non-bytes payloads.
        if isinstance(payload, (bytes, bytearray)):
            payload_bytes = bytes(payload)
        else:
            payload_bytes = str(payload).encode('utf-8')

        datagram = header_bytes + payload_bytes

        self._log(
            f"Host {self.virtual_address} created a packet.\n"
            f"     bytes={len(datagram)}"
        )

        return datagram

    def _send_datagram(self, datagram, end_destination):
        self._log(f"Host {self.virtual_address} is sending a packet.\n"
                  f"     dest={end_destination}\n"
                  f"     entry_router_address={self.entry_router_address}\n"
                  f"     entry_real={self.entry_router_meta}\n")
        self.socket.sendto(datagram, self.entry_router_meta)

    def _keep_datagram(self, payload, header):
        """
        :param payload: The payload to keep.
        :param header: The header of the datagram.

        Copies the payload to the host's storage, logs receiving the datagram.
        """

        payload_results = payload.decode('utf-8', errors='replace')

        with self._storage_lock:
            self.storage.append(payload)

        truncated = payload_results[:30] + ("..." if len(payload_results) > 30 else "")
        self._log(
            f"Host {self.virtual_address} is keeping a datagram.\n"
            f"     from={header['source']}\n"
            f"     len_payload={len(payload)}\n"
            f"     payload={truncated}\n"
        )

    def _handle_datagram(self, data):
        """Process one received datagram (keep it, respond to flags)."""
        head, payload = self._interpret_datagram(data)

        self._log(f"Host {self.virtual_address} received packet.\n"
                  f"     src={head['source']}\n"
                  f"     dest={head['destination']}\n"
                  f"     size_head={HEADER_SIZE}\n"
                  f"     size_payload={len(payload)}\n")

        # Hosts are end points: anything not addressed to us is dropped
        # rather than bounced back into the network.
        if head["destination"] != self.virtual_address:
            self._log(f"Host {self.virtual_address} dropped a packet "
                      f"not addressed to it.\n")
            return

        self._keep_datagram(payload, head)

        # Insert any other needed logic here. I.e., responding to certain flags.
        if head["type_flags"] & self.flags["echo"]:
            # Clear the echo flag on the reply, otherwise two hosts would
            # echo each other forever.
            reply_flags = head["type_flags"] & ~self.flags["echo"] | self.flags["reply"]
            echo_datagram = self._create_datagram(DEFAULT_TTL,
                                                  reply_flags,
                                                  head["reserve"],
                                                  head["identifier"],
                                                  source=self.virtual_address,
                                                  destination=head["source"],
                                                  payload=payload)

            self._send_datagram(echo_datagram, head["source"])

    # ------------------------------------------------------------------
    # Loops
    # ------------------------------------------------------------------
    def listening_loop(self):
        """Runs in a background thread; receives and handles datagrams."""
        while not self._stop_event.is_set():
            try:
                data, _ = self.socket.recvfrom(UDP_MAX_SIZE)
            except socket.timeout:
                continue  # Nothing arrived; re-check the stop flag.
            except ConnectionResetError:
                continue  # Windows reports ICMP "port unreachable" this way.
            except OSError:
                break  # Socket was closed during shutdown.

            try:
                self._handle_datagram(data)
            except Exception as exc:  # Keep the listener alive on bad packets.
                self._log(f"Host {self.virtual_address} failed to handle "
                          f"a packet: {exc!r}")

    def operation_loop(self):

        self._log(f"Host {self.virtual_address} ready.\n")

        # TODO: Write new host messaging protocol. This code below relies on the "input" function, which, while
        # functional for manual testing, wouldn't work with tests containing many simulated "users".
        while not self._stop_event.is_set():
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not line:
                continue
            if line.lower() in ("quit", "exit"):
                break

            type_flags = 0
            tokens = line.split(maxsplit=1)
            if tokens[0].lower() == "echo" and len(tokens) > 1:
                type_flags |= self.flags["echo"]
                line = tokens[1]

            try:
                dest_str, message = line.split(maxsplit=1)
                destination = int(dest_str)
                if not 0 <= destination <= 0xFFFF:
                    raise ValueError
            except ValueError:
                self._log("Usage: [echo] <dest 0-65535> <message>")
                continue

            datagram = self._create_datagram(DEFAULT_TTL,
                                             type_flags,
                                             0,
                                             self._new_identifier(),
                                             self.virtual_address,
                                             destination,
                                             message)
            self._send_datagram(datagram, destination)

    def run(self):
        """Start the listener thread, then run the user-input loop."""
        listener = threading.Thread(target=self.listening_loop,
                                    name=f"host-{self.virtual_address}-listener",
                                    daemon=True)
        listener.start()

        try:
            self.operation_loop()
        finally:
            self._stop_event.set()
            listener.join(timeout=2)
            self.socket.close()
            self._log(f"Host {self.virtual_address} shut down.")