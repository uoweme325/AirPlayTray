"""Application resource and writable data directory resolution.

Separates bundled read-only resources (PyInstaller ``_MEIPASS`` or source root)
from user-writable runtime data (``data/`` beside the executable when frozen, or
the repository directory when running directly from source).
"""

import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return True when running inside a frozen PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> Path:
    """Return the executable directory when frozen, or source directory otherwise."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    """Return the read-only root containing bundled assets and documentation."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return exe_dir()
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """Return the writable runtime directory for config, logs, and locks."""
    if is_frozen():
        return exe_dir() / "data"
    return Path(__file__).resolve().parent


def ensure_writable_data_dir() -> Path:
    """Create the writable data directory if missing and return its Path."""
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_config_path() -> Path:
    """Return the default ``config.json`` path in the writable data directory."""
    return data_dir() / "config.json"


def log_path() -> Path:
    """Return the tray log file path in the writable data directory."""
    return data_dir() / "tray.log"


def lock_path() -> Path:
    """Return the CLI single-instance lock file path in the data directory."""
    return data_dir() / "airplay-system-audio.lock"


def pid_path() -> Path:
    """Return the CLI PID file path in the data directory."""
    return data_dir() / "airplay-system-audio.pid"


def asset_path(name: str) -> Path:
    """Resolve an asset file from bundled resources, falling back to ``exe_dir``."""
    bundled = resource_dir() / "assets" / name
    if bundled.exists():
        return bundled
    return exe_dir() / "assets" / name
