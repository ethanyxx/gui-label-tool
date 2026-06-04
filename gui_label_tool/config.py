#!/usr/bin/env python3
"""Centralized YAML configuration loader.

Loads and validates the YAML config files that live in the project's top-level
``config/`` directory (this module is the *code* that reads them; ``config/`` is
the *data*).

Resolution order:
1. If the environment variable ``OSWORLD_CONFIG_PATH`` is set, load from there;
2. otherwise try ``config.yml`` / ``config.yaml`` in the project root;
3. if neither exists, raise.

Services only need::

    from gui_label_tool import get_config, require_key
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

# Project root directory (this file lives at gui_label_tool/config.py).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_CACHE: Dict[str, Any] | None = None


def _resolve_paths_in_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Turn every relative path field into an absolute path (relative to PROJECT_ROOT)."""
    path_keys = {
        "disk_image",
        "shared_path",
        "log_base_dir",
        "storage_dir",
        "log_path",
        "uefi_rom",
    }

    def _resolve(value: Any) -> Any:
        if isinstance(value, str):
            # Absolute paths are returned as-is; relative paths are resolved
            # against the project root.
            p = Path(value)
            if p.is_absolute():
                return str(p)
            return str((PROJECT_ROOT / p).resolve())
        return value

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            new_node = {}
            for k, v in node.items():
                if k in path_keys:
                    new_node[k] = _resolve(v)
                else:
                    new_node[k] = _walk(v)
            return new_node
        if isinstance(node, list):
            return [_walk(i) for i in node]
        return node

    return _walk(cfg)


def _load_config_from(path: Path) -> Dict[str, Any]:
    if not yaml:
        raise RuntimeError(
            "PyYAML is not installed; cannot read the config file. "
            "Install it with: pip install pyyaml"
        )

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}

    if not isinstance(loaded, dict):
        raise ValueError(f"Invalid format in {path}: the top level must be a mapping.")

    return _resolve_paths_in_config(loaded)


def get_config() -> Dict[str, Any]:
    """Global configuration entry point.

    Prefers the YAML file pointed to by ``OSWORLD_CONFIG_PATH``; otherwise falls
    back to ``PROJECT_ROOT/config.yml`` or ``config.yaml``.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    # 1. Try the path from the environment (manage_services.sh sets it per service).
    env_path = os.environ.get("OSWORLD_CONFIG_PATH")
    if env_path:
        cfg_path = Path(env_path).expanduser().resolve()
        _CONFIG_CACHE = _load_config_from(cfg_path)
        return _CONFIG_CACHE

    # 2. Fallback: config.yml / config.yaml in the project root.
    for name in ("config.yml", "config.yaml"):
        candidate = PROJECT_ROOT / name
        if candidate.exists():
            _CONFIG_CACHE = _load_config_from(candidate)
            return _CONFIG_CACHE

    # 3. Nothing found -> raise.
    raise FileNotFoundError(
        "No config file found: OSWORLD_CONFIG_PATH is not set, and neither "
        "config.yml nor config.yaml exists in the project root."
    )


def require_key(cfg: dict, key: str, path: str = "") -> Any:
    """Fetch ``key`` from ``cfg``, raising ``RuntimeError`` if it is missing.

    Args:
        cfg: the dict at the current level, e.g. ``CONFIG`` / ``CONFIG["vm"]``.
        key: the field name to fetch.
        path: dotted path used only in error messages, e.g. ``"vm"``
            or ``"vm.ports"``.
    """
    if not isinstance(cfg, dict):
        where = path or "<root>"
        raise TypeError(
            f"require_key expected cfg to be a dict, but got {type(cfg)!r} at '{where}'"
        )

    if key not in cfg:
        full = f"{path}.{key}" if path else key
        raise RuntimeError(
            f"Config error: required field '{full}' is missing; "
            "please add it to config.yml."
        )
    return cfg[key]


__all__ = ["get_config", "require_key", "PROJECT_ROOT"]
