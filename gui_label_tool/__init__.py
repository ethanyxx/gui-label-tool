"""Backend package entry point for the GUI labeling tool."""

from .config import get_config, require_key

__all__ = [
    "get_config",
    "require_key",
]

__version__ = "0.1.0"
