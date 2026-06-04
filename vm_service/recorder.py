#!/usr/bin/env python3
"""Screenshot Service - in-memory, stable.

Runs inside the QEMU/X11 guest and serves PNG screenshots over HTTP.

Highlights:
1. fully in-memory (no disk I/O);
2. persistent X11 connection (no connection exhaustion);
3. thread-safe.
"""

from __future__ import annotations

import array
import ctypes
import ctypes.util
import io
import logging
import os
import struct
import subprocess
import threading
import time
from subprocess import CalledProcessError, TimeoutExpired

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

# Port this service listens on (inside the guest).
SCREENSHOT_PORT = 5001

app = FastAPI(title="Screenshot Service (in-memory)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global lock: serialize X11 access to avoid segfaults from concurrent calls.
X11_LOCK = threading.Lock()

# Stats.
stats = {
    "requests": 0,
    "success": 0,
    "failed": 0,
    "avg_latency_ms": 0.0,
    "backend": {
        "scrot_available": False,
        "import_available": False,
    },
}

# =========================
#  Backend detection
# =========================


def detect_backends():
    """Detect whether scrot / import are available."""
    scrot_ok = subprocess.run(["which", "scrot"], capture_output=True).returncode == 0
    import_ok = subprocess.run(["which", "import"], capture_output=True).returncode == 0

    stats["backend"]["scrot_available"] = scrot_ok
    stats["backend"]["import_available"] = import_ok

    logger.info("backend detection: scrot=%s, import=%s", scrot_ok, import_ok)
    if not (scrot_ok or import_ok):
        logger.warning(
            "no screenshot backend found (scrot / import); all captures will fail"
        )

    return scrot_ok, import_ok


SCROT_AVAILABLE, IMPORT_AVAILABLE = detect_backends()

# =========================
#  XFixes cursor (persistent)
# =========================

PIXEL_DATA_PTR = ctypes.POINTER(ctypes.c_ulong)
Atom = ctypes.c_ulong


class XFixesCursorImage(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
        ("width", ctypes.c_ushort),
        ("height", ctypes.c_ushort),
        ("xhot", ctypes.c_ushort),
        ("yhot", ctypes.c_ushort),
        ("cursor_serial", ctypes.c_ulong),
        ("pixels", PIXEL_DATA_PTR),
        ("atom", Atom),
        ("name", ctypes.c_char_p),
    ]


class Display(ctypes.Structure):
    pass


class CursorManager:
    """Holds a persistent X11 connection to avoid reopening the Display."""

    def __init__(self):
        self.display = None
        self.XFixeslib = None
        self.xlib = None
        self.XFixesGetCursorImage = None
        self.valid = False
        self._init_x11()

    def _init_x11(self):
        try:
            display_str = os.environ.get("DISPLAY", ":0").encode("utf-8")

            # Load libraries.
            xfixes_path = ctypes.util.find_library("Xfixes")
            x11_path = ctypes.util.find_library("X11")

            if not xfixes_path or not x11_path:
                logger.warning("libX11 or libXfixes not found; cursor overlay disabled")
                return

            self.XFixeslib = ctypes.cdll.LoadLibrary(xfixes_path)
            self.xlib = ctypes.cdll.LoadLibrary(x11_path)

            # Bind functions.
            self.XFixesGetCursorImage = self.XFixeslib.XFixesGetCursorImage
            self.XFixesGetCursorImage.restype = ctypes.POINTER(XFixesCursorImage)
            self.XFixesGetCursorImage.argtypes = [ctypes.POINTER(Display)]

            XOpenDisplay = self.xlib.XOpenDisplay
            XOpenDisplay.restype = ctypes.POINTER(Display)
            XOpenDisplay.argtypes = [ctypes.c_char_p]

            # Open the Display.
            self.display = XOpenDisplay(display_str)
            if not self.display:
                logger.warning(
                    "could not open Display %s; cursor overlay disabled", display_str
                )
                return

            self.valid = True
            logger.info("X11 connection established (cursor manager ready)")

        except OSError as exc:
            logger.warning("X11 initialization failed: %s", exc)
            self.valid = False

    def get_cursor(self):
        """Get the cursor image and position."""
        if not self.valid:
            return None

        # X11 is not thread-safe; the lock is required.
        with X11_LOCK:
            try:
                cursor_data = self.XFixesGetCursorImage(self.display)
                if not (cursor_data and cursor_data[0]):
                    return None

                data = cursor_data[0]
                width = int(data.width)
                height = int(data.height)

                if width == 0 or height == 0:
                    return None

                # Parse pixels.
                length = width * height

                # Manually convert ARGB -> RGBA.
                b = array.array("B", b"\x00" * 4 * length)
                offset = 0
                for i in range(length):
                    argb = data.pixels[i] & 0xFFFFFFFF
                    struct.pack_into(
                        "=BBBB",
                        b,
                        offset,
                        (argb >> 16) & 0xFF,  # R
                        (argb >> 8) & 0xFF,  # G
                        argb & 0xFF,  # B
                        (argb >> 24) & 0xFF,  # A
                    )
                    offset += 4

                imgarray = np.frombuffer(b, dtype=np.uint8).reshape((height, width, 4))

                return {
                    "img": Image.fromarray(imgarray, mode="RGBA"),
                    "x": int(data.x),
                    "y": int(data.y),
                    "xhot": int(data.xhot),
                    "yhot": int(data.yhot),
                }

            except (OSError, ValueError) as exc:
                # A cursor failure is non-fatal; do not raise.
                logger.debug("cursor capture error: %s", exc)
                return None


# Initialize the global instance.
cursor_manager = CursorManager()

# =========================
#  Core capture logic (in-memory)
# =========================


def capture_screen_to_memory() -> io.BytesIO:
    """Capture the screen straight into memory via scrot, falling back to import."""
    # Prefer scrot.
    if SCROT_AVAILABLE:
        try:
            img_bytes = subprocess.check_output(["scrot", "-", "--silent"], timeout=2)
            return io.BytesIO(img_bytes)
        except (CalledProcessError, TimeoutExpired) as exc:
            logger.warning("scrot capture failed/timed out: %s", exc)

    # Fall back to import (ImageMagick).
    if IMPORT_AVAILABLE:
        img_bytes = subprocess.check_output(
            ["import", "-window", "root", "png:-"], timeout=2
        )
        return io.BytesIO(img_bytes)

    # Neither backend available.
    raise RuntimeError("no screenshot backend available (scrot/import)")


def process_screenshot() -> bytes:
    """Capture once (in memory) and overlay the cursor, returning PNG bytes."""
    start_time = time.time()
    stats["requests"] += 1

    try:
        # 1. In-memory capture.
        raw_io = capture_screen_to_memory()

        # 2. Convert to a PIL image (in memory only).
        base_img = Image.open(raw_io).convert("RGBA")

        # 3. Fetch and overlay the cursor.
        cursor_data = cursor_manager.get_cursor()
        if cursor_data:
            c_img = cursor_data["img"]
            dest_x = cursor_data["x"] - cursor_data["xhot"]
            dest_y = cursor_data["y"] - cursor_data["yhot"]
            base_img.alpha_composite(c_img, dest=(dest_x, dest_y))

        # 4. Export to PNG bytes.
        output_io = io.BytesIO()
        # compress_level=1: trade a little size for speed.
        base_img.save(output_io, format="PNG", compress_level=1)
        result_bytes = output_io.getvalue()

        # Stats.
        stats["success"] += 1
        elapsed = (time.time() - start_time) * 1000
        stats["avg_latency_ms"] = stats["avg_latency_ms"] * 0.9 + elapsed * 0.1

        return result_bytes

    except Exception as exc:  # noqa: BLE001 - report, rate-limit, and re-raise
        stats["failed"] += 1
        # Rate-limit error logging to avoid flooding.
        if stats["failed"] % 10 == 1:
            logger.warning(
                "screenshot processing failed (#%s): %s", stats["failed"], exc
            )
        raise


# =========================
#  API
# =========================


@app.get("/")
async def root():
    return {
        "status": "running",
        "mode": "in-memory-optimized",
        "cursor_enabled": cursor_manager.valid,
        "backend": stats["backend"],
    }


@app.get("/health")
async def health():
    """Simple health check."""
    return {
        "status": "ok" if stats["failed"] == 0 else "degraded",
        "requests": stats["requests"],
        "success": stats["success"],
        "failed": stats["failed"],
        "avg_latency_ms": stats["avg_latency_ms"],
        "cursor_enabled": cursor_manager.valid,
        "backend": stats["backend"],
    }


@app.get("/screenshot")
async def get_screenshot():
    """Return the current screen as an image/png stream."""
    try:
        # Run in a threadpool to avoid blocking the asyncio event loop.
        img_bytes = await run_in_threadpool(process_screenshot)
        return Response(content=img_bytes, media_type="image/png")
    except Exception:  # noqa: BLE001 - details already logged in process_screenshot
        raise HTTPException(status_code=500, detail="Capture failed")


@app.get("/stats")
async def get_stats():
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("starting in-memory screenshot service on port %s", SCREENSHOT_PORT)
    # workers=1 is essential: the X11 connection and screen capture do not
    # support multi-process concurrency.
    uvicorn.run(app, host="0.0.0.0", port=SCREENSHOT_PORT, workers=1)
