"""Annotation persistence.

All disk IO for one annotation lives here: directory layout, per-event JSON,
screenshots, metadata, and the final ``events_full.json``. Keeping this separate
from :class:`~gui_label_tool.annotation.session.AnnotationSession` lets the
session stay focused on lifecycle and the processing loop.

Layout::

    <storage_dir>/<annotator_id>/<annotation_id>/
        metadata.json
        events/event_<id>_<orig_type>.json
        screenshots/screenshot_<id>.png
        events_full.json        (written on finalize)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Set

logger = logging.getLogger(__name__)


class AnnotationStore:
    """Disk persistence for a single annotation."""

    def __init__(self, storage_dir: str, annotator_id: str, annotation_id: str):
        self.dir = Path(storage_dir) / annotator_id / annotation_id
        self.events_dir = self.dir / "events"
        self.screenshots_dir = self.dir / "screenshots"

    def ensure_dirs(self) -> None:
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    # ---- metadata ----

    def write_metadata(self, metadata: Dict) -> None:
        self._dump(self.dir / "metadata.json", metadata)

    def read_metadata(self) -> Dict:
        with open(self.dir / "metadata.json", "r") as f:
            return json.load(f)

    def update_metadata(self, updates: Dict) -> Dict:
        metadata = self.read_metadata()
        metadata.update(updates)
        self.write_metadata(metadata)
        return metadata

    # ---- per-event ----

    def save_screenshot(self, event_id: str, data: bytes) -> Path:
        path = self.screenshots_dir / f"screenshot_{event_id}.png"
        with open(path, "wb") as f:
            f.write(data)
        return path

    def write_event(self, event_id: str, orig_type: str, event_data: Dict) -> Path:
        path = self.events_dir / f"event_{event_id}_{orig_type}.json"
        self._dump(path, event_data)
        return path

    # ---- finalize ----

    def collect_steps(self, deleted_ids: Set[str]) -> Dict[str, Dict]:
        """Read event files in order, skipping deleted ones, into a step map."""
        step_map: Dict[str, Dict] = {}
        step_index = 1
        for ef in sorted(self.events_dir.glob("event_*.json")):
            try:
                with open(ef, "r") as f:
                    event_data = json.load(f)
            except (OSError, ValueError) as exc:
                logger.warning("failed to read event file %s: %s", ef, exc)
                continue
            if event_data.get("event_id") in deleted_ids:
                continue
            step_map[f"step{step_index}"] = event_data
            step_index += 1
        return step_map

    def write_full_task(self, full_task: Dict) -> Path:
        path = self.dir / "events_full.json"
        self._dump(path, full_task)
        return path

    @staticmethod
    def _dump(path: Path, data: Dict) -> None:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


__all__ = ["AnnotationStore"]
