"""UDP host endpoints with echo probes, RTT measurement, and timeouts."""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass

# Universal header format
HEADER_FORMAT = "!BBBHHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# Use one universal local hosting address, as we assume that our mini internet
# is run on one device.
LOCAL_HOST = "127.0.0.1"

UDP_MAX_SIZE = 65535
DEFAULT_TTL = 64

# Protocol-wide maximum packet size, in bytes (header plus payload). This is
# independent of the UDP receive buffer used by recvfrom.
MAX_PACKET_SIZE_B = 1200

# Default finite probe response timeout (seconds).
DEFAULT_PROBE_TIMEOUT_S = 1.0

# Upper bound on socket wait when no nearer probe deadline exists.
_DEFAULT_SOCKET_TIMEOUT_S = 1.0
_MIN_SOCKET_TIMEOUT_S = 0.001

# Local datagram that unblocks recvfrom so deadlines can be recomputed.
_WAKEUP_DATAGRAM = b"\xff\xffPROBE_WAKE"


@dataclass
class OutstandingProbe:
    """One echo request waiting for a matching reply or timeout."""

    identifier: int
    destination: int
    sent_at: float
    deadline: float


@dataclass(frozen=True)
class ProbeResult:
    """Immutable record of a completed probe (success or timeout)."""

    identifier: int
    destination: int
    status: str
    rtt: float | None
    completed_at: float


class Host:
    """Mini-internet host that sends and answers echo probes over UDP."""

    flags = {
        "echo": 0b0000_0001,
        "reply": 0b0000_0010,
    }

    def __init__(
        self,
        IP_ADDRESS,
        PORT,
        virtual_address,
        entry_router_address,
        probe_timeout: float = DEFAULT_PROBE_TIMEOUT_S,
    ):
        """
        :param IP_ADDRESS: Real IP address the host socket binds to.
        :param PORT: Real UDP port the host socket binds to.
        :param virtual_address: This host's address in the mini internet.
        :param entry_router_address: Virtual address of the first-hop router.
        :param probe_timeout: Seconds to wait for an echo reply before timeout.
        """
        if not isinstance(probe_timeout, (int, float)) or isinstance(
            probe_timeout, bool
        ):
            raise ValueError("probe_timeout must be a positive number")
        if not (probe_timeout > 0) or probe_timeout != probe_timeout:  # NaN check
            raise ValueError("probe_timeout must be finite and positive")
        if probe_timeout == float("inf"):
            raise ValueError("probe_timeout must be finite and positive")

        self.meta_ip = IP_ADDRESS
        self.meta_port = PORT
        self.virtual_address = virtual_address
        self.probe_timeout = float(probe_timeout)

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((self.meta_ip, self.meta_port))
        # Initial timeout; listening_loop adjusts it from probe deadlines.
        self.socket.settimeout(_DEFAULT_SOCKET_TIMEOUT_S)

        self.entry_router_address = entry_router_address
        self.entry_router_meta = (LOCAL_HOST, 60000 + entry_router_address)

        self.storage = []

        # Threading state
        self._stop_event = threading.Event()
        self._storage_lock = threading.Lock()
        self._print_lock = threading.Lock()
        self._probe_lock = threading.Lock()
        self._next_identifier = 0
        self._outstanding: dict[int, OutstandingProbe] = {}
        self._probe_results: list[ProbeResult] = []
        self._listener_thread: threading.Thread | None = None
        self.malformed_drops = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _log(self, message):
        """Thread-safe print so output from both threads doesn't interleave."""
        with self._print_lock:
            print(message)

    def _allocate_identifier(self) -> int:
        """Return a free 16-bit id not currently used by an outstanding probe."""
        with self._probe_lock:
            for _ in range(0x10000):
                candidate = self._next_identifier
                self._next_identifier = (self._next_identifier + 1) & 0xFFFF
                if candidate not in self._outstanding:
                    return candidate
        raise RuntimeError(
            "no free probe identifiers; all 65536 ids have outstanding probes"
        )

    def _new_identifier(self):
        """Allocate the next packet identifier, skipping outstanding probe ids."""
        return self._allocate_identifier()

    def _socket_wait_timeout(self) -> float:
        """Seconds until the next probe deadline, capped for shutdown wakeups."""
        with self._probe_lock:
            if not self._outstanding:
                return _DEFAULT_SOCKET_TIMEOUT_S
            now = time.monotonic()
            earliest = min(probe.deadline for probe in self._outstanding.values())
        remaining = earliest - now
        if remaining <= 0:
            return _MIN_SOCKET_TIMEOUT_S
        return min(_DEFAULT_SOCKET_TIMEOUT_S, max(_MIN_SOCKET_TIMEOUT_S, remaining))

    def _wake_listener(self) -> None:
        """Unblock recvfrom so the listener can adopt a nearer probe deadline."""
        try:
            self.socket.sendto(_WAKEUP_DATAGRAM, (self.meta_ip, self.meta_port))
        except OSError:
            pass

    def _expire_probes(self, now: float | None = None) -> list[ProbeResult]:
        """Mark unanswered probes past their deadline as timed out."""
        if now is None:
            now = time.monotonic()
        expired: list[ProbeResult] = []
        with self._probe_lock:
            due = [
                identifier
                for identifier, probe in self._outstanding.items()
                if now >= probe.deadline
            ]
            for identifier in due:
                probe = self._outstanding.pop(identifier)
                result = ProbeResult(
                    identifier=identifier,
                    destination=probe.destination,
                    status="timeout",
                    rtt=None,
                    completed_at=now,
                )
                self._probe_results.append(result)
                expired.append(result)
        for result in expired:
            self._log(
                f"Host {self.virtual_address} probe timed out.\n"
                f"     id={result.identifier}\n"
                f"     dest={result.destination}\n"
            )
        return expired

    def outstanding_probe_ids(self) -> set[int]:
        """Return a snapshot of identifiers still waiting for a reply."""
        with self._probe_lock:
            return set(self._outstanding)

    def probe_results(self) -> tuple[ProbeResult, ...]:
        """Return completed probe results in completion order."""
        with self._probe_lock:
            return tuple(self._probe_results)

    def rtt_samples(self) -> tuple[float, ...]:
        """Return successful probe RTT values in completion order."""
        with self._probe_lock:
            return tuple(
                result.rtt
                for result in self._probe_results
                if result.status == "success" and result.rtt is not None
            )

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

        if len(datagram) > MAX_PACKET_SIZE_B:
            self._log(
                f"Host {self.virtual_address} refused to build an "
                f"oversized packet.\n"
                f"     bytes={len(datagram)}\n"
                f"     max={MAX_PACKET_SIZE_B}\n"
            )
            raise ValueError(
                f"packet of {len(datagram)} bytes exceeds the "
                f"{MAX_PACKET_SIZE_B}-byte protocol maximum"
            )

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

    def _match_probe_reply(self, header) -> ProbeResult | None:
        """Match a reply to an outstanding probe; ignore invalid replies."""
        if not (header["type_flags"] & self.flags["reply"]):
            return None

        identifier = header["identifier"]
        source = header["source"]
        now = time.monotonic()

        with self._probe_lock:
            probe = self._outstanding.get(identifier)
            if probe is None:
                self._log(
                    f"Host {self.virtual_address} ignored unmatched reply.\n"
                    f"     id={identifier}\n"
                    f"     src={source}\n"
                )
                return None
            if source != probe.destination:
                self._log(
                    f"Host {self.virtual_address} ignored reply from "
                    f"unexpected source.\n"
                    f"     id={identifier}\n"
                    f"     src={source}\n"
                    f"     expected={probe.destination}\n"
                )
                return None
            if now > probe.deadline:
                # Late reply: do not record RTT; leave expiry to _expire_probes
                # or finalize timeout here so the id is freed promptly.
                self._outstanding.pop(identifier, None)
                result = ProbeResult(
                    identifier=identifier,
                    destination=probe.destination,
                    status="timeout",
                    rtt=None,
                    completed_at=now,
                )
                self._probe_results.append(result)
                self._log(
                    f"Host {self.virtual_address} ignored late reply.\n"
                    f"     id={identifier}\n"
                    f"     dest={probe.destination}\n"
                )
                return result

            self._outstanding.pop(identifier)
            rtt = now - probe.sent_at
            result = ProbeResult(
                identifier=identifier,
                destination=probe.destination,
                status="success",
                rtt=rtt,
                completed_at=now,
            )
            self._probe_results.append(result)

        self._log(
            f"Host {self.virtual_address} probe succeeded.\n"
            f"     id={result.identifier}\n"
            f"     dest={result.destination}\n"
            f"     rtt={result.rtt:.6f}s\n"
        )
        return result

    def _handle_datagram(self, data):
        """Process one received datagram (keep it, match probes, respond)."""
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

        # Measurement matching runs before echo responses.
        if head["type_flags"] & self.flags["reply"]:
            self._match_probe_reply(head)

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

    def send_probe(
        self,
        destination: int,
        payload: str | bytes = "",
        timeout: float | None = None,
    ) -> int:
        """Send an echo probe and track it until reply or timeout.

        :param destination: Virtual destination address.
        :param payload: Probe payload as text or bytes.
        :param timeout: Optional per-probe timeout in seconds; defaults to
            ``self.probe_timeout``.
        :return: The 16-bit message identifier used for the probe.
        :raises ValueError: if destination or timeout is invalid.
        :raises OSError: if the UDP send fails (no outstanding entry left).
        :raises RuntimeError: if no free identifiers remain.
        """
        if not isinstance(destination, int) or isinstance(destination, bool):
            raise ValueError("destination must be an integer")
        if not 0 <= destination <= 0xFFFF:
            raise ValueError("destination must be between 0 and 65535")

        if timeout is None:
            timeout_s = self.probe_timeout
        else:
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
                raise ValueError("timeout must be a positive number")
            if not (timeout > 0) or timeout != timeout or timeout == float("inf"):
                raise ValueError("timeout must be finite and positive")
            timeout_s = float(timeout)

        # Validate the payload size before registering any probe state so an
        # oversized probe cannot leave a pending entry behind.
        if isinstance(payload, (bytes, bytearray)):
            payload_size = len(payload)
        else:
            payload_size = len(str(payload).encode("utf-8"))
        if HEADER_SIZE + payload_size > MAX_PACKET_SIZE_B:
            raise ValueError(
                f"probe of {HEADER_SIZE + payload_size} bytes exceeds the "
                f"{MAX_PACKET_SIZE_B}-byte protocol maximum"
            )

        identifier = self._allocate_identifier()
        sent_at = time.monotonic()
        deadline = sent_at + timeout_s
        probe = OutstandingProbe(
            identifier=identifier,
            destination=destination,
            sent_at=sent_at,
            deadline=deadline,
        )

        with self._probe_lock:
            self._outstanding[identifier] = probe

        datagram = self._create_datagram(
            DEFAULT_TTL,
            self.flags["echo"],
            0,
            identifier,
            self.virtual_address,
            destination,
            payload,
        )

        try:
            self._send_datagram(datagram, destination)
        except OSError:
            with self._probe_lock:
                self._outstanding.pop(identifier, None)
            raise

        # Recalculate recv timeout against this probe's deadline.
        self._wake_listener()

        self._log(
            f"Host {self.virtual_address} started probe.\n"
            f"     id={identifier}\n"
            f"     dest={destination}\n"
            f"     timeout={timeout_s}s\n"
        )
        return identifier

    # ------------------------------------------------------------------
    # Loops
    # ------------------------------------------------------------------
    def listening_loop(self):
        """Runs in a background thread; receives and handles datagrams."""
        while not self._stop_event.is_set():
            try:
                self.socket.settimeout(self._socket_wait_timeout())
            except OSError:
                break

            try:
                data, _ = self.socket.recvfrom(UDP_MAX_SIZE)
            except socket.timeout:
                self._expire_probes()
                continue  # Nothing arrived; re-check the stop flag.
            except ConnectionResetError:
                self._expire_probes()
                continue  # Windows reports ICMP "port unreachable" this way.
            except OSError:
                break  # Socket was closed during shutdown.

            if data == _WAKEUP_DATAGRAM:
                self._expire_probes()
                continue

            if len(data) < HEADER_SIZE:
                self.malformed_drops += 1
                self._log(
                    f"Host {self.virtual_address} rejected a malformed "
                    f"packet: {len(data)}-byte datagram is shorter than "
                    f"the {HEADER_SIZE}-byte header "
                    f"(malformed_drops={self.malformed_drops}).\n"
                )
                self._expire_probes()
                continue

            if len(data) > MAX_PACKET_SIZE_B:
                self.malformed_drops += 1
                self._log(
                    f"Host {self.virtual_address} rejected a malformed "
                    f"packet: {len(data)}-byte datagram exceeds the "
                    f"{MAX_PACKET_SIZE_B}-byte maximum "
                    f"(malformed_drops={self.malformed_drops}).\n"
                )
                self._expire_probes()
                continue

            try:
                self._handle_datagram(data)
            except Exception as exc:  # Keep the listener alive on bad packets.
                self._log(f"Host {self.virtual_address} failed to handle "
                          f"a packet: {exc!r}")
            finally:
                self._expire_probes()

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

            use_probe = False
            tokens = line.split(maxsplit=1)
            if tokens[0].lower() == "echo" and len(tokens) > 1:
                use_probe = True
                line = tokens[1]

            try:
                dest_str, message = line.split(maxsplit=1)
                destination = int(dest_str)
                if not 0 <= destination <= 0xFFFF:
                    raise ValueError
            except ValueError:
                self._log("Usage: [echo] <dest 0-65535> <message>")
                continue

            if use_probe:
                try:
                    self.send_probe(destination, message)
                except (OSError, RuntimeError, ValueError) as error:
                    self._log(f"Probe send failed: {error}")
                continue

            try:
                datagram = self._create_datagram(DEFAULT_TTL,
                                                 0,
                                                 0,
                                                 self._new_identifier(),
                                                 self.virtual_address,
                                                 destination,
                                                 message)
                self._send_datagram(datagram, destination)
            except (OSError, ValueError) as error:
                self._log(f"Send failed: {error}")

    def start(self) -> None:
        """Start the background listening thread if it is not already running."""
        if self._listener_thread is not None and self._listener_thread.is_alive():
            return
        self._stop_event.clear()
        self._listener_thread = threading.Thread(
            target=self.listening_loop,
            name=f"host-{self.virtual_address}-listener",
            daemon=True,
        )
        self._listener_thread.start()

    def shutdown(self, join_timeout: float = 2.0) -> None:
        """Stop the listener and close the socket."""
        self._stop_event.set()
        try:
            self.socket.close()
        except OSError:
            pass
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=join_timeout)
        self._log(f"Host {self.virtual_address} shut down.")

    def run(self):
        """Start the listener thread, then run the user-input loop."""
        self.start()
        try:
            self.operation_loop()
        finally:
            self.shutdown()
