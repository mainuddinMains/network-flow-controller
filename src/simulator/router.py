"""UDP router with concurrent receive, forward, and delayed-delivery workers."""

import heapq
import itertools
import logging
import queue
import random
import socket
import struct
import threading
import time

from src.telemetry.netlog import format_event, get_device_logger

# Use one universal local hosting address, as we assume that our mini internet
# is run on one device.
LOCAL_HOST = "127.0.0.1"

# Size limits, in bytes. Every packet is at most 1,200 bytes; malformed or
# oversized datagrams are rejected without crashing (Issue #30).
MAX_PACKET_SIZE_B = 1200

# recvfrom buffer: large enough to receive an oversized datagram intact so it
# can be measured and rejected, instead of being truncated (Linux) or raising
# WSAEMSGSIZE and killing the receiver (Windows). This is intentionally
# independent of the configured socket receive-buffer size (SO_RCVBUF).
UDP_MAX_SIZE = 65535

# Universal header format: TTL, type_flags, reserve, identifier, source, destination.
HEADER_FORMAT = "!BBBHHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# Default inbound queue capacity (datagrams). Overflow drops are logged.
# Delayed delivery is handled by a nonblocking scheduler (issue #34), so the
# queue only has to absorb short receive bursts, not packets stuck behind a
# sleeping forwarder.
DEFAULT_INBOUND_QUEUE_SIZE = 128

# How long recvfrom / queue.get wait before re-checking the stop flag.
_SOCKET_TIMEOUT_S = 1.0
_QUEUE_GET_TIMEOUT_S = 1.0

# Type-flag bits of the universal header (mirrors Host.flags, like
# HEADER_FORMAT and the other protocol constants duplicated above).
_FLAG_ECHO = 0b0000_0001
_FLAG_REPLY = 0b0000_0010


def _packet_type(type_flags: int) -> str:
    """Name the packet type encoded in the header's type flags."""
    if type_flags & _FLAG_ECHO:
        return "echo"
    if type_flags & _FLAG_REPLY:
        return "reply"
    return "data"


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
        link_loss: dict | None = None,
        seed: int | None = None,
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
        :param loss: Default probability (0-1) that a sent packet is dropped.
        :param inbound_queue_size: Max datagrams waiting for the forwarder.
        :param link_loss: Optional map of neighbor address -> loss probability
            for that directed link, overriding ``loss`` on that link only.
        :param seed: Optional seed for this router's random loss decisions.
            When omitted, a seed is generated and recorded so the run can be
            reproduced; the seed in use is always logged and kept on
            ``self.seed``.
        """

        # Meta configuration used by the real-world socket infrastructure.
        self.meta_ip = IP_ADDRESS
        self.meta_port = int(PORT)

        # Router configuration.
        self.bandwidth = float(bandwidth)    # In Mbps.
        self.buffer_size = int(buffer_size)  # In bytes.
        self.virtual_address = int(virtual_address)
        self.loss_chance = float(loss)

        # Per-link (directed, by next-hop neighbor) loss overrides.
        self.link_loss = {
            int(neighbor): float(chance)
            for neighbor, chance in (link_loss or {}).items()
        }

        # Reproducible randomness: every loss decision draws from this
        # router-local generator, and the seed is recorded and logged.
        if seed is None:
            seed = random.SystemRandom().getrandbits(32)
        self.seed = int(seed)
        self._rng = random.Random(self.seed)

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
        # SO_RCVBUF must be large enough for the OS to queue full-size datagrams.
        # buffer_size models the in-memory simulation queue; the socket buffer
        # must be at least UDP_MAX_SIZE so the OS never silently drops valid packets.
        self.socket.setsockopt(
            socket.SOL_SOCKET, socket.SO_RCVBUF, max(self.buffer_size, UDP_MAX_SIZE)
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

        # Concurrency: one receiver, one forwarder, one delayed-delivery
        # scheduler, bounded inbound queue.
        self._stop_event = threading.Event()
        self._inbound = queue.Queue(maxsize=inbound_queue_size)
        self._send_lock = threading.Lock()
        self._receiver_thread: threading.Thread | None = None
        self._forwarder_thread: threading.Thread | None = None
        self._delivery_thread: threading.Thread | None = None

        # Delayed-delivery schedule: (due_monotonic, seq, datagram, header).
        # The seq tie-breaker keeps heap ordering FIFO for equal due times.
        self._delay_heap: list = []
        self._delay_cond = threading.Condition()
        self._delay_seq = itertools.count()

        self.overflow_drops = 0
        self.malformed_drops = 0
        self.no_route_drops = 0
        self.ttl_expired_drops = 0
        # Injected random-loss drops, kept separate from the drop counters
        # above so experiments can tell configured loss from other causes.
        self.loss_drops = 0
        # Packets that were scheduled with a positive link delay.
        self.delayed_sends = 0

        self._logger = get_device_logger(f"router-{self.virtual_address}")
        self._log_event(
            "init",
            meta_ip=self.meta_ip,
            meta_port=self.meta_port,
            address=self.virtual_address,
            neighbors=self.neighboring_sockets,
            forwarding_table=self.forwarding_table,
            bandwidth=self.bandwidth,
            buffer_size=self.buffer_size,
            inbound_queue_size=inbound_queue_size,
            loss_chance=self.loss_chance,
            link_loss=self.link_loss,
            seed=self.seed,
        )

    def _log_event(self, event: str, _level: int = logging.INFO, **fields) -> None:
        """Log one structured network event for this router.

        Single-line records keep concurrent worker output readable; the
        shared formatter adds the timestamp, run id, and device name.
        """
        self._logger.log(_level, format_event(event, **fields))

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

        # Forwarded payloads stay raw bytes; only non-bytes input is encoded.
        if isinstance(payload, (bytes, bytearray)):
            payload_bytes = bytes(payload)
        else:
            payload_bytes = str(payload).encode("utf-8")

        datagram = header_bytes + payload_bytes

        if len(datagram) > MAX_PACKET_SIZE_B:
            self._log_event(
                "drop",
                _level=logging.WARNING,
                reason="oversized_build",
                id=identifier,
                type=_packet_type(type_flags),
                src=source,
                dest=destination,
                ttl=TTL,
                bytes=len(datagram),
                max=MAX_PACKET_SIZE_B,
            )
            raise ValueError(
                f"packet of {len(datagram)} bytes exceeds the "
                f"{MAX_PACKET_SIZE_B}-byte protocol maximum"
            )

        return datagram

    # ------------------------------------------------------------------
    # Sending and keeping datagrams
    # ------------------------------------------------------------------
    def _link_loss_chance(self, forward) -> float:
        """Return the loss probability for the directed link to ``forward``."""
        return self.link_loss.get(forward, self.loss_chance)

    def _schedule_send(self, datagram, end_destination) -> None:
        """Schedule ``datagram`` for delivery after the next hop's link delay.

        Runs on the forwarder thread and never sleeps: the datagram is
        placed on the delayed-delivery heap and the scheduler thread
        transmits it when its due time arrives, so a delayed packet cannot
        block unrelated packets from being processed.
        """
        header, _ = self._interpret_datagram(datagram)
        packet = {
            "id": header["identifier"],
            "type": _packet_type(header["type_flags"]),
            "src": header["source"],
            "dest": end_destination,
            "ttl": header["TTL"],
        }
        forward = self.forwarding_table.get(end_destination)
        if forward is None:
            self.no_route_drops += 1
            self._log_event(
                "drop",
                _level=logging.WARNING,
                reason="no_route",
                **packet,
                no_route_drops=self.no_route_drops,
            )
            return

        if forward not in self.address_to_real_dest_map:
            self.no_route_drops += 1
            self._log_event(
                "drop",
                _level=logging.WARNING,
                reason="next_hop_not_neighbor",
                next_hop=forward,
                **packet,
                no_route_drops=self.no_route_drops,
            )
            return

        delay = max(0.0, float(self.delays.get(forward, 0.0)))
        due = time.monotonic() + delay
        if delay > 0:
            self.delayed_sends += 1
            self._log_event(
                "delay",
                next_hop=forward,
                delay_s=delay,
                delayed_sends=self.delayed_sends,
                **packet,
            )

        with self._delay_cond:
            heapq.heappush(
                self._delay_heap,
                (due, next(self._delay_seq), datagram, header),
            )
            self._delay_cond.notify()

    def _send_datagram(self, datagram, header):
        """Apply the loss decision and transmit ``datagram`` to its next hop.

        Runs on the delayed-delivery scheduler thread once the link delay
        has elapsed. Contains no sleeps. The header carries the decremented
        TTL so the logged value matches the datagram on the wire.
        """
        end_destination = header["destination"]
        packet = {
            "id": header["identifier"],
            "type": _packet_type(header["type_flags"]),
            "src": header["source"],
            "dest": end_destination,
            "ttl": header["TTL"],
        }

        forward = self.forwarding_table.get(end_destination)
        if forward is None:
            self.no_route_drops += 1
            self._log_event(
                "drop",
                _level=logging.WARNING,
                reason="no_route",
                **packet,
                no_route_drops=self.no_route_drops,
            )
            return

        dest = self.address_to_real_dest_map.get(forward)
        if dest is None:
            self.no_route_drops += 1
            self._log_event(
                "drop",
                _level=logging.WARNING,
                reason="next_hop_not_neighbor",
                next_hop=forward,
                **packet,
                no_route_drops=self.no_route_drops,
            )
            return

        delay = max(0.0, float(self.delays.get(forward, 0.0)))
        # Simulated random loss, drawn from this router's seeded generator.
        # A 0.0 chance can never drop because random() < 0.0 is never true.
        loss_chance = self._link_loss_chance(forward)
        if self._rng.random() < loss_chance:
            self.loss_drops += 1
            self._log_event(
                "drop",
                _level=logging.WARNING,
                reason="injected_loss",
                next_hop=forward,
                delay_s=delay,
                loss_chance=loss_chance,
                loss_drops=self.loss_drops,
                **packet,
            )
            return

        self._log_event(
            "forward",
            next_hop=forward,
            next_hop_real=f"{dest[0]}:{dest[1]}",
            delay_s=delay,
            **packet,
        )
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

        payload_results = payload.decode("utf-8", errors="replace")

        truncated = payload_results[:30] + (
            "..." if len(payload_results) > 30 else ""
        )
        truncated = truncated.replace("\r", " ").replace("\n", " ")
        self._log_event(
            "receive",
            id=header["identifier"],
            type=_packet_type(header["type_flags"]),
            src=header["source"],
            dest=self.virtual_address,
            ttl=header["TTL"],
            bytes=len(payload),
            payload=truncated,
        )

    def _process_datagram(self, data: bytes) -> None:
        """Apply local-delivery, TTL, and forwarding rules to one datagram."""
        head, payload = self._interpret_datagram(data)

        self._log_event(
            "receive",
            id=head["identifier"],
            type=_packet_type(head["type_flags"]),
            src=head["source"],
            dest=head["destination"],
            ttl=head["TTL"],
            bytes=len(payload),
        )

        if head["destination"] == self.virtual_address:
            self._keep_datagram(payload, head)
            return

        # Hop limit rule:
        # If the TTL of a received packet is 0 or less, drop it.
        if head["TTL"] <= 0:
            self.ttl_expired_drops += 1
            self._log_event(
                "drop",
                _level=logging.WARNING,
                reason="ttl_expired",
                id=head["identifier"],
                type=_packet_type(head["type_flags"]),
                src=head["source"],
                dest=head["destination"],
                ttl=head["TTL"],
                ttl_expired_drops=self.ttl_expired_drops,
            )
            return

        # Forward the payload as raw bytes; decoding is only for local use.
        datagram = self._create_datagram(
            head["TTL"] - 1,
            head["type_flags"],
            head["reserve"],
            head["identifier"],
            head["source"],
            head["destination"],
            payload,
        )

        self._schedule_send(datagram, head["destination"])

    # ------------------------------------------------------------------
    # Concurrent workers
    # ------------------------------------------------------------------
    def delivery_loop(self) -> None:
        """Transmit scheduled datagrams once their link delay has elapsed."""
        while True:
            with self._delay_cond:
                while not self._delay_heap and not self._stop_event.is_set():
                    self._delay_cond.wait(timeout=_QUEUE_GET_TIMEOUT_S)
                if self._stop_event.is_set():
                    return

                now = time.monotonic()
                due = self._delay_heap[0][0]
                if due > now:
                    # Sleep inside the condition so a nearer deadline or
                    # shutdown can interrupt the wait.
                    self._delay_cond.wait(timeout=due - now)
                    continue
                _, _, datagram, header = heapq.heappop(
                    self._delay_heap
                )

            try:
                self._send_datagram(datagram, header)
            except Exception as exc:  # Keep the scheduler alive on bad sends.
                self._log_event(
                    "error",
                    _level=logging.ERROR,
                    reason="delivery_failed",
                    id=header["identifier"],
                    type=_packet_type(header["type_flags"]),
                    src=header["source"],
                    dest=header["destination"],
                    ttl=header["TTL"],
                    error=repr(exc),
                )

    def listening_loop(self) -> None:
        """Receive, validate, and enqueue UDP datagrams for the forwarder."""
        while not self._stop_event.is_set():
            try:
                data, _ = self.socket.recvfrom(UDP_MAX_SIZE)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # Windows reports ICMP "port unreachable" from a prior send
                # this way; it must not stop the receiver.
                continue
            except OSError:
                # Socket closed during shutdown, or other fatal socket error.
                break

            if len(data) < HEADER_SIZE:
                self.malformed_drops += 1
                self._log_event(
                    "drop",
                    _level=logging.WARNING,
                    reason="malformed_too_short",
                    bytes=len(data),
                    min=HEADER_SIZE,
                    malformed_drops=self.malformed_drops,
                )
                continue

            if len(data) > MAX_PACKET_SIZE_B:
                self.malformed_drops += 1
                self._log_event(
                    "drop",
                    _level=logging.WARNING,
                    reason="malformed_oversized",
                    bytes=len(data),
                    max=MAX_PACKET_SIZE_B,
                    malformed_drops=self.malformed_drops,
                )
                continue

            try:
                self._inbound.put_nowait(data)
            except queue.Full:
                self.overflow_drops += 1
                # The datagram passed both size checks, so its header is
                # parseable and the dropped packet can be identified.
                head, _ = self._interpret_datagram(data)
                self._log_event(
                    "drop",
                    _level=logging.WARNING,
                    reason="queue_overflow",
                    id=head["identifier"],
                    type=_packet_type(head["type_flags"]),
                    src=head["source"],
                    dest=head["destination"],
                    ttl=head["TTL"],
                    overflow_drops=self.overflow_drops,
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
                self._log_event(
                    "error",
                    _level=logging.ERROR,
                    reason="process_failed",
                    error=repr(exc),
                )
            finally:
                self._inbound.task_done()

    def start(self) -> None:
        """Start the receiver, forwarder, and delivery workers if needed."""
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
        self._delivery_thread = threading.Thread(
            target=self.delivery_loop,
            name=f"router-{self.virtual_address}-delivery",
            daemon=True,
        )
        self._receiver_thread.start()
        self._forwarder_thread.start()
        self._delivery_thread.start()
        self._log_event("start")

    def shutdown(self, join_timeout: float = 2.0) -> None:
        """Stop workers and close the socket so the bind port is released."""
        self._stop_event.set()
        # Wake the delivery scheduler so it notices the stop flag.
        with self._delay_cond:
            self._delay_cond.notify_all()
        try:
            self.socket.close()
        except OSError:
            pass

        workers = (
            self._receiver_thread,
            self._forwarder_thread,
            self._delivery_thread,
        )
        for worker in workers:
            if worker is not None and worker.is_alive():
                worker.join(timeout=join_timeout)

        self._log_event("shutdown")

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
