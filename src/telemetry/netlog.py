"""Structured logging for mini-internet network events (issue #31).

Every record is a single line, so records from concurrent router and host
threads cannot interleave mid-record::

    2026-10-21 00:40:00,123 | run=1f3a9c2e | INFO | netlog.router-3 | \
event=forward id=17 type=echo src=1 dest=2 next_hop=4 ttl=63

Fields, in order: timestamp, run identifier, level, device identifier,
then the event type followed by ``key=value`` pairs.

The run identifier is created once per process. Hosts and routers launched
as separate processes can share one identifier by setting the
``MININET_RUN_ID`` environment variable before starting them.
"""

import logging
import os
import uuid

RUN_ID = os.environ.get("MININET_RUN_ID") or uuid.uuid4().hex[:8]

_ROOT_LOGGER_NAME = "netlog"
_LOG_FORMAT = (
    f"%(asctime)s | run={RUN_ID} | %(levelname)s | %(name)s | %(message)s"
)


def _configure_root() -> None:
    """Attach the shared stderr handler to the ``netlog`` logger once."""
    root = logging.getLogger(_ROOT_LOGGER_NAME)
    if getattr(root, "_netlog_configured", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Keep device records out of any application root logger.
    root.propagate = False
    root._netlog_configured = True


def get_device_logger(device: str) -> logging.Logger:
    """Return the event logger for one device, e.g. ``router-3`` or ``host-1``."""
    _configure_root()
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{device}")


def format_event(event: str, **fields) -> str:
    """Render one event and its ``key=value`` fields as a single line.

    ``None`` values are omitted so callers can pass optional fields
    unconditionally.
    """
    parts = [f"event={event}"]
    parts.extend(
        f"{key}={value}" for key, value in fields.items() if value is not None
    )
    return " ".join(parts)
