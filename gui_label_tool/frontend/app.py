#!/usr/bin/env python3
"""GUI labeling tool frontend (Gradio UI).

The user-facing control console: it drives the whole recording session (start
the VM, start capture + annotation) and lets you review, prune and export the
resulting trajectory.

Port: taken from ``ports.frontend`` in the config.
"""

import base64
import functools
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Set

import gradio as gr
import requests

from gui_label_tool import get_config, require_key

logger = logging.getLogger(__name__)

# ============= Service URL config =============
CONFIG = get_config()

# Service ports (one per pipeline package).
PORTS = require_key(CONFIG, "ports", "")
FRONTEND_PORT = PORTS["frontend"]

# Assemble URLs dynamically: http://localhost + the matching port.
VM_URL = f"http://localhost:{PORTS['vm']}"
ANNOTATION_URL = f"http://localhost:{PORTS['annotation']}"
QEMU_LOG_URL = f"http://localhost:{PORTS['qemu_log']}"

# VNC config: read the VNC info from the vm section.
VM_CFG = require_key(CONFIG, "vm", "")
VM_PORTS = require_key(VM_CFG, "ports", "vm")

VNC_HOST = require_key(VM_CFG, "vnc_host", "vm")
VNC_PORT = int(require_key(VM_PORTS, "vnc", "vm.ports"))
# ============= Custom CSS =============
CUSTOM_CSS = """
/* Make the whole app span the full browser width. */
html, body, #root {
    margin: 0 !important;
    padding: 0 !important;
    width: 100% !important;
}

/* Stop the outermost gradio container from centering and drop its padding. */
#root > .gradio-container {
    max-width: 100% !important;
    width: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
}

/* Inner blocks also have default padding; remove it. */
#root .gradio-container .block.gradio-app {
    padding-left: 0 !important;
    padding-right: 0 !important;
}

/* Clear any wrap / app padding too. */
#root .gradio-container .wrap,
#root .gradio-container .app {
    padding-left: 0 !important;
    padding-right: 0 !important;
}

/* Give the whole Blocks our own padding (replacing the default). */
.gradio-container {
    padding: 10px 20px !important;
}

/* Control panel width. */
.control-panel {
    min-width: 0 !important;      /* allow it to shrink */
    max-width: none !important;   /* drop the 300px limit */
}

/* VNC container - 16:9 ratio for 1920x1080. */
.vnc-container {
    width: 100%;
    aspect-ratio: 16 / 9;
    max-height: 70vh;
    border: 2px solid #ddd;
    border-radius: 8px;
    overflow: hidden;
}
.vnc-container iframe {
    width: 100%;
    height: 100%;
    border: none;
}

/* Event card. */
.event-card {
    background: white;
    border-radius: 6px;
    padding: 10px 12px;
    margin-bottom: 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

/* Enlarged event screenshot. */
.event-screenshot {
    width: 480px !important;
    max-width: 480px !important;
    height: auto;
    border-radius: 6px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.12);
}

/* Event list container. */
.events-container {
    max-height: calc(100vh - 200px);
    overflow-y: auto;
    padding: 10px;
}

/* Hint text style. */
.hint {
    font-size: 12px;
    color: #888;
}

.event-title-id {
    font-weight: 700;
    font-size: 20px;
    color: #1976d2;   /* blue */
}

.event-title-type {
    font-size: 20px;
    color: #666;
}

.event-desc {
    font-size: 20px;
    margin-bottom: 6px;
    color: #555;
}

.event-meta-time {
    font-size: 20px;
    color: #999;
}

/* Larger font for the main action buttons (start / stop / confirm). */
.main-action-btn,              /* when the button itself carries main-action-btn */
.main-action-btn > button,     /* main-action-btn is the outer div, button is the child */
.main-action-btn button {      /* tolerate an extra wrapping div */
    font-size: 20px !important;
    padding: 8px 18px !important;
    font-weight: 600;
}

/* VNC header row style. */
.vnc-header-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
}
"""


@functools.lru_cache(maxsize=512)
def image_to_base64(image_path: str) -> str:
    try:
        if not Path(image_path).exists():
            return ""
        with open(image_path, "rb") as f:
            return f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
    except OSError as e:
        logger.warning("failed to convert image %s: %s", image_path, e)
        return ""


def normalize_event_id(raw_id: str) -> str:
    if not raw_id:
        return ""
    cleaned = raw_id.strip().lstrip("#").strip()
    try:
        num = int(cleaned)
        return f"{num:04d}"
    except ValueError:
        return cleaned


# ============= Frontend DOM-update JS =============
DOM_UPDATE_JS = """
(events_data_str) => {
    if (!events_data_str) return;
    let data;
    try { data = JSON.parse(events_data_str); } catch(e) { return; }

    if (data.stats) {
        const statsEl = document.getElementById('stats-summary-content');
        if (statsEl) {
            statsEl.innerHTML = `📊 Total: <b>${data.stats.total}</b> | Kept: <b style="color:#22aa44;">${data.stats.final_count}</b> | Excluded: <b style="color:#ff4444;">${data.stats.deleted_count}</b>`;
        }
    }

    if (data.newly_deleted_ids && data.newly_deleted_ids.length > 0) {
        data.newly_deleted_ids.forEach(id => {
            const eventCard = document.querySelector(`#event-${id}`);
            if (eventCard) {
                eventCard.style.opacity = '0.4';
                eventCard.style.filter = 'grayscale(100%)';
                const actionBtn = eventCard.querySelector('.action-button-container');
                if (actionBtn) actionBtn.innerHTML = `<span style="color:#888;font-size:12px;">Excluded</span>`;
                const badge = eventCard.querySelector('.deleted-badge-container');
                if (badge) badge.innerHTML = `<span style='background:#ff4444;color:white;padding:2px 8px;border-radius:3px;font-size:11px;margin-left:8px;'>Excluded</span>`;
            }
        });
    }
    
    if (data.new_events_html) {
        const container = document.getElementById('events-display-container');
        if (!container) return;
        const firstEvent = container.querySelector('.event-card');
        const placeholder = container.querySelector('.empty-placeholder');
        if (placeholder) placeholder.remove();
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = data.new_events_html;
        Array.from(tempDiv.children).reverse().forEach(child => {
            if (firstEvent) container.insertBefore(child, firstEvent);
            else container.appendChild(child);
        });
    }
    return events_data_str;
}
"""


class ServiceOrchestrator:
    def build_single_event_html(
        self, evt: Dict[str, Any], deleted_ids: Set[str]
    ) -> str:
        event_id = evt["event_id"]
        event_type = evt[
            "type"
        ]  # left_click / right_click / double_click / text_input / scroll / hotkey / key_press
        timestamp = evt["timestamp"][:19] if evt["timestamp"] else ""
        screenshot_path = evt.get("screenshot_path")
        event_data = evt.get("event_data", {})

        is_deleted = event_id in deleted_ids
        deleted_style = "opacity:0.4;filter:grayscale(100%);" if is_deleted else ""
        deleted_badge = (
            "<span class='deleted-badge-container' "
            "style='background:#ff4444;color:white;padding:2px 8px;"
            "border-radius:3px;font-size:11px;margin-left:8px;'>Excluded</span>"
            if is_deleted
            else "<span class='deleted-badge-container'></span>"
        )

        # Base type icon/color.
        base_type_map = {
            "text_input": ("⌨️", "#4444ff"),
            "scroll": ("🔄", "#44aa44"),
            # key_press is handled separately, but keeping it here is harmless.
        }

        # Mouse click subtypes.
        click_type_map = {
            "left_click": ("🖱️", "#ff4444", "Left click"),
            "right_click": ("🖱️", "#ff9800", "Right click"),
            "double_click": ("🖱️", "#9c27b0", "Double click"),
            "middle_click": ("🖱️", "#e91e63", "Middle click"),
        }

        icon, color = base_type_map.get(event_type, ("📌", "#888"))
        desc = event_type

        # === 1. Mouse click: use the resolved screen coordinates ===
        if event_type in click_type_map:
            icon, color, click_label = click_type_map[event_type]

            # Prefer the screen_position computed by the backend.
            screen_pos = evt.get("screen_position") or {}
            sx = screen_pos.get("x")
            sy = screen_pos.get("y")

            # Fall back to the raw position (QEMU coordinates) if absent.
            if sx is None or sy is None:
                raw_pos = event_data.get("position", {}) or {}
                sx = raw_pos.get("x", 0)
                sy = raw_pos.get("y", 0)

            try:
                sx = int(sx)
                sy = int(sy)
            except (TypeError, ValueError):
                pass

            desc = f"{click_label}: ({sx}, {sy})"

        # === 2. Text input ===
        elif event_type == "text_input":
            text = event_data.get("text", "")
            desc = f'Input: "{text[:25]}{"..." if len(text) > 25 else ""}"'

        # === 3. key_press: a single action, shown as Key press: <key> x<count> ===
        elif event_type == "key_press":
            icon = "⌨️"
            color = "#3f51b5"
            key_name = (event_data.get("key") or "key").lower()
            count = int(event_data.get("count") or 1)
            if count > 1:
                desc = f"Key press: {key_name} x{count}"
            else:
                desc = f"Key press: {key_name}"

        # === 4. Scroll ===
        elif event_type == "scroll":
            desc = f"Scroll {event_data.get('direction', '')} x{event_data.get('amount', 1)}"

        # Fallback for other types, e.g. hotkey.
        elif event_type == "hotkey":
            icon = "⌨️"
            color = "#ffa000"
            combo = (
                event_data.get("combo")
                or "+".join(event_data.get("keys") or [])
                or "hotkey"
            )
            desc = f"Hotkey: {combo}"
        else:
            desc = event_type

        # Screenshot.
        thumb_html = ""
        if screenshot_path and (base64_img := image_to_base64(screenshot_path)):
            thumb_html = f"""
                <div style="flex-shrink:0;">
                    <img src="{base64_img}" class="event-screenshot"
                         style="border-radius:6px;box-shadow:0 2px 6px rgba(0,0,0,0.12);{deleted_style}">
                </div>
                """

        if is_deleted:
            action_html = '<span style="color:#888;font-size:12px;">Excluded</span>'
        else:
            action_html = (
                f'<code style="background:#f5f5f5;padding:3px 8px;border-radius:3px;'
                f'font-size:13px;color:#333;font-weight:bold;">{event_id}</code>'
            )

        # The title row keeps only the icon + excluded badge; the description goes in desc.
        return f"""
        <div id="event-{event_id}" class="event-card"
             style="background:white;border-left:4px solid {color};border-radius:6px;">
            <div style="display:flex;align-items:flex-start;gap:12px;{deleted_style}">
                {thumb_html}
                <div style="flex:1;min-width:0;">
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                        <span>{icon}</span>
                        {deleted_badge}
                    </div>
                    <div class="event-desc" style="color:#555;margin-bottom:2px;">
                        {desc}
                    </div>
                    <div class="event-meta-time" style="color:#999;font-size:12px;">
                        ⏰ {timestamp}
                    </div>
                </div>
                <div class="action-button-container" style="flex-shrink:0;padding-top:4px;">
                    {action_html}
                </div>
            </div>
        </div>
        """

    def build_events_html_initial(
        self, events_data: Dict, frontend_deleted: Set[str] = None
    ) -> str:
        events = events_data.get("events", [])
        backend_deleted = set(events_data.get("deleted", []))
        deleted_ids = backend_deleted | (frontend_deleted or set())

        total = len(events)
        deleted_count = len(deleted_ids)
        final_count = total - deleted_count

        stats_html = f"""
        <div style="padding:10px 14px;background:#f0f4f8;border-radius:6px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center;">
            <span id="stats-summary-content" style="font-size:14px;color:#555;">
                📊 Total: <b>{total}</b> | Kept: <b style="color:#22aa44;">{final_count}</b> | Excluded: <b style="color:#ff4444;">{deleted_count}</b>
            </span>
            <span style="font-size:11px;color:#888;">Live updates</span>
        </div>
        """

        if not events:
            events_html = "<div class='empty-placeholder' style='padding:30px;text-align:center;color:#888;font-size:14px;'>📭 No events yet; actions in the VNC view appear here in real time</div>"
        else:
            events_html = "".join(
                [self.build_single_event_html(evt, deleted_ids) for evt in events]
            )

        return f"<div id='events-display-container' class='events-container' style='max-height:calc(100vh - 200px);overflow-y:auto;padding:10px;'>{stats_html}{events_html}</div>"

    @staticmethod
    def sync_deleted_to_backend(deleted_ids: Set[str]) -> Dict:
        results = []
        for event_id in deleted_ids:
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
    def _ensure_clean_state_before_start():
        # 1) Stop the log parser first.
        try:
            requests.post(f"{QEMU_LOG_URL}/stop", timeout=5)
        except requests.RequestException as e:
            logger.warning("[cleanup] stop log parser failed: %s", e)

        # 2) Tear down the annotation session, whether active or frozen.
        try:
            status = requests.get(
                f"{ANNOTATION_URL}/annotation/status", timeout=3
            ).json()
            has_session = bool(status.get("current_annotation_id"))
            is_active = bool(status.get("is_active"))

            if has_session:
                # Still capturing -> prefer /stop (which finalizes + clears state).
                if is_active:
                    try:
                        logger.info(
                            "[cleanup] annotation active, calling /annotation/stop"
                        )
                        requests.post(
                            f"{ANNOTATION_URL}/annotation/stop", timeout=10
                        )
                    except requests.RequestException as e:
                        logger.warning("[cleanup] call /annotation/stop failed: %s", e)
                else:
                    # Already frozen but not finalized -> force a finalize.
                    try:
                        logger.info(
                            "[cleanup] annotation frozen, calling /annotation/finalize"
                        )
                        requests.post(
                            f"{ANNOTATION_URL}/annotation/finalize", timeout=10
                        )
                    except requests.RequestException as e:
                        logger.warning(
                            "[cleanup] call /annotation/finalize failed: %s", e
                        )
        except requests.RequestException as e:
            logger.warning("[cleanup] get annotation status failed: %s", e)

        # 3) Clean up all Docker containers.
        try:
            requests.post(f"{VM_URL}/containers/cleanup", timeout=15)
        except requests.RequestException as e:
            logger.warning("[cleanup] docker cleanup failed: %s", e)

    @staticmethod
    def start_full_stack(annotator_id: str, annotation_name: str) -> Dict:
        ServiceOrchestrator._ensure_clean_state_before_start()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        annotation_id = f"{annotator_id}_{timestamp}"

        log = [
            f"👤 Annotator: {annotator_id}",
            f"📝 Task: {annotation_name}",
            "",
            "🔧 Starting Docker...",
        ]

        try:
            docker_res = requests.post(
                f"{VM_URL}/container/start",
                json={
                    "annotation_id": annotation_id,
                    "annotator_id": annotator_id,
                    "annotation_name": annotation_name,
                },
                timeout=60,
            )

            if docker_res.status_code != 200:
                return {
                    "success": False,
                    "message": f"Failed to start Docker: {docker_res.text}",
                }
            docker_info = docker_res.json()
            log.append("✓ Docker started")
            time.sleep(10)

            ann_res = requests.post(
                f"{ANNOTATION_URL}/annotation/start",
                params={
                    "annotation_name": annotation_name,
                    "annotator_id": annotator_id,
                    "annotation_id": annotation_id,
                },
                timeout=10,
            )
            if ann_res.status_code != 200:
                return {
                    "success": False,
                    "message": "Failed to start annotation service",
                }
            log.append("✓ Annotation service started")

            requests.post(f"{QEMU_LOG_URL}/stop", timeout=5)
            log_res = requests.post(
                f"{QEMU_LOG_URL}/start",
                params={"log_path": docker_info["log_file"]},
                timeout=10,
            )
            if log_res.status_code != 200:
                return {"success": False, "message": "Failed to start log parser"}
            log.append("✓ Log parser started")

        except requests.RequestException as e:
            return {"success": False, "message": f"Startup error: {e}"}

        log.append("\n✅ Startup complete!")
        return {
            "success": True,
            "message": "\n".join(log),
            "annotation_id": annotation_id,
            "annotator_id": annotator_id,
            "vnc_url": docker_info["vnc_url"],
        }

    @staticmethod
    def freeze_recording() -> Dict:
        log = ["⏸️ Stopping capture..."]
        try:
            requests.post(f"{QEMU_LOG_URL}/stop", timeout=5)
            log.append("✓ Log parser stopped")
        except requests.RequestException as e:
            log.append(f"⚠ {e}")

        try:
            freeze_res = requests.post(
                f"{ANNOTATION_URL}/annotation/freeze", timeout=10
            )
            if freeze_res.status_code != 200:
                return {"success": False, "message": f"Stop failed: {freeze_res.text}"}
            data = freeze_res.json()
            log.append(f"✓ {data.get('total_events', 0)} event(s) total")
            log.append("\n📝 You can exclude events on the left")
        except requests.RequestException as e:
            return {"success": False, "message": f"Stop failed: {e}"}

        return {"success": True, "message": "\n".join(log)}

    @staticmethod
    def finalize_annotation(annotation_id: str, frontend_deleted: Set[str]) -> Dict:
        log = []

        if frontend_deleted:
            log.append(f"📤 Syncing {len(frontend_deleted)} exclusion(s)...")
            ServiceOrchestrator.sync_deleted_to_backend(frontend_deleted)
            log.append("✓ Synced")

        log.append("💾 Writing JSON...")
        try:
            requests.post(f"{QEMU_LOG_URL}/stop", timeout=5)
        except requests.RequestException:
            pass

        try:
            fin_res = requests.post(
                f"{ANNOTATION_URL}/annotation/finalize", timeout=20
            )
            if fin_res.status_code != 200:
                return {"success": False, "message": f"Confirm failed: {fin_res.text}"}
            data = fin_res.json()
            log.append("✓ Saved")
        except requests.RequestException as e:
            return {"success": False, "message": f"Confirm failed: {e}"}

        try:
            requests.post(
                f"{VM_URL}/container/stop/{annotation_id}", timeout=30
            )
            log.append("✓ Docker stopped")
        except requests.RequestException:
            pass

        log.append("\n✅ Done. You can start the next one.")
        return {
            "success": True,
            "message": "\n".join(log),
            "full_task_file": data.get("full_task_file"),
        }

    @staticmethod
    def get_annotation_status() -> str:
        try:
            status = requests.get(
                f"{ANNOTATION_URL}/annotation/status", timeout=3
            ).json()
            if status.get("is_active"):
                return f"🟢 Running | Events: {status.get('event_counter', 0)}"
            if status.get("current_annotation_id"):
                return "🟡 Paused | You can exclude events"
            return "🔴 Idle"
        except requests.RequestException:
            return "⚠️ Connection failed"

    @staticmethod
    def get_events_data() -> Dict:
        try:
            # Check the status first.
            status = requests.get(
                f"{ANNOTATION_URL}/annotation/status", timeout=3
            ).json()
            if not status.get("current_annotation_id"):
                # No session at all -> return empty.
                return {"events": [], "deleted": []}

            resp = requests.get(
                f"{ANNOTATION_URL}/annotation/events", timeout=3
            )
            if resp.status_code == 200:
                return resp.json()
            return {"events": [], "deleted": []}
        except requests.RequestException:
            return {"events": [], "deleted": []}


# ============= Global state =============

orchestrator = ServiceOrchestrator()
current_annotation_id = None
current_annotator_id = None
current_vnc_url = None
last_events_data = {"events": [], "deleted": []}
frontend_deleted_ids: Set[str] = set()

# orchestrator._ensure_clean_state_before_start()


# ============= Gradio callbacks =============


def start_annotation(annotator_id: str, annotation_name: str):
    global \
        current_annotation_id, \
        current_annotator_id, \
        last_events_data, \
        current_vnc_url, \
        frontend_deleted_ids

    if not annotator_id or not annotator_id.strip():
        return (
            "✗ Please enter an annotator ID",
            get_vnc_iframe(),
            orchestrator.get_annotation_status(),
            get_events_html_initial(),
            "",
            annotator_id,
            annotation_name,
        )

    if not annotation_name or not annotation_name.strip():
        return (
            "✗ Please enter a task name",
            get_vnc_iframe(),
            orchestrator.get_annotation_status(),
            get_events_html_initial(),
            "",
            annotator_id,
            annotation_name,
        )

    annotator_id = annotator_id.strip()
    annotation_name = annotation_name.strip()

    result = orchestrator.start_full_stack(annotator_id, annotation_name)
    if result["success"]:
        current_annotation_id = result["annotation_id"]
        current_annotator_id = result["annotator_id"]
        current_vnc_url = result.get("vnc_url")
        last_events_data = {"events": [], "deleted": []}
        frontend_deleted_ids = set()
        return (
            result["message"],
            get_vnc_iframe(current_vnc_url),
            orchestrator.get_annotation_status(),
            get_events_html_initial(),
            "",
            annotator_id,
            annotation_name,
        )

    return (
        f"✗ {result['message']}",
        get_vnc_iframe(),
        orchestrator.get_annotation_status(),
        get_events_html_initial(),
        "",
        annotator_id,
        annotation_name,
    )


def end_annotation(annotator_id: str, annotation_name: str):
    global current_annotation_id, current_vnc_url
    if not current_annotation_id:
        return (
            "✗ No active task",
            get_vnc_iframe(current_vnc_url),
            orchestrator.get_annotation_status(),
            get_events_html_initial(),
            get_deleted_list_display(),
            annotator_id,
            annotation_name,
        )
    result = orchestrator.freeze_recording()
    msg = result["message"] if result["success"] else f"✗ {result['message']}"
    return (
        msg,
        get_vnc_iframe(current_vnc_url),
        orchestrator.get_annotation_status(),
        get_events_html_initial(),
        get_deleted_list_display(),
        annotator_id,
        annotation_name,
    )


def confirm_annotation():
    global \
        current_annotation_id, \
        current_annotator_id, \
        last_events_data, \
        current_vnc_url, \
        frontend_deleted_ids

    if not current_annotation_id:
        return (
            "✗ No task to confirm",
            get_vnc_iframe(),
            orchestrator.get_annotation_status(),
            get_events_html_initial(),
            "",
            "",
            "",
        )

    result = orchestrator.finalize_annotation(
        current_annotation_id, frontend_deleted_ids
    )

    if result["success"]:
        current_annotation_id = None
        current_annotator_id = None
        current_vnc_url = None
        last_events_data = {"events": [], "deleted": []}
        frontend_deleted_ids = set()
        return (
            result["message"],
            get_vnc_iframe(),
            orchestrator.get_annotation_status(),
            get_events_html_initial(),
            "",
            "",
            "",
        )

    return (
        result["message"],
        get_vnc_iframe(),
        orchestrator.get_annotation_status(),
        get_events_html_initial(),
        get_deleted_list_display(),
        "",
        "",
    )


def get_vnc_iframe(vnc_url: str = None) -> str:
    """Build the VNC iframe, sized for 1920x1080."""
    vnc_url = (
        vnc_url
        or f"http://{VNC_HOST}:{VNC_PORT}/vnc.html?view_only=0&autoconnect=1&resize=scale"
    )
    # Add a timestamp to bust the cache.
    timestamp = int(time.time() * 1000)
    vnc_url_with_ts = f"{vnc_url}&_t={timestamp}"
    # Use a 16:9 ratio to maximize space.
    return f"""
    <div class="vnc-container" style="width:100%;aspect-ratio:16/9;max-height:70vh;border:2px solid #ddd;border-radius:8px;overflow:hidden;">
        <iframe src="{vnc_url_with_ts}" style="width:100%;height:100%;border:none;" allow="fullscreen"></iframe>
    </div>
    """


def refresh_vnc():
    """Manually refresh the VNC connection."""
    global current_vnc_url
    return get_vnc_iframe(current_vnc_url)


def get_events_html_initial() -> str:
    global last_events_data, frontend_deleted_ids
    events_data = orchestrator.get_events_data()
    if not events_data or "events" not in events_data:
        events_data = {"events": [], "deleted": []}
    last_events_data = events_data
    return orchestrator.build_events_html_initial(events_data, frontend_deleted_ids)


def get_deleted_list_display() -> str:
    global frontend_deleted_ids
    if not frontend_deleted_ids:
        return ""
    return "Excluded: " + ", ".join(sorted(frontend_deleted_ids))


def refresh_status():
    return orchestrator.get_annotation_status()


def refresh_events_delta() -> str:
    global last_events_data, frontend_deleted_ids
    if last_events_data is None:
        last_events_data = {"events": [], "deleted": []}

    events_data = orchestrator.get_events_data()
    if not events_data or "events" not in events_data:
        return "{}"

    all_deleted = set(events_data.get("deleted", [])) | frontend_deleted_ids

    if (
        events_data.get("events") == last_events_data.get("events")
        and all_deleted
        == set(last_events_data.get("deleted", [])) | frontend_deleted_ids
    ):
        return "{}"

    new_events = [
        evt
        for evt in events_data["events"]
        if evt not in last_events_data.get("events", [])
    ]
    old_deleted = set(last_events_data.get("deleted", [])) | frontend_deleted_ids
    newly_deleted_ids = list(all_deleted - old_deleted)

    if not new_events and not newly_deleted_ids:
        last_events_data = events_data
        return "{}"

    new_events_html = "".join(
        [
            orchestrator.build_single_event_html(evt, all_deleted)
            for evt in reversed(new_events)
        ]
    )

    delta_data = {
        "new_events_html": new_events_html,
        "newly_deleted_ids": newly_deleted_ids,
        "stats": {
            "total": len(events_data["events"]),
            "deleted_count": len(all_deleted),
            "final_count": len(events_data["events"]) - len(all_deleted),
        },
    }
    last_events_data = events_data
    return json.dumps(delta_data)


def handle_delete_event(event_id: str):
    global frontend_deleted_ids, last_events_data

    if not event_id or not event_id.strip():
        return "⚠️ Please enter an event ID", "", get_deleted_list_display()

    normalized_id = normalize_event_id(event_id)

    if not normalized_id:
        return "⚠️ Invalid ID", event_id, get_deleted_list_display()

    events = last_events_data.get("events", [])
    event_exists = any(evt.get("event_id") == normalized_id for evt in events)

    if not event_exists:
        valid_ids = [evt.get("event_id") for evt in events[:5]]
        return (
            f"⚠️ {normalized_id} not found (valid: {', '.join(valid_ids)})",
            event_id,
            get_deleted_list_display(),
        )

    if normalized_id in frontend_deleted_ids:
        return f"ℹ️ {normalized_id} already in the list", "", get_deleted_list_display()

    frontend_deleted_ids.add(normalized_id)
    return f"✓ {normalized_id} excluded", "", get_deleted_list_display()


def handle_restore_event(event_id: str):
    global frontend_deleted_ids

    if not event_id or not event_id.strip():
        return "⚠️ Please enter an ID", "", get_deleted_list_display()

    normalized_id = normalize_event_id(event_id)

    if normalized_id not in frontend_deleted_ids:
        return (
            f"ℹ️ {normalized_id} not in the list",
            event_id,
            get_deleted_list_display(),
        )

    frontend_deleted_ids.discard(normalized_id)
    return f"✓ {normalized_id} restored", "", get_deleted_list_display()


def reset_on_load():
    """Called when the page loads.

    - If the backend has a running/pending session: do not clean up; restore the
      UI (so a refresh or a second tab does not wipe an in-progress recording).
    - Otherwise: clear the frontend global state, tell the backend to clean up,
      and return to the idle initial UI.

    Maps to outputs: status_msg, vnc_html, annotation_status, events_html,
    deleted_list, annotator_id_input, annotation_name_input.
    """
    global \
        current_annotation_id, \
        current_annotator_id, \
        current_vnc_url, \
        last_events_data, \
        frontend_deleted_ids

    # First check whether the backend has a running/pending session.
    status = {}
    try:
        status = requests.get(
            f"{ANNOTATION_URL}/annotation/status", timeout=3
        ).json()
    except requests.RequestException as e:
        logger.warning("reset_on_load: failed to query status: %s", e)

    if status.get("current_annotation_id"):
        # A session is running: keep it, do not clean up, just restore the UI.
        current_annotation_id = status.get("current_annotation_id")
        current_annotator_id = (
            status.get("current_annotator_id") or current_annotator_id
        )
        return (
            "↩️ Detected an in-progress session; restored (not cleaned up)",
            get_vnc_iframe(current_vnc_url),
            orchestrator.get_annotation_status(),
            get_events_html_initial(),
            get_deleted_list_display(),
            current_annotator_id or "",
            status.get("current_annotation_name") or "",
        )

    # No session: perform a clean reset.
    current_annotation_id = None
    current_annotator_id = None
    current_vnc_url = None
    last_events_data = {"events": [], "deleted": []}
    frontend_deleted_ids = set()

    try:
        orchestrator._ensure_clean_state_before_start()
    except requests.RequestException as e:
        logger.warning("reset_on_load: cleanup failed: %s", e)

    return (
        "Ready",
        get_vnc_iframe(),
        "🔴 Idle",
        orchestrator.build_events_html_initial({"events": [], "deleted": []}, set()),
        "",
        "",
        "",
    )


# ============= Gradio UI =============
with gr.Blocks(
    title="GUI Label Tool",
    theme=gr.themes.Soft(),
    css=CUSTOM_CSS,
    js=DOM_UPDATE_JS,
    elem_id="app-root",
) as demo:
    gr.Markdown("# GUI Label Tool")
    js_data_channel = gr.Textbox(visible=False)

    with gr.Row():
        # Left control panel - fixed width.
        with gr.Column(scale=1, min_width=180, elem_classes=["control-panel"]):
            with gr.Group():
                gr.Markdown("### ⚙️ Controls")
                annotator_id_input = gr.Textbox(
                    label="👤 Annotator ID", placeholder="Your ID..."
                )
                annotation_name_input = gr.Textbox(
                    label="📝 Task", placeholder="Task description..."
                )
                with gr.Row():
                    start_btn = gr.Button(
                        "▶️ Start",
                        variant="primary",
                        size="sm",
                        elem_classes=["main-action-btn"],
                    )
                    end_btn = gr.Button(
                        "⏸️ Stop",
                        variant="stop",
                        size="sm",
                        elem_classes=["main-action-btn"],
                    )
                    confirm_btn = gr.Button(
                        "✅ Confirm",
                        variant="secondary",
                        size="sm",
                        elem_classes=["main-action-btn"],
                    )
                status_msg = gr.Textbox(
                    label="Log", value="Ready", lines=5, interactive=False
                )

            with gr.Group():
                gr.Markdown("### 🗑️ Exclude")
                with gr.Row():
                    delete_input = gr.Textbox(label="ID", placeholder="e.g. 1", scale=2)
                    delete_btn = gr.Button("Exclude", variant="stop", size="sm")
                    restore_btn = gr.Button("Restore", size="sm")
                delete_result = gr.Textbox(show_label=False, lines=1, interactive=False)
                deleted_list = gr.Textbox(label="Excluded", lines=1, interactive=False)

            with gr.Group():
                gr.Markdown("### 📊 Status")
                annotation_status = gr.Textbox(
                    value=orchestrator.get_annotation_status(),
                    lines=2,
                    interactive=False,
                    show_label=False,
                )

        # Right main area - auto-expanding.
        with gr.Column(scale=8):
            with gr.Row(equal_height=True):
                with gr.Column(scale=9):
                    gr.Markdown("### 🖥️ Remote desktop (1920×1080)")
                with gr.Column(scale=1, min_width=120):
                    refresh_vnc_btn = gr.Button(
                        "🔄 Refresh VNC", variant="primary", size="lg"
                    )
            vnc_html = gr.HTML(value=get_vnc_iframe())

            gr.Markdown("### 📋 Event stream")
            events_html = gr.HTML(value=get_events_html_initial())

    # Timer.
    timer = gr.Timer(1)
    timer.tick(fn=refresh_status, outputs=[annotation_status])
    timer.tick(fn=refresh_events_delta, outputs=[js_data_channel])
    js_data_channel.change(fn=None, js=DOM_UPDATE_JS, inputs=[js_data_channel])

    # VNC refresh button.
    refresh_vnc_btn.click(fn=refresh_vnc, outputs=[vnc_html])

    # Delete / restore.
    delete_btn.click(
        fn=handle_delete_event,
        inputs=[delete_input],
        outputs=[delete_result, delete_input, deleted_list],
    ).then(fn=get_events_html_initial, outputs=[events_html])

    restore_btn.click(
        fn=handle_restore_event,
        inputs=[delete_input],
        outputs=[delete_result, delete_input, deleted_list],
    ).then(fn=get_events_html_initial, outputs=[events_html])

    # Main buttons.
    start_btn.click(
        fn=start_annotation,
        inputs=[annotator_id_input, annotation_name_input],
        outputs=[
            status_msg,
            vnc_html,
            annotation_status,
            events_html,
            deleted_list,
            annotator_id_input,
            annotation_name_input,
        ],
    )

    end_btn.click(
        fn=end_annotation,
        inputs=[annotator_id_input, annotation_name_input],
        outputs=[
            status_msg,
            vnc_html,
            annotation_status,
            events_html,
            deleted_list,
            annotator_id_input,
            annotation_name_input,
        ],
    )

    confirm_btn.click(
        fn=confirm_annotation,
        outputs=[
            status_msg,
            vnc_html,
            annotation_status,
            events_html,
            deleted_list,
            annotator_id_input,
            annotation_name_input,
        ],
    )

    demo.load(
        fn=reset_on_load,
        inputs=[],
        outputs=[
            status_msg,
            vnc_html,
            annotation_status,
            events_html,
            deleted_list,
            annotator_id_input,
            annotation_name_input,
        ],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("GUI Label Tool")
    logger.info("open: http://0.0.0.0:%s", FRONTEND_PORT)

    demo.launch(
        server_name="0.0.0.0",
        server_port=FRONTEND_PORT,
        share=False,
        show_error=True,
    )
