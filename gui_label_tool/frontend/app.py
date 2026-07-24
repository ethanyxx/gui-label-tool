#!/usr/bin/env python3
"""Frontend service: web console for driving a labeling session.

Port: taken from ``ports.frontend`` in the config.

A FastAPI app with two responsibilities:

1. **Orchestration API** (``/api/*``): thin JSON endpoints the browser calls to
   start/freeze/finalize a session. Each action fans out to the three pipeline
   services (``vm`` / ``annotation`` / ``qemu_log``) in the right order.
2. **Static hosting**: serves the built React app (``web/`` sources compiled
   into ``gui_label_tool/frontend/static/``) and the annotation screenshots
   (``/screenshots/*``) so the browser can load them over HTTP.

State model: the browser is the source of truth for *locally excluded* event
ids (synced to the annotation service only on finalize); this module keeps a
small record of the current session (id / vnc url) so a page refresh can
restore an in-progress session.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gui_label_tool import get_config, require_key

logger = logging.getLogger(__name__)

# ============= Config =============
CONFIG = get_config()

PORTS = require_key(CONFIG, "ports", "")
FRONTEND_PORT = PORTS["frontend"]

VM_URL = f"http://localhost:{PORTS['vm']}"
ANNOTATION_URL = f"http://localhost:{PORTS['annotation']}"
QEMU_LOG_URL = f"http://localhost:{PORTS['qemu_log']}"

VM_CFG = require_key(CONFIG, "vm", "")
VNC_HOST = require_key(VM_CFG, "vnc_host", "vm")
VNC_PORT = int(require_key(VM_CFG, "ports", "vm")["vnc"])

DEFAULT_VNC_URL = (
    f"http://{VNC_HOST}:{VNC_PORT}/vnc.html?view_only=0&autoconnect=1&resize=scale"
)

# Where the annotation service writes trajectories; served under /screenshots.
STORAGE_DIR = (
    Path(CONFIG.get("annotation", {}).get("storage_dir", "./data/annotations"))
    .expanduser()
    .resolve()
)

STATIC_DIR = Path(__file__).parent / "static"


# ============= Current session record =============
# Lets a refreshed page recover the VNC url; the authoritative session state
# lives in the annotation service.
class _SessionState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.annotation_id: Optional[str] = None
        self.annotator_id: Optional[str] = None
        self.task: Optional[str] = None
        self.vnc_url: Optional[str] = None

    def set(self, annotation_id, annotator_id, task, vnc_url) -> None:
        with self.lock:
            self.annotation_id = annotation_id
            self.annotator_id = annotator_id
            self.task = task
            self.vnc_url = vnc_url

    def clear(self) -> None:
        self.set(None, None, None, None)

    def snapshot(self) -> Dict:
        with self.lock:
            return {
                "annotation_id": self.annotation_id,
                "annotator_id": self.annotator_id,
                "task": self.task,
                "vnc_url": self.vnc_url,
            }


SESSION = _SessionState()


# ============= Orchestration (ported from the Gradio console) =============
class ServiceOrchestrator:
    """Fan out one user action to the pipeline services, in order."""

    @staticmethod
    def sync_deleted_to_backend(deleted_ids: Set[str]) -> Dict:
        results = []
        for event_id in sorted(deleted_ids):
            try:
                resp = requests.delete(
                    f"{ANNOTATION_URL}/annotation/event/{event_id}", timeout=5
                )
                if resp.status_code == 200:
                    results.append(f"✓ {event_id}")
                else:
                    results.append(f"⚠ {event_id}: {resp.status_code}")
            except requests.RequestException as e:
                results.append(f"✗ {event_id}: {e}")
        return {"synced": len(deleted_ids), "details": results}

    @staticmethod
    def _ensure_clean_state_before_start() -> None:
        # 1) Stop the log parser first.
        try:
            requests.post(f"{QEMU_LOG_URL}/stop", timeout=5)
        except requests.RequestException as e:
            logger.warning("[cleanup] stop log parser failed: %s", e)

        # 2) Tear down any leftover annotation session, active or frozen.
        try:
            status = requests.get(
                f"{ANNOTATION_URL}/annotation/status", timeout=3
            ).json()
            if status.get("current_annotation_id"):
                endpoint = (
                    "/annotation/stop"
                    if status.get("is_active")
                    else "/annotation/finalize"
                )
                try:
                    logger.info("[cleanup] leftover session, calling %s", endpoint)
                    requests.post(f"{ANNOTATION_URL}{endpoint}", timeout=10)
                except requests.RequestException as e:
                    logger.warning("[cleanup] call %s failed: %s", endpoint, e)
        except requests.RequestException as e:
            logger.warning("[cleanup] get annotation status failed: %s", e)

        # 3) Clean up all containers.
        try:
            requests.post(f"{VM_URL}/containers/cleanup", timeout=15)
        except requests.RequestException as e:
            logger.warning("[cleanup] docker cleanup failed: %s", e)

    @staticmethod
    def start_full_stack(annotator_id: str, task: str) -> Dict:
        ServiceOrchestrator._ensure_clean_state_before_start()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        annotation_id = f"{annotator_id}_{timestamp}"

        log = [
            f"Annotator: {annotator_id}",
            f"Task: {task}",
            "Starting VM container...",
        ]

        try:
            docker_res = requests.post(
                f"{VM_URL}/container/start",
                json={"annotation_id": annotation_id},
                timeout=180,
            )
            if docker_res.status_code != 200:
                return {
                    "success": False,
                    "message": f"Failed to start the VM container: {docker_res.text}",
                }
            docker_info = docker_res.json()
            log.append("✓ VM container started")
            time.sleep(10)

            ann_res = requests.post(
                f"{ANNOTATION_URL}/annotation/start",
                params={
                    "annotation_name": task,
                    "annotator_id": annotator_id,
                    "annotation_id": annotation_id,
                },
                timeout=10,
            )
            if ann_res.status_code != 200:
                return {
                    "success": False,
                    "message": f"Failed to start the annotation session: {ann_res.text}",
                }
            log.append("✓ Annotation session started")

            requests.post(f"{QEMU_LOG_URL}/stop", timeout=5)
            log_res = requests.post(
                f"{QEMU_LOG_URL}/start",
                params={"log_path": docker_info["log_file"]},
                timeout=10,
            )
            if log_res.status_code != 200:
                return {
                    "success": False,
                    "message": f"Failed to start the log parser: {log_res.text}",
                }
            log.append("✓ Log parser started")

        except requests.RequestException as e:
            return {"success": False, "message": f"Startup error: {e}"}

        log.append("Recording. Interact with the desktop on the right.")
        return {
            "success": True,
            "message": "\n".join(log),
            "annotation_id": annotation_id,
            "annotator_id": annotator_id,
            "vnc_url": docker_info.get("vnc_url") or DEFAULT_VNC_URL,
        }

    @staticmethod
    def freeze_recording() -> Dict:
        log = ["Stopping capture..."]
        try:
            requests.post(f"{QEMU_LOG_URL}/stop", timeout=5)
            log.append("✓ Log parser stopped")
        except requests.RequestException as e:
            log.append(f"⚠ {e}")

        try:
            freeze_res = requests.post(f"{ANNOTATION_URL}/annotation/freeze", timeout=10)
            if freeze_res.status_code != 200:
                return {"success": False, "message": f"Stop failed: {freeze_res.text}"}
            data = freeze_res.json()
            log.append(f"✓ {data.get('total_events', 0)} event(s) captured")
            log.append("Review the trajectory, exclude bad events, then confirm.")
        except requests.RequestException as e:
            return {"success": False, "message": f"Stop failed: {e}"}

        return {"success": True, "message": "\n".join(log)}

    @staticmethod
    def finalize_annotation(annotation_id: str, excluded: Set[str]) -> Dict:
        log = []

        if excluded:
            log.append(f"Syncing {len(excluded)} exclusion(s)...")
            sync = ServiceOrchestrator.sync_deleted_to_backend(excluded)
            log.append(f"✓ Synced ({sync['synced']})")

        try:
            requests.post(f"{QEMU_LOG_URL}/stop", timeout=5)
        except requests.RequestException:
            pass

        log.append("Writing trajectory JSON...")
        try:
            fin_res = requests.post(f"{ANNOTATION_URL}/annotation/finalize", timeout=20)
            if fin_res.status_code != 200:
                return {"success": False, "message": f"Confirm failed: {fin_res.text}"}
            data = fin_res.json()
            log.append("✓ Saved")
        except requests.RequestException as e:
            return {"success": False, "message": f"Confirm failed: {e}"}

        try:
            requests.post(f"{VM_URL}/container/stop/{annotation_id}", timeout=60)
            log.append("✓ VM container stopped")
        except requests.RequestException:
            log.append("⚠ Could not stop the VM container (stop it manually)")

        log.append("Done. You can start the next session.")
        return {
            "success": True,
            "message": "\n".join(log),
            "full_task_file": data.get("full_task_file"),
        }


# ============= Helpers =============
def _screenshot_url(screenshot_path: Optional[str]) -> Optional[str]:
    """Map an absolute screenshot path to its /screenshots/* URL (or None)."""
    if not screenshot_path:
        return None
    try:
        rel = Path(screenshot_path).resolve().relative_to(STORAGE_DIR)
    except ValueError:
        return None
    return f"/screenshots/{rel.as_posix()}"


def _fetch_annotation_status() -> Optional[Dict]:
    try:
        return requests.get(f"{ANNOTATION_URL}/annotation/status", timeout=3).json()
    except requests.RequestException:
        return None


# ============= FastAPI app =============
app = FastAPI(title="GUI Label Tool Frontend")


class StartRequest(BaseModel):
    annotator_id: str
    task: str


class FinalizeRequest(BaseModel):
    excluded: List[str] = []


@app.get("/api/config")
def api_config():
    return {"default_vnc_url": DEFAULT_VNC_URL}


@app.get("/api/status")
def api_status():
    """Combined poll endpoint: backend session state + this service's record."""
    status = _fetch_annotation_status()
    session = SESSION.snapshot()

    if status is None:
        return {"connected": False, "phase": "disconnected", "session": session}

    if status.get("is_active"):
        phase = "running"
    elif status.get("current_annotation_id"):
        phase = "paused"
    else:
        phase = "idle"

    # If the annotation service knows a session this process does not (e.g. the
    # frontend restarted mid-session), adopt it so a page refresh can restore.
    if status.get("current_annotation_id") and not session["annotation_id"]:
        SESSION.set(
            status.get("current_annotation_id"),
            status.get("current_annotator_id"),
            status.get("current_annotation_name"),
            DEFAULT_VNC_URL,
        )
        session = SESSION.snapshot()

    return {
        "connected": True,
        "phase": phase,
        "event_counter": status.get("event_counter", 0),
        "start_time": status.get("start_time"),
        "session": session,
    }


@app.get("/api/events")
def api_events():
    status = _fetch_annotation_status()
    if not status or not status.get("current_annotation_id"):
        return {"events": [], "deleted": []}
    try:
        resp = requests.get(f"{ANNOTATION_URL}/annotation/events", timeout=3)
        if resp.status_code != 200:
            return {"events": [], "deleted": []}
        data = resp.json()
    except requests.RequestException:
        return {"events": [], "deleted": []}

    for evt in data.get("events", []):
        evt["screenshot_url"] = _screenshot_url(evt.get("screenshot_path"))
        evt.pop("screenshot_path", None)  # do not leak server paths
    return {"events": data.get("events", []), "deleted": data.get("deleted", [])}


@app.post("/api/session/start")
def api_session_start(req: StartRequest):
    annotator_id = req.annotator_id.strip()
    task = req.task.strip()
    if not annotator_id:
        return {"success": False, "message": "Please enter an annotator ID"}
    if not task:
        return {"success": False, "message": "Please enter a task name"}

    result = ServiceOrchestrator.start_full_stack(annotator_id, task)
    if result["success"]:
        SESSION.set(result["annotation_id"], annotator_id, task, result["vnc_url"])
    return result


@app.post("/api/session/freeze")
def api_session_freeze():
    if not SESSION.snapshot()["annotation_id"]:
        return {"success": False, "message": "No active session"}
    return ServiceOrchestrator.freeze_recording()


@app.post("/api/session/finalize")
def api_session_finalize(req: FinalizeRequest):
    session = SESSION.snapshot()
    if not session["annotation_id"]:
        return {"success": False, "message": "No session to confirm"}
    result = ServiceOrchestrator.finalize_annotation(
        session["annotation_id"], set(req.excluded)
    )
    if result["success"]:
        SESSION.clear()
    return result


# ============= Static hosting =============
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/screenshots", StaticFiles(directory=STORAGE_DIR), name="screenshots")

if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="app")
else:

    @app.get("/")
    def missing_build():
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Web assets not built. Run `npm install && npm run build` "
                    "in the web/ directory, then restart this service."
                )
            },
        )


@app.exception_handler(404)
async def spa_fallback(request, exc):
    """Serve the SPA entrypoint for unknown non-API paths (client routing)."""
    if request.url.path.startswith(("/api/", "/screenshots/")):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse(status_code=404, content={"detail": "Not found"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("GUI Label Tool frontend")
    logger.info("open: http://0.0.0.0:%s", FRONTEND_PORT)
    uvicorn.run(app, host="0.0.0.0", port=FRONTEND_PORT, log_level="info")
