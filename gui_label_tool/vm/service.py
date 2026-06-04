#!/usr/bin/env python3
"""VM manager service.

Port: taken from ``ports.vm`` in the config.

FastAPI wiring around :class:`~gui_label_tool.vm.manager.VmManager`;
the container logic lives in :mod:`~gui_label_tool.vm.manager`.
"""

from __future__ import annotations

import atexit
import logging
import subprocess
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from gui_label_tool.vm.manager import (
    TARGET_OS,
    VM_LOG_DIR,
    VM_SERVICE_PORT,
    ContainerConfig,
    VmManager,
)

logger = logging.getLogger(__name__)

manager = VmManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    logger.info("FastAPI shutting down, cleaning up containers...")
    try:
        manager.shutdown()
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("error cleaning up containers on shutdown: %s", exc)


app = FastAPI(title="VM Manager Service (ubuntu/windows)", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _cleanup_on_exit() -> None:
    """Last-resort cleanup when the Python process exits."""
    logger.info("process exiting, running atexit container cleanup...")
    try:
        manager.shutdown()
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("error during atexit container cleanup: %s", exc)


atexit.register(_cleanup_on_exit)


# Sync defs: internally subprocess.run + time.sleep (startup can take ~35s), so
# FastAPI runs them in a threadpool to avoid blocking the uvicorn event loop.
@app.post("/container/start")
def api_start_container(config: ContainerConfig):
    result = manager.start_container(config)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.post("/container/stop/{annotation_id}")
def api_stop_container(annotation_id: str):
    result = manager.stop_container(annotation_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.get("/container/status/{annotation_id}")
def api_get_status(annotation_id: str):
    return manager.get_container_status(annotation_id)


@app.get("/containers/list")
def api_list_containers():
    return manager.list_active_containers()


@app.post("/containers/cleanup")
def api_cleanup_all():
    return manager.cleanup_all()


@app.get("/health")
def api_health_check():
    try:
        result = subprocess.run(["docker", "version"], capture_output=True, timeout=3)
        docker_available = result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        docker_available = False

    return {
        "status": "healthy" if docker_available else "unhealthy",
        "docker_available": docker_available,
        "active_containers": len(manager.active_containers),
        "os_type": TARGET_OS,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("VM Manager Service (os=%s)", TARGET_OS)
    logger.info("listening on port %s", VM_SERVICE_PORT)
    logger.info("log directory: %s", VM_LOG_DIR)
    uvicorn.run(app, host="0.0.0.0", port=VM_SERVICE_PORT, log_level="info")
