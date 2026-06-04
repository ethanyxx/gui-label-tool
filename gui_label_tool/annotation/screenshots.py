"""Screenshot source.

Continuously polls the screenshot service in the background and keeps a short,
time-indexed ring of recent frames. Given an action's timestamp, it returns the
frame captured just *before* that moment (the pre-action screenshot), so merged
events that arrive after a debounce delay can still recover the right frame.

Pure screenshot IO -- no annotation logic.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class ScreenshotSource:
    """Background poller keeping a time-indexed ring of recent frames.

    ``retention`` must comfortably exceed the longest merge window, since a
    merged event's timestamp can be several seconds before it is processed.
    """

    def __init__(
        self,
        screenshot_service_url: str,
        interval: float = 0.1,
        retention: float = 10.0,
    ):
        self.screenshot_service_url = screenshot_service_url
        # Clamp the minimum interval to avoid hammering the backend.
        self.interval = max(interval, 0.05)
        self.retention = retention

        self.lock = threading.Lock()
        # (capture_time_epoch, png_bytes), oldest first.
        self._frames: Deque[Tuple[float, bytes]] = deque()

        self.is_running = False
        self.thread: Optional[threading.Thread] = None

        self.total = 0
        self.failed = 0
        self._consecutive_failures = 0

    def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        logger.info(
            "screenshot source started (interval=%ss, retention=%ss)",
            self.interval,
            self.retention,
        )

    def stop(self) -> None:
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None
        with self.lock:
            self._frames.clear()
        logger.info("screenshot source stopped")

    def _loop(self) -> None:
        # Reuse a Session to avoid TCP port exhaustion.
        with requests.Session() as session:
            while self.is_running:
                start_ts = time.time()
                try:
                    resp = session.get(
                        f"{self.screenshot_service_url}/screenshot", timeout=1.0
                    )
                    if resp.status_code == 200:
                        self._add_frame(resp.content)
                    else:
                        self._on_failure()
                except requests.RequestException as exc:
                    self._on_failure()
                    if self.failed % 20 == 1:
                        logger.warning("screenshot poll error: %s", exc)

                elapsed = time.time() - start_ts
                time.sleep(max(0.0, self.interval - elapsed))

    def _add_frame(self, data: bytes) -> None:
        now = time.time()
        with self.lock:
            self._frames.append((now, data))
            self._consecutive_failures = 0
            self._prune(now)
        self.total += 1

    def _prune(self, now: float) -> None:
        cutoff = now - self.retention
        while self._frames and self._frames[0][0] < cutoff:
            self._frames.popleft()

    def _on_failure(self) -> None:
        self.failed += 1
        self._consecutive_failures += 1

    def frame_at(self, ts: float) -> Optional[bytes]:
        """Return the most recent frame captured at or before ``ts``."""
        with self.lock:
            chosen: Optional[bytes] = None
            for frame_ts, data in self._frames:
                if frame_ts <= ts:
                    chosen = data
                else:
                    break
            return chosen

    def get_live(self) -> Optional[bytes]:
        """Fetch a fresh frame now (fallback when the ring has nothing usable)."""
        try:
            resp = requests.get(f"{self.screenshot_service_url}/screenshot", timeout=5)
            if resp.status_code == 200:
                return resp.content
            logger.warning("live screenshot failed: %s", resp.status_code)
        except requests.RequestException as exc:
            logger.warning("live screenshot error: %s", exc)
        return None

    def get_stats(self) -> Dict:
        with self.lock:
            count = len(self._frames)
        return {
            "total": self.total,
            "failed": self.failed,
            "consecutive_failures": self._consecutive_failures,
            "buffered_frames": count,
            "interval": self.interval,
            "retention": self.retention,
        }


__all__ = ["ScreenshotSource"]
