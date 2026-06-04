"""Central runtime path resolution for Movie Library.

The default remains the legacy app-local layout. A future migration can enable
Application Support data paths only by adding an explicit marker/config flag.
This module does not create, copy, move, or delete any runtime data.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

LEGACY_MODE = "legacy-app-local"
APP_SUPPORT_MODE = "app-support"

APP_DIR = Path(__file__).resolve().parent
APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "Movie Library"
APP_SUPPORT_MODE_MARKER = APP_SUPPORT_DIR / "runtime-path-mode.json"


@dataclass(frozen=True)
class RuntimePaths:
    app_dir: Path
    data_dir: Path
    config_path: Path
    db_path: Path
    cache_dir: Path
    logs_dir: Path
    exports_dir: Path
    backups_dir: Path
    mode: str
    marker_path: Path
    app_support_dir: Path
    app_support_config_path: Path
    app_support_db_path: Path
    app_support_cache_dir: Path
    app_support_logs_dir: Path

    @property
    def app_support_ready(self) -> bool:
        return self.app_support_dir.exists()

    @property
    def is_app_support(self) -> bool:
        return self.mode == APP_SUPPORT_MODE


def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
    except Exception:
        pass
    return {}


def _normalise_mode(value) -> str:
    text = str(value or "").strip().lower()
    if text in {"app-support", "app_support", "application-support", "application_support"}:
        return APP_SUPPORT_MODE
    return LEGACY_MODE


def _mode_from_app_config(app_dir: Path) -> str:
    """Read the future opt-in flag from the legacy app-local config."""
    config = _read_json(app_dir / "config.json")
    candidates = [
        config.get("runtime_data_mode"),
        config.get("runtime_path_mode"),
        config.get("data_mode"),
    ]
    runtime_paths = config.get("runtime_paths")
    if isinstance(runtime_paths, dict):
        candidates.append(runtime_paths.get("mode"))
    runtime_data = config.get("runtime_data")
    if isinstance(runtime_data, dict):
        candidates.append(runtime_data.get("mode"))
    for candidate in candidates:
        if _normalise_mode(candidate) == APP_SUPPORT_MODE:
            return APP_SUPPORT_MODE
    return LEGACY_MODE


def _mode_from_marker(marker_path: Path) -> str:
    """Read a future explicit marker file without creating it."""
    if not marker_path.exists():
        return LEGACY_MODE
    marker = _read_json(marker_path)
    if marker:
        return _normalise_mode(marker.get("mode") or marker.get("runtime_path_mode"))
    try:
        return _normalise_mode(marker_path.read_text(encoding="utf-8").strip())
    except Exception:
        return LEGACY_MODE


def resolve_runtime_paths(app_dir: Path | None = None) -> RuntimePaths:
    app_dir = Path(app_dir or APP_DIR).resolve()
    marker_path = APP_SUPPORT_MODE_MARKER
    mode = APP_SUPPORT_MODE if (
        _mode_from_app_config(app_dir) == APP_SUPPORT_MODE
        or _mode_from_marker(marker_path) == APP_SUPPORT_MODE
    ) else LEGACY_MODE

    if mode == APP_SUPPORT_MODE:
        data_dir = APP_SUPPORT_DIR
        config_path = APP_SUPPORT_DIR / "config.json"
        db_path = APP_SUPPORT_DIR / "library.db"
        cache_dir = APP_SUPPORT_DIR / "cache"
        logs_dir = APP_SUPPORT_DIR / "logs"
        exports_dir = APP_SUPPORT_DIR / "exports"
        backups_dir = APP_SUPPORT_DIR / "backups"
    else:
        data_dir = app_dir
        config_path = app_dir / "config.json"
        db_path = app_dir / "library.db"
        cache_dir = app_dir / ".cache"
        logs_dir = app_dir / "logs"
        exports_dir = app_dir / "exports"
        backups_dir = app_dir / "backups"

    return RuntimePaths(
        app_dir=app_dir,
        data_dir=data_dir,
        config_path=config_path,
        db_path=db_path,
        cache_dir=cache_dir,
        logs_dir=logs_dir,
        exports_dir=exports_dir,
        backups_dir=backups_dir,
        mode=mode,
        marker_path=marker_path,
        app_support_dir=APP_SUPPORT_DIR,
        app_support_config_path=APP_SUPPORT_DIR / "config.json",
        app_support_db_path=APP_SUPPORT_DIR / "library.db",
        app_support_cache_dir=APP_SUPPORT_DIR / "cache",
        app_support_logs_dir=APP_SUPPORT_DIR / "logs",
    )


RUNTIME_PATHS = resolve_runtime_paths()


def runtime_paths_summary(paths: RuntimePaths = RUNTIME_PATHS) -> dict:
    return {
        "mode": paths.mode,
        "is_app_support": paths.is_app_support,
        "app_support_ready": paths.app_support_ready,
        "marker_path": str(paths.marker_path),
        "app_dir": str(paths.app_dir),
        "data_dir": str(paths.data_dir),
        "config_path": str(paths.config_path),
        "db_path": str(paths.db_path),
        "cache_dir": str(paths.cache_dir),
        "logs_dir": str(paths.logs_dir),
        "exports_dir": str(paths.exports_dir),
        "backups_dir": str(paths.backups_dir),
    }
