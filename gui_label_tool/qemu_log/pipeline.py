"""QEMU log pipeline.

Owns the single background thread that tails the QEMU input-event trace, decodes
each line with :class:`~gui_label_tool.qemu_log.parser.QemuEventParser`, merges
the stream with :class:`~gui_label_tool.qemu_log.parser.EventMerger`, and hands
each ready :class:`~gui_label_tool.qemu_log.parser.Event` to a ``sink`` callback.

Running everything on one thread lets the merger be lock-free: the loop both
feeds events (:meth:`EventMerger.submit`) and flushes idle merges
(:meth:`EventMerger.flush_due`) on every iteration, which also removes the
timer-cancellation races of a timer-per-pending design.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional

from gui_label_tool.qemu_log.parser import Event, EventMerger, QemuEventParser

logger = logging.getLogger(__name__)


class QemuLogPipeline:
    """Tail the QEMU trace, decode + merge it, and emit events to a sink."""

    def __init__(
        self,
        sink: Callable[[Event], None],
        *,
        text_debounce: float,
        scroll_debounce: float,
        key_debounce: float,
        double_click_threshold: float,
        double_click_distance_ratio: float,
        file_wait_timeout: float = 30.0,
        poll_interval: float = 0.01,
    ):
        self.sink = sink
        self.parser = QemuEventParser()
        self.merger = EventMerger(
            emit=sink,
            text_debounce=text_debounce,
            scroll_debounce=scroll_debounce,
            key_debounce=key_debounce,
            double_click_threshold=double_click_threshold,
            double_click_distance_ratio=double_click_distance_ratio,
        )
        self.file_wait_timeout = file_wait_timeout
        self.poll_interval = poll_interval

        self.log_path: Optional[str] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, log_path: str) -> bool:
        """Start tailing ``log_path``. Returns False if already running."""
        if self._running:
            return False
        self.log_path = log_path
        self.parser.reset()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("pipeline started on %s", log_path)
        return True

    def stop(self) -> bool:
        """Stop tailing and join the thread (pending merges are flushed)."""
        if not self._running:
            return False
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        return True

    def _run(self) -> None:
        if not self._wait_for_file():
            self._running = False
            return
        try:
            with open(self.log_path, "r") as f:
                f.seek(0, os.SEEK_END)
                while self._running:
                    line = f.readline()
                    if line:
                        self._handle_line(line)
                    else:
                        time.sleep(self.poll_interval)
                    self.merger.flush_due(time.time())
        except (FileNotFoundError, OSError) as exc:
            logger.error("tail error on %s: %s", self.log_path, exc)
        finally:
            # Emit any remaining pending merges, on this same thread.
            self.merger.flush_all()
            logger.info("pipeline stopped")

    def _handle_line(self, line: str) -> None:
        try:
            event = self.parser.parse_line(line)
        except Exception as exc:  # noqa: BLE001 - one bad line must not stop tailing
            logger.warning("parse error: %s", exc)
            return
        if event:
            self.merger.submit(event, time.time())

    def _wait_for_file(self) -> bool:
        waited = 0.0
        while (
            self._running
            and not os.path.exists(self.log_path)
            and waited < self.file_wait_timeout
        ):
            logger.info("waiting for log file: %s", self.log_path)
            time.sleep(2)
            waited += 2
        if not os.path.exists(self.log_path):
            logger.error("log file not created in time: %s", self.log_path)
            return False
        return True


__all__ = ["QemuLogPipeline"]
