"""annotation -- turn the merged event stream into a reviewable, stored annotation.

- :mod:`session`      session lifecycle, queue and processing thread;
- :mod:`screenshots`  pre-action screenshots (time-indexed ring + live fallback);
- :mod:`store`        disk persistence (events, screenshots, metadata, export);
- :mod:`service`      FastAPI wiring.
"""

from __future__ import annotations

from .screenshots import ScreenshotSource
from .session import AnnotationSession
from .store import AnnotationStore

__all__ = [
    "AnnotationSession",
    "ScreenshotSource",
    "AnnotationStore",
]
