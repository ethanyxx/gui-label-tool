#!/usr/bin/env python3
"""QEMU log service - runs the tail/decode/merge pipeline and forwards events.

Port: taken from ``ports.qemu_log`` in the config.

Tails the QEMU ``input_event`` trace, merges it into semantic events, and POSTs
each event to the annotation service's ``/annotation/event``. The interpretation
logic lives in :mod:`~gui_label_tool.qemu_log.pipeline` (and the pure decoder /
merger it composes); this module is only config + HTTP wiring.
"""

from __future__ import annotations

import logging

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from gui_label_tool import get_config, require_key
from gui_label_tool.qemu_log.parser import Event
from gui_label_tool.qemu_log.pipeline import QemuLogPipeline

logger = logging.getLogger(__name__)

# Endpoint on the annotation service that receives events.
EVENT_ENDPOINT = "/annotation/event"

CONFIG = get_config()

PORTS = require_key(CONFIG, "ports", "ports")
QEMU_PORT = int(require_key(PORTS, "qemu_log", "ports"))
ANNOTATION_PORT = int(require_key(PORTS, "annotation", "ports"))
ANNOTATION_SERVICE_URL = f"http://localhost:{ANNOTATION_PORT}"

MERGE_CFG = require_key(CONFIG, "qemu_log", "qemu_log")

# Reused HTTP session for keep-alive when forwarding events.
_session = requests.Session()


def _forward(event: Event) -> None:
    """Sink: POST a merged event to the annotation service."""
    try:
        resp = _session.post(
            f"{ANNOTATION_SERVICE_URL}{EVENT_ENDPOINT}",
            json=event.model_dump(),
            timeout=2,
        )
        if resp.status_code != 200:
            logger.warning("forward failed (%s): %s", resp.status_code, event.type)
    except requests.RequestException as exc:
        logger.warning("forward error: %s", exc)


pipeline = QemuLogPipeline(
    _forward,
    text_debounce=float(require_key(MERGE_CFG, "text_debounce", "qemu_log")),
    scroll_debounce=float(require_key(MERGE_CFG, "scroll_debounce", "qemu_log")),
    key_debounce=float(require_key(MERGE_CFG, "key_debounce", "qemu_log")),
    double_click_threshold=float(
        require_key(MERGE_CFG, "double_click_threshold", "qemu_log")
    ),
    double_click_distance_ratio=float(
        require_key(MERGE_CFG, "double_click_distance_ratio", "qemu_log")
    ),
)


# ============= FastAPI app =============

app = FastAPI(title="QEMU Log Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Sync defs: start/stop spawn/join a thread, so let FastAPI run them in its
# threadpool instead of blocking the event loop.
@app.post("/start")
def start(log_path: str):
    if not pipeline.start(log_path):
        raise HTTPException(status_code=400, detail="Already running")
    return {"status": "success", "message": "Started", "log_path": log_path}


@app.post("/stop")
def stop():
    if not pipeline.stop():
        raise HTTPException(status_code=400, detail="Not running")
    return {"status": "success", "message": "Stopped"}


@app.get("/status")
def status():
    return {"is_running": pipeline.is_running, "log_path": pipeline.log_path}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("QEMU Log Service -> %s | port %s", ANNOTATION_SERVICE_URL, QEMU_PORT)
    uvicorn.run(app, host="0.0.0.0", port=QEMU_PORT, log_level="info")
