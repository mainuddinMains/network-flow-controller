"""UDP router with concurrent receive and forward workers."""

import queue
import random
import socket
import struct
import threading
import time

# TODO: Replace global seed with a random seed that is logged at the beginning of the run.
random.seed(42)

# Use one universal local hosting address, as we assume that our mini internet
# is run on one device.
LOCAL_HOST = "127.0.0.1"

# Size limits, in bytes.
# TODO: The spec requires every packet to be at most 1,200 bytes, and malformed or oversized packets must
# be rejected without crashing.
MAX_PACKET_SIZE_B = 1200
# MAX_PAYLOAD_SIZE_B = 900

# Universal header format: TTL, type_flags, reserve, identifier, source, destination.
HEADER_FORMAT = "!BBBHHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# Default inbound queue capacity (datagrams). Overflow drops are logged.
DEFAULT_INBOUND_QUEUE_SIZE = 32

# How long recvfrom / queue.get wait before re-checking the stop flag.
_SOCKET_TIMEOUT_S = 1.0
_QUEUE_GET_TIMEOUT_S = 1.0


class Router:
    """Forward UDP mini-internet datagrams between configured neighbors.

    Receiving and forwarding run on separate threads connected by a bounded
    queue so a delayed send cannot stall new arrivals.
    """

    def __init__(
        self,
        IP_ADDRESS,
        PORT,
        virtual_address,
        bandwidth,
        buffer_size,
        neighbors: list,
        forwarding_table: dict,
        delays: dict,
        loss,
        inbound_queue_size: int = DEFAULT_INBOUND_QUEUE_SIZE,
    ):
        """
        :param IP_ADDRESS: Real IP address the router's socket binds to.
        :param PORT: Real UDP port the router's socket binds to.
        :param virtual_address: This router's address in the mini internet.
        :param bandwidth: Link bandwidth, in Mbps.
        :param buffer_size: Receive buffer size, in bytes.
        :param neighbors: Virtual addresses of directly connected devices.
        :param forwarding_table: Maps destination address -> next-hop neighbor.
        :param delays: Maps neighbor address -> added delay, in seconds.
        :param loss: Probability (0-1) that a sent packet is dropped.
        :param inbound_queue_size: Max datagrams waiting for the forwarder.
        """

        # Meta configuration used by the real-world socket infrastructure.
        self.meta_ip = IP_ADDRESS
        self.meta_port = int(PORT)

        # Router configuration.
        self.bandwidth = float(bandwidth)    # In Mbps.
        self.buffer_size = int(buffer_size)  # In bytes.
        self.virtual_address = int(virtual_address)
        self.loss_chance = float(loss)

        # Virtual addresses of neighboring devices.
        self.neighboring_sockets = neighbors.copy()
        # Maps packet_destination -> next router.
        self.forwarding_table = forwarding_table.copy()
        # Maps neighbor -> delay (s).
        self.delays = delays.copy()

        if inbound_queue_size < 1:
            raise ValueError("inbound_queue_size must be at least 1")

        # Socket setup.
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.meta_ip, self.meta_port))
        self.socket.setsockopt(
            socket.SOL_SOCKET, socket.SO_RCVBUF, self.buffer_size
        )
        # Timeout lets the receiver notice shutdown without a permanent block.
        self.socket.settimeout(_SOCKET_TIMEOUT_S)

        # TODO(functionality): This hardcodes the local host address and the
        # 60000+virtual_address port convention. The spec says actual UDP
        # endpoints should come from the configuration file. Consider also
        # validating the config here and reporting startup errors clearly.
        self.address_to_real_dest_map = {
            neighbor: (LOCAL_HOST, 60000 + neighbor)
            for neighbor in self.neighboring_sockets
        }

        # Concurrency: one receiver, one forwarder, bounded inbound queue.
        self._stop_event = threading.Event()
        self._inbound = queue.Queue(maxsize=inbound_queue_size)
        self._print_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._receiver_thread: threading.Thread | None = None
        self._forwarder_thread: threading.Thread | None = None
        self.overflow_drops = 0

        print(
            "Router initialized.\n"
            f"     meta_ip={self.meta_ip}\n"
            f"     meta_port={self.meta_port}\n"
            f"     address={self.virtual_address}\n"
            f"     neighbors={self.neighboring_sockets}\n"
            f"     forwarding_table={self.forwarding_table}\n"
            f"     bandwidth={self.bandwidth}\n"
            f"     buffer_size={self.buffer_size}\n"
            f"     inbound_queue_size={inbound_queue_size}\n"
        )

    def _log(self, message: str) -> None:
        """Print without interleaving output from worker threads."""
        with self._print_lock:
            print(message)

    # ------------------------------------------------------------------
    # Datagram encoding and decoding
    # ------------------------------------------------------------------
    def _interpret_datagram(self, datagram):
        """
        :param datagram: The datagram to unpack.
        :return: Header dictionary and a payload.

        Unpacks a datagram into its header and the payload.
        """

        (TTL,
         type_flags,
         reserve,
         identifier,
         source,
         destination) = struct.unpack_from(HEADER_FORMAT, datagram, offset=0)

        header = {
            "TTL": TTL,
            "type_flags": type_flags,
            "reserve": reserve,
            "identifier": identifier,
            "source": source,
            "destination": destination,
        }

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
        :param payload: The payload to attach to the header.
        """

        header_bytes = struct.pack(
            HEADER_FORMAT,
            TTL,
            type_flags,
            reserve,
            identifier,
            source,
            destination,
        )

        # TODO: I think this line here might wrap payload in quotes that shouldn't be there. Not entirely sure.
        payload_bytes = str(payload).encode("utf-8")

        # TODO(functionality): Enforce the maximum packet size here or before
        # sending (see the size constants at the top of the file).

        datagram = header_bytes + payload_bytes

        self._log(
            f"Router {self.virtual_address} created a packet.\n"
            f"     bytes={len(datagram)}"
        )

        return datagram

    # ------------------------------------------------------------------
    # Sending and keeping datagrams
    # ------------------------------------------------------------------
    def _send_datagram(self, datagram, end_destination):
        # TODO(functionality): An unknown destination or a next hop that is not
        # a configured neighbor currently raises KeyError. The spec requires
        # logging the reason, dropping the packet, and keeping the process
        # running.
        forward = self.forwarding_table[end_destination]
        dest = self.address_to_real_dest_map[forward]

        self._log(
            f"Router {self.virtual_address} is sending a packet.\n"
            f"     dest={end_destination}\n"
            f"     forward={forward}\n"
            f"     forward_real={dest}\n"
        )

        # Simulated transmission delay (runs on the forwarder thread only).
        # TODO: sleep() must be replaced with a non-blocking delay.
        delay = self.delays[forward]
        time.sleep(delay)

        # Simulated random loss.
        # TODO: Replace global loss with per-link loss. Low priority.
        if random.random() < self.loss_chance:
            self._log(
                f"Router {self.virtual_address} lost a packet in transit.\n"
                f"     dest={end_destination}\n"
                f"     forward={forward}\n"
                f"     forward_real={dest}\n"
                f"     loss_chance={self.loss_chance}\n"
            )
            return

        with self._send_lock:
            self.socket.sendto(datagram, dest)

    def _keep_datagram(self, payload, header):
        """
        :param payload: The payload of the datagram.
        :param header: The header of the datagram.

        Currently a dummy function that just logs the payload, as no
        functionality has been implemented that would require a router to keep
        a datagram.
        """

        payload_results = payload.decode("utf-8")

        truncated = payload_results[:30] + (
            "..." if len(payload_results) > 30 else ""
        )
        self._log(
            f"Router {self.virtual_address} has been given a packet to keep.\n"
            f"     len_payload={len(payload)}\n"
            f"     payload={truncated}\n"
        )

    def _process_datagram(self, data: bytes) -> None:
        """Apply local-delivery, TTL, and forwarding rules to one datagram."""
        head, payload = self._interpret_datagram(data)

        # TODO(functionality): size_head below reports len(head), which is
        # the number of dictionary keys, not the header size in bytes.
        self._log(
            f"Router {self.virtual_address} received packet.\n"
            f"     src={head['source']}\n"
            f"     identifier={head['identifier']}\n"
            f"     dest={head['destination']}\n"
            f"     size_head={len(head)}\n"
            f"     size_payload={len(payload)}\n"
            f"     ttl={head['TTL']}\n"
        )

        if head["destination"] == self.virtual_address:
            self._keep_datagram(payload, head)
            return

        # Hop limit rule:
        # If the TTL of a received packet is 0 or less, drop it.
        if head["TTL"] <= 0:
            self._log(
                f"Router {self.virtual_address} dropped an expired packet.\n"
                f"     src={head['source']}"
            )
            return

        datagram = self._create_datagram(
            head["TTL"] - 1,
            head["type_flags"],
            head["reserve"],
            head["identifier"],
            head["source"],
            head["destination"],
            payload.decode("utf-8"),
        )

        self._send_datagram(datagram, head["destination"])

    # ------------------------------------------------------------------
    # Concurrent workers
    # ------------------------------------------------------------------
    def listening_loop(self) -> None:
        """Receive UDP datagrams and enqueue them for the forwarder."""
        recv_size = max(self.buffer_size, MAX_PACKET_SIZE_B)
        while not self._stop_event.is_set():
            try:
                data, _ = self.socket.recvfrom(recv_size)
            except socket.timeout:
                continue
            except OSError:
                # Socket closed during shutdown, or other fatal socket error.
                break

            try:
                self._inbound.put_nowait(data)
            except queue.Full:
                self.overflow_drops += 1
                self._log(
                    f"Router {self.virtual_address} dropped a packet: "
                    f"inbound queue full "
                    f"(overflow_drops={self.overflow_drops}).\n"
                )

    def forwarding_loop(self) -> None:
        """Dequeue datagrams and apply routing / delay / loss / send."""
        while not self._stop_event.is_set():
            try:
                data = self._inbound.get(timeout=_QUEUE_GET_TIMEOUT_S)
            except queue.Empty:
                continue

            try:
                self._process_datagram(data)
            except Exception as exc:  # Keep the forwarder alive on bad packets.
                self._log(
                    f"Router {self.virtual_address} failed to process "
                    f"a packet: {exc!r}"
                )
            finally:
                self._inbound.task_done()

    def start(self) -> None:
        """Start the receiver and forwarder workers if they are not running."""
        if (
            self._receiver_thread is not None
            and self._receiver_thread.is_alive()
        ):
            return

        self._stop_event.clear()
        self._receiver_thread = threading.Thread(
            target=self.listening_loop,
            name=f"router-{self.virtual_address}-receiver",
            daemon=True,
        )
        self._forwarder_thread = threading.Thread(
            target=self.forwarding_loop,
            name=f"router-{self.virtual_address}-forwarder",
            daemon=True,
        )
        self._receiver_thread.start()
        self._forwarder_thread.start()
        self._log(f"Router {self.virtual_address} workers started.\n")

    def shutdown(self, join_timeout: float = 2.0) -> None:
        """Stop workers and close the socket so the bind port is released."""
        self._stop_event.set()
        try:
            self.socket.close()
        except OSError:
            pass

        for worker in (self._receiver_thread, self._forwarder_thread):
            if worker is not None and worker.is_alive():
                worker.join(timeout=join_timeout)

        self._log(f"Router {self.virtual_address} shut down.\n")

    def operation_loop(self) -> None:
        """Run concurrent workers until shutdown (process entry point)."""
        self.start()
        try:
            while not self._stop_event.is_set():
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
