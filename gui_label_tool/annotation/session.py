"""Annotation session.

Owns the session lifecycle (start / stop / freeze / finalize), the in-process
event queue and the single background processing thread. Per-event work is
delegated: screenshots to :class:`~gui_label_tool.annotation.screenshots.ScreenshotSource`,
disk IO to :class:`~gui_label_tool.annotation.store.AnnotationStore`.

Events arrive already merged (the QEMU log service does the merging), so this
module no longer debounces; it just associates a pre-action screenshot (by the
event's timestamp), computes screen coordinates, and persists.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from queue import Empty, Queue
from typing import Dict, List, Optional, Tuple

from PIL import Image

from gui_label_tool.annotation.screenshots import ScreenshotSource
from gui_label_tool.annotation.store import AnnotationStore
from gui_label_tool.qemu_log.parser import QEMU_MAX_X, QEMU_MAX_Y, Event

logger = logging.getLogger(__name__)

_CLICK_DISPLAY_TYPES = {"left", "right", "middle", "double"}


def qemu_to_screen(
    raw_x: int, raw_y: int, width: int, height: int
) -> Tuple[int, int, float, float]:
    """Scale raw QEMU coordinates (0..32767) onto a screenshot.

    Returns ``(x, y, rel_x, rel_y)`` where ``x/y`` are clamped screen pixels and
    ``rel_x/rel_y`` are in ``0..1``. Lives here because the screen position only
    exists once we have a screenshot, whose resolution is known only to this
    (annotation) stage.
    """
    x = int(raw_x / QEMU_MAX_X * (width - 1))
    y = int(raw_y / QEMU_MAX_Y * (height - 1))

    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))

    rel_x = x / (width - 1) if width > 1 else 0.0
    rel_y = y / (height - 1) if height > 1 else 0.0

    return x, y, rel_x, rel_y


def display_type_for(event_type: Optional[str], click_type: Optional[str]) -> str:
    """Normalize a raw action into the display label shown in the frontend.

    ``mouse_click`` + click_type -> ``left_click`` / ``right_click`` /
    ``double_click`` / ``middle_click``; other types are returned unchanged.
    """
    if event_type == "mouse_click":
        ct = (click_type or "left").lower()
        if ct not in _CLICK_DISPLAY_TYPES:
            ct = "left"
        return f"{ct}_click"
    return event_type


class AnnotationSession:
    """Session lifecycle, processing thread, and persistence orchestration."""

    def __init__(
        self,
        screenshot_service_url: str,
        storage_dir: str,
        pre_screenshot_interval: float = 0.1,
        screenshot_retention: float = 10.0,
    ):
        self.screenshot_service_url = screenshot_service_url
        self.storage_dir = storage_dir
        self.pre_screenshot_interval = pre_screenshot_interval
        self.screenshot_retention = screenshot_retention

        # Session state.
        self.is_active = False
        self.current_annotation_id: Optional[str] = None
        self.current_annotation_name: Optional[str] = None
        self.current_annotator_id: Optional[str] = None
        self.start_time: Optional[datetime] = None
        self.freeze_time: Optional[datetime] = None

        self.event_counter = 0
        self.latest_screenshot: Optional[str] = None
        self.latest_event: Optional[Dict] = None
        self.events_list: List[Dict] = []
        self.deleted_event_ids: set = set()

        self.event_queue: "Queue[Dict]" = Queue()
        self.processor_thread: Optional[threading.Thread] = None
        self.lock = threading.RLock()

        self.store: Optional[AnnotationStore] = None
        self.screenshots: Optional[ScreenshotSource] = None

    # ---- Session control ----

    def start_annotation(
        self,
        annotation_name: str,
        annotator_id: Optional[str] = None,
        annotation_id: Optional[str] = None,
    ) -> Dict:
        """Start a new annotation.

        ``annotation_id`` may be supplied by the frontend to stay consistent
        with the docker container; otherwise it is generated here.
        """
        with self.lock:
            if self.is_active:
                return {
                    "status": "error",
                    "message": "An annotation is already active; stop it first",
                }
            if not annotator_id or not annotator_id.strip():
                return {"status": "error", "message": "Missing annotator ID"}

            annotator_id = annotator_id.strip()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.current_annotation_id = (
                annotation_id.strip()
                if annotation_id and annotation_id.strip()
                else f"{annotator_id}_{timestamp}"
            )
            self.current_annotation_name = annotation_name
            self.current_annotator_id = annotator_id

            self.store = AnnotationStore(
                self.storage_dir, annotator_id, self.current_annotation_id
            )
            self.store.ensure_dirs()
            self.store.write_metadata(
                {
                    "annotation_id": self.current_annotation_id,
                    "annotator_id": annotator_id,
                    "task": annotation_name,
                    "start_time": datetime.now().isoformat(),
                    "screenshot_service": self.screenshot_service_url,
                    "screenshot_mode": "pre_action",
                }
            )

            # Reset state.
            self.is_active = True
            self.start_time = datetime.now()
            self.freeze_time = None
            self.event_counter = 0
            self.events_list = []
            self.deleted_event_ids = set()
            self.latest_screenshot = None
            self.latest_event = None
            self.event_queue = Queue()

            self.screenshots = ScreenshotSource(
                self.screenshot_service_url,
                interval=self.pre_screenshot_interval,
                retention=self.screenshot_retention,
            )
            self.screenshots.start()

            self.processor_thread = threading.Thread(
                target=self._process_events, daemon=True
            )
            self.processor_thread.start()

            logger.info(
                "annotation started: %s (annotator=%s, task=%s)",
                self.current_annotation_id,
                annotator_id,
                annotation_name,
            )
            return {
                "status": "success",
                "message": "Annotation started",
                "annotation_id": self.current_annotation_id,
                "annotator_id": annotator_id,
                "annotation_dir": str(self.store.dir),
                "screenshot_mode": "pre_action",
            }

    def stop_annotation(self) -> Dict:
        """Stop capturing and finalize immediately."""
        with self.lock:
            if not self.is_active:
                return {"status": "error", "message": "No active annotation"}
            self.freeze_time = self._stop_capture()
            return self._finalize_session(self.freeze_time, clear_state=True)

    def freeze_annotation(self) -> Dict:
        """Stop capturing but keep the session for later finalization."""
        with self.lock:
            if not self.store:
                return {"status": "error", "message": "No active annotation"}
            if not self.is_active:
                return {
                    "status": "error",
                    "message": "Annotation already stopped, awaiting confirmation",
                }
            self.freeze_time = self._stop_capture()
            duration = (
                (self.freeze_time - self.start_time).total_seconds()
                if self.start_time
                else 0.0
            )
            return {
                "status": "success",
                "message": "Annotation stopped; delete events in the frontend, then confirm",
                "annotation_id": self.current_annotation_id,
                "annotator_id": self.current_annotator_id,
                "annotation_dir": str(self.store.dir),
                "duration_seconds": round(duration, 2),
                "total_events": self.event_counter,
            }

    def finalize_annotation(self) -> Dict:
        """Confirm and write ``events_full.json``, then clear in-memory state."""
        with self.lock:
            if not self.store:
                return {"status": "error", "message": "No annotation to confirm"}
            end_time = self.freeze_time or datetime.now()
            return self._finalize_session(end_time, clear_state=True)

    def _stop_capture(self) -> datetime:
        """Stop capturing: drain the processing thread, then stop screenshots.

        Setting ``is_active`` False lets the processor finish the queue and exit;
        joining it (instead of a fixed sleep) guarantees every event is written
        before finalize reads them back.
        """
        freeze_time = datetime.now()
        self.is_active = False
        if self.processor_thread:
            self.processor_thread.join(timeout=10)
            self.processor_thread = None
        if self.screenshots:
            self.screenshots.stop()
            self.screenshots = None
        return freeze_time

    def _finalize_session(self, end_time: datetime, clear_state: bool = False) -> Dict:
        duration = (
            (end_time - self.start_time).total_seconds() if self.start_time else 0.0
        )
        metadata = self.store.update_metadata(
            {
                "end_time": end_time.isoformat(),
                "duration_seconds": duration,
                "total_events": self.event_counter,
            }
        )
        step_map = self.store.collect_steps(self.deleted_event_ids)
        full_task_file = self.store.write_full_task(
            {
                "annotation_id": self.current_annotation_id,
                "annotator_id": self.current_annotator_id,
                "task": metadata.get("task"),
                "start_time": metadata.get("start_time"),
                "end_time": metadata.get("end_time"),
                "duration_seconds": metadata.get("duration_seconds"),
                "total_events": metadata.get("total_events"),
                "deleted_events": list(self.deleted_event_ids),
                "final_events_count": len(step_map),
                "screenshot_mode": "pre_action",
                "steps": step_map,
            }
        )
        result = {
            "status": "success",
            "message": "Annotation stopped",
            "annotation_id": self.current_annotation_id,
            "annotator_id": self.current_annotator_id,
            "annotation_dir": str(self.store.dir),
            "duration_seconds": round(duration, 2),
            "total_events": self.event_counter,
            "full_task_file": str(full_task_file),
        }
        logger.info(
            "annotation finished: %s (events=%s, %.2fs)",
            self.current_annotation_id,
            self.event_counter,
            duration,
        )
        if clear_state:
            self._clear_state()
        return result

    def _clear_state(self) -> None:
        with self.lock:
            # Always drop the active flag: ``finalize`` may be called without a
            # prior ``freeze``, in which case ``_stop_capture`` never ran and
            # ``is_active`` would otherwise stay True forever, blocking any new
            # session until the service is restarted.
            self.is_active = False
            self.current_annotation_id = None
            self.current_annotation_name = None
            self.current_annotator_id = None
            self.freeze_time = None
            self.events_list = []
            self.deleted_event_ids = set()
            self.latest_event = None
            self.latest_screenshot = None
            self.event_counter = 0
            self.event_queue = Queue()
            self.store = None
            if self.screenshots:
                self.screenshots.stop()
                self.screenshots = None

    # ---- Event intake ----

    def receive_event(self, event: Event) -> Dict:
        """Receive a (already merged) event and enqueue it for processing."""
        if not self.is_active or not self.store:
            return {"status": "error", "message": "Annotation not started"}
        self.event_queue.put(event.model_dump())
        return {"status": "success", "message": "Event received"}

    # ---- Background processing ----

    def _process_events(self) -> None:
        logger.info("event processor thread started")
        while self.is_active or not self.event_queue.empty():
            try:
                event = self.event_queue.get(timeout=0.5)
            except Empty:
                continue
            try:
                self._process_single_event(event)
            except Exception as exc:  # noqa: BLE001 - one bad event must not kill the thread
                logger.warning("failed to process event: %s", exc)
        logger.info("event processor thread stopped")

    def _process_single_event(self, event: Dict) -> None:
        self.event_counter += 1
        event_id = f"{self.event_counter:04d}"
        orig_type = event.get("type")
        display_type = display_type_for(orig_type, event.get("click_type"))

        # Pre-action screenshot: the frame just before the event's timestamp.
        screenshot_path, screenshot_error = self._resolve_screenshot(event_id, event)

        screen_position = None
        relative_position = None
        if screenshot_path:
            screen_position, relative_position = self._compute_position(
                screenshot_path, event
            )

        event_data = {
            "event_id": event_id,
            "annotator_id": self.current_annotator_id,
            "annotation_id": self.current_annotation_id,
            "event": event,
            "screen_position": screen_position,
            "relative_position": relative_position,
            "screenshot": screenshot_path.name if screenshot_path else None,
            "has_screenshot": screenshot_path is not None,
            "screenshot_error": screenshot_error,
            "screenshot_mode": "pre_action",
            "timestamp": datetime.now().isoformat(),
            "type": display_type,
            "orig_type": orig_type,
        }
        self.store.write_event(event_id, orig_type, event_data)

        if screenshot_path:
            self.latest_screenshot = str(screenshot_path)
        self.latest_event = event_data

        with self.lock:
            self.events_list.append(
                {
                    "event_id": event_id,
                    "type": display_type,
                    "timestamp": event.get("timestamp", ""),
                    "has_screenshot": screenshot_path is not None,
                    "screenshot_path": (
                        str(screenshot_path) if screenshot_path else None
                    ),
                    "event_data": event,
                    "screen_position": screen_position,
                    "relative_position": relative_position,
                    "annotator_id": self.current_annotator_id,
                }
            )
        logger.info(
            "processed event_%s (%s) by %s",
            event_id,
            display_type,
            self.current_annotator_id,
        )

    def _resolve_screenshot(self, event_id: str, event: Dict):
        """Return ``(path|None, error|None)`` for this event's screenshot."""
        if not self.screenshots:
            return None, "screenshot_unavailable"
        ts = self._event_epoch(event.get("timestamp"))
        data = self.screenshots.frame_at(ts)
        if data is None:
            # No buffered pre-action frame; fall back to a live grab.
            data = self.screenshots.get_live()
        if data is None:
            logger.warning("no screenshot for event_%s", event_id)
            return None, "screenshot_unavailable"
        try:
            return self.store.save_screenshot(event_id, data), None
        except OSError as exc:
            logger.warning("failed to save screenshot: %s", exc)
            return None, f"screenshot_save_failed: {exc}"

    def _compute_position(self, screenshot_path, event: Dict):
        """Return ``(screen, relative)`` coords, opening the image only for size."""
        try:
            with Image.open(screenshot_path) as im:
                width, height = im.size
        except (OSError, ValueError) as exc:
            logger.warning("failed to read screenshot size: %s", exc)
            return None, None
        position = event.get("position") or {}
        raw_x = int(position.get("x", 0) or 0)
        raw_y = int(position.get("y", 0) or 0)
        x, y, rel_x, rel_y = qemu_to_screen(raw_x, raw_y, width, height)
        return {"x": x, "y": y}, {"x": rel_x, "y": rel_y}

    @staticmethod
    def _event_epoch(timestamp: Optional[str]) -> float:
        if timestamp:
            try:
                return datetime.fromisoformat(timestamp).timestamp()
            except ValueError:
                pass
        return time.time()

    # ---- Status / listing / deletion ----

    def get_status(self) -> Dict:
        with self.lock:
            screenshot_stats = self.screenshots.get_stats() if self.screenshots else {}
            return {
                "is_active": self.is_active,
                "current_annotation_id": self.current_annotation_id,
                "current_annotation_name": self.current_annotation_name,
                "current_annotator_id": self.current_annotator_id,
                "event_counter": self.event_counter,
                "queue_size": self.event_queue.qsize(),
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "screenshot_mode": "pre_action",
                "screenshot_source": screenshot_stats,
            }

    def get_latest(self) -> Dict:
        return {
            "latest_screenshot": self.latest_screenshot,
            "latest_event": self.latest_event,
        }

    def get_events_list(self) -> Dict:
        with self.lock:
            return {
                "status": "success",
                "events": self.events_list.copy(),
                "total": len(self.events_list),
                "deleted": list(self.deleted_event_ids),
                "annotator_id": self.current_annotator_id,
                "is_active": self.is_active,
            }

    def delete_event(self, event_id: str) -> Dict:
        with self.lock:
            if not self.store:
                return {"status": "error", "message": "No active annotation"}
            if not any(evt["event_id"] == event_id for evt in self.events_list):
                return {"status": "error", "message": f"Event {event_id} not found"}
            self.deleted_event_ids.add(event_id)
            logger.info("marked event deleted: %s", event_id)
            return {
                "status": "success",
                "message": f"Event {event_id} marked as deleted",
                "deleted_count": len(self.deleted_event_ids),
            }


__all__ = ["AnnotationSession"]
