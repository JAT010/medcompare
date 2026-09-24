"""Graceful worker signal handling (P25).

Wires SIGINT/SIGTERM (where available) to an ``asyncio.Event`` so continuous
verification and monitoring workers drain the current cycle, close the
database cleanly, and exit without a stack trace. Idempotent per event loop.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import Callable

__all__ = ["install_worker_signal_handlers"]


def install_worker_signal_handlers(
    stop_event: asyncio.Event,
    *,
    on_signal: Callable[[str], None] | None = None,
) -> asyncio.Event:
    """Arrange for SIGINT/SIGTERM to set ``stop_event``.

    Returns the same event for convenient chaining. On platforms without a
    usable event loop signal policy (or outside the main thread), the
    KeyboardInterrupt path in the CLI remains the fallback.
    """
    loop = asyncio.get_running_loop()

    def _handler(signum: int) -> None:
        name = signal.Signals(signum).name
        if on_signal is not None:
            on_signal(name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handler, int(sig))
        except (NotImplementedError, RuntimeError, ValueError):
            # Windows without Proactor support / non-main thread: best effort.
            try:
                signal.signal(sig, lambda s, f: _handler(s))
            except (ValueError, OSError, AttributeError):
                pass

    if sys.platform == "win32":  # pragma: no cover - exercised via KeyboardInterrupt
        # asyncio loop.add_signal_handler is unavailable on Windows; rely on
        # signal.signal above when possible, else KeyboardInterrupt in CLI.
        pass

    return stop_event
