#!/usr/bin/env python3
"""QEMU virtual-machine lifecycle management (ubuntu / windows).

Starts/stops the Docker container that runs the QEMU VM being labeled. Pure
container logic (no HTTP); the FastAPI wiring lives in
:mod:`~gui_label_tool.vm.service`.

The target OS is selected via ``config.yml``::

    runtime:
      target_os: "ubuntu"   # or "windows"

which decides which kind of QEMU container is launched.
"""

from __future__ import annotations

import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict

from pydantic import BaseModel

from gui_label_tool import get_config, require_key

logger = logging.getLogger(__name__)

CONFIG = get_config()

# 1. runtime.target_os: controls OS-specific behavior only, not the config section names.
RUNTIME_CFG = require_key(CONFIG, "runtime", "runtime")
TARGET_OS = require_key(RUNTIME_CFG, "target_os", "runtime").lower()
if TARGET_OS not in ("ubuntu", "windows"):
    raise RuntimeError(
        "Config error: runtime.target_os must be 'ubuntu' or 'windows', "
        f"got: {TARGET_OS!r}"
    )

# 2. VM settings all live under the `vm` config section.
VM_CFG = require_key(CONFIG, "vm", "vm")

# 3. This service's listen port comes from ports.vm.
PORTS = require_key(CONFIG, "ports", "ports")
VM_SERVICE_PORT = int(require_key(PORTS, "vm", "ports"))

# 4. Required fields shared across OSes (host -> container).
VM_LOG_DIR = require_key(VM_CFG, "log_base_dir", "vm")
VM_DISK_IMAGE = require_key(VM_CFG, "disk_image", "vm")
VM_SHARED_PATH = require_key(VM_CFG, "shared_path", "vm")
VM_IMAGE = require_key(VM_CFG, "image", "vm")
VM_CONTAINER_NAME = require_key(VM_CFG, "container_name", "vm")
VM_VNC_HOST = require_key(VM_CFG, "vnc_host", "vm")

VM_PORTS = require_key(VM_CFG, "ports", "vm.ports")
VM_RESOURCES = require_key(VM_CFG, "resources", "vm.resources")

# 5. QEMU-related ports.
VM_VNC_PORT = int(require_key(VM_PORTS, "vnc", "vm.ports"))
VM_API_PORT = int(require_key(VM_PORTS, "api", "vm.ports"))
VM_SERVER_PORT = int(require_key(VM_PORTS, "server", "vm.ports"))
VM_SCREENSHOT_PORT = int(require_key(VM_PORTS, "screenshot", "vm.ports"))

# 6. QEMU resources.
VM_DISK_SIZE = require_key(VM_RESOURCES, "disk_size", "vm.resources")
VM_RAM_SIZE = require_key(VM_RESOURCES, "ram_size", "vm.resources")
VM_CPU_CORES = require_key(VM_RESOURCES, "cpu_cores", "vm.resources")

# 7. Windows-only field (may be left empty for ubuntu).
VM_UEFI_ROM = VM_CFG.get("uefi_rom")  # validated lazily when starting in windows mode


# ========== Pydantic container config ==========


class ContainerConfig(BaseModel):
    """Container config (uniform for the frontend, interpreted per TARGET_OS)."""

    annotation_id: str

    # Disk / shared dir / resources.
    disk_image: str = VM_DISK_IMAGE
    shared_path: str = VM_SHARED_PATH
    disk_size: str = VM_DISK_SIZE
    ram_size: str = VM_RAM_SIZE
    cpu_cores: str = VM_CPU_CORES

    # Ports.
    vnc_port: int = VM_VNC_PORT
    api_port: int = VM_API_PORT
    server_port: int = VM_SERVER_PORT

    # Windows-only; unused in ubuntu mode.
    uefi_rom: str | None = VM_UEFI_ROM


# ========== Core manager class ==========


class VmManager:
    """Manages QEMU containers (ubuntu / windows, depending on TARGET_OS)."""

    def __init__(self, log_base_dir: str = VM_LOG_DIR, os_type: str = TARGET_OS):
        self.os_type = os_type  # "ubuntu" / "windows"
        self.log_base_dir = Path(log_base_dir).resolve()
        self.log_base_dir.mkdir(parents=True, exist_ok=True)

        # Currently running containers (in-process state only).
        self.active_containers: Dict[str, Dict] = {}

        # Clean up any leftover container on startup.
        self.cleanup_leftover_container()

    # ---------- Start container ----------

    def start_container(self, config: ContainerConfig) -> Dict:
        """Start a new QEMU container (ubuntu/windows per self.os_type)."""
        annotation_id = config.annotation_id
        container_name = VM_CONTAINER_NAME

        # Prevent starting the same annotation_id twice.
        if annotation_id in self.active_containers:
            return {"success": False, "message": "Container already exists"}

        # Clean up any container left over under the same name.
        if self._container_exists(container_name):
            self._force_stop_container(container_name)
            time.sleep(2)

        # Host-side log directory.
        log_dir = self.log_base_dir / annotation_id
        log_dir.mkdir(parents=True, exist_ok=True)
        log_dir_host = log_dir.resolve()
        log_file = log_dir_host / "qemu_input.log"

        # Shared base parameters.
        disk_image = config.disk_image
        shared_path = config.shared_path
        disk_size = config.disk_size
        ram_size = config.ram_size
        cpu_cores = config.cpu_cores
        vnc_port = config.vnc_port
        api_port = config.api_port
        server_port = config.server_port

        # ===== Build ARGUMENTS per OS =====
        if self.os_type == "ubuntu":
            arguments = (
                "-virtfs local,path=/shared,security_model=none,mount_tag=shared "
                "--trace enable=input_event_* "
                "-D /qemu_logs/qemu_input.log"
            )
        else:
            # Windows ARGUMENTS: UEFI + SMB.
            uefi_rom = config.uefi_rom or VM_UEFI_ROM
            if not uefi_rom:
                raise RuntimeError(
                    "Config error: uefi_rom is required in windows mode "
                    "(set it in config.yml or the request body)"
                )

            arguments = (
                f"-drive if=pflash,format=raw,readonly=on,file={uefi_rom} "
                "-netdev user,id=n1,smb=/shared,hostfwd=tcp::8000-:8000 "
                "-device virtio-net-pci,netdev=n1,id=net1,bus=pcie.0,addr=0x6 "
                "--trace enable=input_event_* "
                "-D /qemu_logs/qemu_input.log"
            )

        # ===== docker run command =====
        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--device",
            "/dev/kvm:/dev/kvm",
            "--cap-add",
            "NET_ADMIN",
            # Resource environment variables.
            "-e",
            f"DISK_SIZE={disk_size}",
            "-e",
            f"RAM_SIZE={ram_size}",
            "-e",
            f"CPU_CORES={cpu_cores}",
            "-e",
            f"ARGUMENTS={arguments}",
            # Volume mounts.
            "-v",
            f"{shared_path}:/shared:rw",
            "-v",
            f"{disk_image}:/boot.qcow2",
            "-v",
            f"{log_dir_host}:/qemu_logs",
            # Port mappings.
            "-p",
            f"{vnc_port}:8006",
            "-p",
            f"{api_port}:5000",
            "-p",
            f"{server_port}:8000",
            "-p",
            f"{VM_SCREENSHOT_PORT}:5001",
            # Image.
            VM_IMAGE,
        ]

        try:
            # First attempt.
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Port already allocated -> clean up this instance's container and retry once.
            if result.returncode != 0 and "port is already allocated" in result.stderr:
                logger.warning(
                    "port already allocated, cleaning up this instance and retrying..."
                )
                self.cleanup_leftover_container()
                time.sleep(3)

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

            if result.returncode != 0:
                return {
                    "success": False,
                    "message": f"Failed to start container: {result.stderr}",
                }

            container_id = result.stdout.strip()

            # Windows boots more slowly.
            if self.os_type == "windows":
                time.sleep(10)
            else:
                time.sleep(5)

            if not self._is_container_running(container_name):
                msg = "Container exited immediately after start; check the config"
                if self.os_type == "windows":
                    msg += " (Windows needs a correct UEFI/image config)"
                return {"success": False, "message": msg}

            # Record container info.
            self.active_containers[annotation_id] = {
                "container_id": container_id,
                "container_name": container_name,
                "log_file": str(log_file),
                "log_dir": str(log_dir_host),
                "start_time": datetime.now().isoformat(),
                "config": config.model_dump(),
                "os_type": self.os_type,
            }

            logger.info(
                "container started: %s (os=%s, id=%s)",
                container_name,
                self.os_type,
                container_id,
            )
            logger.info("log file: %s", log_file)

            result_data = {
                "success": True,
                "message": f"{self.os_type} container started",
                "container_id": container_id,
                "container_name": container_name,
                "log_file": str(log_file),
                "vnc_url": (
                    f"http://{VM_VNC_HOST}:{vnc_port}"
                    "/vnc.html?view_only=0&autoconnect=1&resize=scale"
                ),
                "os_type": self.os_type,
            }
            if self.os_type == "windows":
                result_data["smb_share"] = (
                    r"\\10.0.2.4\qemu  (shared dir inside the Windows VM)"
                )

            return result_data

        except subprocess.TimeoutExpired:
            return {"success": False, "message": "Timed out starting container"}
        except OSError as exc:
            return {"success": False, "message": f"Failed to start container: {exc}"}

    # ---------- Stop container ----------

    def stop_container(self, annotation_id: str) -> Dict:
        """Stop and remove a container."""
        if annotation_id not in self.active_containers:
            return {
                "success": False,
                "message": "Container does not exist or not started",
            }

        container_info = self.active_containers[annotation_id]
        container_name = container_info["container_name"]

        try:
            # Windows shuts down a little slower.
            stop_timeout = 30 if self.os_type == "windows" else 10

            result = subprocess.run(
                ["docker", "stop", "-t", str(stop_timeout), container_name],
                capture_output=True,
                text=True,
                timeout=stop_timeout + 5,
            )

            if result.returncode != 0:
                logger.warning("docker stop warning: %s", result.stderr)

            subprocess.run(
                ["docker", "rm", container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )

            start_time = datetime.fromisoformat(container_info["start_time"])
            duration = (datetime.now() - start_time).total_seconds()

            del self.active_containers[annotation_id]

            logger.info("container stopped: %s (os=%s)", container_name, self.os_type)

            return {
                "success": True,
                "message": "Container stopped and removed",
                "duration_seconds": round(duration, 2),
            }

        except (subprocess.SubprocessError, OSError) as exc:
            return {"success": False, "message": f"Failed to stop container: {exc}"}

    # ---------- Batch / status / cleanup ----------

    def get_container_status(self, annotation_id: str) -> Dict:
        if annotation_id not in self.active_containers:
            return {
                "exists": False,
                "running": False,
            }

        info = self.active_containers[annotation_id]
        container_name = info["container_name"]
        is_running = self._is_container_running(container_name)

        return {
            "exists": True,
            "running": is_running,
            "container_name": container_name,
            "container_id": info["container_id"],
            "log_file": info["log_file"],
            "start_time": info["start_time"],
            "os_type": info.get("os_type", self.os_type),
        }

    def list_active_containers(self) -> Dict:
        return {
            "count": len(self.active_containers),
            "containers": list(self.active_containers.keys()),
        }

    def cleanup_all(self) -> Dict:
        results = []
        for annotation_id in list(self.active_containers.keys()):
            res = self.stop_container(annotation_id)
            results.append({"annotation_id": annotation_id, "result": res})

        return {
            "success": True,
            "message": f"Cleaned up {len(results)} container(s)",
            "results": results,
        }

    def shutdown(self) -> None:
        """Stop tracked containers and remove any leftover (called on exit)."""
        self.cleanup_all()
        self.cleanup_leftover_container()

    # ---------- Docker helpers ----------

    def _container_exists(self, container_name: str) -> bool:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name={container_name}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
        )
        return container_name in result.stdout

    def _is_container_running(self, container_name: str) -> bool:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"name={container_name}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
        )
        return container_name in result.stdout

    def _force_stop_container(self, container_name: str) -> None:
        try:
            subprocess.run(
                ["docker", "stop", "-t", "5", container_name], capture_output=True
            )
            subprocess.run(["docker", "rm", container_name], capture_output=True)
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("force-stop of %s failed: %s", container_name, exc)

    def cleanup_leftover_container(self) -> None:
        """Remove this instance's container by exact name (multi-instance safe).

        Matching by exact ``container_name`` (e.g. container_ubuntu_v1) avoids
        deleting other instances' containers.
        """
        container_name = VM_CONTAINER_NAME
        try:
            if self._container_exists(container_name):
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    capture_output=True,
                    text=True,
                )
                logger.info("cleaned up leftover container: %s", container_name)

            # Drop matching in-memory records too.
            for ann_id, info in list(self.active_containers.items()):
                if info.get("container_name") == container_name:
                    del self.active_containers[ann_id]
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("failed to clean up container %s: %s", container_name, exc)


__all__ = [
    "VmManager",
    "ContainerConfig",
    "TARGET_OS",
    "VM_SERVICE_PORT",
    "VM_LOG_DIR",
]
