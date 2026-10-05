"""Userspace watchdog (``watchdog.driver: software``).

A fallback for hosts without a kernel watchdog device. A thread waits for
keepalive calls. When a keepalive is late, the thread calls the fence
function.

The fence function must not read or write the disk. A stalled disk is the
usual reason for a late keepalive.
"""
import logging
import time

from threading import Event, Thread
from typing import Callable, Optional

from .base import WatchdogBase

logger = logging.getLogger(__name__)


class SoftwareWatchdog(WatchdogBase):
    """Watchdog in a Python thread.

    It can not reset the host. It only runs the fence function in this
    process. It does not help when the whole process is frozen. Use a
    kernel watchdog device when you can.

    The timeout is 60 seconds until :meth:`set_timeout` is called.
    :attr:`poll_interval` is the longest sleep between deadline checks.
    """

    poll_interval = 1.0

    def __init__(self, fence: Callable[[], None]) -> None:
        """Create the watchdog. It is not running until :meth:`open` is called.

        :param fence: function to call when a keepalive is late.
        """
        self._fence = fence
        self._timeout = 60
        self._deadline = 0.0
        self._stop = Event()
        self._thread: Optional[Thread] = None

    @property
    def is_running(self) -> bool:
        return not self._stop.is_set() and self._thread is not None and self._thread.is_alive()

    @property
    def is_healthy(self) -> bool:
        return True

    def open(self) -> None:
        """Start the watchdog thread. Do nothing if it already runs."""
        if self.is_running:
            return
        # Each thread gets its own stop event. close() does not join
        # the thread, because the fence function can take a long time.
        self._stop = Event()
        self.keepalive()
        self._thread = Thread(target=self._run, args=(self._stop,), name='watchdog', daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Tell the watchdog thread to stop. Do not wait for it."""
        self._stop.set()

    def keepalive(self) -> None:
        self._deadline = time.monotonic() + self._timeout

    def get_timeout(self) -> int:
        return self._timeout

    def has_set_timeout(self) -> bool:
        return True

    def set_timeout(self, timeout: int) -> None:
        self._timeout = timeout
        self.keepalive()

    def _run(self, stop: Event) -> None:
        """Wait for the deadline. Call the fence function when it passes.

        :param stop: event that ends the thread.
        """
        # Sleep until the deadline, but at most poll_interval. A keepalive
        # can move the deadline while we sleep.
        while not stop.wait(max(0.0, min(self.poll_interval, self._deadline - time.monotonic()))):
            if time.monotonic() > self._deadline:
                logger.error('No watchdog keepalive for %s seconds. Fencing this node.', self._timeout)
                # The thread stays "running" while the fence runs. The
                # facade must not start a second thread in the meantime.
                try:
                    self._fence()
                except Exception:
                    logger.exception('Fence function failed')
                stop.set()
                return
