#!/usr/bin/env python3
"""Annotation service.

Port: taken from ``ports.annotation`` in the config.

Receives the merged event stream from the QEMU log service, attaches a
pre-action screenshot to each event, computes screen coordinates, and persists
everything into an annotation session. The logic lives in
:class:`~gui_label_tool.annotation.session.AnnotationSession`; this module only
reads config and wires up FastAPI.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from gui_label_tool import get_config, require_key
from gui_label_tool.annotation.session import AnnotationSession
from gui_label_tool.qemu_log.parser import Event

logger = logging.getLogger(__name__)


def build_session_from_config() -> AnnotationSession:
    """Build an :class:`AnnotationSession` from ``config.yml``."""
    config = get_config()
    ann_cfg = require_key(config, "annotation", "annotation")

    storage_dir = require_key(ann_cfg, "storage_dir", "annotation")
    pre_screenshot_interval = float(ann_cfg.get("pre_screenshot_interval", 0.1))
    screenshot_retention = float(ann_cfg.get("screenshot_retention", 10.0))

    # The screenshot service port comes from vm.ports.screenshot.
    vm_ports = require_key(
        require_key(config, "vm", "vm"),
        "ports",
        "vm",
    )
    screenshot_port = int(require_key(vm_ports, "screenshot", "vm.ports"))
    screenshot_service_url = f"http://localhost:{screenshot_port}"

    return AnnotationSession(
        screenshot_service_url,
        storage_dir,
        pre_screenshot_interval=pre_screenshot_interval,
        screenshot_retention=screenshot_retention,
    )


def _annotation_port() -> int:
    """Port this service listens on."""
    ports = require_key(get_config(), "ports", "ports")
    return int(require_key(ports, "annotation", "ports"))


def create_app(session: Optional[AnnotationSession] = None) -> FastAPI:
    """Build the FastAPI app and bind handlers to the given session.

    The session is built from config when not supplied. Handlers are sync defs
    (they take locks and join threads); FastAPI runs them in a threadpool so the
    event loop is not blocked.
    """
    service = session or build_session_from_config()

    app = FastAPI(title="Annotation Service")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.service = service

    def _ok_or_400(result: Dict) -> Dict:
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result["message"])
        return result

    @app.post("/annotation/start")
    def start_annotation(
        annotation_name: str, annotator_id: str = None, annotation_id: str = None
    ):
        return _ok_or_400(
            service.start_annotation(annotation_name, annotator_id, annotation_id)
        )

    @app.post("/annotation/stop")
    def stop_annotation():
        return _ok_or_400(service.stop_annotation())

    @app.post("/annotation/freeze")
    def freeze_annotation():
        return _ok_or_400(service.freeze_annotation())

    @app.post("/annotation/finalize")
    def finalize_annotation():
        return _ok_or_400(service.finalize_annotation())

    @app.post("/annotation/event")
    def receive_event(event: Event):
        return _ok_or_400(service.receive_event(event))

    @app.get("/annotation/status")
    def get_status():
        return service.get_status()

    @app.get("/annotation/latest")
    def get_latest():
        return service.get_latest()

    @app.get("/annotation/events")
    def get_events():
        return service.get_events_list()

    @app.delete("/annotation/event/{event_id}")
    def delete_event(event_id: str):
        return _ok_or_400(service.delete_event(event_id))

    return app


# Module-level app for ``python -m`` and ``uvicorn module:app``.
app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = _annotation_port()
    service: AnnotationSession = app.state.service
    logger.info(
        "Annotation Service | screenshot=%s | storage=%s | port=%s",
        service.screenshot_service_url,
        service.storage_dir,
        port,
    )
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
