import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import shutil
import shlex
import sys
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from collections import OrderedDict
from flask import Flask, request, redirect, url_for, session, render_template_string, send_file, flash, Response, jsonify, make_response, has_request_context
from runtime_paths import RUNTIME_PATHS, runtime_paths_summary
from scanner import init_db, scan_movies_folder, scan_tv_folder

APP_DIR = RUNTIME_PATHS.app_dir
DB_PATH = RUNTIME_PATHS.db_path
CONFIG_PATH = RUNTIME_PATHS.config_path
MOVIE_ROOT_DIR = APP_DIR.parent
WEB_ROOT_DIR = MOVIE_ROOT_DIR.parent
CADDY_BINARY_NAME = "caddy.exe" if os.name == "nt" else "caddy"
CADDY_EXE_PATH = WEB_ROOT_DIR / CADDY_BINARY_NAME
CADDYFILE_PATH = WEB_ROOT_DIR / "Caddyfile"
BACKUP_DIR = RUNTIME_PATHS.backups_dir
ARTWORK_CACHE_DIR = RUNTIME_PATHS.cache_dir / "artwork"
LOGS_DIR = RUNTIME_PATHS.logs_dir
EXPORTS_DIR = RUNTIME_PATHS.exports_dir
APP_SUPPORT_DIR = RUNTIME_PATHS.app_support_dir
APP_SUPPORT_CONFIG_PATH = RUNTIME_PATHS.app_support_config_path
APP_SUPPORT_DB_PATH = RUNTIME_PATHS.app_support_db_path
APP_SUPPORT_CACHE_DIR = RUNTIME_PATHS.app_support_cache_dir
APP_SUPPORT_LOGS_DIR = RUNTIME_PATHS.app_support_logs_dir
DMG_BUILD_DIR = APP_DIR / "build"
TEST_DMG_PATH = DMG_BUILD_DIR / "Movie Library Test.dmg"
TEST_DMG_CHECKSUM_PATH = DMG_BUILD_DIR / "Movie Library Test.sha256.txt"
TEST_DMG_MANIFEST_PATH = DMG_BUILD_DIR / "Movie Library Test DMG CONTENTS MANIFEST.txt"
TEST_DMG_INSTALL_NOTES_PATH = DMG_BUILD_DIR / "Movie Library Test DMG INSTALL NOTES.txt"
TEST_DMG_SUMMARY_PATH = DMG_BUILD_DIR / "Movie Library Test DMG SUMMARY.txt"
TEST_DMG_VERIFY_SUMMARY_PATH = DMG_BUILD_DIR / "Movie Library Test DMG VERIFY SUMMARY.txt"
DMG_BUILD_LOG_PATH = LOGS_DIR / "movie_library_dmg_build.log"
DMG_VERIFY_LOG_PATH = LOGS_DIR / "movie_library_dmg_verify.log"
APP_SUPPORT_PREP_HELPER_PATH = APP_DIR / "Prepare Movie Library App Support.command"
APP_SUPPORT_PREP_LOG_PATH = LOGS_DIR / "movie_library_app_support_prep.log"
APP_SUPPORT_VERIFY_HELPER_PATH = APP_DIR / "Verify Movie Library App Support.command"
APP_SUPPORT_VERIFY_LOG_PATH = LOGS_DIR / "movie_library_app_support_verify.log"
MIGRATION_DRY_RUN_HELPER_PATH = APP_DIR / "Dry Run Movie Library Migration.command"
MIGRATION_DRY_RUN_LOG_PATH = LOGS_DIR / "movie_library_migration_dry_run.log"
MIGRATION_DRY_RUN_SUMMARY_PATH = LOGS_DIR / "movie_library_migration_dry_run_summary.txt"
MIGRATION_BACKUP_HELPER_PATH = APP_DIR / "Backup Movie Library Migration Data.command"
MIGRATION_BACKUP_VERIFY_HELPER_PATH = APP_DIR / "Verify Movie Library Migration Backup.command"
MIGRATION_STAGE_HELPER_PATH = APP_DIR / "Stage Movie Library Migration Data.command"
MIGRATION_STAGE_VERIFY_HELPER_PATH = APP_DIR / "Verify Movie Library Migration Stage.command"
MIGRATION_CUTOVER_READINESS_HELPER_PATH = APP_DIR / "Check Movie Library Migration Cutover.command"
MIGRATION_CUTOVER_PLAN_HELPER_PATH = APP_DIR / "Plan Movie Library Migration Cutover.command"
MIGRATION_BACKUP_LOG_PATH = LOGS_DIR / "movie_library_migration_backup.log"
MIGRATION_BACKUP_VERIFY_LOG_PATH = LOGS_DIR / "movie_library_migration_backup_verify.log"
MIGRATION_STAGE_LOG_PATH = LOGS_DIR / "movie_library_migration_stage.log"
MIGRATION_STAGE_VERIFY_LOG_PATH = LOGS_DIR / "movie_library_migration_stage_verify.log"
MIGRATION_CUTOVER_READINESS_LOG_PATH = LOGS_DIR / "movie_library_migration_cutover_readiness.log"
MIGRATION_CUTOVER_PLAN_LOG_PATH = LOGS_DIR / "movie_library_migration_cutover_plan.log"
MIGRATION_BACKUP_SUMMARY_PATH = APP_SUPPORT_DIR / "migration-backups" / "Movie Library Migration BACKUP SUMMARY.txt"
MIGRATION_BACKUP_VERIFY_SUMMARY_PATH = APP_SUPPORT_DIR / "migration-backups" / "Movie Library Migration BACKUP VERIFY SUMMARY.txt"
MIGRATION_STAGE_DIR = APP_SUPPORT_DIR / "migration-staging"
MIGRATION_STAGE_CURRENT_DIR = MIGRATION_STAGE_DIR / "current"
MIGRATION_STAGE_SUMMARY_PATH = MIGRATION_STAGE_DIR / "Movie Library Migration STAGE SUMMARY.txt"
MIGRATION_STAGE_VERIFY_SUMMARY_PATH = MIGRATION_STAGE_DIR / "Movie Library Migration STAGE VERIFY SUMMARY.txt"
MIGRATION_CUTOVER_READINESS_SUMMARY_PATH = MIGRATION_STAGE_DIR / "Movie Library Migration CUTOVER READINESS SUMMARY.txt"
MIGRATION_CUTOVER_PLAN_SUMMARY_PATH = MIGRATION_STAGE_DIR / "Movie Library Migration CUTOVER PLAN SUMMARY.txt"
APP_SUPPORT_PREP_SUMMARY_PATH = APP_SUPPORT_DIR / "Movie Library App Support PREP SUMMARY.txt"
APP_SUPPORT_VERIFY_SUMMARY_PATH = APP_SUPPORT_DIR / "Movie Library App Support VERIFY SUMMARY.txt"
APP_SUPPORT_README_PATH = APP_SUPPORT_DIR / "README - Movie Library Data Folder.txt"
UI_VERSION = "UI v28.5.31 - app-support marker preview"


def caddy_command_path():
    """Return the Caddy executable path.

    On macOS/Linux this app should use the trusted Homebrew/system `caddy`
    command, not a downloaded binary beside the Caddyfile. On Windows we keep
    the previous local caddy.exe fallback.
    """
    if os.name != "nt":
        found = shutil.which("caddy")
        if found:
            return found
        return "caddy"

    if CADDY_EXE_PATH.exists():
        return str(CADDY_EXE_PATH)
    found = shutil.which(CADDY_BINARY_NAME) or shutil.which("caddy")
    if found:
        return found
    return str(CADDY_EXE_PATH)


def is_caddy_running():
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq caddy.exe"],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return "caddy.exe" in (result.stdout or "").lower()

    result = subprocess.run(
        ["pgrep", "-x", "caddy"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def ensure_caddy_paths():
    if not CADDYFILE_PATH.exists():
        raise FileNotFoundError(f"Caddyfile not found: {CADDYFILE_PATH}")
    command_path = caddy_command_path()
    if not Path(command_path).exists() and shutil.which("caddy") is None:
        raise FileNotFoundError(
            f"Caddy executable not found. Put caddy in {WEB_ROOT_DIR} or install it so the caddy command is available."
        )


def restart_caddy_process():
    """Reload Caddy safely when possible, or start it if it is not running.

    On macOS/Linux we deliberately avoid killing the Caddy process because the same
    Caddy instance can serve both the Movie Library and Tutor app hostnames.
    """
    ensure_caddy_paths()
    command_path = caddy_command_path()
    log_path = WEB_ROOT_DIR / "caddy.log"

    if os.name == "nt":
        detached_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        if is_caddy_running():
            # Prefer a graceful config reload. Fall back to the old Windows restart method.
            reload_result = subprocess.run(
                [command_path, "reload", "--config", str(CADDYFILE_PATH)],
                cwd=str(WEB_ROOT_DIR),
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if reload_result.returncode == 0:
                return
            subprocess.run(
                ["taskkill", "/IM", "caddy.exe", "/F"],
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        subprocess.Popen(
            [command_path, "run", "--config", str(CADDYFILE_PATH)],
            cwd=str(WEB_ROOT_DIR),
            creationflags=detached_flags,
            close_fds=True,
        )
        return

    # macOS/Linux: reload if Caddy is already running; otherwise start it.
    if is_caddy_running():
        reload_result = subprocess.run(
            [command_path, "reload", "--config", str(CADDYFILE_PATH)],
            cwd=str(WEB_ROOT_DIR),
            capture_output=True,
            text=True,
        )
        if reload_result.returncode == 0:
            return
        # If reload cannot contact the admin endpoint, start is still safer than pkill.
        # The caller will see any failure in caddy.log if another instance owns the port.

    with log_path.open("ab") as log_file:
        subprocess.Popen(
            [command_path, "run", "--config", str(CADDYFILE_PATH)],
            cwd=str(WEB_ROOT_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )



def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"folder": "", "tv_folder": "", "web": {"username": "admin", "password": "change-me-now", "port": 8765, "viewers": [{"username": "Laila", "password": "La1laz3b3st", "enabled": True}]}}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


cfg = load_config()
cfg.setdefault("folder", "")
cfg.setdefault("tv_folder", "")
cfg.setdefault("web", {})
cfg["web"].setdefault("username", "admin")
cfg["web"].setdefault("password", "change-me-now")
cfg["web"].setdefault("port", 8765)

def _normalise_viewers(web_cfg):
    """Return viewer accounts, migrating the older single-viewer config.

    Usernames are matched case-insensitively at login, so Laila, laila,
    and LAILA all sign in as the same viewer account.
    """
    viewers = []
    existing = web_cfg.get("viewers")
    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username") or "").strip()
            password = str(item.get("password") or "").strip()
            if not username or not password:
                continue
            viewers.append({
                "username": username,
                "password": password,
                "enabled": bool(item.get("enabled", True)),
            })

    legacy_username = str(web_cfg.get("viewer_username") or "").strip()
    legacy_password = str(web_cfg.get("viewer_password") or "").strip()
    if legacy_username and legacy_password and not any(v["username"].lower() == legacy_username.lower() for v in viewers):
        viewers.append({"username": legacy_username, "password": legacy_password, "enabled": True})

    if not viewers:
        viewers.append({"username": "Laila", "password": "La1laz3b3st", "enabled": True})

    # Keep a stable canonical list and remove old single-viewer keys from active use.
    web_cfg["viewers"] = viewers
    return viewers

_normalise_viewers(cfg["web"])
cfg.setdefault("ignored_alerts", [])
cfg.setdefault("review_alerts", [])
cfg.setdefault("alert_notes", {})

def _source_defaults(kind, index, path=""):
    label = "Movie folder" if kind == "movies" else "TV folder"
    return {
        "name": f"{label} {index}",
        "path": str(path or "").strip(),
        "type": "Movies" if kind == "movies" else "TV",
        "enabled": True,
        "last_scanned": "",
    }

def _normalise_sources(values, kind):
    """Return a flexible list of library folders.

    v28.3.1 removes the fixed Movie 1 / Movie 2 layout. The config can now
    hold any number of movie or TV folder sources behind a single Movies or TV
    library card, while still accepting the older string/list formats.
    """
    objects = []
    if isinstance(values, list):
        for item in values:
            if isinstance(item, dict):
                objects.append(item)
            elif str(item).strip():
                objects.append({"path": str(item).strip()})
    elif isinstance(values, str) and values.strip():
        objects.append({"path": values.strip()})

    normalised = []
    for i, raw in enumerate(objects, start=1):
        if not isinstance(raw, dict):
            raw = {"path": str(raw or "").strip()}
        path = str(raw.get("path") or "").strip()
        if not path:
            continue
        source = _source_defaults(kind, i, path)
        source["name"] = (raw.get("name") or source["name"]).strip()
        source["name"] = re.sub(r"^(Movies|TV) source (\d+)$", lambda m: ("Movie folder " if m.group(1) == "Movies" else "TV folder ") + m.group(2), source["name"])
        source["enabled"] = bool(raw.get("enabled", True))
        source["last_scanned"] = raw.get("last_scanned") or ""
        normalised.append(source)
    return normalised

def get_movie_sources():
    sources = cfg.get("movie_sources")
    if sources is None:
        sources = cfg.get("movie_folders") or [cfg.get("folder", "")]
    return _normalise_sources(sources, "movies")

def get_tv_sources():
    sources = cfg.get("tv_sources")
    if sources is None:
        sources = cfg.get("tv_folders") or [cfg.get("tv_folder", "")]
    return _normalise_sources(sources, "tv")

def save_movie_sources(sources):
    sources = _normalise_sources(sources, "movies")
    cfg["movie_sources"] = sources
    cfg["movie_folders"] = [s["path"] for s in sources]
    cfg["folder"] = sources[0]["path"] if sources else ""
    save_config(cfg)

def save_tv_sources(sources):
    sources = _normalise_sources(sources, "tv")
    cfg["tv_sources"] = sources
    cfg["tv_folders"] = [s["path"] for s in sources]
    cfg["tv_folder"] = sources[0]["path"] if sources else ""
    save_config(cfg)

def _configured_sources(kind):
    values = get_movie_sources() if kind == "movies" else get_tv_sources()
    return [(i + 1, source) for i, source in enumerate(values) if source.get("enabled", True) and source.get("path", "").strip()]

def _source_path(source):
    return source.get("path", "") if isinstance(source, dict) else str(source or "")

def _mark_source_scanned(kind, index):
    sources = get_movie_sources() if kind == "movies" else get_tv_sources()
    if 1 <= index <= len(sources):
        sources[index - 1]["last_scanned"] = now_label()
        if kind == "movies":
            save_movie_sources(sources)
        else:
            save_tv_sources(sources)

def _source_summary(sources):
    enabled_count = sum(1 for source in sources if source.get("enabled", True))
    scans = [source.get("last_scanned") for source in sources if source.get("last_scanned")]
    return enabled_count, (scans[-1] if scans else "Never")

def clear_movie_library(conn_obj):
    init_db(conn_obj)
    conn_obj.execute("DELETE FROM movies")
    conn_obj.execute("DELETE FROM scan_folders")
    conn_obj.commit()

def clear_tv_library(conn_obj):
    init_db(conn_obj)
    conn_obj.execute("DELETE FROM tv_episodes")
    conn_obj.execute("DELETE FROM tv_shows")
    conn_obj.commit()

# Persist migrated two-source config without changing existing source 1 values.
save_movie_sources(get_movie_sources())
save_tv_sources(get_tv_sources())
app = Flask(__name__)
app.secret_key = os.environ.get("MOVIE_LIBRARY_SECRET", "change-this-secret")

SCAN_LOCK = threading.Lock()
SCAN_STATUS = {
    "running": False,
    "label": "No scan has run yet",
    "message": "No scan has run yet.",
    "ok": True,
    "started": None,
    "finished": None,
}

DEFAULT_SCHEDULED_SCAN = {
    "enabled": False,
    "frequency": "daily",
    "time_of_day": "03:00",
    "interval_minutes": 360,
    "last_run_at": "",
    "last_run_key": "",
    "last_result": "Never run",
}
_scheduler_started = False
_scheduler_lock = threading.Lock()


def get_scheduled_scan_settings():
    raw = cfg.get("scheduled_scan") or {}
    settings = dict(DEFAULT_SCHEDULED_SCAN)
    if isinstance(raw, dict):
        settings.update(raw)
    try:
        settings["interval_minutes"] = max(15, int(settings.get("interval_minutes") or 360))
    except Exception:
        settings["interval_minutes"] = 360
    if settings.get("frequency") not in {"daily", "interval"}:
        settings["frequency"] = "daily"
    time_text = str(settings.get("time_of_day") or "03:00").strip()
    if not re.match(r"^\d{2}:\d{2}$", time_text):
        time_text = "03:00"
    settings["time_of_day"] = time_text
    settings["enabled"] = bool(settings.get("enabled"))
    return settings


def save_scheduled_scan_settings(settings):
    cfg["scheduled_scan"] = settings
    save_config(cfg)


def scheduled_scan_summary(settings=None):
    settings = settings or get_scheduled_scan_settings()
    if not settings.get("enabled"):
        next_text = "Disabled"
    elif settings.get("frequency") == "daily":
        next_text = f"Daily at {settings.get('time_of_day', '03:00')}"
    else:
        next_text = f"Every {settings.get('interval_minutes', 360)} minutes"
    return {
        **settings,
        "next_text": next_text,
        "last_run_at": settings.get("last_run_at") or "Never",
        "last_result": settings.get("last_result") or "Never run",
    }


def _schedule_is_due(settings, now=None):
    now = now or datetime.now()
    if not settings.get("enabled"):
        return False
    if get_scan_status().get("running"):
        return False

    if settings.get("frequency") == "daily":
        time_text = settings.get("time_of_day") or "03:00"
        try:
            hour, minute = [int(x) for x in time_text.split(":", 1)]
        except Exception:
            hour, minute = 3, 0
        run_key = f"{now.date().isoformat()}-{hour:02d}:{minute:02d}"
        due_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return now >= due_time and settings.get("last_run_key") != run_key

    last_text = settings.get("last_run_at") or ""
    try:
        last_dt = datetime.fromisoformat(last_text)
    except Exception:
        last_dt = None
    interval = timedelta(minutes=max(15, int(settings.get("interval_minutes") or 360)))
    return last_dt is None or now - last_dt >= interval


def _mark_scheduled_scan_completed(ok=True):
    settings = get_scheduled_scan_settings()
    now = datetime.now()
    if settings.get("frequency") == "daily":
        time_text = settings.get("time_of_day") or "03:00"
        settings["last_run_key"] = f"{now.date().isoformat()}-{time_text}"
    settings["last_run_at"] = now.isoformat(timespec="seconds")
    settings["last_result"] = "Completed" if ok else "Failed"
    save_scheduled_scan_settings(settings)


def _scheduled_scan_loop():
    while True:
        try:
            settings = get_scheduled_scan_settings()
            if _schedule_is_due(settings):
                _run_scan_action("refresh-all")
                _mark_scheduled_scan_completed(ok=get_scan_status().get("ok", False))
        except Exception as exc:
            try:
                settings = get_scheduled_scan_settings()
                settings["last_run_at"] = datetime.now().isoformat(timespec="seconds")
                settings["last_result"] = f"Failed: {exc}"
                save_scheduled_scan_settings(settings)
                record_activity("error", "scan", "scheduled_scan_failed", f"Scheduled scan failed: {exc}", actor="system")
            except Exception:
                pass
        time.sleep(60)


def start_scheduled_scan_thread():
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
        threading.Thread(target=_scheduled_scan_loop, daemon=True, name="library-scheduled-scan").start()


def _try_import_pillow():
    try:
        from PIL import Image, ImageOps
        return Image, ImageOps
    except Exception:
        return None, None


def _cache_response(response, max_age=604800):
    response.headers["Cache-Control"] = f"public, max-age={int(max_age)}, immutable"
    return response


def _safe_artwork_path(path):
    if not path:
        return None
    try:
        candidate = Path(path).expanduser().resolve()
    except Exception:
        return None
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def _send_artwork_file(path, max_age=604800):
    candidate = _safe_artwork_path(path)
    if not candidate:
        return ("", 404)
    return _cache_response(send_file(str(candidate), conditional=True, max_age=max_age), max_age=max_age)


def _thumbnail_cache_path(source_path, namespace, kind, item_id, width, height):
    source = _safe_artwork_path(source_path)
    if not source:
        return None
    try:
        stamp = int(source.stat().st_mtime)
    except Exception:
        stamp = 0
    ext = ".jpg" if source.suffix.lower() not in {".png", ".webp"} else source.suffix.lower()
    safe_kind = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(kind or "artwork"))
    return ARTWORK_CACHE_DIR / namespace / safe_kind / f"{item_id}_{width}x{height}_{stamp}{ext}"


def _send_thumbnail(source_path, namespace, kind, item_id, width, height):
    source = _safe_artwork_path(source_path)
    if not source:
        return ("", 404)
    Image, ImageOps = _try_import_pillow()
    if Image is None:
        # Pillow is optional at runtime. If it is not installed yet, still serve
        # with browser caching so the app keeps working.
        return _send_artwork_file(str(source))

    cache_path = _thumbnail_cache_path(source, namespace, kind, item_id, width, height)
    if cache_path is None:
        return ("", 404)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if not cache_path.exists():
        try:
            with Image.open(source) as img:
                img = ImageOps.exif_transpose(img)
                img.thumbnail((int(width), int(height)), Image.Resampling.LANCZOS)
                if cache_path.suffix.lower() in {".jpg", ".jpeg"}:
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    img.save(cache_path, quality=78, optimize=True, progressive=True)
                else:
                    img.save(cache_path, optimize=True)
        except Exception:
            return _send_artwork_file(str(source))

    return _cache_response(send_file(str(cache_path), conditional=True, max_age=604800), max_age=604800)


def _row_to_public_dict(row, include_paths=False):
    if not row:
        return None
    data = dict(row)
    if not include_paths:
        for key in list(data.keys()):
            if key.endswith("_path") or key in {"movie_path", "show_path", "episode_path", "folder_path"}:
                data.pop(key, None)
    return data

def now_label():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def set_scan_status(**updates):
    with SCAN_LOCK:
        SCAN_STATUS.update(updates)

def get_scan_status():
    with SCAN_LOCK:
        return dict(SCAN_STATUS)


def ensure_maintenance_tables(c):
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            label TEXT,
            ok INTEGER DEFAULT 1,
            message TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT DEFAULT 'info',
            area TEXT,
            action TEXT,
            actor TEXT,
            message TEXT,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_activity_log_created ON activity_log(created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_activity_log_area ON activity_log(area)")
    c.commit()


def _current_actor_label():
    try:
        if has_request_context():
            role = session.get("role") or "guest"
            if role == "admin":
                return f"admin:{cfg.get('web', {}).get('username', 'admin')}"
            viewer_name = session.get("viewer_username") or "viewer"
            return f"viewer:{viewer_name}"
    except Exception:
        pass
    return "system"


def record_activity(level="info", area="system", action="event", message="", details=None, actor=None):
    """Store an admin-readable audit/activity event.

    This is intentionally lightweight and local-only: it records actions such as
    scans, backups, settings changes, login attempts, cleanup, and errors so the
    admin can understand what the server has been doing without opening Terminal.
    """
    try:
        level = str(level or "info").lower()
        if level not in {"info", "warning", "error", "success"}:
            level = "info"
        details_text = ""
        if details is not None:
            if isinstance(details, (dict, list, tuple)):
                details_text = json.dumps(details, ensure_ascii=False, default=str)
            else:
                details_text = str(details)
        c = sqlite3.connect(DB_PATH)
        try:
            ensure_maintenance_tables(c)
            c.execute(
                """
                INSERT INTO activity_log(level, area, action, actor, message, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (level, str(area or "system"), str(action or "event"), actor or _current_actor_label(), str(message or ""), details_text, now_label()),
            )
            c.execute(
                "DELETE FROM activity_log WHERE id NOT IN (SELECT id FROM activity_log ORDER BY id DESC LIMIT 500)"
            )
            c.commit()
        finally:
            c.close()
    except Exception:
        pass


def get_activity_log(limit=100, level="", area="", q=""):
    c = conn()
    try:
        ensure_maintenance_tables(c)
        filters = []
        params = []
        if level:
            filters.append("level=?")
            params.append(level)
        if area:
            filters.append("area=?")
            params.append(area)
        if q:
            filters.append("(message LIKE ? OR action LIKE ? OR actor LIKE ? OR details LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like, like, like])
        where = "WHERE " + " AND ".join(filters) if filters else ""
        rows = c.execute(
            f"SELECT * FROM activity_log {where} ORDER BY id DESC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        c.close()


def get_activity_stats():
    c = conn()
    try:
        ensure_maintenance_tables(c)
        total = int(c.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0])
        errors = int(c.execute("SELECT COUNT(*) FROM activity_log WHERE level='error'").fetchone()[0])
        warnings = int(c.execute("SELECT COUNT(*) FROM activity_log WHERE level='warning'").fetchone()[0])
        last = c.execute("SELECT created_at FROM activity_log ORDER BY id DESC LIMIT 1").fetchone()
        return {"total": total, "errors": errors, "warnings": warnings, "last": last[0] if last else "Never"}
    finally:
        c.close()


def record_scan_history(action, label, ok, message, started_at, finished_at):
    try:
        c = conn()
        try:
            ensure_maintenance_tables(c)
            c.execute(
                """
                INSERT INTO scan_history(action, label, ok, message, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (action, label, 1 if ok else 0, message, started_at or "", finished_at or ""),
            )
            c.execute(
                "DELETE FROM scan_history WHERE id NOT IN (SELECT id FROM scan_history ORDER BY id DESC LIMIT 50)"
            )
            c.commit()
            record_activity(
                "success" if ok else "error",
                "scan",
                action,
                label,
                {"message": message, "started_at": started_at or "", "finished_at": finished_at or ""},
                actor="system" if str(action or "").startswith("scheduled") else None,
            )
        finally:
            c.close()
    except Exception:
        pass


def get_scan_history(limit=12):
    c = conn()
    try:
        ensure_maintenance_tables(c)
        return [dict(row) for row in c.execute(
            "SELECT * FROM scan_history ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()]
    finally:
        c.close()


def _path_root_text(path_text):
    text = str(path_text or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve(strict=False)).rstrip("/\\")
    except Exception:
        return text.rstrip("/\\")


def _safe_count_path_prefix(c, table_name, path_column, root_text, extra_sql=""):
    root = _path_root_text(root_text)
    if not root:
        return 0
    try:
        exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return 0
        where = f"(COALESCE({path_column}, '')=? OR COALESCE({path_column}, '') LIKE ?)"
        if extra_sql:
            where += f" AND ({extra_sql})"
        row = c.execute(
            f"SELECT COUNT(*) AS n FROM {table_name} WHERE {where}",
            (root, root + "/%"),
        ).fetchone()
        return int(row["n"] if row else 0)
    except Exception:
        return 0


def _safe_max_scan_folder_update(c, root_text):
    root = _path_root_text(root_text)
    if not root:
        return ""
    try:
        exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scan_folders'"
        ).fetchone()
        if not exists:
            return ""
        row = c.execute(
            """
            SELECT MAX(updated_at) AS updated_at
            FROM scan_folders
            WHERE folder_path=? OR folder_path LIKE ?
            """,
            (root, root + "/%"),
        ).fetchone()
        return (row["updated_at"] if row and row["updated_at"] else "")
    except Exception:
        return ""


def _source_health_status(source, last_signature_update):
    if not source.get("enabled", True):
        return "Disabled", "warn"
    path_text = source.get("path") or ""
    if not path_text.strip():
        return "No folder path", "danger"
    if not Path(path_text).expanduser().exists():
        return "Folder not reachable", "danger"
    if not source.get("last_scanned") and not last_signature_update:
        return "Ready, never checked", "warn"
    return "Healthy", "good"


def get_library_dashboard_data():
    movie_sources = get_movie_sources()
    tv_sources = get_tv_sources()
    about = get_about_info()
    report_summary = get_report_summary()
    missing = missing_counts()
    scheduled = scheduled_scan_summary()
    scan_status = get_scan_status()
    history = get_scan_history(6)
    activity = get_activity_stats()

    c = conn()
    try:
        movie_cards = []
        for i, source in enumerate(movie_sources, start=1):
            last_signature_update = _safe_max_scan_folder_update(c, source.get("path"))
            status_text, status_class = _source_health_status(source, last_signature_update)
            total_items = _safe_count_path_prefix(c, "movies", "movie_path", source.get("path"))
            missing_items = _safe_count_path_prefix(c, "movies", "movie_path", source.get("path"), "COALESCE(missing,0)=1")
            movie_cards.append({
                **source,
                "index": i,
                "status_text": status_text,
                "status_class": status_class,
                "last_signature_update": last_signature_update or "Never",
                "total_items": total_items,
                "missing_items": missing_items,
                "check_url": f"/scan-wait/refresh-movies-{i}",
                "manage_url": "/settings#movies-library",
            })

        tv_cards = []
        for i, source in enumerate(tv_sources, start=1):
            last_signature_update = _safe_max_scan_folder_update(c, source.get("path"))
            status_text, status_class = _source_health_status(source, last_signature_update)
            show_count = _safe_count_path_prefix(c, "tv_shows", "show_path", source.get("path"))
            episode_count = _safe_count_path_prefix(c, "tv_episodes", "file_path", source.get("path"))
            missing_episodes = _safe_count_path_prefix(c, "tv_episodes", "file_path", source.get("path"), "COALESCE(missing,0)=1")
            tv_cards.append({
                **source,
                "index": i,
                "status_text": status_text,
                "status_class": status_class,
                "last_signature_update": last_signature_update or "Never",
                "show_count": show_count,
                "episode_count": episode_count,
                "missing_items": missing_episodes,
                "check_url": f"/scan-wait/refresh-tv-{i}",
                "manage_url": "/settings#tv-library",
            })
    finally:
        c.close()

    warnings = []
    for card in movie_cards + tv_cards:
        if card["status_class"] != "good":
            warnings.append(f"{card.get('name')}: {card['status_text']}")
        if int(card.get("missing_items") or 0):
            warnings.append(f"{card.get('name')}: {card['missing_items']} missing item(s)")

    try:
        all_alerts = get_all_issue_alerts(max_per_type=40, max_total=300)
    except Exception:
        all_alerts = []
    visible_alerts = [item for item in all_alerts if not item.get("ignored")]
    alert_summary = {
        "visible_total": len(visible_alerts),
        "review_total": sum(1 for item in visible_alerts if item.get("review")),
        "ignored_total": sum(1 for item in all_alerts if item.get("ignored")),
        "movie_total": sum(1 for item in visible_alerts if item.get("area") == "Movies"),
        "tv_total": sum(1 for item in visible_alerts if item.get("area") == "TV Shows"),
        "high_total": sum(1 for item in visible_alerts if item.get("severity") == "Alert"),
        "warning_total": sum(1 for item in visible_alerts if item.get("severity") == "Warning"),
    }

    return {
        "about": about,
        "report_summary": report_summary,
        "missing": missing,
        "scheduled": scheduled,
        "scan_status": scan_status,
        "history": history,
        "movie_sources": movie_cards,
        "tv_sources": tv_cards,
        "warning_count": len(warnings),
        "warnings": warnings[:8],
        "activity": activity,
        "alert_summary": alert_summary,
    }



DIAGNOSTIC_VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.mpg', '.mpeg',
    '.iso', '.vob', '.m2ts', '.mts', '.webm', '.divx', '.xvid', '.flv', '.ogm', '.ogv', '.3gp'
}


def _diagnostic_suffix(file_path):
    suffix = (Path(str(file_path)).suffix or '').lower().strip()
    return '.' + suffix.lstrip('.').strip() if suffix else ''


def _diagnostic_folder_signature(folder_path):
    parts = []
    try:
        children = sorted(Path(folder_path).iterdir(), key=lambda p: p.name.lower())
    except Exception:
        return ''
    for child in children:
        if not child.is_file():
            continue
        try:
            stat = child.stat()
        except Exception:
            continue
        parts.append(f"{child.name.lower()}|{int(stat.st_mtime_ns)}|{stat.st_size}")
    return f"{len(parts)}#" + "#".join(parts)


def _cached_signatures_under_root(c, root_text):
    root = _path_root_text(root_text)
    if not root:
        return {}
    try:
        exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scan_folders'").fetchone()
        if not exists:
            return {}
        rows = c.execute(
            """
            SELECT folder_path, folder_signature, updated_at
            FROM scan_folders
            WHERE folder_path=? OR folder_path LIKE ?
            """,
            (root, root + "/%"),
        ).fetchall()
        return {row["folder_path"]: {"signature": row["folder_signature"], "updated_at": row["updated_at"]} for row in rows}
    except Exception:
        return {}


def _current_media_folder_signatures(root_text, max_samples=8):
    root = Path(str(root_text or '')).expanduser()
    output = {
        "folders": {},
        "media_file_count": 0,
        "error": "",
        "sample_folders": [],
    }
    try:
        root = root.resolve(strict=False)
    except Exception:
        pass
    if not root.exists():
        output["error"] = "Folder not reachable"
        return output
    if not root.is_dir():
        output["error"] = "Path is not a folder"
        return output
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            media_files = [name for name in filenames if _diagnostic_suffix(Path(name)) in DIAGNOSTIC_VIDEO_EXTENSIONS]
            if not media_files:
                continue
            folder_path = Path(dirpath).resolve()
            output["media_file_count"] += len(media_files)
            output["folders"][str(folder_path)] = _diagnostic_folder_signature(folder_path)
            if len(output["sample_folders"]) < max_samples:
                output["sample_folders"].append(str(folder_path))
    except Exception as exc:
        output["error"] = str(exc)
    return output


def _source_database_counts(c, kind, source_path):
    if kind == "movies":
        return {
            "primary_label": "Movies",
            "primary_count": _safe_count_path_prefix(c, "movies", "movie_path", source_path),
            "secondary_label": "Needs artwork/NFO attention",
            "secondary_count": _safe_count_path_prefix(c, "movies", "movie_path", source_path, "poster_path IS NULL OR fanart_path IS NULL OR nfo_path IS NULL"),
            "missing_count": _safe_count_path_prefix(c, "movies", "movie_path", source_path, "COALESCE(missing,0)=1"),
        }
    return {
        "primary_label": "Episodes",
        "primary_count": _safe_count_path_prefix(c, "tv_episodes", "file_path", source_path),
        "secondary_label": "Shows",
        "secondary_count": _safe_count_path_prefix(c, "tv_shows", "show_path", source_path),
        "missing_count": _safe_count_path_prefix(c, "tv_episodes", "file_path", source_path, "COALESCE(missing,0)=1"),
    }


def _diagnose_source(c, kind, index, source, deep=False):
    """Build one source diagnostics card.

    Default mode is intentionally fast: it reads config, DB counts, and cached
    scan signatures only. The old behaviour walked every configured media
    folder on every Libraries page load, which made the admin tab slow for
    large libraries or network/external drives. Deep comparison is now only
    done when the admin explicitly asks for it.
    """
    path_text = source.get("path", "") if isinstance(source, dict) else str(source or "")
    display_type = "Movies" if kind == "movies" else "TV Shows"
    root = _path_root_text(path_text)
    cached = _cached_signatures_under_root(c, root)
    db_counts = _source_database_counts(c, kind, path_text)

    enabled = bool(source.get("enabled", True)) if isinstance(source, dict) else True
    last_signature_update = _safe_max_scan_folder_update(c, path_text) or "Never"
    status_text, status_class = _source_health_status(source if isinstance(source, dict) else {"path": path_text, "enabled": enabled}, last_signature_update if last_signature_update != "Never" else "")

    current = {"folders": {}, "media_file_count": 0, "error": "", "sample_folders": []}
    unchanged = changed = new = 0
    changed_samples = []
    new_samples = []
    missing_cached = []

    if deep:
        current = _current_media_folder_signatures(path_text)
        current_signatures = current["folders"]
        for folder_path, signature in current_signatures.items():
            old = cached.get(folder_path, {}).get("signature")
            if old is None:
                new += 1
                if len(new_samples) < 8:
                    new_samples.append(folder_path)
            elif old == signature:
                unchanged += 1
            else:
                changed += 1
                if len(changed_samples) < 8:
                    changed_samples.append(folder_path)
        missing_cached = [folder_path for folder_path in cached.keys() if folder_path not in current_signatures]
        if enabled and not current.get("error") and current_signatures and (changed or new or missing_cached):
            status_text = "Changes waiting for scan"
            status_class = "warn"
        if enabled and current.get("error"):
            status_text = current["error"]
            status_class = "danger"
        if enabled and not current.get("error") and not current_signatures:
            status_text = "Reachable, no media files found"
            status_class = "warn"

    return {
        "kind": kind,
        "type_label": display_type,
        "index": index,
        "name": source.get("name", f"{display_type} folder {index}") if isinstance(source, dict) else f"{display_type} folder {index}",
        "path": path_text,
        "enabled": enabled,
        "last_scanned": source.get("last_scanned", "") if isinstance(source, dict) else "",
        "status_text": status_text,
        "status_class": status_class,
        "last_signature_update": last_signature_update,
        "cached_folder_count": len(cached),
        "current_folder_count": len(current["folders"]),
        "media_file_count": current["media_file_count"] if deep else None,
        "unchanged_folder_count": unchanged if deep else None,
        "changed_folder_count": changed if deep else None,
        "new_folder_count": new if deep else None,
        "missing_cached_folder_count": len(missing_cached) if deep else None,
        "changed_samples": changed_samples,
        "new_samples": new_samples,
        "missing_cached_samples": missing_cached[:8],
        "sample_folders": current["sample_folders"],
        "error": current.get("error", ""),
        "database": db_counts,
        "deep": deep,
        "check_url": f"/scan-wait/refresh-{kind}-{index}",
        "rebuild_url": f"/scan-wait/rebuild-{kind}-{index}",
        "manage_url": "/settings#movies-library" if kind == "movies" else "/settings#tv-library",
    }

def get_source_diagnostics_data(deep=False):
    c = conn()
    try:
        movie_sources = [_diagnose_source(c, "movies", i, source, deep=deep) for i, source in enumerate(get_movie_sources(), start=1)]
        tv_sources = [_diagnose_source(c, "tv", i, source, deep=deep) for i, source in enumerate(get_tv_sources(), start=1)]
    finally:
        c.close()
    all_sources = movie_sources + tv_sources
    return {
        "movie_sources": movie_sources,
        "tv_sources": tv_sources,
        "total_sources": len(all_sources),
        "healthy_sources": sum(1 for item in all_sources if item["status_class"] == "good"),
        "attention_sources": sum(1 for item in all_sources if item["status_class"] != "good"),
        "changed_sources": sum(1 for item in all_sources if (item.get("changed_folder_count") or 0) or (item.get("new_folder_count") or 0) or (item.get("missing_cached_folder_count") or 0)),
        "deep": deep,
        "scan_status": get_scan_status(),
        "scheduled": scheduled_scan_summary(),
    }

def get_missing_items(limit=200):
    c = conn()
    try:
        init_db(c)
        movies = [dict(row) for row in c.execute(
            """
            SELECT id, title, year, movie_path AS path, missing_since
            FROM movies
            WHERE COALESCE(missing,0)=1
            ORDER BY COALESCE(missing_since,''), title
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()]
        episodes = [dict(row) for row in c.execute(
            """
            SELECT e.id, COALESCE(s.title, 'Unknown show') AS show_title, e.season_number,
                   e.episode_number, e.title, e.file_path AS path, e.missing_since
            FROM tv_episodes e
            LEFT JOIN tv_shows s ON s.id=e.show_id
            WHERE COALESCE(e.missing,0)=1
            ORDER BY COALESCE(e.missing_since,''), show_title, e.season_number, e.episode_number
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()]
        return {"movies": movies, "episodes": episodes}
    finally:
        c.close()


def missing_counts():
    items = get_missing_items(limit=1)
    c = conn()
    try:
        return {
            "movies": int(c.execute("SELECT COUNT(*) FROM movies WHERE COALESCE(missing,0)=1").fetchone()[0]),
            "episodes": int(c.execute("SELECT COUNT(*) FROM tv_episodes WHERE COALESCE(missing,0)=1").fetchone()[0]),
        }
    except Exception:
        return {"movies": len(items.get("movies", [])), "episodes": len(items.get("episodes", []))}
    finally:
        c.close()


def mark_movie_restored_if_present(movie_id):
    c = conn()
    try:
        row = c.execute("SELECT movie_path FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not row:
            return "Movie item not found."
        if not Path(row["movie_path"] or "").exists():
            return "The movie file is still missing. It was not restored."
        c.execute("UPDATE movies SET missing=0, missing_since=NULL WHERE id=?", (movie_id,))
        c.commit()
        return "Movie restored because the file exists again."
    finally:
        c.close()


def mark_episode_restored_if_present(episode_id):
    c = conn()
    try:
        row = c.execute("SELECT file_path FROM tv_episodes WHERE id=?", (episode_id,)).fetchone()
        if not row:
            return "TV episode not found."
        if not Path(row["file_path"] or "").exists():
            return "The episode file is still missing. It was not restored."
        c.execute("UPDATE tv_episodes SET missing=0, missing_since=NULL WHERE id=?", (episode_id,))
        c.commit()
        return "TV episode restored because the file exists again."
    finally:
        c.close()

def clean_missing_items_older_than(days):
    """Permanently remove database rows that have been missing for at least N days.

    This never deletes files from disk. It only removes movie/episode rows already
    marked missing, and only after the configured safety age has passed.
    """
    try:
        days = int(days)
    except Exception:
        days = 7
    days = max(1, min(days, 365))
    cutoff = datetime.now() - timedelta(days=days)

    c = conn()
    removed_movies = 0
    removed_episodes = 0
    kept_movies = 0
    kept_episodes = 0
    try:
        movie_rows = c.execute(
            "SELECT id, missing_since FROM movies WHERE COALESCE(missing,0)=1"
        ).fetchall()
        episode_rows = c.execute(
            "SELECT id, missing_since FROM tv_episodes WHERE COALESCE(missing,0)=1"
        ).fetchall()

        def is_old_enough(value):
            if not value:
                return False
            text = str(value).strip()
            for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
                    return dt <= cutoff
                except Exception:
                    continue
            return False

        movie_ids = [row["id"] for row in movie_rows if is_old_enough(row["missing_since"])]
        episode_ids = [row["id"] for row in episode_rows if is_old_enough(row["missing_since"])]
        kept_movies = len(movie_rows) - len(movie_ids)
        kept_episodes = len(episode_rows) - len(episode_ids)

        if movie_ids:
            c.executemany("DELETE FROM movies WHERE id=? AND COALESCE(missing,0)=1", [(i,) for i in movie_ids])
            removed_movies = len(movie_ids)
        if episode_ids:
            c.executemany("DELETE FROM tv_episodes WHERE id=? AND COALESCE(missing,0)=1", [(i,) for i in episode_ids])
            removed_episodes = len(episode_ids)
        c.commit()
        return {
            "days": days,
            "removed_movies": removed_movies,
            "removed_episodes": removed_episodes,
            "kept_movies": kept_movies,
            "kept_episodes": kept_episodes,
        }
    finally:
        c.close()


def safe_table_count(c, table_name):
    """Return a table count without breaking Settings if a table is missing."""
    try:
        exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return 0
        row = c.execute(f"SELECT COUNT(*) AS n FROM {table_name}").fetchone()
        return int(row["n"] if row else 0)
    except Exception:
        return 0


def format_file_size(num_bytes):
    try:
        size = float(num_bytes or 0)
    except Exception:
        size = 0.0
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"


def backup_database(reason="manual"):
    """Copy library.db before destructive rebuild operations and return the backup path."""
    if not DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    safe_reason = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(reason or "manual"))
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = BACKUP_DIR / f"library_{safe_reason}_{stamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def _safe_backup_name(name):
    return Path(str(name or "")).name


def _safe_backup_path(name):
    safe_name = _safe_backup_name(name)
    if not safe_name:
        return None
    candidate = (BACKUP_DIR / safe_name).resolve()
    try:
        candidate.relative_to(BACKUP_DIR.resolve())
    except Exception:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    if candidate.suffix.lower() not in {".db", ".zip"}:
        return None
    return candidate


def create_full_backup(reason="manual"):
    """Create a restore-friendly backup containing library.db and config.json."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    safe_reason = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(reason or "manual"))
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = BACKUP_DIR / f"movie_library_{safe_reason}_{stamp}.zip"
    manifest = {
        "app": "Movie Library",
        "version": UI_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "contains": [],
    }
    with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        if DB_PATH.exists():
            z.write(DB_PATH, "library.db")
            manifest["contains"].append("library.db")
        if CONFIG_PATH.exists():
            z.write(CONFIG_PATH, "config.json")
            manifest["contains"].append("config.json")
        if (APP_DIR / "info.txt").exists():
            z.write(APP_DIR / "info.txt", "info.txt")
            manifest["contains"].append("info.txt")
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    return backup_path


def list_backup_files():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = []
    for path in sorted(BACKUP_DIR.glob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file() or path.suffix.lower() not in {".zip", ".db"}:
            continue
        backup_type = "Full app backup" if path.suffix.lower() == ".zip" else "Database-only backup"
        contains = "library.db + config.json" if path.suffix.lower() == ".zip" else "library.db only"
        backups.append({
            "name": path.name,
            "type": backup_type,
            "contains": contains,
            "size": format_file_size(path.stat().st_size),
            "created": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "can_restore_config": path.suffix.lower() == ".zip",
        })
    return backups


def restore_backup_file(name):
    """Restore a known backup from the app backups folder.

    ZIP backups restore both config.json and library.db when present. Legacy .db
    backups restore the database only. A fresh pre-restore backup is always made
    first so the admin can roll back if they selected the wrong file.
    """
    if get_scan_status().get("running"):
        return False, "A scan is currently running. Wait for it to finish before restoring a backup."
    source = _safe_backup_path(name)
    if not source:
        return False, "Backup file not found."

    safety_backup = create_full_backup("before_restore")
    restored_parts = []

    try:
        if source.suffix.lower() == ".zip":
            with zipfile.ZipFile(source, "r") as z:
                names = set(z.namelist())
                if "library.db" in names:
                    tmp_db = BACKUP_DIR / "_restore_library.tmp"
                    tmp_db.write_bytes(z.read("library.db"))
                    shutil.copy2(tmp_db, DB_PATH)
                    tmp_db.unlink(missing_ok=True)
                    restored_parts.append("database")
                if "config.json" in names:
                    tmp_cfg = BACKUP_DIR / "_restore_config.tmp"
                    tmp_cfg.write_bytes(z.read("config.json"))
                    # Validate JSON before replacing the live config.
                    json.loads(tmp_cfg.read_text(encoding="utf-8"))
                    shutil.copy2(tmp_cfg, CONFIG_PATH)
                    tmp_cfg.unlink(missing_ok=True)
                    restored_parts.append("configuration")
        else:
            shutil.copy2(source, DB_PATH)
            restored_parts.append("database")
    except Exception as exc:
        return False, f"Restore failed: {exc}. A safety backup was created first: {safety_backup.name}"

    global cfg
    cfg = load_config()
    cfg.setdefault("folder", "")
    cfg.setdefault("tv_folder", "")
    cfg.setdefault("web", {})
    cfg["web"].setdefault("username", "admin")
    cfg["web"].setdefault("password", "change-me-now")
    cfg["web"].setdefault("port", 8765)
    _normalise_viewers(cfg["web"])
    cfg.setdefault("ignored_alerts", [])
    save_config(cfg)

    restored_text = ", ".join(restored_parts) if restored_parts else "nothing"
    return True, f"Restored {restored_text} from {source.name}. Safety backup created first: {safety_backup.name}. Restart the web app if restored settings changed."


def delete_backup_file(name):
    source = _safe_backup_path(name)
    if not source:
        return False, "Backup file not found."
    try:
        source.unlink()
        return True, f"Deleted backup: {source.name}"
    except Exception as exc:
        return False, f"Could not delete backup: {exc}"


def get_about_info():
    c = conn()
    try:
        movie_count = safe_table_count(c, "movies")
        tv_show_count = safe_table_count(c, "tv_shows")
        episode_count = safe_table_count(c, "tv_episodes")
    finally:
        c.close()

    db_exists = DB_PATH.exists()
    last_backup = None
    if BACKUP_DIR.exists():
        backups = sorted(BACKUP_DIR.glob("library_*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
        if backups:
            last_backup = backups[0]

    return {
        "version": UI_VERSION,
        "runtime_paths": get_runtime_path_status(),
        "app_path": str(APP_DIR),
        "movie_root": str(MOVIE_ROOT_DIR),
        "web_root": str(WEB_ROOT_DIR),
        "db_path": str(DB_PATH),
        "db_size": format_file_size(DB_PATH.stat().st_size) if db_exists else "Missing",
        "backup_path": str(BACKUP_DIR),
        "future_app_support_path": str(APP_SUPPORT_DIR),
        "future_app_support_exists": APP_SUPPORT_DIR.exists(),
        "last_backup": str(last_backup.name) if last_backup else "No backups yet",
        "movie_count": movie_count,
        "tv_show_count": tv_show_count,
        "episode_count": episode_count,
        "port": cfg.get("web", {}).get("port", 8765),
    }


def get_runtime_path_status():
    summary = runtime_paths_summary(RUNTIME_PATHS)
    app_support_active = bool(summary["is_app_support"])
    return {
        **summary,
        "label": "App Support data mode" if app_support_active else "Legacy app-local data mode",
        "class": "good" if app_support_active else "warn",
        "message": (
            "Runtime data is being read from Application Support."
            if app_support_active
            else "Runtime data still uses the app folder. Application Support is available only for a later explicit switch."
        ),
    }


def _runtime_path_admin_item(label, path, kind):
    path = Path(path)
    return {
        "label": label,
        "kind": kind,
        "path": str(path),
        "exists": path.exists(),
        "status": "Present" if path.exists() else "Not found",
        "class": "good" if path.exists() else "warn",
    }


def get_runtime_paths_admin_data():
    """Return a read-only view of resolved runtime data paths."""
    runtime_status = get_runtime_path_status()
    path_items = [
        _runtime_path_admin_item("App folder", runtime_status["app_dir"], "dir"),
        _runtime_path_admin_item("Data folder", runtime_status["data_dir"], "dir"),
        _runtime_path_admin_item("Config file", runtime_status["config_path"], "file"),
        _runtime_path_admin_item("Database file", runtime_status["db_path"], "file"),
        _runtime_path_admin_item("Cache folder", runtime_status["cache_dir"], "dir"),
        _runtime_path_admin_item("Logs folder", runtime_status["logs_dir"], "dir"),
        _runtime_path_admin_item("Exports folder", runtime_status["exports_dir"], "dir"),
        _runtime_path_admin_item("Backups folder", runtime_status["backups_dir"], "dir"),
    ]
    return {
        "about": get_about_info(),
        "runtime": runtime_status,
        "mode": runtime_status["mode"],
        "app_support_active": runtime_status["is_app_support"],
        "safe_default_note": "legacy-app-local is the active safe default unless an explicit app-support marker/config flag is present.",
        "warning": "WARNING: app-support runtime path mode is active." if runtime_status["is_app_support"] else "",
        "path_items": path_items,
    }


def get_app_support_switch_preview_data():
    """Return a read-only plan for a later app-support runtime mode switch."""
    runtime_status = get_runtime_path_status()
    current_paths = [
        {"label": "Runtime mode", "path": runtime_status["mode"]},
        {"label": "Config", "path": runtime_status["config_path"]},
        {"label": "Database", "path": runtime_status["db_path"]},
        {"label": "Cache", "path": runtime_status["cache_dir"]},
        {"label": "Logs", "path": runtime_status["logs_dir"]},
    ]
    future_paths = [
        {"label": "Config", "path": str(APP_SUPPORT_CONFIG_PATH)},
        {"label": "Database", "path": str(APP_SUPPORT_DB_PATH)},
        {"label": "Cache", "path": str(APP_SUPPORT_CACHE_DIR)},
        {"label": "Logs", "path": str(APP_SUPPORT_LOGS_DIR)},
    ]
    marker_options = [
        {"label": "Marker file", "value": runtime_status["marker_path"]},
        {"label": "Marker content", "value": '{"mode": "app-support"}'},
        {"label": "Config flag option", "value": 'runtime_path_mode: "app-support"'},
    ]
    checklist = [
        "Confirm the Runtime Paths page still reports legacy-app-local before starting.",
        "Prepare and verify the Application Support folder skeleton.",
        "Create and verify migration safety backups.",
        "Stage config.json and library.db, then verify staged checksums.",
        "Run cutover readiness and confirm staged data still matches current live data.",
        "Stop the Flask server before any future live switch helper writes a marker/config flag.",
        "After any future switch, verify admin login, viewer login, library counts, scans, cache, logs, and remote HTTPS through unchanged Caddy.",
    ]
    return {
        "about": get_about_info(),
        "runtime": runtime_status,
        "current_paths": current_paths,
        "future_paths": future_paths,
        "marker_options": marker_options,
        "checklist": checklist,
        "warning": "This page is read-only. It does not switch runtime mode, create marker files, edit config.json, copy data, move data, delete data, migrate data, edit Caddy, or change scanner logic.",
        "app_support_active_warning": "WARNING: app-support mode is already active." if runtime_status["is_app_support"] else "",
    }


def _app_support_readiness_item(label, path, required=True):
    path = Path(path)
    exists = path.exists()
    return {
        "label": label,
        "path": str(path),
        "exists": exists,
        "required": required,
        "status": "Present" if exists else "Not found",
        "class": "good" if exists else ("bad" if required else "warn"),
    }


def get_app_support_readiness_data():
    """Return read-only readiness checks for a later App Support cutover."""
    runtime_status = get_runtime_path_status()
    still_legacy = runtime_status["mode"] == "legacy-app-local"
    checks = [
        _app_support_readiness_item("Legacy config", CONFIG_PATH),
        _app_support_readiness_item("Legacy database", DB_PATH),
        _app_support_readiness_item("Future App Support base folder", APP_SUPPORT_DIR),
        _app_support_readiness_item("Future cache folder", APP_SUPPORT_CACHE_DIR),
        _app_support_readiness_item("Future logs folder", APP_SUPPORT_LOGS_DIR),
        _app_support_readiness_item("Future exports folder", APP_SUPPORT_DIR / "exports"),
        _app_support_readiness_item("Future backups folder", APP_SUPPORT_DIR / "backups"),
        _app_support_readiness_item("Migration backup summary", MIGRATION_BACKUP_SUMMARY_PATH),
        _app_support_readiness_item("Migration stage verification summary", MIGRATION_STAGE_VERIFY_SUMMARY_PATH),
        _app_support_readiness_item("Cutover readiness summary", MIGRATION_CUTOVER_READINESS_SUMMARY_PATH),
    ]
    required_ready_count = sum(1 for item in checks if item["required"] and item["exists"])
    required_total = sum(1 for item in checks if item["required"])
    if still_legacy and required_ready_count == required_total:
        readiness_status = "Ready for manual cutover planning"
        readiness_class = "good"
    elif still_legacy and required_ready_count:
        readiness_status = "Partially ready"
        readiness_class = "warn"
    else:
        readiness_status = "Not ready"
        readiness_class = "bad"
    return {
        "about": get_about_info(),
        "runtime": runtime_status,
        "checks": checks,
        "legacy_safe": still_legacy,
        "legacy_safe_status": "Yes" if still_legacy else "No",
        "legacy_safe_class": "good" if still_legacy else "bad",
        "ready_count": required_ready_count,
        "total_count": required_total,
        "readiness_status": readiness_status,
        "readiness_class": readiness_class,
        "warning": "WARNING: app-support mode is already active." if runtime_status["is_app_support"] else "",
        "note": "This validator is read-only. It does not switch runtime mode, create marker files, edit config.json, copy data, move data, delete data, migrate data, edit Caddy, or change scanner logic.",
    }


def get_app_support_marker_preview_data():
    """Return a read-only preview of the future app-support opt-in marker."""
    runtime_status = get_runtime_path_status()
    marker_json = json.dumps({"runtime_mode": "app-support"}, indent=2)
    config_flag_text = 'runtime_path_mode: "app-support"'
    return {
        "about": get_about_info(),
        "runtime": runtime_status,
        "marker_path": runtime_status["marker_path"],
        "marker_json": marker_json,
        "config_flag_text": config_flag_text,
        "current_paths": [
            {"label": "Legacy config", "path": runtime_status["config_path"]},
            {"label": "Legacy database", "path": runtime_status["db_path"]},
        ],
        "future_paths": [
            {"label": "App Support config", "path": str(APP_SUPPORT_CONFIG_PATH)},
            {"label": "App Support database", "path": str(APP_SUPPORT_DB_PATH)},
        ],
        "warning": "Preview only. This page does not switch runtime mode, create a marker file, edit config.json, copy data, move data, delete data, migrate data, edit Caddy, or change scanner logic.",
        "app_support_active_warning": "WARNING: app-support mode is already active." if runtime_status["is_app_support"] else "",
    }


def _dir_stats(path):
    """Return a lightweight count/size summary for a directory."""
    root = Path(path)
    stats = {"exists": root.exists(), "path": str(root), "files": 0, "dirs": 0, "bytes": 0, "size": "0 B"}
    if not root.exists():
        return stats
    try:
        for item in root.rglob("*"):
            try:
                if item.is_dir():
                    stats["dirs"] += 1
                elif item.is_file():
                    stats["files"] += 1
                    stats["bytes"] += item.stat().st_size
            except Exception:
                continue
    except Exception:
        pass
    stats["size"] = format_file_size(stats["bytes"])
    return stats


def clear_artwork_cache():
    """Delete generated thumbnails only. Original posters/fanart are untouched."""
    if not ARTWORK_CACHE_DIR.exists():
        return {"files": 0, "bytes": 0, "size": "0 B"}
    before = _dir_stats(ARTWORK_CACHE_DIR)
    for item in sorted(ARTWORK_CACHE_DIR.glob("**/*"), key=lambda p: len(p.parts), reverse=True):
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
            elif item.is_dir():
                item.rmdir()
        except Exception:
            continue
    ARTWORK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return before


def _database_integrity_status():
    if not DB_PATH.exists():
        return {"status": "Missing", "class": "danger", "message": "library.db was not found."}
    try:
        raw = sqlite3.connect(DB_PATH)
        try:
            row = raw.execute("PRAGMA integrity_check").fetchone()
            result = row[0] if row else "No result"
        finally:
            raw.close()
        if str(result).lower() == "ok":
            return {"status": "OK", "class": "good", "message": "SQLite integrity check passed."}
        return {"status": "Warning", "class": "warn", "message": str(result)}
    except Exception as exc:
        return {"status": "Error", "class": "danger", "message": str(exc)}


def _source_health_rollup():
    movie_sources = get_movie_sources()
    tv_sources = get_tv_sources()
    rows = []
    c = conn()
    try:
        for kind, sources in (("Movies", movie_sources), ("TV Shows", tv_sources)):
            for index, source in enumerate(sources, start=1):
                last_signature_update = _safe_max_scan_folder_update(c, source.get("path"))
                status_text, status_class = _source_health_status(source, last_signature_update)
                rows.append({
                    "kind": kind,
                    "index": index,
                    "name": source.get("name") or f"{kind} source {index}",
                    "path": source.get("path") or "",
                    "enabled": bool(source.get("enabled", True)),
                    "status_text": status_text,
                    "status_class": status_class,
                    "last_signature_update": last_signature_update or "Never",
                    "last_scanned": source.get("last_scanned") or "Never",
                })
    finally:
        c.close()
    return rows


def get_system_health_data():
    c = conn()
    try:
        tables = []
        for table in ["movies", "tv_shows", "tv_episodes", "scan_folders", "scan_history", "activity_log"]:
            tables.append({"name": table, "count": safe_table_count(c, table)})
    finally:
        c.close()

    backups = list_backup_files()
    cache_stats = _dir_stats(ARTWORK_CACHE_DIR)
    source_rows = _source_health_rollup()
    unhealthy_sources = [row for row in source_rows if row["status_class"] != "good"]
    activity = get_activity_stats()
    recent_errors = get_activity_log(8, level="error")
    scheduled = scheduled_scan_summary()
    scan_status = get_scan_status()
    viewers = _normalise_viewers(cfg.get("web", {}))
    enabled_viewers = [viewer for viewer in viewers if viewer.get("enabled", True)]

    checks = [
        {"label": "Database integrity", **_database_integrity_status()},
        {"label": "Database file", "status": "Present" if DB_PATH.exists() else "Missing", "class": "good" if DB_PATH.exists() else "danger", "message": f"{DB_PATH} · {format_file_size(DB_PATH.stat().st_size) if DB_PATH.exists() else 'Missing'}"},
        {"label": "Config file", "status": "Present" if CONFIG_PATH.exists() else "Missing", "class": "good" if CONFIG_PATH.exists() else "danger", "message": str(CONFIG_PATH)},
        {"label": "Artwork cache", "status": "Ready" if ARTWORK_CACHE_DIR.exists() else "Not created yet", "class": "good" if ARTWORK_CACHE_DIR.exists() else "warn", "message": f"{cache_stats['files']} files · {cache_stats['size']}"},
        {"label": "Backups", "status": f"{len(backups)} available", "class": "good" if backups else "warn", "message": f"Stored in {BACKUP_DIR}"},
        {"label": "Scheduled scan", "status": "Enabled" if scheduled.get("enabled") else "Disabled", "class": "good" if scheduled.get("enabled") else "warn", "message": scheduled.get("next_text") or "Disabled"},
        {"label": "Caddy process", "status": "Running" if is_caddy_running() else "Not detected", "class": "good" if is_caddy_running() else "warn", "message": f"Shared Caddyfile: {CADDYFILE_PATH}"},
        {"label": "Source folders", "status": f"{len(source_rows) - len(unhealthy_sources)}/{len(source_rows)} healthy" if source_rows else "None configured", "class": "good" if source_rows and not unhealthy_sources else "warn", "message": "Use Source diagnostics for deeper checks."},
    ]

    return {
        "about": get_about_info(),
        "checks": checks,
        "tables": tables,
        "cache": cache_stats,
        "backups": backups[:8],
        "backup_count": len(backups),
        "source_rows": source_rows,
        "unhealthy_sources": unhealthy_sources,
        "activity": activity,
        "recent_errors": recent_errors,
        "scheduled": scheduled,
        "scan_status": scan_status,
        "viewer_count": len(viewers),
        "enabled_viewer_count": len(enabled_viewers),
        "admin_username": cfg.get("web", {}).get("username", "admin"),
        "config_path": str(CONFIG_PATH),
        "caddyfile_path": str(CADDYFILE_PATH),
        "artwork_cache_path": str(ARTWORK_CACHE_DIR),
    }


def _run_short_command(args, cwd=None, timeout=4):
    """Run a short diagnostic command and return a safe display dictionary."""
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout or result.stderr or "").strip()
        return {"ok": result.returncode == 0, "returncode": result.returncode, "output": output[-4000:]}
    except FileNotFoundError as exc:
        return {"ok": False, "returncode": None, "output": str(exc)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "output": f"Command timed out after {timeout} seconds."}
    except Exception as exc:
        return {"ok": False, "returncode": None, "output": str(exc)}


def _service_file_status(path):
    path = Path(path)
    return {
        "path": str(path),
        "exists": path.exists(),
        "class": "good" if path.exists() else "warn",
        "status": "Present" if path.exists() else "Missing",
    }


def get_mac_service_data():
    """Return macOS launchd/service readiness information without changing the system."""
    web_cfg = cfg.get("web", {}) if isinstance(cfg, dict) else {}
    port = int(web_cfg.get("port") or 8765)
    launch_label = "com.jay.movie-library"
    launch_agent_path = Path.home() / "Library" / "LaunchAgents" / f"{launch_label}.plist"
    runner_path = APP_DIR / "run_movie_library_server.command"
    installer_path = APP_DIR / "install_movie_library_launchd.command"
    uninstaller_path = APP_DIR / "uninstall_movie_library_launchd.command"
    launchctl_path = shutil.which("launchctl")
    platform_name = sys.platform

    launchd_status = {
        "label": launch_label,
        "available": bool(launchctl_path),
        "class": "warn",
        "status": "Not checked",
        "message": "launchctl is only available on macOS.",
        "output": "",
    }
    if launchctl_path:
        result = _run_short_command([launchctl_path, "list", launch_label], timeout=3)
        launchd_status["output"] = result.get("output") or ""
        if result.get("ok"):
            launchd_status.update({"class": "good", "status": "Loaded", "message": "Movie Library LaunchAgent is loaded for the current user."})
        else:
            launchd_status.update({"class": "warn", "status": "Not loaded", "message": "LaunchAgent is not currently loaded for the current user."})

    port_check = {"class": "warn", "status": "Not checked", "message": f"Port {port}"}
    if shutil.which("lsof"):
        result = _run_short_command(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], timeout=3)
        if result.get("ok") and result.get("output"):
            port_check = {"class": "good", "status": "Listening", "message": result.get("output")}
        else:
            port_check = {"class": "warn", "status": "Not detected", "message": f"No listener detected on port {port}. If you are viewing this page from the app, this can mean lsof is unavailable or restricted."}
    else:
        port_check = {"class": "warn", "status": "Tool unavailable", "message": "lsof command not found; port listener was not checked."}

    caddy_status = {"class": "good" if is_caddy_running() else "warn", "status": "Running" if is_caddy_running() else "Not detected", "message": f"Shared Caddyfile: {CADDYFILE_PATH}"}

    readiness = [
        {"label": "Movie Library runner", **_service_file_status(runner_path), "message": "Script used by launchd to start the Flask app."},
        {"label": "LaunchAgent installer", **_service_file_status(installer_path), "message": "Creates ~/Library/LaunchAgents/com.jay.movie-library.plist."},
        {"label": "LaunchAgent uninstaller", **_service_file_status(uninstaller_path), "message": "Stops and removes the Movie Library LaunchAgent."},
        {"label": "LaunchAgent plist", "path": str(launch_agent_path), "exists": launch_agent_path.exists(), "class": "good" if launch_agent_path.exists() else "warn", "status": "Installed" if launch_agent_path.exists() else "Not installed", "message": "Installed in the signed-in Mac user's LaunchAgents folder."},
        {"label": "launchd status", "path": launch_label, "exists": launchd_status["status"] == "Loaded", "class": launchd_status["class"], "status": launchd_status["status"], "message": launchd_status["message"]},
        {"label": f"Flask port {port}", "path": f"127.0.0.1:{port}", "exists": port_check["status"] == "Listening", "class": port_check["class"], "status": port_check["status"], "message": port_check["message"]},
        {"label": "Shared Caddy", "path": str(CADDYFILE_PATH), "exists": is_caddy_running(), "class": caddy_status["class"], "status": caddy_status["status"], "message": caddy_status["message"]},
    ]

    install_command = f'chmod +x {shlex.quote(str(installer_path))}\n{shlex.quote(str(installer_path))}'
    uninstall_command = f'chmod +x {shlex.quote(str(uninstaller_path))}\n{shlex.quote(str(uninstaller_path))}'
    manual_run_command = f'cd {shlex.quote(str(APP_DIR))}\nchmod +x ./run_movie_library_server.command\n./run_movie_library_server.command'
    caddy_validate_command = f'caddy validate --config {shlex.quote(str(CADDYFILE_PATH))}'
    caddy_reload_command = f'caddy reload --config {shlex.quote(str(CADDYFILE_PATH))}'

    return {
        "about": get_about_info(),
        "platform": platform_name,
        "is_macos": platform_name == "darwin",
        "port": port,
        "launch_label": launch_label,
        "launch_agent_path": str(launch_agent_path),
        "runner_path": str(runner_path),
        "installer_path": str(installer_path),
        "uninstaller_path": str(uninstaller_path),
        "readiness": readiness,
        "launchd_status": launchd_status,
        "port_check": port_check,
        "caddy_status": caddy_status,
        "commands": {
            "manual_run": manual_run_command,
            "install": install_command,
            "uninstall": uninstall_command,
            "caddy_validate": caddy_validate_command,
            "caddy_reload": caddy_reload_command,
        },
        "urls": {
            "local": f"http://127.0.0.1:{port}",
            "remote": "https://mjeromem75.dyndns.org",
        },
    }


def get_mac_app_readiness_data():
    """Return a no-change plan for the cleaner future macOS app layout."""
    web_cfg = cfg.get("web", {}) if isinstance(cfg, dict) else {}
    port = int(web_cfg.get("port") or 8765)
    movie_sources = get_movie_sources()
    tv_sources = get_tv_sources()
    runtime_status = get_runtime_path_status()
    current_items = [
        {"label": "Current app folder", "path": str(APP_DIR), "status": "In use now"},
        {"label": "Runtime data mode", "path": runtime_status["data_dir"], "status": runtime_status["label"]},
        {"label": "Current config", "path": str(CONFIG_PATH), "status": "Present" if CONFIG_PATH.exists() else "Missing"},
        {"label": "Current database", "path": str(DB_PATH), "status": format_file_size(DB_PATH.stat().st_size) if DB_PATH.exists() else "Missing"},
        {"label": "Current artwork cache", "path": str(ARTWORK_CACHE_DIR), "status": "Present" if ARTWORK_CACHE_DIR.exists() else "Not created yet"},
        {"label": "Shared Caddyfile", "path": str(CADDYFILE_PATH), "status": "Present" if CADDYFILE_PATH.exists() else "Missing"},
    ]
    target_items = [
        {"label": "Program", "path": "/Applications/Movie Library.app", "status": "Future DMG-installed app"},
        {"label": "Data folder", "path": str(APP_SUPPORT_DIR), "status": "Future database/config/cache/logs home"},
        {"label": "Config", "path": str(APP_SUPPORT_CONFIG_PATH), "status": "Future settings file"},
        {"label": "Database", "path": str(APP_SUPPORT_DB_PATH), "status": "Future library database"},
        {"label": "Cache", "path": str(APP_SUPPORT_CACHE_DIR), "status": "Future artwork/thumbnails cache"},
        {"label": "Logs", "path": str(APP_SUPPORT_LOGS_DIR), "status": "Future diagnostic logs"},
    ]
    checklist = [
        {"step": "1", "title": "Keep Caddy separate", "text": "Caddy remains the HTTPS front door for mjeromem75.dyndns.org and the Tutor hostname. The Movie Library app should not overwrite the shared Caddyfile."},
        {"step": "2", "title": "Move changing data outside the app bundle", "text": "The future .app should contain program code only. config.json, library.db, cache, and logs should live in Application Support."},
        {"step": "3", "title": "Add first-run setup", "text": "On a fresh install, the app should ask for Movies folders, TV folders, admin account, viewer account, and port 8765 before the first scan."},
        {"step": "4", "title": "Offer migration, not automatic overwrite", "text": "A later migration screen can copy the current database/config into Application Support, but it should ask first and make a backup."},
        {"step": "5", "title": "Package after live testing", "text": "Only after the server, scans, login, and control app are stable should we build a proper .app and then a .dmg."},
    ]
    return {
        "about": get_about_info(),
        "port": port,
        "local_url": f"http://127.0.0.1:{port}",
        "remote_url": "https://mjeromem75.dyndns.org",
        "current": current_items,
        "target": target_items,
        "movie_sources": movie_sources,
        "tv_sources": tv_sources,
        "checklist": checklist,
        "caddyfile_path": str(CADDYFILE_PATH),
        "app_support_dir": str(APP_SUPPORT_DIR),
        "app_support_exists": APP_SUPPORT_DIR.exists(),
        "runtime_paths": runtime_status,
    }



def get_first_run_setup_preview_data():
    """Return a read-only first-run setup preview for the future packaged Mac app."""
    web_cfg = cfg.get("web", {}) if isinstance(cfg, dict) else {}
    port = int(web_cfg.get("port") or 8765)
    runtime_status = get_runtime_path_status()
    movie_sources = get_movie_sources()
    tv_sources = get_tv_sources()
    admin_username = str(web_cfg.get("username") or "Jay1")
    viewer_accounts = get_viewer_accounts()
    default_viewer = next((v for v in viewer_accounts if str(v.get("username", "")).lower() == "laila"), None)
    setup_cards = [
        {"title": "Welcome", "status": "Preview only", "text": "Explain that Movie Library.app is the local library manager and Caddy remains the secure remote front door."},
        {"title": "Choose data location", "status": str(APP_SUPPORT_DIR), "text": "Use Application Support for config, database, cache, logs, and exports instead of storing changing data inside the app bundle."},
        {"title": "Choose media folders", "status": f"{len(movie_sources)} movie · {len(tv_sources)} TV currently configured", "text": "Future setup should let you pick Movies and TV Shows folders again, or import existing folder choices."},
        {"title": "Create admin account", "status": admin_username, "text": "Admin credentials control scans, settings, users, server restart, and diagnostics."},
        {"title": "Create viewer account", "status": "Laila configured" if default_viewer else "Laila not detected", "text": "Viewer accounts should only see Home, Movies, TV Shows, and Logout."},
        {"title": "Confirm server port", "status": str(port), "text": "Keep port 8765 so the existing Caddy reverse proxy can continue forwarding remote HTTPS traffic."},
        {"title": "First scan", "status": "Manual confirmation later", "text": "After setup, run Check All Libraries and build the first clean database in the chosen data folder."},
    ]
    current_config = [
        {"label": "Current app folder", "value": str(APP_DIR)},
        {"label": "Runtime data mode", "value": f"{runtime_status['label']} - {runtime_status['data_dir']}"},
        {"label": "Current config", "value": str(CONFIG_PATH)},
        {"label": "Current database", "value": str(DB_PATH)},
        {"label": "Future data folder", "value": str(APP_SUPPORT_DIR)},
        {"label": "Local URL", "value": f"http://127.0.0.1:{port}"},
        {"label": "Remote URL", "value": "https://mjeromem75.dyndns.org"},
        {"label": "Shared Caddyfile", "value": str(CADDYFILE_PATH)},
    ]
    next_build_plan = [
        "Add a real first-run state flag after the Mac-style data folder exists.",
        "Add safe copy/import buttons for config and database with timestamped backups.",
        "Add folder picker support in the packaged control app, not inside the browser alone.",
        "Only package a DMG after the first-run flow is tested on the live Mac.",
    ]
    return {
        "about": get_about_info(),
        "port": port,
        "admin_username": admin_username,
        "movie_sources": movie_sources,
        "tv_sources": tv_sources,
        "default_viewer_detected": bool(default_viewer),
        "setup_cards": setup_cards,
        "current_config": current_config,
        "next_build_plan": next_build_plan,
        "app_support_exists": APP_SUPPORT_DIR.exists(),
        "app_support_dir": str(APP_SUPPORT_DIR),
        "runtime_paths": runtime_status,
    }

def get_migration_safety_preview_data():
    """Return a read-only migration safety plan for the future packaged Mac app."""
    web_cfg = cfg.get("web", {}) if isinstance(cfg, dict) else {}
    port = int(web_cfg.get("port") or 8765)
    runtime_status = get_runtime_path_status()
    current_db_size = format_file_size(DB_PATH.stat().st_size) if DB_PATH.exists() else "Missing"
    current_config_size = format_file_size(CONFIG_PATH.stat().st_size) if CONFIG_PATH.exists() else "Missing"
    app_support_exists = APP_SUPPORT_DIR.exists()
    migration_steps = [
        {"title": "1. Pre-flight checks", "text": "Confirm Flask is stopped or idle, confirm port 8765 is the intended port, and confirm the target Application Support folder is available."},
        {"title": "2. Create Application Support structure", "text": "Create config, database, cache, logs, exports, and migration-backups folders outside the .app bundle."},
        {"title": "3. Back up before copying", "text": "Create timestamped backup copies of config.json and library.db before any import or migration is attempted."},
        {"title": "4. Copy/import data", "text": "Copy the existing config and database into Application Support only after the admin confirms the source and target paths."},
        {"title": "5. Switch the app path", "text": "Only a later build should actually point the packaged app at the new data folder. This build only previews the plan."},
        {"title": "6. Verify then scan", "text": "Open the library, verify counts, verify admin/viewer login, then run Check All Libraries if a fresh rebuild is wanted."},
    ]
    path_pairs = [
        {"label": "Runtime data mode", "source": runtime_status["mode"], "target": runtime_status["data_dir"], "status": runtime_status["label"]},
        {"label": "Current config source", "source": str(CONFIG_PATH), "target": str(APP_SUPPORT_CONFIG_PATH), "status": current_config_size},
        {"label": "Current database source", "source": str(DB_PATH), "target": str(APP_SUPPORT_DB_PATH), "status": current_db_size},
        {"label": "Current artwork cache", "source": str(ARTWORK_CACHE_DIR), "target": str(APP_SUPPORT_CACHE_DIR), "status": "Present" if ARTWORK_CACHE_DIR.exists() else "Not created yet"},
        {"label": "Future logs", "source": "Runtime generated", "target": str(APP_SUPPORT_LOGS_DIR), "status": "Future"},
    ]
    guardrails = [
        "Do not move files automatically from the browser page.",
        "Do not delete the current database during migration.",
        "Do not store changing database/cache files inside /Applications/Movie Library.app.",
        "Do not edit or replace the shared Caddyfile.",
        "Do not touch the Tutor app route or port 8123.",
        "Do not start a second Flask process if port 8765 is already responding.",
    ]
    return {
        "about": get_about_info(),
        "port": port,
        "local_url": f"http://127.0.0.1:{port}",
        "remote_url": "https://mjeromem75.dyndns.org",
        "app_support_dir": str(APP_SUPPORT_DIR),
        "app_support_exists": app_support_exists,
        "path_pairs": path_pairs,
        "migration_steps": migration_steps,
        "guardrails": guardrails,
        "caddyfile_path": str(CADDYFILE_PATH),
        "runtime_paths": runtime_status,
    }


def _migration_dry_run_item(label, source, target, kind="file", required=True, note=""):
    """Return read-only source/target details for the future migration dry run."""
    source = Path(source)
    target = Path(target)
    if kind == "dir":
        exists = source.exists() and source.is_dir()
    else:
        exists = source.exists() and source.is_file()
    size = "—"
    if exists:
        try:
            if kind == "dir":
                size = "folder"
            else:
                size = format_file_size(source.stat().st_size)
        except Exception:
            size = "present"
    return {
        "label": label,
        "source": str(source),
        "target": str(target),
        "kind": kind,
        "required": required,
        "exists": exists,
        "status": "Ready" if exists else ("Missing" if required else "Optional"),
        "class": "good" if exists else ("bad" if required else "warn"),
        "size": size,
        "note": note,
    }


def get_migration_dry_run_preview_data():
    """Return a read-only dry-run view for the future move to Application Support."""
    runtime_status = get_runtime_path_status()
    source_items = [
        _migration_dry_run_item("config.json", CONFIG_PATH, APP_SUPPORT_CONFIG_PATH, "file", True, "Would be copied later only after explicit confirmation."),
        _migration_dry_run_item("library.db", DB_PATH, APP_SUPPORT_DB_PATH, "file", True, "Would be copied later with a timestamped safety copy first."),
        _migration_dry_run_item("artwork cache", ARTWORK_CACHE_DIR, APP_SUPPORT_CACHE_DIR / "artwork", "dir", False, "Optional cache; it can also be rebuilt by scans if not migrated."),
        _migration_dry_run_item("logs", LOGS_DIR, APP_SUPPORT_LOGS_DIR, "dir", False, "Optional runtime logs; not required for the app to work."),
    ]
    target_items = [
        {"label": "Application Support root", **_app_support_path_status(APP_SUPPORT_DIR)},
        {"label": "cache/", **_app_support_path_status(APP_SUPPORT_CACHE_DIR)},
        {"label": "logs/", **_app_support_path_status(APP_SUPPORT_LOGS_DIR)},
        {"label": "exports/", **_app_support_path_status(APP_SUPPORT_DIR / "exports")},
        {"label": "migration-backups/", **_app_support_path_status(APP_SUPPORT_DIR / "migration-backups")},
    ]
    required_sources_ok = all(item["exists"] for item in source_items if item["required"])
    skeleton_ok = all(item["exists"] for item in target_items)
    overall_ok = required_sources_ok and skeleton_ok
    return {
        "about": get_about_info(),
        "future_app": "/Applications/Movie Library.app",
        "future_data": str(APP_SUPPORT_DIR),
        "helper_path": str(MIGRATION_DRY_RUN_HELPER_PATH),
        "summary_path": str(MIGRATION_DRY_RUN_SUMMARY_PATH),
        "log_path": str(MIGRATION_DRY_RUN_LOG_PATH),
        "runtime_paths": runtime_status,
        "overall_status": "Dry-run ready" if overall_ok else "Needs prep before migration",
        "overall_class": "good" if overall_ok else "warn",
        "source_items": source_items,
        "target_items": target_items,
        "helper_items": [
            {"label": "Migration dry-run helper", **_package_status(MIGRATION_DRY_RUN_HELPER_PATH)},
            {"label": "Dry-run summary", **_package_status(MIGRATION_DRY_RUN_SUMMARY_PATH)},
            {"label": "Dry-run log", **_package_status(MIGRATION_DRY_RUN_LOG_PATH)},
        ],
        "summary": _read_text_tail(MIGRATION_DRY_RUN_SUMMARY_PATH, 120),
        "log_tail": _read_text_tail(MIGRATION_DRY_RUN_LOG_PATH, 100),
        "planned_sequence": [
            "Run Prepare App Support if the target skeleton is missing.",
            "Run Verify App Support to confirm the target folder structure.",
            "Run this dry-run helper to preview exactly what would be copied later.",
            "Review source/target paths, sizes, and missing optional items.",
            "Only a later migration wizard should copy config.json/library.db, and only after explicit confirmation.",
        ],
        "safety": [
            "This page is read-only.",
            "The dry-run helper writes only a report/log.",
            "No database, config, artwork cache, media folder, Tutor file, or Caddyfile is copied, moved, edited, or deleted.",
        ],
    }


def _latest_migration_backup_items(limit=8):
    """Return recent timestamped migration backup folders, if any."""
    backup_root = APP_SUPPORT_DIR / "migration-backups"
    items = []
    try:
        if backup_root.exists() and backup_root.is_dir():
            candidates = [p for p in backup_root.iterdir() if p.is_dir() and p.name.startswith("backup-")]
            candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            for path in candidates[:limit]:
                config_copy = path / "config.json"
                db_copy = path / "library.db"
                checksum = path / "SHA256SUMS.txt"
                summary = path / "BACKUP SUMMARY.txt"
                items.append({
                    "name": path.name,
                    "path": str(path),
                    "modified": _safe_file_mtime(path),
                    "config": "Present" if config_copy.exists() else "Missing",
                    "database": format_file_size(db_copy.stat().st_size) if db_copy.exists() else "Missing",
                    "checksum": "Present" if checksum.exists() else "Missing",
                    "summary": "Present" if summary.exists() else "Missing",
                })
    except Exception:
        pass
    return items


def get_migration_backup_preview_data():
    """Return a read-only view of the safe migration backup helper."""
    backup_root = APP_SUPPORT_DIR / "migration-backups"
    checks = [
        {"label": "Current config.json", **_package_status(CONFIG_PATH)},
        {"label": "Current library.db", **_package_status(DB_PATH)},
        {"label": "Application Support root", **_app_support_path_status(APP_SUPPORT_DIR)},
        {"label": "Migration backups folder", **_app_support_path_status(backup_root)},
        {"label": "Backup helper", **_package_status(MIGRATION_BACKUP_HELPER_PATH)},
        {"label": "Backup verify helper", **_package_status(MIGRATION_BACKUP_VERIFY_HELPER_PATH)},
        {"label": "Latest backup summary", **_app_support_path_status(MIGRATION_BACKUP_SUMMARY_PATH, "file")},
        {"label": "Backup verify summary", **_app_support_path_status(MIGRATION_BACKUP_VERIFY_SUMMARY_PATH, "file")},
        {"label": "Backup log", **_package_status(MIGRATION_BACKUP_LOG_PATH)},
        {"label": "Backup verify log", **_package_status(MIGRATION_BACKUP_VERIFY_LOG_PATH)},
    ]
    ready = CONFIG_PATH.exists() and DB_PATH.exists() and APP_SUPPORT_DIR.exists() and backup_root.exists() and MIGRATION_BACKUP_HELPER_PATH.exists()
    return {
        "about": get_about_info(),
        "future_app": "/Applications/Movie Library.app",
        "future_data": str(APP_SUPPORT_DIR),
        "backup_root": str(backup_root),
        "helper_path": str(MIGRATION_BACKUP_HELPER_PATH),
        "summary_path": str(MIGRATION_BACKUP_SUMMARY_PATH),
        "log_path": str(MIGRATION_BACKUP_LOG_PATH),
        "overall_status": "Ready for safety backup" if ready else "Needs prep before safety backup",
        "overall_class": "good" if ready else "warn",
        "checks": checks,
        "latest_backups": _latest_migration_backup_items(),
        "summary": _read_text_tail(MIGRATION_BACKUP_SUMMARY_PATH, 120),
        "log_tail": _read_text_tail(MIGRATION_BACKUP_LOG_PATH, 100),
        "sequence": [
            "Prepare and verify the Application Support folder first.",
            "Run Migration Dry Run and review the paths that would later be migrated.",
            "Run Backup Movie Library Migration Data.command to create timestamped safety copies of config.json and library.db.",
            "Run Verify Movie Library Migration Backup.command to confirm the latest safety backup is readable and checksum-valid.",
            "Review the checksum file, backup summary, and verification summary before any future migration/copy step.",
            "Only a later migration wizard should switch the app to using Application Support as the live data folder.",
        ],
        "safety": [
            "This page is read-only.",
            "The helper copies config.json and library.db into a timestamped backup folder only.",
            "It does not delete, move, or edit the current database/config.",
            "It does not switch the live app to Application Support yet.",
            "It does not touch media folders, Tutor files, or the shared Caddyfile.",
        ],
    }



def get_migration_backup_verify_preview_data():
    """Return a read-only view of the migration backup verification helper."""
    backup_root = APP_SUPPORT_DIR / "migration-backups"
    latest = _latest_migration_backup_items(1)
    checks = [
        {"label": "Migration backups folder", **_app_support_path_status(backup_root)},
        {"label": "Backup helper", **_package_status(MIGRATION_BACKUP_HELPER_PATH)},
        {"label": "Backup verify helper", **_package_status(MIGRATION_BACKUP_VERIFY_HELPER_PATH)},
        {"label": "Latest backup summary", **_app_support_path_status(MIGRATION_BACKUP_SUMMARY_PATH, "file")},
        {"label": "Backup verify summary", **_app_support_path_status(MIGRATION_BACKUP_VERIFY_SUMMARY_PATH, "file")},
        {"label": "Backup log", **_package_status(MIGRATION_BACKUP_LOG_PATH)},
        {"label": "Backup verify log", **_package_status(MIGRATION_BACKUP_VERIFY_LOG_PATH)},
    ]
    ready = bool(latest) and backup_root.exists() and MIGRATION_BACKUP_VERIFY_HELPER_PATH.exists()
    return {
        "about": get_about_info(),
        "future_app": "/Applications/Movie Library.app",
        "future_data": str(APP_SUPPORT_DIR),
        "backup_root": str(backup_root),
        "helper_path": str(MIGRATION_BACKUP_VERIFY_HELPER_PATH),
        "summary_path": str(MIGRATION_BACKUP_VERIFY_SUMMARY_PATH),
        "log_path": str(MIGRATION_BACKUP_VERIFY_LOG_PATH),
        "overall_status": "Ready to verify latest backup" if ready else "Run migration backup before verification",
        "overall_class": "good" if ready else "warn",
        "checks": checks,
        "latest_backups": _latest_migration_backup_items(),
        "summary": _read_text_tail(MIGRATION_BACKUP_VERIFY_SUMMARY_PATH, 120),
        "log_tail": _read_text_tail(MIGRATION_BACKUP_VERIFY_LOG_PATH, 100),
        "sequence": [
            "Run the migration backup helper first so there is a timestamped safety copy to inspect.",
            "Run this verification helper to check the latest backup folder, required files, and SHA-256 checksum entries.",
            "Review the backup verification summary and log before any future migration/copy step.",
            "Only a later migration wizard should switch the app to using Application Support as the live data folder.",
        ],
        "safety": [
            "This page is read-only.",
            "The verification helper only reads the latest timestamped backup folder.",
            "It does not copy, restore, delete, move, edit, or switch the current database/config.",
            "It does not touch media folders, Tutor files, or the shared Caddyfile.",
        ],
    }


def get_migration_stage_preview_data():
    """Return a read-only view of the non-live migration staging helper."""
    staging_checks = [
        {"label": "Current config.json", **_package_status(CONFIG_PATH)},
        {"label": "Current library.db", **_package_status(DB_PATH)},
        {"label": "App Support root", **_app_support_path_status(APP_SUPPORT_DIR)},
        {"label": "Migration backups folder", **_app_support_path_status(APP_SUPPORT_DIR / "migration-backups")},
        {"label": "Latest backup verify summary", **_app_support_path_status(MIGRATION_BACKUP_VERIFY_SUMMARY_PATH, "file")},
        {"label": "Stage helper", **_package_status(MIGRATION_STAGE_HELPER_PATH)},
        {"label": "Stage verify helper", **_package_status(MIGRATION_STAGE_VERIFY_HELPER_PATH)},
        {"label": "Stage folder", **_app_support_path_status(MIGRATION_STAGE_CURRENT_DIR)},
        {"label": "Stage summary", **_app_support_path_status(MIGRATION_STAGE_SUMMARY_PATH, "file")},
        {"label": "Stage log", **_package_status(MIGRATION_STAGE_LOG_PATH)},
        {"label": "Stage verify summary", **_app_support_path_status(MIGRATION_STAGE_VERIFY_SUMMARY_PATH, "file")},
        {"label": "Stage verify log", **_package_status(MIGRATION_STAGE_VERIFY_LOG_PATH)},
    ]
    staged_files = [
        {"label": "Staged config", **_app_support_path_status(MIGRATION_STAGE_CURRENT_DIR / "config.json", "file")},
        {"label": "Staged database", **_app_support_path_status(MIGRATION_STAGE_CURRENT_DIR / "library.db", "file")},
        {"label": "Staged checksums", **_app_support_path_status(MIGRATION_STAGE_CURRENT_DIR / "SHA256SUMS.txt", "file")},
        {"label": "Staged detail summary", **_app_support_path_status(MIGRATION_STAGE_CURRENT_DIR / "STAGE SUMMARY.txt", "file")},
    ]
    required_ready = CONFIG_PATH.exists() and DB_PATH.exists() and APP_SUPPORT_DIR.exists() and MIGRATION_STAGE_HELPER_PATH.exists()
    already_staged = (MIGRATION_STAGE_CURRENT_DIR / "config.json").exists() and (MIGRATION_STAGE_CURRENT_DIR / "library.db").exists()
    return {
        "about": get_about_info(),
        "future_app": "/Applications/Movie Library.app",
        "future_data": str(APP_SUPPORT_DIR),
        "staging_dir": str(MIGRATION_STAGE_DIR),
        "current_stage_dir": str(MIGRATION_STAGE_CURRENT_DIR),
        "helper_path": str(MIGRATION_STAGE_HELPER_PATH),
        "summary_path": str(MIGRATION_STAGE_SUMMARY_PATH),
        "log_path": str(MIGRATION_STAGE_LOG_PATH),
        "overall_status": "Staged copy present" if already_staged else ("Ready to stage safe copy" if required_ready else "Prep/backup checks needed before staging"),
        "overall_class": "good" if already_staged or required_ready else "warn",
        "checks": staging_checks,
        "staged_files": staged_files,
        "summary": _read_text_tail(MIGRATION_STAGE_SUMMARY_PATH, 120),
        "log_tail": _read_text_tail(MIGRATION_STAGE_LOG_PATH, 120),
        "sequence": [
            "Prepare and verify the Application Support folder skeleton.",
            "Run the dry run so source/target paths are visible before copying anything.",
            "Create and verify a timestamped migration safety backup first.",
            "Run this staging helper to copy config.json and library.db into Application Support/migration-staging/current.",
            "Run Verify Stage to confirm staged files and checksums match before any future live-data switch is added.",
        ],
        "safety": [
            "This page is read-only.",
            "The staging helper copies config.json and library.db into a non-live staging folder only.",
            "It does not switch the running app to Application Support.",
            "It does not delete, move, overwrite, or replace the current config/database.",
            "It does not copy media folders, Tutor files, or the shared Caddyfile.",
        ],
    }

def get_dmg_packaging_preview_data():
    """Return a read-only packaging plan for a future DMG-installed Mac app."""
    web_cfg = cfg.get("web", {}) if isinstance(cfg, dict) else {}
    port = int(web_cfg.get("port") or 8765)
    runtime_status = get_runtime_path_status()
    package_items = [
        {"title": "Runtime path mode", "status": runtime_status["label"], "text": runtime_status["message"]},
        {"title": "Movie Library.app", "status": "Program bundle", "text": "The final app bundle should contain the control UI, Flask app code, templates/static assets, scanner code, and a packaged Python runtime or launcher."},
        {"title": "Application Support data", "status": str(APP_SUPPORT_DIR), "text": "Changing files stay outside the .app: config.json, library.db, cache, logs, exports, and migration backups."},
        {"title": "DMG installer", "status": "Future", "text": "The DMG should present a simple drag-to-Applications install flow and should not contain your live database or media files."},
        {"title": "Caddy remains separate", "status": str(CADDYFILE_PATH), "text": "The packaged app must not overwrite the shared Caddyfile because Tutor also depends on the same Caddy instance."},
    ]
    dmg_sequence = [
        "Freeze the web app layout and admin API used by the control app.",
        "Create a clean .app bundle that launches the server from the packaged program files.",
        "Point runtime data to ~/Library/Application Support/Movie Library/.",
        "Add first-run setup for media folders, admin account, viewer account, and port 8765.",
        "Test local URL, remote URL through Caddy, scans, admin login, viewer login, and restart actions.",
        "Use Build Test Movie Library DMG.command to validate the local DMG build mechanics without moving live data.",
        "Use Verify Test Movie Library DMG.command to confirm the generated test DMG can be inspected and includes only the expected test package files.",
        "Use Test DMG Status to review the generated image, checksum, manifest, and helper logs from the browser.",
        "Build the final DMG only after the packaged .app and test DMG behave correctly on the live Mac.",
    ]
    package_guardrails = [
        "Do not place library.db or cache inside /Applications/Movie Library.app.",
        "Do not package media folders into the DMG.",
        "Do not add playback, streaming, transcoding, or subtitle features.",
        "Do not edit the shared Caddyfile from the packaging process.",
        "Do not package generated build/ DMG output back into the app bundle.",
        "Do not start a second Flask process if port 8765 is already responding.",
    ]
    checks = [
        {"label": "Current app folder", "value": str(APP_DIR)},
        {"label": "Runtime data mode", "value": f"{runtime_status['mode']} -> {runtime_status['data_dir']}"},
        {"label": "Future app", "value": "/Applications/Movie Library.app"},
        {"label": "Future data folder", "value": str(APP_SUPPORT_DIR)},
        {"label": "Local URL", "value": f"http://127.0.0.1:{port}"},
        {"label": "Remote URL", "value": "https://mjeromem75.dyndns.org"},
        {"label": "Shared Caddyfile", "value": str(CADDYFILE_PATH)},
    ]
    return {
        "about": get_about_info(),
        "port": port,
        "package_items": package_items,
        "dmg_sequence": dmg_sequence,
        "package_guardrails": package_guardrails,
        "checks": checks,
        "runtime_paths": runtime_status,
    }


def get_migration_stage_verify_preview_data():
    """Return a read-only view of the staged migration verification helper."""
    staged_config = MIGRATION_STAGE_CURRENT_DIR / "config.json"
    staged_db = MIGRATION_STAGE_CURRENT_DIR / "library.db"
    staged_checksums = MIGRATION_STAGE_CURRENT_DIR / "SHA256SUMS.txt"
    staged_detail = MIGRATION_STAGE_CURRENT_DIR / "STAGE SUMMARY.txt"
    checks = [
        {"label": "Stage verify helper", **_package_status(MIGRATION_STAGE_VERIFY_HELPER_PATH)},
        {"label": "Stage folder", **_app_support_path_status(MIGRATION_STAGE_CURRENT_DIR)},
        {"label": "Staged config", **_app_support_path_status(staged_config, "file")},
        {"label": "Staged database", **_app_support_path_status(staged_db, "file")},
        {"label": "Staged checksums", **_app_support_path_status(staged_checksums, "file")},
        {"label": "Staged detail summary", **_app_support_path_status(staged_detail, "file")},
        {"label": "Stage verify summary", **_app_support_path_status(MIGRATION_STAGE_VERIFY_SUMMARY_PATH, "file")},
        {"label": "Stage verify log", **_package_status(MIGRATION_STAGE_VERIFY_LOG_PATH)},
    ]
    staged_ready = staged_config.exists() and staged_db.exists() and staged_checksums.exists() and staged_detail.exists()
    verified = MIGRATION_STAGE_VERIFY_SUMMARY_PATH.exists()
    return {
        "about": get_about_info(),
        "future_app": "/Applications/Movie Library.app",
        "future_data": str(APP_SUPPORT_DIR),
        "staging_dir": str(MIGRATION_STAGE_DIR),
        "current_stage_dir": str(MIGRATION_STAGE_CURRENT_DIR),
        "helper_path": str(MIGRATION_STAGE_VERIFY_HELPER_PATH),
        "summary_path": str(MIGRATION_STAGE_VERIFY_SUMMARY_PATH),
        "log_path": str(MIGRATION_STAGE_VERIFY_LOG_PATH),
        "overall_status": "Staged copy verification summary present" if verified else ("Ready to verify staged copy" if staged_ready and MIGRATION_STAGE_VERIFY_HELPER_PATH.exists() else "Run migration staging before verification"),
        "overall_class": "good" if verified or (staged_ready and MIGRATION_STAGE_VERIFY_HELPER_PATH.exists()) else "warn",
        "checks": checks,
        "summary": _read_text_tail(MIGRATION_STAGE_VERIFY_SUMMARY_PATH, 140),
        "log_tail": _read_text_tail(MIGRATION_STAGE_VERIFY_LOG_PATH, 120),
        "sequence": [
            "Prepare and verify the Application Support folder skeleton.",
            "Create and verify a timestamped migration backup.",
            "Run Migration Stage to create the non-live staged copy of config.json and library.db.",
            "Run this verification helper to confirm staged files and checksums match.",
            "Only a later migration wizard should switch the app to using Application Support as the live data folder.",
        ],
        "safety": [
            "This page is read-only.",
            "The helper only reads the non-live staged copy under Application Support/migration-staging/current.",
            "It does not switch the running app to Application Support.",
            "It does not copy, restore, move, delete, edit, or replace the current database/config.",
            "It does not touch media folders, Tutor files, or the shared Caddyfile.",
        ],
    }



def get_migration_cutover_readiness_preview_data():
    """Return a read-only view of the migration cutover readiness helper.

    This is intentionally not a switch/migration action. It checks whether the
    already-staged copy still matches the current live config/database and
    whether the previous prep/backup/stage verification summaries exist.
    """
    staged_config = MIGRATION_STAGE_CURRENT_DIR / "config.json"
    staged_db = MIGRATION_STAGE_CURRENT_DIR / "library.db"
    staged_checksums = MIGRATION_STAGE_CURRENT_DIR / "SHA256SUMS.txt"
    checks = [
        {"label": "Cutover readiness helper", **_package_status(MIGRATION_CUTOVER_READINESS_HELPER_PATH)},
        {"label": "Current config.json", **_package_status(CONFIG_PATH)},
        {"label": "Current library.db", **_package_status(DB_PATH)},
        {"label": "Application Support folder", **_app_support_path_status(APP_SUPPORT_DIR)},
        {"label": "App Support verify summary", **_app_support_path_status(APP_SUPPORT_VERIFY_SUMMARY_PATH, "file")},
        {"label": "Backup verify summary", **_app_support_path_status(MIGRATION_BACKUP_VERIFY_SUMMARY_PATH, "file")},
        {"label": "Stage verify summary", **_app_support_path_status(MIGRATION_STAGE_VERIFY_SUMMARY_PATH, "file")},
        {"label": "Staged config", **_app_support_path_status(staged_config, "file")},
        {"label": "Staged database", **_app_support_path_status(staged_db, "file")},
        {"label": "Staged checksums", **_app_support_path_status(staged_checksums, "file")},
        {"label": "Cutover readiness summary", **_app_support_path_status(MIGRATION_CUTOVER_READINESS_SUMMARY_PATH, "file")},
        {"label": "Cutover readiness log", **_package_status(MIGRATION_CUTOVER_READINESS_LOG_PATH)},
    ]
    ready_inputs = (
        CONFIG_PATH.exists()
        and DB_PATH.exists()
        and staged_config.exists()
        and staged_db.exists()
        and staged_checksums.exists()
        and APP_SUPPORT_VERIFY_SUMMARY_PATH.exists()
        and MIGRATION_BACKUP_VERIFY_SUMMARY_PATH.exists()
        and MIGRATION_STAGE_VERIFY_SUMMARY_PATH.exists()
        and MIGRATION_CUTOVER_READINESS_HELPER_PATH.exists()
    )
    checked = MIGRATION_CUTOVER_READINESS_SUMMARY_PATH.exists()
    return {
        "about": get_about_info(),
        "future_app": "/Applications/Movie Library.app",
        "future_data": str(APP_SUPPORT_DIR),
        "staging_dir": str(MIGRATION_STAGE_DIR),
        "current_stage_dir": str(MIGRATION_STAGE_CURRENT_DIR),
        "helper_path": str(MIGRATION_CUTOVER_READINESS_HELPER_PATH),
        "summary_path": str(MIGRATION_CUTOVER_READINESS_SUMMARY_PATH),
        "log_path": str(MIGRATION_CUTOVER_READINESS_LOG_PATH),
        "overall_status": "Cutover readiness report present" if checked else ("Ready to run cutover readiness check" if ready_inputs else "Complete prep/backup/stage verification first"),
        "overall_class": "good" if checked or ready_inputs else "warn",
        "checks": checks,
        "summary": _read_text_tail(MIGRATION_CUTOVER_READINESS_SUMMARY_PATH, 160),
        "log_tail": _read_text_tail(MIGRATION_CUTOVER_READINESS_LOG_PATH, 140),
        "sequence": [
            "Prepare and verify the Application Support folder skeleton.",
            "Create and verify a timestamped migration backup.",
            "Stage config.json and library.db into Application Support/migration-staging/current.",
            "Verify the staged copy and checksum metadata.",
            "Run this cutover readiness check to confirm the staged copy still matches the current live config/database.",
            "Only after a clean readiness report should a later explicit live-data switch helper/page be considered.",
        ],
        "safety": [
            "This page is read-only.",
            "The helper writes only a readiness report/log.",
            "It does not switch the app to Application Support.",
            "It does not copy, restore, move, delete, edit, or replace the current database/config.",
            "It does not touch media folders, Tutor files, or the shared Caddyfile.",
        ],
    }



def get_migration_cutover_plan_preview_data():
    """Return a read-only view of the generated migration cutover plan.

    This is deliberately still not a live-data switch. It gathers the cutover
    readiness results and produces a human-readable sequence for a future
    explicit switch helper/page.
    """
    staged_config = MIGRATION_STAGE_CURRENT_DIR / "config.json"
    staged_db = MIGRATION_STAGE_CURRENT_DIR / "library.db"
    checks = [
        {"label": "Cutover plan helper", **_package_status(MIGRATION_CUTOVER_PLAN_HELPER_PATH)},
        {"label": "Cutover readiness summary", **_app_support_path_status(MIGRATION_CUTOVER_READINESS_SUMMARY_PATH, "file")},
        {"label": "Staged config", **_app_support_path_status(staged_config, "file")},
        {"label": "Staged database", **_app_support_path_status(staged_db, "file")},
        {"label": "Future live config target", **_app_support_path_status(APP_SUPPORT_CONFIG_PATH, "file")},
        {"label": "Future live database target", **_app_support_path_status(APP_SUPPORT_DB_PATH, "file")},
        {"label": "Cutover plan summary", **_app_support_path_status(MIGRATION_CUTOVER_PLAN_SUMMARY_PATH, "file")},
        {"label": "Cutover plan log", **_package_status(MIGRATION_CUTOVER_PLAN_LOG_PATH)},
    ]
    ready_inputs = (
        MIGRATION_CUTOVER_PLAN_HELPER_PATH.exists()
        and MIGRATION_CUTOVER_READINESS_SUMMARY_PATH.exists()
        and staged_config.exists()
        and staged_db.exists()
    )
    planned = MIGRATION_CUTOVER_PLAN_SUMMARY_PATH.exists()
    return {
        "about": get_about_info(),
        "future_app": "/Applications/Movie Library.app",
        "future_data": str(APP_SUPPORT_DIR),
        "current_app_dir": str(APP_DIR),
        "current_config": str(CONFIG_PATH),
        "current_database": str(DB_PATH),
        "future_config": str(APP_SUPPORT_CONFIG_PATH),
        "future_database": str(APP_SUPPORT_DB_PATH),
        "staging_dir": str(MIGRATION_STAGE_CURRENT_DIR),
        "helper_path": str(MIGRATION_CUTOVER_PLAN_HELPER_PATH),
        "summary_path": str(MIGRATION_CUTOVER_PLAN_SUMMARY_PATH),
        "log_path": str(MIGRATION_CUTOVER_PLAN_LOG_PATH),
        "overall_status": "Cutover plan report present" if planned else ("Ready to generate cutover plan" if ready_inputs else "Run cutover readiness first"),
        "overall_class": "good" if planned or ready_inputs else "warn",
        "checks": checks,
        "summary": _read_text_tail(MIGRATION_CUTOVER_PLAN_SUMMARY_PATH, 180),
        "log_tail": _read_text_tail(MIGRATION_CUTOVER_PLAN_LOG_PATH, 140),
        "plan_steps": [
            "Stop the Movie Library Flask server cleanly before any future live-data switch.",
            "Confirm the latest cutover readiness report still says the staged config/database match the current live files.",
            "Copy the staged config.json and library.db into the Application Support live target paths only in a later explicit switch helper.",
            "Start Movie Library using a launcher that points at Application Support data paths, not the old developer app folder.",
            "Verify local login, viewer login, library counts, artwork cache behaviour, scans, and remote HTTPS through Caddy.",
            "Keep the old app-folder database/config untouched until the Application Support run has been tested successfully.",
        ],
        "safety": [
            "This page is read-only.",
            "The helper writes only a plan/report/log.",
            "It does not switch DB_PATH or CONFIG_PATH.",
            "It does not copy staged data into live Application Support targets.",
            "It does not edit launchers, delete backups, touch media folders, Tutor files, or the shared Caddyfile.",
        ],
    }



def get_package_manifest_preview_data():
    """Return a read-only manifest for what a future packaged Mac app should include/exclude."""
    runtime_status = get_runtime_path_status()
    include_items = [
        {"label": "runtime_paths.py", "reason": "Central runtime path resolver that keeps legacy app-local data active until an explicit App Support switch is enabled."},
        {"label": "web_app.py", "reason": "Flask routes, admin console, viewer UI, and admin API."},
        {"label": "scanner.py", "reason": "Movie/TV library scanner and database update logic."},
        {"label": "mac_control_app.py", "reason": "Native macOS control window used to manage the local server."},
        {"label": "requirements.txt", "reason": "Dependency reference for the development build and packaging process."},
        {"label": "templates/static assets", "reason": "If these are later split out of web_app.py, they belong inside the program bundle."},
        {"label": "icons and app metadata", "reason": "Future .app icon, Info.plist values, display name, and bundle metadata."},
        {"label": "safe launch helper", "reason": "Start the Flask server only when port 8765 is not already responding."},
        {"label": "Fix Movie Library Permissions.command", "reason": "Small Mac helper for chmod/xattr repair when Finder blocks a copied or unzipped launcher."},
        {"label": "Build Test Movie Library DMG.command", "reason": "Mac-only helper for creating a safe test DMG from the current test .app bundle, before final packaging."},
        {"label": "Verify Test Movie Library DMG.command", "reason": "Mac-only helper for checking the generated test DMG can be inspected and contains the expected lightweight packaging files."},
        {"label": "Prepare Movie Library App Support.command", "reason": "Mac-only helper that creates the future Application Support data-folder skeleton without moving live data."},
        {"label": "Verify Movie Library App Support.command", "reason": "Mac-only helper that checks the Application Support skeleton and writes a verification summary/log without migrating live data."},
        {"label": "Dry Run Movie Library Migration.command", "reason": "Mac-only helper that previews the future config/database/cache migration without copying or moving live data."},
        {"label": "Backup Movie Library Migration Data.command", "reason": "Mac-only helper that creates timestamped safety copies of config.json and library.db before any future migration switch."},
        {"label": "Stage Movie Library Migration Data.command", "reason": "Mac-only helper that creates a non-live staged copy of config.json and library.db in Application Support for inspection."},
        {"label": "Verify Movie Library Migration Stage.command", "reason": "Mac-only helper that verifies the staged config/database files and checksum metadata before any future live-data switch."},
        {"label": "Check Movie Library Migration Cutover.command", "reason": "Mac-only helper that checks whether staged config/database still match the current live files before a later explicit switch helper is considered."},
        {"label": "Plan Movie Library Migration Cutover.command", "reason": "Mac-only helper that writes the future live-data switch plan without changing runtime paths."},
        {"label": "Movie Library.app test bundle", "reason": "Double-clickable test app wrapper that uses the same safe launch behaviour before the final DMG packaging stage."},
    ]
    external_items = [
        {"label": "config.json", "target": str(APP_SUPPORT_CONFIG_PATH), "reason": "User settings must survive app updates."},
        {"label": "library.db", "target": str(APP_SUPPORT_DB_PATH), "reason": "The scanned library database changes over time and must not live inside the app bundle."},
        {"label": "cache/", "target": str(APP_SUPPORT_CACHE_DIR), "reason": "Artwork thumbnails and cached images should be rebuildable user data."},
        {"label": "logs/", "target": str(APP_SUPPORT_LOGS_DIR), "reason": "Runtime logs should be writable outside /Applications."},
        {"label": "exports/", "target": str(APP_SUPPORT_DIR / "exports"), "reason": "CSV/report exports belong with user data."},
        {"label": "migration-backups/", "target": str(APP_SUPPORT_DIR / "migration-backups"), "reason": "Safety copies created during future migration."},
    ]
    exclude_items = [
        "Your media folders and video files.",
        "The live library.db file inside the DMG or .app bundle.",
        "Artwork cache copied into the .app bundle.",
        "The shared /Volumes/D/Webserver/Caddyfile.",
        "Tutor app files or Tutor Python environment.",
        "Old Windows-only build scripts in the final Mac package.",
    ]
    readiness_checks = [
        {"label": "Program source folder", "value": str(APP_DIR), "exists": APP_DIR.exists()},
        {"label": "Runtime data mode", "value": f"{runtime_status['label']} - {runtime_status['data_dir']}", "exists": True},
        {"label": "Current config", "value": str(CONFIG_PATH), "exists": CONFIG_PATH.exists()},
        {"label": "Current database", "value": str(DB_PATH), "exists": DB_PATH.exists()},
        {"label": "Future data folder", "value": str(APP_SUPPORT_DIR), "exists": APP_SUPPORT_DIR.exists()},
        {"label": "Test Movie Library.app bundle", "value": str(APP_DIR / "Movie Library.app"), "exists": (APP_DIR / "Movie Library.app").exists()},
        {"label": "Permission helper", "value": str(APP_DIR / "Fix Movie Library Permissions.command"), "exists": (APP_DIR / "Fix Movie Library Permissions.command").exists()},
        {"label": "Test DMG build helper", "value": str(APP_DIR / "Build Test Movie Library DMG.command"), "exists": (APP_DIR / "Build Test Movie Library DMG.command").exists()},
        {"label": "Test DMG verify helper", "value": str(APP_DIR / "Verify Test Movie Library DMG.command"), "exists": (APP_DIR / "Verify Test Movie Library DMG.command").exists()},
        {"label": "App Support prep helper", "value": str(APP_SUPPORT_PREP_HELPER_PATH), "exists": APP_SUPPORT_PREP_HELPER_PATH.exists()},
        {"label": "App Support verify helper", "value": str(APP_SUPPORT_VERIFY_HELPER_PATH), "exists": APP_SUPPORT_VERIFY_HELPER_PATH.exists()},
        {"label": "Migration dry-run helper", "value": str(MIGRATION_DRY_RUN_HELPER_PATH), "exists": MIGRATION_DRY_RUN_HELPER_PATH.exists()},
        {"label": "Migration backup helper", "value": str(MIGRATION_BACKUP_HELPER_PATH), "exists": MIGRATION_BACKUP_HELPER_PATH.exists()},
        {"label": "Migration stage helper", "value": str(MIGRATION_STAGE_HELPER_PATH), "exists": MIGRATION_STAGE_HELPER_PATH.exists()},
        {"label": "Migration stage verify helper", "value": str(MIGRATION_STAGE_VERIFY_HELPER_PATH), "exists": MIGRATION_STAGE_VERIFY_HELPER_PATH.exists()},
        {"label": "Migration backup verify helper", "value": str(MIGRATION_BACKUP_VERIFY_HELPER_PATH), "exists": MIGRATION_BACKUP_VERIFY_HELPER_PATH.exists()},
        {"label": "Migration cutover plan helper", "value": str(MIGRATION_CUTOVER_PLAN_HELPER_PATH), "exists": MIGRATION_CUTOVER_PLAN_HELPER_PATH.exists()},
        {"label": "Shared Caddyfile", "value": str(CADDYFILE_PATH), "exists": CADDYFILE_PATH.exists()},
    ]
    return {
        "about": get_about_info(),
        "future_app": "/Applications/Movie Library.app",
        "future_data": str(APP_SUPPORT_DIR),
        "include_items": include_items,
        "external_items": external_items,
        "exclude_items": exclude_items,
        "readiness_checks": readiness_checks,
        "runtime_paths": runtime_status,
    }


def _app_support_path_status(path, expected="dir"):
    path = Path(path)
    if expected == "file":
        exists = path.exists() and path.is_file()
    else:
        exists = path.exists() and path.is_dir()
    return {
        "path": str(path),
        "exists": exists,
        "status": "Present" if exists else "Not created yet",
        "class": "good" if exists else "warn",
        "modified": _safe_file_mtime(path) if path.exists() else "—",
    }


def get_app_support_prep_preview_data():
    """Return read-only readiness for the future Application Support data folder."""
    folders = [
        {"label": "Application Support root", **_app_support_path_status(APP_SUPPORT_DIR)},
        {"label": "Cache", **_app_support_path_status(APP_SUPPORT_CACHE_DIR)},
        {"label": "Artwork cache", **_app_support_path_status(APP_SUPPORT_CACHE_DIR / "artwork")},
        {"label": "Poster cache", **_app_support_path_status(APP_SUPPORT_CACHE_DIR / "posters")},
        {"label": "Fanart cache", **_app_support_path_status(APP_SUPPORT_CACHE_DIR / "fanart")},
        {"label": "Thumbnail cache", **_app_support_path_status(APP_SUPPORT_CACHE_DIR / "thumbnails")},
        {"label": "Logs", **_app_support_path_status(APP_SUPPORT_LOGS_DIR)},
        {"label": "Exports", **_app_support_path_status(APP_SUPPORT_DIR / "exports")},
        {"label": "Migration backups", **_app_support_path_status(APP_SUPPORT_DIR / "migration-backups")},
    ]
    files = [
        {"label": "Data folder README", **_app_support_path_status(APP_SUPPORT_README_PATH, "file")},
        {"label": "Prep summary", **_app_support_path_status(APP_SUPPORT_PREP_SUMMARY_PATH, "file")},
        {"label": "Prep helper", **_package_status(APP_SUPPORT_PREP_HELPER_PATH)},
        {"label": "Verify helper", **_package_status(APP_SUPPORT_VERIFY_HELPER_PATH)},
        {"label": "Verify summary", **_app_support_path_status(APP_SUPPORT_VERIFY_SUMMARY_PATH, "file")},
    ]
    return {
        "about": get_about_info(),
        "future_app": "/Applications/Movie Library.app",
        "future_data": str(APP_SUPPORT_DIR),
        "helper_path": str(APP_SUPPORT_PREP_HELPER_PATH),
        "current_config": str(CONFIG_PATH),
        "current_database": str(DB_PATH),
        "current_cache": str(ARTWORK_CACHE_DIR),
        "future_config": str(APP_SUPPORT_CONFIG_PATH),
        "future_database": str(APP_SUPPORT_DB_PATH),
        "future_cache": str(APP_SUPPORT_CACHE_DIR),
        "folders": folders,
        "files": files,
        "prep_log_tail": _read_text_tail(APP_SUPPORT_PREP_LOG_PATH, 80),
        "verify_log_tail": _read_text_tail(APP_SUPPORT_VERIFY_LOG_PATH, 80),
        "prep_summary": _read_text_tail(APP_SUPPORT_PREP_SUMMARY_PATH, 80),
        "verify_summary": _read_text_tail(APP_SUPPORT_VERIFY_SUMMARY_PATH, 80),
        "readme": _read_text_tail(APP_SUPPORT_README_PATH, 80),
        "next_steps": [
            "Run Prepare Movie Library App Support.command on the Mac when you are ready to create the future data-folder skeleton.",
            "Confirm this page shows the Application Support folders and README as present.",
            "Run Verify Movie Library App Support.command to write a verification summary and log.",
            "Open App Support Status to confirm the required skeleton is ready for later migration planning.",
            "Do not copy library.db/config.json automatically yet; that belongs to the later migration wizard step.",
            "Keep Caddy separate and shared with Tutor; this prep helper does not touch the Caddyfile.",
        ],
        "safety": [
            "This page is read-only and does not create folders by itself.",
            "The helper creates empty Application Support folders and README/summary notes only.",
            "It does not move or copy database, config, artwork cache, media folders, Tutor files, or the shared Caddyfile.",
        ],
    }


def _app_support_verify_item(label, path, expected="dir", required=True, message=""):
    """Return one read-only Application Support verification item."""
    status = _app_support_path_status(path, expected)
    status.update({
        "label": label,
        "expected": expected,
        "required": required,
        "message": message,
    })
    if not required and not status["exists"]:
        status["status"] = "Optional / not created yet"
        status["class"] = "warn"
    return status


def get_app_support_status_preview_data():
    """Return read-only verification status for the future Application Support layout."""
    runtime_status = get_runtime_path_status()
    required_items = [
        _app_support_verify_item("Application Support root", APP_SUPPORT_DIR, "dir", True, "Main future data folder."),
        _app_support_verify_item("cache/", APP_SUPPORT_CACHE_DIR, "dir", True, "Root cache folder."),
        _app_support_verify_item("cache/artwork/", APP_SUPPORT_CACHE_DIR / "artwork", "dir", True, "Future artwork cache."),
        _app_support_verify_item("cache/posters/", APP_SUPPORT_CACHE_DIR / "posters", "dir", True, "Future poster cache."),
        _app_support_verify_item("cache/fanart/", APP_SUPPORT_CACHE_DIR / "fanart", "dir", True, "Future fanart cache."),
        _app_support_verify_item("cache/thumbnails/", APP_SUPPORT_CACHE_DIR / "thumbnails", "dir", True, "Future generated thumbnails."),
        _app_support_verify_item("logs/", APP_SUPPORT_LOGS_DIR, "dir", True, "Future runtime logs."),
        _app_support_verify_item("exports/", APP_SUPPORT_DIR / "exports", "dir", True, "Future report exports."),
        _app_support_verify_item("migration-backups/", APP_SUPPORT_DIR / "migration-backups", "dir", True, "Future safety copies during migration."),
        _app_support_verify_item("Data-folder README", APP_SUPPORT_README_PATH, "file", True, "Notes explaining what belongs in the future data folder."),
    ]
    optional_items = [
        _app_support_verify_item("Prep summary", APP_SUPPORT_PREP_SUMMARY_PATH, "file", False, "Created when the prep helper has run."),
        _app_support_verify_item("Verify summary", APP_SUPPORT_VERIFY_SUMMARY_PATH, "file", False, "Created when the verify helper has run."),
        _app_support_verify_item("Future config.json", APP_SUPPORT_CONFIG_PATH, "file", False, "Should appear only after the future migration/first-run step."),
        _app_support_verify_item("Future library.db", APP_SUPPORT_DB_PATH, "file", False, "Should appear only after the future migration/first-run step."),
    ]
    helper_items = [
        {"label": "Prep helper", **_package_status(APP_SUPPORT_PREP_HELPER_PATH)},
        {"label": "Verify helper", **_package_status(APP_SUPPORT_VERIFY_HELPER_PATH)},
        {"label": "Prep log", **_package_status(APP_SUPPORT_PREP_LOG_PATH)},
        {"label": "Verify log", **_package_status(APP_SUPPORT_VERIFY_LOG_PATH)},
    ]
    required_ok = all(item["exists"] for item in required_items)
    current_live_items = [
        {"label": "Runtime data mode", "path": runtime_status["data_dir"], "exists": True, "size": runtime_status["mode"], "message": runtime_status["message"]},
        {"label": "Current config", "path": str(CONFIG_PATH), "exists": CONFIG_PATH.exists(), "size": format_file_size(CONFIG_PATH.stat().st_size) if CONFIG_PATH.exists() else "—", "message": "Resolved through the runtime path helper."},
        {"label": "Current database", "path": str(DB_PATH), "exists": DB_PATH.exists(), "size": format_file_size(DB_PATH.stat().st_size) if DB_PATH.exists() else "—", "message": "Resolved through the runtime path helper."},
        {"label": "Current artwork cache", "path": str(ARTWORK_CACHE_DIR), "exists": ARTWORK_CACHE_DIR.exists(), "size": "folder" if ARTWORK_CACHE_DIR.exists() else "—", "message": "Resolved through the runtime path helper."},
    ]
    return {
        "about": get_about_info(),
        "future_app": "/Applications/Movie Library.app",
        "future_data": str(APP_SUPPORT_DIR),
        "required_ok": required_ok,
        "overall_status": "Ready for later migration planning" if required_ok else "Run App Support prep first",
        "overall_class": "good" if required_ok else "warn",
        "prep_helper": str(APP_SUPPORT_PREP_HELPER_PATH),
        "verify_helper": str(APP_SUPPORT_VERIFY_HELPER_PATH),
        "required_items": required_items,
        "optional_items": optional_items,
        "helper_items": helper_items,
        "current_live_items": current_live_items,
        "runtime_paths": runtime_status,
        "prep_summary": _read_text_tail(APP_SUPPORT_PREP_SUMMARY_PATH, 80),
        "verify_summary": _read_text_tail(APP_SUPPORT_VERIFY_SUMMARY_PATH, 100),
        "prep_log_tail": _read_text_tail(APP_SUPPORT_PREP_LOG_PATH, 60),
        "verify_log_tail": _read_text_tail(APP_SUPPORT_VERIFY_LOG_PATH, 80),
        "readme": _read_text_tail(APP_SUPPORT_README_PATH, 80),
        "next_steps": [
            "Run Prepare Movie Library App Support.command first if the required folders are missing.",
            "Run Verify Movie Library App Support.command to write a verification summary and log.",
            "Use this page to confirm the required skeleton exists before the later migration wizard.",
            "Keep config.json, library.db, and artwork cache in the current app folder until the migration step is intentionally added.",
            "Do not move or edit the shared Caddyfile; Caddy remains the HTTPS front door for Movie Library and Tutor.",
        ],
        "safety": [
            "This status page is read-only.",
            "The verify helper checks folders and writes notes only.",
            "No database, config, artwork cache, media folders, Tutor files, or Caddyfile are copied, moved, edited, or deleted.",
        ],
    }


def _package_status(path, expected="file"):
    """Return a small read-only package readiness status for a path."""
    path = Path(path)
    if expected == "dir":
        exists = path.exists() and path.is_dir()
    elif expected == "missing":
        exists = not path.exists()
    else:
        exists = path.exists() and path.is_file()
    return {
        "path": str(path),
        "exists": exists,
        "status": "OK" if exists else "Needs attention",
        "class": "good" if exists else "warn",
    }


def _launcher_permission_status(path, label):
    """Return read-only executable permission status for Mac launch helpers."""
    path = Path(path)
    exists = path.exists() and path.is_file()
    executable = exists and os.access(path, os.X_OK)
    if not exists:
        status = "Missing"
        css_class = "warn"
    elif executable:
        status = "Executable"
        css_class = "good"
    else:
        status = "Exists, but may need chmod +x"
        css_class = "warn"
    return {
        "label": label,
        "path": str(path),
        "exists": exists,
        "executable": executable,
        "status": status,
        "class": css_class,
    }


def _safe_file_mtime(path):
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "Not available"


def _read_text_tail(path, max_lines=80):
    path = Path(path)
    if not path.exists() or not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception as exc:
        return f"Could not read {path}: {exc}"


def _sha256_file(path):
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _test_dmg_checksum_status():
    if not TEST_DMG_PATH.exists():
        return {"status": "Not checked", "class": "warn", "message": "Test DMG has not been built yet."}
    if not TEST_DMG_CHECKSUM_PATH.exists():
        return {"status": "Missing checksum", "class": "warn", "message": "Build the test DMG again to create a SHA-256 checksum file."}
    try:
        first = TEST_DMG_CHECKSUM_PATH.read_text(encoding="utf-8", errors="replace").strip().split()[0]
        actual = _sha256_file(TEST_DMG_PATH)
        if first and actual.lower() == first.lower():
            return {"status": "Checksum matches", "class": "good", "message": actual}
        return {"status": "Checksum mismatch", "class": "warn", "message": f"Expected {first}; got {actual}"}
    except Exception as exc:
        return {"status": "Checksum error", "class": "warn", "message": str(exc)}


def get_test_dmg_status_preview_data():
    """Return read-only status for local test DMG artifacts and logs."""
    artifacts = [
        {"label": "Build folder", "path": str(DMG_BUILD_DIR), "exists": DMG_BUILD_DIR.exists(), "type": "folder"},
        {"label": "Test DMG", "path": str(TEST_DMG_PATH), "exists": TEST_DMG_PATH.exists(), "type": "file"},
        {"label": "SHA-256 checksum", "path": str(TEST_DMG_CHECKSUM_PATH), "exists": TEST_DMG_CHECKSUM_PATH.exists(), "type": "file"},
        {"label": "Contents manifest", "path": str(TEST_DMG_MANIFEST_PATH), "exists": TEST_DMG_MANIFEST_PATH.exists(), "type": "file"},
        {"label": "Install notes", "path": str(TEST_DMG_INSTALL_NOTES_PATH), "exists": TEST_DMG_INSTALL_NOTES_PATH.exists(), "type": "file"},
        {"label": "Build summary", "path": str(TEST_DMG_SUMMARY_PATH), "exists": TEST_DMG_SUMMARY_PATH.exists(), "type": "file"},
        {"label": "Verify summary", "path": str(TEST_DMG_VERIFY_SUMMARY_PATH), "exists": TEST_DMG_VERIFY_SUMMARY_PATH.exists(), "type": "file"},
        {"label": "Build log", "path": str(DMG_BUILD_LOG_PATH), "exists": DMG_BUILD_LOG_PATH.exists(), "type": "file"},
        {"label": "Verify log", "path": str(DMG_VERIFY_LOG_PATH), "exists": DMG_VERIFY_LOG_PATH.exists(), "type": "file"},
    ]
    for item in artifacts:
        p = Path(item["path"])
        item["status"] = "Found" if item["exists"] else "Not created yet"
        item["class"] = "good" if item["exists"] else "warn"
        item["size"] = format_file_size(p.stat().st_size) if item["exists"] and p.is_file() else "—"
        item["modified"] = _safe_file_mtime(p) if item["exists"] else "—"
    manifest_text = _read_text_tail(TEST_DMG_MANIFEST_PATH, 200)
    finder_layout = [
        {"label": "Movie Library.app", "status": "Expected", "message": "The test app bundle is included for Finder layout testing."},
        {"label": "Applications alias", "status": "Expected", "message": "The test DMG now includes the normal drag-to-Applications alias used by Mac installers."},
        {"label": "README - Test DMG.txt", "status": "Expected", "message": "Explains that this image is still a packaging/layout test."},
        {"label": "INSTALL NOTES.txt", "status": "Expected", "message": "Clarifies that the current test app still expects server files beside it until final packaging."},
    ]
    if manifest_text:
        for item in finder_layout:
            token = item["label"]
            item["found_in_manifest"] = token in manifest_text
            item["class"] = "good" if item["found_in_manifest"] else "warn"
            item["status"] = "Found in last manifest" if item["found_in_manifest"] else "Not found in last manifest"
    else:
        for item in finder_layout:
            item["found_in_manifest"] = False
            item["class"] = "warn"
            item["status"] = "Waiting for test DMG build"
    dmg_info = {
        "path": str(TEST_DMG_PATH),
        "exists": TEST_DMG_PATH.exists(),
        "size": format_file_size(TEST_DMG_PATH.stat().st_size) if TEST_DMG_PATH.exists() else "Not built yet",
        "modified": _safe_file_mtime(TEST_DMG_PATH) if TEST_DMG_PATH.exists() else "—",
        "checksum": _test_dmg_checksum_status(),
    }
    next_steps = [
        "Run Fix Movie Library Permissions.command if macOS blocks any command/app launcher.",
        "Run Build Test Movie Library DMG.command on the Mac to create the local packaging test image with a Finder-style Applications alias.",
        "Run Verify Test Movie Library DMG.command to mount and inspect the generated image read-only, including the Finder layout files.",
        "Open this page again to confirm the DMG, checksum, manifest, build log, and verify log were created.",
        "Only move toward a final DMG after the test Movie Library.app launches correctly from Finder on the live Mac.",
    ]
    return {
        "about": get_about_info(),
        "dmg_info": dmg_info,
        "artifacts": artifacts,
        "build_log_tail": _read_text_tail(DMG_BUILD_LOG_PATH, 80),
        "verify_log_tail": _read_text_tail(DMG_VERIFY_LOG_PATH, 80),
        "build_summary": _read_text_tail(TEST_DMG_SUMMARY_PATH, 80),
        "verify_summary": _read_text_tail(TEST_DMG_VERIFY_SUMMARY_PATH, 80),
        "manifest_tail": _read_text_tail(TEST_DMG_MANIFEST_PATH, 120),
        "install_notes": _read_text_tail(TEST_DMG_INSTALL_NOTES_PATH, 120),
        "finder_layout": finder_layout,
        "next_steps": next_steps,
        "safety": [
            "This page is read-only and does not build, mount, copy, move, or delete anything.",
            "The test DMG does not include library.db, config.json, artwork cache, logs, media folders, Tutor files, or the shared Caddyfile.",
            "The Applications alias is for Finder layout testing only; this is still not the final installable app.",
            "Caddy remains separate and continues to handle remote HTTPS for mjeromem75.dyndns.org.",
        ],
    }


def get_package_preflight_preview_data():
    """Return read-only checks for the future DMG/.app packaging stage."""
    web_cfg = cfg.get("web", {}) if isinstance(cfg, dict) else {}
    port = int(web_cfg.get("port") or 8765)
    runtime_status = get_runtime_path_status()
    required_program_files = [
        {"label": "Flask server", "filename": "web_app.py", **_package_status(APP_DIR / "web_app.py")},
        {"label": "Scanner", "filename": "scanner.py", **_package_status(APP_DIR / "scanner.py")},
        {"label": "Runtime path helper", "filename": "runtime_paths.py", **_package_status(APP_DIR / "runtime_paths.py")},
        {"label": "macOS control app", "filename": "mac_control_app.py", **_package_status(APP_DIR / "mac_control_app.py")},
        {"label": "Python requirements", "filename": "requirements.txt", **_package_status(APP_DIR / "requirements.txt")},
        {"label": "Server runner", "filename": "run_movie_library_server.command", **_package_status(APP_DIR / "run_movie_library_server.command")},
        {"label": "Control app launcher", "filename": "Movie Library Control.command", **_package_status(APP_DIR / "Movie Library Control.command")},
        {"label": "Safe library launcher", "filename": "Movie Library.command", **_package_status(APP_DIR / "Movie Library.command")},
        {"label": "Permission helper", "filename": "Fix Movie Library Permissions.command", **_package_status(APP_DIR / "Fix Movie Library Permissions.command")},
        {"label": "Test DMG build helper", "filename": "Build Test Movie Library DMG.command", **_package_status(APP_DIR / "Build Test Movie Library DMG.command")},
        {"label": "Test DMG verify helper", "filename": "Verify Test Movie Library DMG.command", **_package_status(APP_DIR / "Verify Test Movie Library DMG.command")},
        {"label": "App Support prep helper", "filename": "Prepare Movie Library App Support.command", **_package_status(APP_SUPPORT_PREP_HELPER_PATH)},
        {"label": "App Support verify helper", "filename": "Verify Movie Library App Support.command", **_package_status(APP_SUPPORT_VERIFY_HELPER_PATH)},
        {"label": "Migration dry-run helper", "filename": "Dry Run Movie Library Migration.command", **_package_status(MIGRATION_DRY_RUN_HELPER_PATH)},
        {"label": "Migration backup helper", "filename": "Backup Movie Library Migration Data.command", **_package_status(MIGRATION_BACKUP_HELPER_PATH)},
        {"label": "Migration backup verify helper", "filename": "Verify Movie Library Migration Backup.command", **_package_status(MIGRATION_BACKUP_VERIFY_HELPER_PATH)},
        {"label": "Migration staging helper", "filename": "Stage Movie Library Migration Data.command", **_package_status(MIGRATION_STAGE_HELPER_PATH)},
        {"label": "Migration staging verify helper", "filename": "Verify Movie Library Migration Stage.command", **_package_status(MIGRATION_STAGE_VERIFY_HELPER_PATH)},
        {"label": "Migration cutover readiness helper", "filename": "Check Movie Library Migration Cutover.command", **_package_status(MIGRATION_CUTOVER_READINESS_HELPER_PATH)},
        {"label": "Migration cutover plan helper", "filename": "Plan Movie Library Migration Cutover.command", **_package_status(MIGRATION_CUTOVER_PLAN_HELPER_PATH)},
        {"label": "Test app bundle", "filename": "Movie Library.app", **_package_status(APP_DIR / "Movie Library.app", "dir")},
        {"label": "Test app bundle launcher", "filename": "Movie Library.app/Contents/MacOS/MovieLibrary", **_package_status(APP_DIR / "Movie Library.app" / "Contents" / "MacOS" / "MovieLibrary")},
        {"label": "Test app bundle plist", "filename": "Movie Library.app/Contents/Info.plist", **_package_status(APP_DIR / "Movie Library.app" / "Contents" / "Info.plist")},
        {"label": "Control app bundle launcher", "filename": "Movie Library Control.app/Contents/MacOS/MovieLibraryControl", **_package_status(APP_DIR / "Movie Library Control.app" / "Contents" / "MacOS" / "MovieLibraryControl")},
        {"label": "Control app bundle plist", "filename": "Movie Library Control.app/Contents/Info.plist", **_package_status(APP_DIR / "Movie Library Control.app" / "Contents" / "Info.plist")},
    ]
    permission_checks = [
        _launcher_permission_status(APP_DIR / "run_movie_library_server.command", "Server runner command"),
        _launcher_permission_status(APP_DIR / "Movie Library.command", "Safe library launcher command"),
        _launcher_permission_status(APP_DIR / "Movie Library Control.command", "Control launcher command"),
        _launcher_permission_status(APP_DIR / "Fix Movie Library Permissions.command", "Permission repair helper"),
        _launcher_permission_status(APP_DIR / "Build Test Movie Library DMG.command", "Test DMG build helper"),
        _launcher_permission_status(APP_DIR / "Verify Test Movie Library DMG.command", "Test DMG verify helper"),
        _launcher_permission_status(APP_SUPPORT_PREP_HELPER_PATH, "App Support prep helper"),
        _launcher_permission_status(APP_SUPPORT_VERIFY_HELPER_PATH, "App Support verify helper"),
        _launcher_permission_status(MIGRATION_DRY_RUN_HELPER_PATH, "Migration dry-run helper"),
        _launcher_permission_status(MIGRATION_BACKUP_HELPER_PATH, "Migration backup helper"),
        _launcher_permission_status(MIGRATION_BACKUP_VERIFY_HELPER_PATH, "Migration backup verify helper"),
        _launcher_permission_status(MIGRATION_STAGE_HELPER_PATH, "Migration staging helper"),
        _launcher_permission_status(MIGRATION_STAGE_VERIFY_HELPER_PATH, "Migration staging verify helper"),
        _launcher_permission_status(MIGRATION_CUTOVER_READINESS_HELPER_PATH, "Migration cutover readiness helper"),
        _launcher_permission_status(MIGRATION_CUTOVER_PLAN_HELPER_PATH, "Migration cutover plan helper"),
        _launcher_permission_status(APP_DIR / "Movie Library.app" / "Contents" / "MacOS" / "MovieLibrary", "Movie Library.app executable"),
        _launcher_permission_status(APP_DIR / "Movie Library Control.app" / "Contents" / "MacOS" / "MovieLibraryControl", "Movie Library Control.app executable"),
    ]
    data_files = [
        {"label": "Runtime data mode", "current": runtime_status["mode"], "future": runtime_status["data_dir"], "exists": True, "message": runtime_status["message"]},
        {"label": "Current config", "current": str(CONFIG_PATH), "future": str(APP_SUPPORT_CONFIG_PATH), "exists": CONFIG_PATH.exists(), "message": "Should be copied/migrated to Application Support, not bundled as a fixed app resource."},
        {"label": "Current database", "current": str(DB_PATH), "future": str(APP_SUPPORT_DB_PATH), "exists": DB_PATH.exists(), "message": "Should remain user data so app updates never overwrite the scanned library."},
        {"label": "Current artwork cache", "current": str(ARTWORK_CACHE_DIR), "future": str(APP_SUPPORT_CACHE_DIR), "exists": ARTWORK_CACHE_DIR.exists(), "message": "Should be rebuildable cache outside the .app bundle."},
        {"label": "Future logs folder", "current": str(LOGS_DIR), "future": str(APP_SUPPORT_LOGS_DIR), "exists": APP_SUPPORT_LOGS_DIR.exists(), "message": "Runtime logs should be writable outside /Applications."},
        {"label": "Future data-folder skeleton", "current": str(APP_SUPPORT_PREP_HELPER_PATH), "future": str(APP_SUPPORT_DIR), "exists": APP_SUPPORT_DIR.exists(), "message": "Can be created with Prepare Movie Library App Support.command before the later migration wizard."},
        {"label": "App Support verification summary", "current": str(APP_SUPPORT_VERIFY_HELPER_PATH), "future": str(APP_SUPPORT_VERIFY_SUMMARY_PATH), "exists": APP_SUPPORT_VERIFY_SUMMARY_PATH.exists(), "message": "Created by Verify Movie Library App Support.command after folder prep."},
        {"label": "Migration dry-run summary", "current": str(MIGRATION_DRY_RUN_HELPER_PATH), "future": str(MIGRATION_DRY_RUN_SUMMARY_PATH), "exists": MIGRATION_DRY_RUN_SUMMARY_PATH.exists(), "message": "Created by the dry-run helper; it previews what would be copied but does not migrate anything."},
        {"label": "Migration backup summary", "current": str(MIGRATION_BACKUP_HELPER_PATH), "future": str(MIGRATION_BACKUP_SUMMARY_PATH), "exists": MIGRATION_BACKUP_SUMMARY_PATH.exists(), "message": "Created by the backup helper; it records timestamped safety copies before any future migration switch."},
        {"label": "Migration backup verify summary", "current": str(MIGRATION_BACKUP_VERIFY_HELPER_PATH), "future": str(MIGRATION_BACKUP_VERIFY_SUMMARY_PATH), "exists": MIGRATION_BACKUP_VERIFY_SUMMARY_PATH.exists(), "message": "Created by the backup verification helper; it confirms the latest timestamped safety backup can be read and checksum-verified."},
        {"label": "Migration staging summary", "current": str(MIGRATION_STAGE_HELPER_PATH), "future": str(MIGRATION_STAGE_SUMMARY_PATH), "exists": MIGRATION_STAGE_SUMMARY_PATH.exists(), "message": "Created by the staging helper; it confirms non-live copies of config.json and library.db exist in Application Support/migration-staging/current."},
        {"label": "Migration staging verify summary", "current": str(MIGRATION_STAGE_VERIFY_HELPER_PATH), "future": str(MIGRATION_STAGE_VERIFY_SUMMARY_PATH), "exists": MIGRATION_STAGE_VERIFY_SUMMARY_PATH.exists(), "message": "Created by the stage verification helper; it confirms the staged config/database files and checksums can be read before any live-data switch."},
        {"label": "Migration cutover readiness summary", "current": str(MIGRATION_CUTOVER_READINESS_HELPER_PATH), "future": str(MIGRATION_CUTOVER_READINESS_SUMMARY_PATH), "exists": MIGRATION_CUTOVER_READINESS_SUMMARY_PATH.exists(), "message": "Created by the cutover readiness helper; it confirms the staged config/database still match the current live config/database before any future switch helper is built."},
        {"label": "Migration cutover plan summary", "current": str(MIGRATION_CUTOVER_PLAN_HELPER_PATH), "future": str(MIGRATION_CUTOVER_PLAN_SUMMARY_PATH), "exists": MIGRATION_CUTOVER_PLAN_SUMMARY_PATH.exists(), "message": "Created by the cutover plan helper; it writes the switch sequence only and still does not change live data paths."},
    ]
    exclude_checks = [
        {"label": "library.db in .app bundle", "status": "Exclude from final app bundle", "reason": "Database is user data and changes during scans."},
        {"label": "config.json as fixed bundle config", "status": "Exclude from final app bundle", "reason": "Config should be created/migrated in Application Support."},
        {"label": ".venv", "status": "Exclude from DMG", "reason": "The packaged app should use its bundled runtime approach, not the developer virtual environment."},
        {"label": "restart.log / runtime logs", "status": "Exclude from app bundle", "reason": "Logs belong in Application Support/logs."},
        {"label": "build/ test DMG output", "status": "Exclude from app bundle", "reason": "DMG artifacts are generated packaging output and should not be nested inside the final app."},
        {"label": "Caddyfile", "status": "Do not bundle or edit", "reason": "Caddy remains shared infrastructure for Movie Library and Tutor."},
    ]
    package_steps = [
        "Confirm the current web app and scanner still compile.",
        "Confirm the admin API routes used by the control app are available.",
        "Keep program files separate from config, database, cache, logs, and media folders.",
        "Use the test Movie Library.app bundle first; do not create the DMG until the .app opens the library, starts the server safely, reads config.json correctly, and still works through the control app.",
        "Confirm the launchers read the configured web.port rather than assuming 8765, with 8765 only as the fallback default.",
        "Confirm Mac launcher scripts and app-bundle executables have execute permission; if needed, run Fix Movie Library Permissions.command after unzipping.",
        "Use Build Test Movie Library DMG.command only as a packaging test; it creates a local build folder and DMG without moving database, cache, Caddy, Tutor, or media files.",
        "Use Verify Test Movie Library DMG.command after building the test DMG to confirm the image can be mounted/read and contains the expected lightweight packaging files.",
        "Only after the .app and test DMG are tested, create a final DMG that contains the packaged app and a simple install/readme note.",
    ]
    api_routes = [
        "/api/admin/status",
        "/api/admin/scan-status",
        "/api/admin/server-status",
        "/api/admin/recent-events",
        "/api/admin/run-scan",
        "/api/admin/login",
        "/api/admin/package-manifest-preview",
        "/api/admin/package-preflight-preview",
        "/api/admin/runtime-paths",
        "/api/admin/app-support-switch-preview",
        "/api/admin/app-support-readiness",
        "/api/admin/app-support-marker-preview",
        "/api/admin/test-dmg-status-preview",
        "/api/admin/app-support-prep-preview",
        "/api/admin/app-support-status-preview",
        "/api/admin/migration-dry-run-preview",
        "/api/admin/migration-backup-preview",
        "/api/admin/migration-backup-verify-preview",
        "/api/admin/migration-stage-preview",
        "/api/admin/migration-stage-verify-preview",
        "/api/admin/migration-cutover-readiness-preview",
        "/api/admin/migration-cutover-plan-preview",
    ]
    return {
        "about": get_about_info(),
        "port": port,
        "future_app": "/Applications/Movie Library.app",
        "future_data": str(APP_SUPPORT_DIR),
        "runtime_paths": runtime_status,
        "required_program_files": required_program_files,
        "permission_checks": permission_checks,
        "data_files": data_files,
        "exclude_checks": exclude_checks,
        "package_steps": package_steps,
        "api_routes": api_routes,
        "caddy": {"path": str(CADDYFILE_PATH), "exists": CADDYFILE_PATH.exists(), "running": is_caddy_running()},
        "urls": {"local": f"http://127.0.0.1:{port}", "remote": "https://mjeromem75.dyndns.org"},
    }

def _tail_text_file(path, max_lines=80, max_chars=20000):
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "exists": False, "text": "", "line_count": 0, "size": "0 B"}
    try:
        text = path.read_text(errors="replace")[-max_chars:]
        lines = text.splitlines()[-max_lines:]
        return {"path": str(path), "exists": True, "text": "\n".join(lines), "line_count": len(lines), "size": format_bytes(path.stat().st_size)}
    except Exception as exc:
        return {"path": str(path), "exists": True, "text": f"Could not read log: {exc}", "line_count": 0, "size": "unknown"}


def get_server_control_data():
    """Return admin server status and safe control information."""
    service = get_mac_service_data()
    scan_status = get_scan_status()
    activity = get_activity_stats()
    recent_server_events = get_activity_log(12, area="server")
    recent_errors = get_activity_log(8, level="error")
    restart_log = _tail_text_file(APP_DIR / "restart.log")
    caddy_log = _tail_text_file(WEB_ROOT_DIR / "caddy.log")
    runner_log = _tail_text_file(APP_DIR / "movie_library_server.log")
    web_cfg = cfg.get("web", {}) if isinstance(cfg, dict) else {}
    port = int(web_cfg.get("port") or 8765)
    return {
        "about": get_about_info(),
        "service": service,
        "scan_status": scan_status,
        "activity": activity,
        "recent_server_events": recent_server_events,
        "recent_errors": recent_errors,
        "logs": {"restart": restart_log, "runner": runner_log, "caddy": caddy_log},
        "urls": service.get("urls", {}),
        "port": port,
        "pid": os.getpid(),
        "python": sys.executable,
        "app_dir": str(APP_DIR),
        "db_path": str(DB_PATH),
        "config_path": str(CONFIG_PATH),
        "caddyfile_path": str(CADDYFILE_PATH),
        "is_caddy_running": is_caddy_running(),
    }

def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    init_db(c)
    ensure_maintenance_tables(c)
    return c


def require_login():
    return session.get("logged_in") is True

def current_role():
    return session.get("role") or "viewer"

def require_admin():
    return require_login() and current_role() == "admin"

def is_admin():
    return require_admin()

@app.context_processor
def inject_role_helpers():
    return {"is_admin": is_admin, "current_role": current_role}

ADMIN_ONLY_PREFIXES = (
    "/admin", "/dashboard", "/source-diagnostics", "/activity-log", "/system-health", "/mac-service", "/server-control", "/maintenance", "/settings", "/reports", "/scan-history", "/missing-items", "/backups", "/about", "/restart-web", "/restart-web-and-caddy",
    "/refresh", "/rebuild", "/tv-refresh", "/scan-wait", "/scan-status", "/export", "/api/admin"
)

@app.before_request
def enforce_viewer_permissions():
    if request.endpoint in {"login", "logout", "static"}:
        return None
    if not require_login():
        return None
    path = request.path or "/"
    if current_role() != "admin" and any(path == prefix or path.startswith(prefix + "/") for prefix in ADMIN_ONLY_PREFIXES):
        if path.startswith("/api/"):
            return jsonify({"ok": False, "error": "admin_required"}), 403
        flash("Your account can only view Movies and TV Shows.")
        return redirect(url_for("index"))
    return None


RESTART_EXIT_CODE = 75

RESTART_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Restarting</title>
<style>body{font-family:Arial,sans-serif;background:#111827;color:#f9fafb;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px}.card{max-width:560px;width:100%;background:#1f2937;border:1px solid #374151;border-radius:16px;padding:28px;box-shadow:0 10px 30px rgba(0,0,0,.35)}h1{margin:0 0 12px;font-size:26px}p{margin:0 0 12px;line-height:1.5;color:#d1d5db}.muted{font-size:14px;color:#9ca3af}
.settings-hero{margin-bottom:14px;padding:18px;display:flex;justify-content:space-between;gap:14px;align-items:flex-start}.settings-hero h1{margin-bottom:4px}.settings-hero-actions{display:flex;gap:10px;flex-wrap:wrap}.settings-card h2,.scan-card h2{display:flex;align-items:center;gap:8px}.about-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:12px}.mini-stat{padding:12px;border-radius:16px;border:1px solid rgba(148,163,184,.14);background:rgba(2,6,23,.28)}.mini-stat b{display:block;font-size:22px;letter-spacing:-.04em}.mini-stat span{display:block;color:var(--muted);font-size:12px;margin-top:3px}.kv{display:grid;gap:8px;margin-top:12px}.kv div{display:grid;grid-template-columns:140px minmax(0,1fr);gap:10px;padding:9px 10px;border-radius:14px;background:rgba(2,6,23,.24);border:1px solid rgba(148,163,184,.10)}.kv b{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}.safe-note{margin-top:10px;padding:10px 12px;border-radius:14px;border:1px solid rgba(52,211,153,.22);background:rgba(16,185,129,.10);color:#d1fae5}@media(max-width:950px){.settings-hero{display:block;padding:14px}.settings-hero-actions{margin-top:10px}.about-grid{grid-template-columns:1fr}.kv div{grid-template-columns:1fr;gap:4px}}
</style>
<script>
(function(){
  const target = {{ target_url|tojson }};
  const status = {{ status_message|tojson }};
  function tryReturn(){
    fetch(target, {cache:'no-store', credentials:'same-origin'})
      .then(function(resp){
        if(resp.ok || resp.redirected){ window.location.href = target; return; }
        throw new Error('not ready');
      })
      .catch(function(){ setTimeout(tryReturn, 1500); });
  }
  window.addEventListener('load', function(){
    const el = document.getElementById('status');
    if (el) el.textContent = status;
    setTimeout(tryReturn, 1800);
  });
})();
</script></head>
<body><div class="card"><h1>Restarting…</h1><p id="status">Please wait while the app restarts.</p><p class="muted">This page will automatically return to the admin settings page when the server is back online.</p></div></body></html>
"""

def restart_wait_page(status_message):
    return render_template_string(
        RESTART_HTML,
        target_url=url_for("settings"),
        status_message=status_message,
    )


def _start_macos_web_after_exit(extra_delay=1.2):
    """Start a fresh macOS/Linux Flask process after the current one exits.

    Do not use os.execv here: Flask may still have the listening socket open,
    which can produce "Address already in use" on port 8765. Instead we
    spawn a tiny detached shell job, then exit this process so the port is
    released before the new copy binds.
    """
    python_exe = sys.executable
    script_path = str((APP_DIR / "web_app.py").resolve())
    log_path = str((APP_DIR / "restart.log").resolve())
    app_dir = str(APP_DIR.resolve())
    command = (
        f"sleep {float(extra_delay):.1f}; "
        f"cd {shlex.quote(app_dir)}; "
        f"exec {shlex.quote(python_exe)} {shlex.quote(script_path)} >> {shlex.quote(log_path)} 2>&1"
    )
    subprocess.Popen(
        ["/bin/sh", "-c", command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def restart_web_process(delay=1.0):
    def _restart():
        try:
            if os.name == "nt":
                os._exit(RESTART_EXIT_CODE)
            else:
                _start_macos_web_after_exit(extra_delay=1.5)
                os._exit(0)
        except Exception:
            os._exit(1)

    threading.Timer(delay, _restart).start()


def restart_web_and_caddy_process():
    ensure_caddy_paths()

    def _restart_both():
        try:
            restart_caddy_process()
        except Exception:
            pass
        try:
            if os.name == "nt":
                os._exit(RESTART_EXIT_CODE)
            else:
                _start_macos_web_after_exit(extra_delay=1.8)
                os._exit(0)
        except Exception:
            os._exit(1)

    threading.Timer(1.5, _restart_both).start()


def validate_library_folder(folder):
    folder = (folder or "").strip()
    if not folder:
        return None, "Please enter a movie folder first."

    path = Path(folder)
    if not path.exists():
        return None, "That folder does not exist on this machine."
    if not path.is_dir():
        return None, "That path exists, but it is not a folder."

    return path.resolve(), None




def validate_tv_folder(folder):
    folder = (folder or "").strip()
    if not folder:
        return None, "Please enter a TV folder first."

    path = Path(folder)
    if not path.exists():
        return None, "That TV folder does not exist on this machine."
    if not path.is_dir():
        return None, "That TV path exists, but it is not a folder."

    return path.resolve(), None



def value_get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except Exception:
        return default

def row_to_dict(row):
    return dict(row) if row is not None else None

def split_csv_values(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            parts.extend(split_csv_values(item))
        return parts
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def tv_missing_assets(show):
    missing = []
    if not show:
        return missing
    if not (value_get(show, "nfo_path") or "").strip():
        missing.append("Missing NFO")
    if not (value_get(show, "poster_path") or "").strip():
        missing.append("Missing poster")
    if not (value_get(show, "fanart_path") or "").strip():
        missing.append("Missing fanart")
    return missing


def tv_asset_status_matches(show, asset_status):
    missing = tv_missing_assets(show)
    if asset_status == "Needs attention":
        return bool(missing)
    return asset_status in missing


def build_tv_filters_query(args, **updates):
    """Build TV navigation query strings.

    TV browsing keeps only a show-name search plus show/season state so
    selection does not preserve stale filter parameters from older URLs.
    """
    params = {
        "q": args.get("q", ""),
        "show_id": args.get("show_id", ""),
        "season": args.get("season", ""),
    }
    for key, value in updates.items():
        params[key] = value
    cleaned = {}
    for key in ("q", "show_id", "season"):
        value = params.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        cleaned[key] = value
    encoded = urlencode(cleaned)
    return f"?{encoded}" if encoded else ""


def format_tv_scan_report_message(prefix, report):
    if not report:
        return prefix

    parts = [
        prefix,
        f"Folder: {report.get('folder', '')}",
        f"Folders seen: {report.get('folders_seen', 0)}",
        f"Folders scanned: {report.get('folders_scanned', 0)}",
    ]

    if not report.get("clean"):
        parts.append(f"Folders skipped unchanged: {report.get('folders_skipped', 0)}")
        parts.append(f"Unchanged folders: {report.get('folders_unchanged_percent', 0)}%")

    parts.extend([
        f"Scanned episode files: {report.get('scanned', 0)}",
        f"Saved episodes: {report.get('saved', 0)}",
        f"Shows inserted: {report.get('inserted_shows', 0)}",
        f"Shows updated: {report.get('updated_shows', 0)}",
        f"Episodes inserted: {report.get('inserted_episodes', 0)}",
        f"Episodes updated: {report.get('updated_episodes', 0)}",
    ])

    if report.get("clean"):
        parts.append(f"Removed old entries: {report.get('deleted', 0)}")
    else:
        parts.append(f"Marked missing entries: {report.get('deleted', 0)}")

    show_nfo = report.get("missing_show_nfo", report.get("missing_nfo", 0))
    episode_nfo = report.get("missing_episode_nfo", 0)
    parts.append(f"Missing show NFO: {show_nfo}")
    parts.append(f"Missing episode NFO: {episode_nfo}")
    parts.append(f"Missing show poster: {report.get('missing_poster', 0)}")
    parts.append(f"Missing show fanart: {report.get('missing_fanart', 0)}")
    if report.get("unparsed_episode_files"):
        parts.append(f"Episode names needing review: {report.get('unparsed_episode_files', 0)}")
    if report.get("duplicate_show_groups"):
        parts.append(f"Duplicate show groups: {report.get('duplicate_show_groups', 0)}")
    if report.get("duplicate_episode_groups"):
        parts.append(f"Duplicate episode groups: {report.get('duplicate_episode_groups', 0)}")
    if report.get("quality_summary"):
        parts.append(report.get("quality_summary"))

    return " • ".join(parts)

def format_scan_report_message(prefix, report):
    if not report:
        return prefix

    parts = [
        prefix,
        f"Folder: {report['folder']}",
        f"Folders seen: {report.get('folders_seen', 0)}",
        f"Folders scanned: {report.get('folders_scanned', 0)}",
    ]

    if not report.get("clean"):
        parts.append(f"Folders skipped unchanged: {report.get('folders_skipped', 0)}")
        parts.append(f"Unchanged folders: {report.get('folders_unchanged_percent', 0)}%")

    parts.extend([
        f"Scanned video files: {report['scanned']}",
        f"Saved: {report['saved']}",
        f"Inserted: {report['inserted']}",
        f"Updated: {report['updated']}",
    ])

    if report.get("clean"):
        parts.append(f"Removed old entries: {report['deleted']}")
    else:
        parts.append(f"Marked missing entries: {report['deleted']}")

    if report.get("duplicate_candidate_groups"):
        parts.append(f"Duplicate candidates in changed folders: {report.get('duplicate_candidate_groups', 0)}")
    if report.get("duplicate_groups"):
        parts.append(f"Duplicate groups fixed: {report.get('duplicate_groups', 0)}")
    if report.get("duplicate_entries_removed"):
        parts.append(f"Duplicate entries removed: {report.get('duplicate_entries_removed', 0)}")

    parts.append(f"Missing NFO: {report['missing_nfo']}")
    parts.append(f"Missing poster: {report['missing_poster']}")
    parts.append(f"Missing fanart: {report['missing_fanart']}")
    if report.get("unparsed_video_files"):
        parts.append(f"Video names needing review: {report.get('unparsed_video_files', 0)}")
    if report.get("quality_summary"):
        parts.append(report.get("quality_summary"))

    return " • ".join(parts)


def normalize_resolution(width, height):
    try:
        w = int(float(width or 0))
    except Exception:
        w = 0

    try:
        h = int(float(height or 0))
    except Exception:
        h = 0

    major = max(w, h)
    minor = min(w, h)

    if major == 0 or minor == 0:
        return ""

    if major >= 3800 or minor >= 2000:
        return "2160p"

    if major >= 1820 and minor >= 760:
        return "1080p"
    if minor >= 1000:
        return "1080p"

    if major >= 1200 and minor >= 520:
        return "720p"
    if minor >= 690:
        return "720p"

    if major >= 700 and minor >= 400:
        return "480p"
    if minor >= 400:
        return "480p"

    if major >= 500 and minor >= 300:
        return "360p"
    if minor >= 300:
        return "360p"

    return ""


def format_channels(ch):
    try:
        n = int(ch or 0)
    except Exception:
        return ""
    if n == 6:
        return "5.1"
    if n == 8:
        return "7.1"
    return f"{n}.0" if n else ""


def format_file_size_gb(value, path=None):
    try:
        size = float(value)
        if size > 0:
            return f"{size:.2f} GB"
    except Exception:
        pass

    if path:
        try:
            size = Path(str(path)).stat().st_size / (1024 ** 3)
            if size > 0:
                return f"{size:.2f} GB"
        except Exception:
            pass

    return ""


def file_size_label(item):
    return format_file_size_gb(
        value_get(item, "file_size_gb"),
        value_get(item, "movie_path") or value_get(item, "file_path"),
    )


def media_file_extension(path):
    """Return a normalised lowercase file extension for filtering/display.

    This is intentionally forgiving for filenames such as
    `Movie (2020) .mkv` or `Movie (2020). mkv`.
    """
    raw = str(path or "").strip()
    if not raw or "." not in raw:
        return ""
    ext = raw.rsplit(".", 1)[-1].strip().lower()
    return f".{ext}" if ext else ""


def movie_file_extension(movie):
    return media_file_extension(value_get(movie, "movie_path"))


def collection_query(collection_name, sort_value="title_asc", page_size=150):
    return build_query(
        {
            "sort": normalize_sort(sort_value),
            "movie_set": [collection_name] if collection_name else [],
            "page_size": page_size,
        },
        page=1,
    )


def media_tags(movie):
    tags = []

    res = normalize_resolution(value_get(movie, "video_width"), value_get(movie, "video_height"))
    if res:
        css_class = {
            "2160p": "tag tag-4k",
            "1080p": "tag tag-1080",
            "720p": "tag tag-720",
            "480p": "tag tag-480",
            "360p": "tag tag-360",
        }.get(res, "tag")
        tags.append({"text": res, "class": css_class})

    if value_get(movie, "video_codec"):
        tags.append({"text": str(value_get(movie, "video_codec")).upper(), "class": "tag"})

    if value_get(movie, "audio_codec"):
        tags.append({"text": str(value_get(movie, "audio_codec")).upper(), "class": "tag"})

    ch = format_channels(value_get(movie, "audio_channels"))
    if ch:
        tags.append({"text": ch, "class": "tag"})

    size = file_size_label(movie)
    if size:
        tags.append({"text": size, "class": "tag"})

    return tags

def missing_asset_tags(movie):
    tags = []
    if not value_get(movie, "nfo_path"):
        tags.append({"text": "Missing NFO", "class": "tag tag-warn"})
    if not value_get(movie, "poster_path"):
        tags.append({"text": "Missing poster", "class": "tag tag-warn"})
    if not value_get(movie, "fanart_path"):
        tags.append({"text": "Missing fanart", "class": "tag tag-warn"})
    return tags


def has_missing_assets(movie):
    return bool(missing_asset_tags(movie))


def filter_match(movie_value, selected_values, split_commas=False):
    if not selected_values:
        return True

    if split_commas:
        values = [v.strip() for v in str(movie_value or "").split(",") if v.strip()]
        return any(v in selected_values for v in values)

    return str(movie_value or "") in selected_values


def text_search_matches(search_text, *allowed_values):
    """Return True when every search word is present in the allowed fields only."""
    words = [word for word in str(search_text or "").lower().split() if word]
    if not words:
        return True
    haystack = " | ".join(str(value or "") for value in allowed_values).lower()
    return all(word in haystack for word in words)

def build_query(params=None, movie_id=None, page=None, page_size=None):
    params = params or {}
    query = []

    q = params.get("q", "")
    if q:
        query.append(("q", q))

    sort_value = params.get("sort", "title_asc")
    if sort_value:
        query.append(("sort", sort_value))

    for key in ["year", "country", "movie_set", "resolution", "file_ext", "asset_status"]:
        values = params.get(key, [])
        if not isinstance(values, list):
            values = [values]
        for v in values:
            if v:
                query.append((key, v))

    query_page = page if page is not None else params.get("page")
    if query_page:
        query.append(("page", str(query_page)))

    query_page_size = page_size if page_size is not None else params.get("page_size")
    if query_page_size:
        query.append(("page_size", str(query_page_size)))

    if movie_id is not None:
        query.append(("movie_id", str(movie_id)))

    return urlencode(query)



def _movie_matches_filters(movie, q="", years_selected=None, countries_selected=None, movie_sets_selected=None, resolutions_selected=None, file_ext_selected=None, asset_status_selected=None):
    years_selected = years_selected or []
    countries_selected = countries_selected or []
    movie_sets_selected = movie_sets_selected or []
    resolutions_selected = resolutions_selected or []
    file_ext_selected = file_ext_selected or []
    asset_status_selected = asset_status_selected or []
    if q and not text_search_matches(q, value_get(movie, "title"), value_get(movie, "movie_set")):
        return False
    if years_selected and str(value_get(movie, "year", "")) not in [str(y) for y in years_selected]:
        return False
    if countries_selected:
        countries = split_csv_values(value_get(movie, "country"))
        if not any(country in countries_selected for country in countries):
            return False
    if movie_sets_selected and str(value_get(movie, "movie_set", "")) not in movie_sets_selected:
        return False
    if resolutions_selected:
        res = normalize_resolution(value_get(movie, "video_width"), value_get(movie, "video_height"))
        if res not in resolutions_selected:
            return False
    if file_ext_selected:
        ext = movie_file_extension(movie)
        if ext not in file_ext_selected:
            return False
    if asset_status_selected:
        missing = missing_asset_tags(movie)
        missing_text = {str(value_get(tag, "text", "")).lower() for tag in missing}
        if "needs_attention" in asset_status_selected and missing:
            return True
        wanted_map = {"missing_nfo": "missing nfo", "missing_poster": "missing poster", "missing_fanart": "missing fanart"}
        explicit = [wanted_map[v] for v in asset_status_selected if v in wanted_map]
        if explicit and any(label in missing_text for label in explicit):
            return True
        return False
    return True

def _sort_movies(movies, sort_value):
    sort_value = normalize_sort(sort_value)
    if sort_value == "title_desc":
        return sorted(movies, key=lambda m: str(value_get(m, "sort_title") or value_get(m, "title") or "").lower(), reverse=True)
    if sort_value == "year_desc":
        return sorted(movies, key=lambda m: (int(value_get(m, "year") or 0), str(value_get(m, "sort_title") or value_get(m, "title") or "").lower()), reverse=True)
    if sort_value == "year_asc":
        return sorted(movies, key=lambda m: (int(value_get(m, "year") or 0), str(value_get(m, "sort_title") or value_get(m, "title") or "").lower()))
    return sorted(movies, key=lambda m: str(value_get(m, "sort_title") or value_get(m, "title") or "").lower())

def _filtered_movies(c, q="", years_selected=None, countries_selected=None, movie_sets_selected=None, resolutions_selected=None, file_ext_selected=None, asset_status_selected=None, sort_value="title_asc"):
    rows = [dict(r) for r in c.execute("SELECT * FROM movies").fetchall()]
    filtered = [m for m in rows if _movie_matches_filters(m, q, years_selected, countries_selected, movie_sets_selected, resolutions_selected, file_ext_selected, asset_status_selected)]
    return _sort_movies(filtered, sort_value)

def get_total_movie_count(c):
    row = c.execute("SELECT COUNT(*) AS n FROM movies").fetchone()
    return int(row["n"] if row else 0)

def get_filtered_movie_count(c, q="", years_selected=None, countries_selected=None, movie_sets_selected=None, resolutions_selected=None, file_ext_selected=None, asset_status_selected=None):
    return len(_filtered_movies(c, q, years_selected, countries_selected, movie_sets_selected, resolutions_selected, file_ext_selected, asset_status_selected))

def get_page_movies(c, q="", years_selected=None, countries_selected=None, movie_sets_selected=None, resolutions_selected=None, file_ext_selected=None, asset_status_selected=None, sort_value="title_asc", page=1, page_size=150):
    movies = _filtered_movies(c, q, years_selected, countries_selected, movie_sets_selected, resolutions_selected, file_ext_selected, asset_status_selected, sort_value)
    start = max(0, (int(page or 1) - 1) * int(page_size or 150))
    return movies[start:start + int(page_size or 150)]

def get_selected_movie(c, movie_id, q="", years_selected=None, countries_selected=None, movie_sets_selected=None, resolutions_selected=None, file_ext_selected=None, asset_status_selected=None):
    row = c.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
    movie = dict(row) if row else None
    if movie and _movie_matches_filters(movie, q, years_selected, countries_selected, movie_sets_selected, resolutions_selected, file_ext_selected, asset_status_selected):
        return movie
    return None

def selected_summary(values, default_label="All"):
    if not values:
        return default_label
    if len(values) == 1:
        return values[0]
    return f"{len(values)} selected"


SORT_OPTIONS = [
    ("title_asc", "Title A-Z"),
    ("title_desc", "Title Z-A"),
    ("year_desc", "Year newest"),
    ("year_asc", "Year oldest"),
]
ALLOWED_SORTS = {value for value, _label in SORT_OPTIONS}


def normalize_sort(value):
    value = (value or "title_asc").strip()
    return value if value in ALLOWED_SORTS else "title_asc"


def get_sort_sql(sort_value):
    sort_value = normalize_sort(sort_value)
    if sort_value == "title_desc":
        return "COALESCE(sort_title, title) COLLATE NOCASE DESC, year DESC, id DESC"
    if sort_value == "year_desc":
        return "COALESCE(year, 0) DESC, COALESCE(sort_title, title) COLLATE NOCASE ASC, id ASC"
    if sort_value == "year_asc":
        return "COALESCE(year, 9999) ASC, COALESCE(sort_title, title) COLLATE NOCASE ASC, id ASC"
    return "COALESCE(sort_title, title) COLLATE NOCASE ASC, year ASC, id ASC"


def get_sort_label(sort_value):
    sort_value = normalize_sort(sort_value)
    for value, label in SORT_OPTIONS:
        if value == sort_value:
            return label
    return "Title A-Z"


def export_timestamp():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _is_blank_sql(column_name):
    return f"({column_name} IS NULL OR TRIM(COALESCE({column_name}, '')) = '')"


def safe_count_where(c, table_name, where_sql="1=1"):
    try:
        exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return 0
        row = c.execute(f"SELECT COUNT(*) AS n FROM {table_name} WHERE {where_sql}").fetchone()
        return int(row["n"] if row else 0)
    except Exception:
        return 0


def csv_download_response(filename, fieldnames, rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        clean = {}
        for field in fieldnames:
            value = row.get(field, "") if isinstance(row, dict) else ""
            clean[field] = "" if value is None else value
        writer.writerow(clean)
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def export_library_rows(c):
    rows = []
    try:
        data = c.execute("SELECT * FROM movies ORDER BY COALESCE(sort_title, title) COLLATE NOCASE, year, id").fetchall()
    except Exception:
        return rows
    for row in data:
        item = dict(row)
        item["resolution_tag"] = normalize_resolution(item.get("video_width"), item.get("video_height"))
        item["audio_channels_display"] = format_channels(item.get("audio_channels"))
        item["missing_nfo"] = "yes" if not item.get("nfo_path") else ""
        item["missing_poster"] = "yes" if not item.get("poster_path") else ""
        item["missing_fanart"] = "yes" if not item.get("fanart_path") else ""
        rows.append(item)
    return rows


def export_missing_asset_rows(c, path_column, asset_label):
    allowed = {"nfo_path", "poster_path", "fanart_path"}
    if path_column not in allowed:
        return []
    rows = []
    try:
        data = c.execute(
            f"""
            SELECT * FROM movies
            WHERE {_is_blank_sql(path_column)}
            ORDER BY COALESCE(sort_title, title) COLLATE NOCASE, year, id
            """
        ).fetchall()
    except Exception:
        return rows
    for row in data:
        item = dict(row)
        item["resolution_tag"] = normalize_resolution(item.get("video_width"), item.get("video_height"))
        item["missing_asset"] = asset_label
        rows.append(item)
    return rows


def export_duplicates_rows(c):
    try:
        rows = c.execute("SELECT id, title, sort_title, year FROM movies ORDER BY COALESCE(sort_title, title) COLLATE NOCASE, year, id").fetchall()
        return build_duplicate_rows(rows, "movie_ids")
    except Exception:
        return []


def export_tv_library_rows(c):
    try:
        data = c.execute(
            """
            SELECT
                s.*,
                COUNT(DISTINCT e.season_number) AS season_count,
                COUNT(e.id) AS episode_count
            FROM tv_shows s
            LEFT JOIN tv_episodes e ON e.show_id = s.id
            GROUP BY s.id
            ORDER BY COALESCE(s.sort_title, s.title) COLLATE NOCASE, s.year, s.id
            """
        ).fetchall()
    except Exception:
        return []
    rows = []
    for row in data:
        item = dict(row)
        item["missing_tvshow_nfo"] = "yes" if not item.get("nfo_path") else ""
        item["missing_poster"] = "yes" if not item.get("poster_path") else ""
        item["missing_fanart"] = "yes" if not item.get("fanart_path") else ""
        rows.append(item)
    return rows


def export_tv_missing_show_asset_rows(c, path_column, asset_label):
    allowed = {"nfo_path", "poster_path", "fanart_path"}
    if path_column not in allowed:
        return []
    try:
        data = c.execute(
            f"""
            SELECT * FROM tv_shows
            WHERE {_is_blank_sql(path_column)}
            ORDER BY COALESCE(sort_title, title) COLLATE NOCASE, year, id
            """
        ).fetchall()
    except Exception:
        return []
    rows = []
    for row in data:
        item = dict(row)
        item["missing_asset"] = asset_label
        rows.append(item)
    return rows


def export_tv_missing_episode_nfo_rows(c):
    try:
        data = c.execute(
            f"""
            SELECT
                e.id,
                e.show_id,
                s.title AS show_title,
                s.year AS show_year,
                e.season_number,
                e.episode_number,
                e.title,
                e.air_date,
                e.file_path,
                e.folder_path,
                e.nfo_path,
                'episode_nfo' AS missing_asset
            FROM tv_episodes e
            LEFT JOIN tv_shows s ON s.id = e.show_id
            WHERE {_is_blank_sql('e.nfo_path')}
            ORDER BY s.title COLLATE NOCASE, e.season_number, e.episode_number, e.id
            """
        ).fetchall()
    except Exception:
        return []
    return [dict(row) for row in data]


def export_tv_duplicates_rows(c):
    try:
        rows = c.execute("SELECT id, title, sort_title, year FROM tv_shows ORDER BY COALESCE(sort_title, title) COLLATE NOCASE, year, id").fetchall()
        return build_duplicate_rows(rows, "show_ids")
    except Exception:
        return []



def normalise_duplicate_text(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"\(\d{4}\)", " ", text)
    text = re.sub(r"\b(the|a|an)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def build_duplicate_rows(rows, id_field):
    groups = OrderedDict()
    for row in rows:
        item = dict(row)
        key = (normalise_duplicate_text(item.get("sort_title") or item.get("title")), item.get("year"))
        if not key[0]:
            continue
        groups.setdefault(key, []).append(item)
    output = []
    for (_norm_title, year), items in groups.items():
        if len(items) < 2:
            continue
        title = items[0].get("title") or "Untitled"
        output.append({
            "title": title,
            "year": "" if year is None else year,
            "duplicate_count": len(items),
            id_field: ", ".join(str(item.get("id")) for item in items),
        })
    output.sort(key=lambda item: (-int(item.get("duplicate_count") or 0), str(item.get("title") or "").lower(), str(item.get("year") or "")))
    return output

def get_report_summary():
    c = conn()
    try:
        movie_missing_nfo = safe_count_where(c, "movies", _is_blank_sql("nfo_path"))
        movie_missing_poster = safe_count_where(c, "movies", _is_blank_sql("poster_path"))
        movie_missing_fanart = safe_count_where(c, "movies", _is_blank_sql("fanart_path"))
        movie_duplicates = len(export_duplicates_rows(c))
        tv_missing_show_nfo = safe_count_where(c, "tv_shows", _is_blank_sql("nfo_path"))
        tv_missing_episode_nfo = safe_count_where(c, "tv_episodes", _is_blank_sql("nfo_path"))
        tv_missing_poster = safe_count_where(c, "tv_shows", _is_blank_sql("poster_path"))
        tv_missing_fanart = safe_count_where(c, "tv_shows", _is_blank_sql("fanart_path"))
        tv_duplicates = len(export_tv_duplicates_rows(c))
    finally:
        c.close()

    movie_attention = movie_missing_nfo + movie_missing_poster + movie_missing_fanart + movie_duplicates
    tv_attention = tv_missing_show_nfo + tv_missing_episode_nfo + tv_missing_poster + tv_missing_fanart + tv_duplicates
    return {
        "movie_attention": movie_attention,
        "tv_attention": tv_attention,
        "total_attention": movie_attention + tv_attention,
        "movies": [
            {"key": "movie-missing-nfo", "label": "Missing NFO", "count": movie_missing_nfo, "href": "/reports/issues/movie-missing-nfo", "export_href": "/export/report/missing-nfo.csv", "note": "Movie metadata files"},
            {"key": "movie-missing-poster", "label": "Missing poster", "count": movie_missing_poster, "href": "/reports/issues/movie-missing-poster", "export_href": "/export/report/missing-poster.csv", "note": "Movie poster artwork"},
            {"key": "movie-missing-fanart", "label": "Missing fanart", "count": movie_missing_fanart, "href": "/reports/issues/movie-missing-fanart", "export_href": "/export/report/missing-fanart.csv", "note": "Movie background artwork"},
            {"key": "movie-duplicates", "label": "Duplicate groups", "count": movie_duplicates, "href": "/reports/issues/movie-duplicates", "export_href": "/export/report/duplicates.csv", "note": "Same movie title/year"},
        ],
        "tv": [
            {"key": "tv-missing-show-nfo", "label": "Missing show NFO", "count": tv_missing_show_nfo, "href": "/reports/issues/tv-missing-show-nfo", "export_href": "/export/tv-report/missing-tvshow-nfo.csv", "note": "Show-level metadata"},
            {"key": "tv-missing-episode-nfo", "label": "Missing episode NFO", "count": tv_missing_episode_nfo, "href": "/reports/issues/tv-missing-episode-nfo", "export_href": "/export/tv-report/missing-episode-nfo.csv", "note": "Episode metadata files"},
            {"key": "tv-missing-poster", "label": "Missing poster", "count": tv_missing_poster, "href": "/reports/issues/tv-missing-poster", "export_href": "/export/tv-report/missing-poster.csv", "note": "Show poster artwork"},
            {"key": "tv-missing-fanart", "label": "Missing fanart", "count": tv_missing_fanart, "href": "/reports/issues/tv-missing-fanart", "export_href": "/export/tv-report/missing-fanart.csv", "note": "Show background artwork"},
            {"key": "tv-duplicates", "label": "Duplicate groups", "count": tv_duplicates, "href": "/reports/issues/tv-duplicates", "export_href": "/export/tv-report/duplicates.csv", "note": "Same TV show title/year"},
        ],
    }



BASE_STYLE = """
<style>
:root{--bg:#070a12;--panel:rgba(15,23,42,.72);--panel2:rgba(24,32,48,.62);--line:rgba(148,163,184,.20);--text:#f8fafc;--muted:#9aa7ba;--soft:#cbd5e1;--brand:#7dd3fc;--accent:#a78bfa;--good:#34d399;--warn:#f59e0b;--danger:#fb7185;--shadow:0 24px 60px rgba(0,0,0,.42);}
*{box-sizing:border-box}html{color-scheme:dark}body{margin:0;font-family:Inter,Segoe UI,Roboto,Arial,sans-serif;background:radial-gradient(circle at 18% -10%,rgba(59,130,246,.34),transparent 32%),radial-gradient(circle at 86% 4%,rgba(168,85,247,.24),transparent 30%),linear-gradient(135deg,#05070d,#0b1020 46%,#111827);color:var(--text);min-height:100vh}a{color:inherit}input,select,button{font:inherit}input,select{width:100%;background:rgba(2,6,23,.72);border:1px solid var(--line);border-radius:14px;color:var(--text);padding:12px 14px;outline:none}input:focus,select:focus{border-color:rgba(125,211,252,.75);box-shadow:0 0 0 3px rgba(125,211,252,.12)}button,.btn{display:inline-flex;align-items:center;justify-content:center;gap:9px;border:1px solid var(--line);border-radius:14px;padding:11px 14px;text-decoration:none;color:var(--text);background:linear-gradient(180deg,rgba(255,255,255,.10),rgba(255,255,255,.045));box-shadow:0 10px 24px rgba(0,0,0,.20);cursor:pointer;min-height:44px;white-space:nowrap}.btn:hover,button:hover{border-color:rgba(125,211,252,.58);transform:translateY(-1px)}.btn.primary,button.primary{background:linear-gradient(135deg,rgba(14,165,233,.92),rgba(124,58,237,.82));border-color:rgba(255,255,255,.16)}.btn.good{background:linear-gradient(135deg,rgba(16,185,129,.78),rgba(14,165,233,.55))}.btn.danger{background:linear-gradient(135deg,rgba(244,63,94,.75),rgba(124,58,237,.44))}.icon{width:34px;height:34px;border-radius:12px;display:inline-grid;place-items:center;background:radial-gradient(circle at 30% 20%,rgba(255,255,255,.72),rgba(255,255,255,.08) 33%,rgba(15,23,42,.28) 72%),linear-gradient(145deg,rgba(125,211,252,.82),rgba(167,139,250,.78));box-shadow:inset 0 1px 0 rgba(255,255,255,.45),0 10px 22px rgba(0,0,0,.34);font-size:18px;line-height:1}.icon.small{width:30px;height:30px;border-radius:10px;font-size:16px}.shell{width:min(1600px,100%);margin:0 auto;padding:14px 18px}.topbar{position:sticky;top:0;z-index:10;backdrop-filter:blur(18px);background:linear-gradient(180deg,rgba(7,10,18,.92),rgba(7,10,18,.66));border-bottom:1px solid rgba(148,163,184,.12)}.nav{display:flex;align-items:center;gap:12px;justify-content:space-between;width:min(1600px,100%);margin:0 auto;padding:12px 18px}.brand{display:flex;align-items:center;gap:12px;font-weight:900;letter-spacing:-.03em}.brand small{display:block;font-size:12px;color:var(--muted);font-weight:650;letter-spacing:0}.ui-version{font-size:11px;color:#67e8f9;border:1px solid rgba(125,211,252,.22);background:rgba(14,165,233,.10);padding:4px 7px;border-radius:999px;margin-left:4px;white-space:nowrap}.navlinks{display:flex;gap:9px;align-items:center}.navlinks .btn{padding:9px 12px}.navlinks .active{border-color:rgba(125,211,252,.58);background:rgba(125,211,252,.12)}.flash{margin:12px 0;padding:13px 15px;border:1px solid rgba(125,211,252,.24);background:rgba(14,165,233,.10);border-radius:16px;color:#dff7ff}.grid-shell{display:grid;grid-template-columns:370px minmax(0,1fr);gap:16px;align-items:start}.panel{background:linear-gradient(180deg,rgba(15,23,42,.80),rgba(15,23,42,.54));border:1px solid var(--line);border-radius:22px;box-shadow:var(--shadow);backdrop-filter:blur(18px);overflow:hidden}.panel-pad{padding:14px}.searchbar{display:grid;grid-template-columns:1fr auto;gap:10px;margin-bottom:10px}.filters{border-top:1px solid rgba(148,163,184,.13);padding-top:12px}.filters summary{cursor:pointer;color:var(--soft);font-weight:900;margin-bottom:10px;list-style:none}.filters summary::-webkit-details-marker{display:none}.filters summary:after{content:"⌄";float:right;color:var(--brand)}.filters[open] summary:after{content:"⌃"}.movie-filter-accordion{border:1px solid rgba(148,163,184,.18);border-radius:18px;background:linear-gradient(180deg,rgba(2,6,23,.38),rgba(2,6,23,.22));padding:12px;margin-top:10px}.filter-grid{display:grid;gap:10px}.filter-group{display:grid;gap:7px;max-height:145px;overflow:auto;padding:8px;border:1px solid rgba(148,163,184,.12);border-radius:14px;background:rgba(2,6,23,.28)}.check{display:flex;gap:8px;align-items:center;color:var(--soft);font-size:13px}.check input{width:auto}.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px}.muted{color:var(--muted)}.list{display:grid;gap:8px;max-height:calc(100vh - 190px);overflow:auto;padding:10px 10px 12px}.item{display:grid;grid-template-columns:50px minmax(0,1fr);gap:11px;padding:9px;border-radius:16px;text-decoration:none;border:1px solid transparent;min-height:88px}.item:hover,.item.active{background:rgba(125,211,252,.10);border-color:rgba(125,211,252,.22)}.thumb{width:50px;height:74px;border-radius:12px;background:#111827 center/cover no-repeat;border:1px solid rgba(255,255,255,.12);box-shadow:0 10px 18px rgba(0,0,0,.26)}.item-title{font-weight:850;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.2}.item-meta{font-size:12px;color:var(--muted);line-height:1.35;margin-top:4px;overflow:hidden}.item-tags{display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-top:6px;min-height:22px}.item-tags .chip{display:inline-flex;align-items:center;justify-content:center;line-height:1;min-height:22px}.hero-art{height:245px;background:#111827 center/cover no-repeat;position:relative}.hero-art:after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(7,10,18,.06),rgba(7,10,18,.88))}.detail{padding:16px 18px 18px}.detail-head{display:grid;grid-template-columns:170px minmax(0,1fr);gap:18px;margin-top:-72px;position:relative;z-index:1}.poster{height:255px;border-radius:20px;background:#111827 center/cover no-repeat;border:1px solid rgba(255,255,255,.18);box-shadow:0 22px 50px rgba(0,0,0,.45)}h1,h2,h3{margin:0 0 10px;letter-spacing:-.035em}h1{font-size:clamp(28px,3.4vw,48px);line-height:1.05}.chips{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.chip{font-size:12px;font-weight:800;color:#e0f2fe;border:1px solid rgba(125,211,252,.25);background:rgba(14,165,233,.14);padding:6px 9px;border-radius:999px}.plot{line-height:1.58;color:#d7dee9;max-width:1050px}.info-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:12px}.info{padding:12px;border:1px solid rgba(148,163,184,.13);background:rgba(2,6,23,.30);border-radius:16px}.info b{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:5px}.back-one{width:44px;height:44px;padding:0;border-radius:15px;margin-bottom:12px}.empty{padding:34px;text-align:center;color:var(--muted)}.pagination{display:flex;gap:8px;justify-content:center;align-items:center;flex-wrap:wrap;padding:12px}.settings-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.settings-card{padding:18px}.settings-card form{display:grid;gap:10px}.path{word-break:break-word;color:var(--muted);font-size:13px;background:rgba(2,6,23,.34);border:1px solid rgba(148,163,184,.12);border-radius:14px;padding:10px}.season-layout{display:grid;grid-template-columns:270px minmax(0,1fr);gap:16px;margin-top:14px;align-items:start}.season-layout section{min-width:0;min-height:0}.season-list{display:grid;gap:9px;max-height:calc(100vh - 360px);overflow:auto;padding-right:4px}.season-card{display:grid;grid-template-columns:48px minmax(0,1fr);gap:10px;padding:9px;border-radius:16px;border:1px solid rgba(148,163,184,.14);text-decoration:none;background:rgba(2,6,23,.26)}.season-card.active{border-color:rgba(125,211,252,.52);background:rgba(125,211,252,.12)}.season-poster{height:68px;border-radius:12px;background:#111827 center/cover no-repeat;border:1px solid rgba(255,255,255,.10)}.episodes{display:grid;gap:9px;max-height:calc(100vh - 410px);overflow:auto;padding-right:4px;align-content:start}.episode{display:grid;grid-template-columns:64px minmax(0,1fr) auto;gap:12px;align-items:start;padding:12px 14px;border-radius:18px;background:rgba(2,6,23,.30);border:1px solid rgba(148,163,184,.14)}.epno{font-weight:900;color:var(--brand)}.episode p{margin:5px 0 0;color:var(--muted);line-height:1.42}.mobile-only{display:none}@media(max-width:950px){.nav{align-items:flex-start;gap:10px}.brand small,.ui-version{display:none}.navlinks{flex-wrap:wrap;justify-content:flex-end}.navlinks .txt{display:none}.shell{padding:10px}.grid-shell,.settings-grid{grid-template-columns:1fr}.list{max-height:none;padding:8px}.item{min-height:84px}.item-tags{gap:4px;margin-top:5px}.item-tags .chip{font-size:10px;padding:4px 6px;max-width:82px}.detail{padding:12px 12px 16px}.detail-head{grid-template-columns:1fr;margin-top:-52px;gap:12px}.poster{width:min(190px,54vw);height:min(285px,82vw);margin:auto}.info-grid,.season-layout{grid-template-columns:1fr}.season-list{max-height:none;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));padding-right:0}.episodes{max-height:58vh;overflow:auto;padding-right:2px}.hero-art{height:165px}.episode{grid-template-columns:1fr;gap:7px}.episode .muted{text-align:left}.chips{gap:6px}.chip{font-size:11px;padding:5px 8px}.desktop-only{display:none}.mobile-only{display:block}}
.tag-row{display:flex;gap:6px;flex-wrap:wrap;margin-top:7px}.collection-row{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin:10px 0 2px}.collection-row .btn{min-height:34px;padding:7px 10px;border-radius:999px;font-weight:850}.episode-meta-tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:7px}.item .chip{font-size:11px;padding:4px 7px;max-width:116px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.episode-pane{display:none}.episode-pane.active{display:grid}.episode-pane::-webkit-scrollbar,.season-list::-webkit-scrollbar,.list::-webkit-scrollbar{width:10px}.episode-pane::-webkit-scrollbar-thumb,.season-list::-webkit-scrollbar-thumb,.list::-webkit-scrollbar-thumb{background:rgba(148,163,184,.35);border-radius:999px}.scan-card{padding:18px}.scan-state{border:1px solid rgba(148,163,184,.14);background:rgba(2,6,23,.30);border-radius:16px;padding:13px;line-height:1.5;word-break:break-word}.scan-state strong{display:block;margin-bottom:4px}.scan-state.running{border-color:rgba(125,211,252,.45);box-shadow:0 0 0 3px rgba(125,211,252,.08)}.scan-state.done{border-color:rgba(52,211,153,.34)}.scan-state.error{border-color:rgba(251,113,133,.42)}


/* Shared Admin console section tabs */
.admin-console-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px}.admin-console-tabs a{display:inline-flex;align-items:center;gap:8px;border:1px solid rgba(148,163,184,.16);background:rgba(15,23,42,.46);color:var(--text);text-decoration:none;border-radius:999px;padding:9px 12px;font-weight:800;font-size:13px;box-shadow:0 10px 24px rgba(0,0,0,.16)}.admin-console-tabs a:hover{border-color:rgba(125,211,252,.58);transform:translateY(-1px)}.admin-console-tabs a.active{background:linear-gradient(135deg,rgba(99,102,241,.95),rgba(14,165,233,.78));border-color:rgba(255,255,255,.20)}.admin-section-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.admin-section-card{padding:16px;border:1px solid rgba(148,163,184,.14);border-radius:20px;background:rgba(2,6,23,.26)}.admin-section-card h3{margin:0 0 6px}.admin-section-card p{margin:0 0 12px;color:var(--muted)}@media(max-width:760px){.admin-section-grid{grid-template-columns:1fr}.admin-console-tabs a{flex:1;justify-content:center}}

.tv-intro{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.count-pill{display:inline-flex;align-items:center;justify-content:center;border:1px solid rgba(125,211,252,.22);background:rgba(14,165,233,.12);color:#dff7ff;border-radius:999px;padding:6px 10px;font-weight:850;font-size:12px}.season-layout>aside{position:sticky;top:82px}.season-layout h2{font-size:22px}.episode b{font-size:15px}.mobile-detail-link:active,.season-card:active{transform:scale(.995)}@media(max-width:950px){.season-layout>aside{position:static}.season-layout h2{font-size:20px}.tv-intro{display:block}.count-pill{margin-top:8px}.back-one{position:relative;z-index:2}.settings-card,.scan-card{padding:14px}}
</style>
"""

def _ico(label):
    return f'<span class="icon" aria-hidden="true">{label}</span>'
ICON_MOVIES=_ico('🎬')
ICON_TV=_ico('📺')
ICON_SETTINGS=_ico('⚙️')
ICON_LOGOUT=_ico('⏻')
ICON_SEARCH='<span class="icon small" aria-hidden="true">⌕</span>'
ICON_BACK='<span class="icon small" aria-hidden="true">‹</span>'
ICON_REFRESH='<span class="icon small" aria-hidden="true">↻</span>'
ICON_YEAR=''
ICON_COUNTRY=''
ICON_COLLECTION=''
ICON_RESOLUTION=''

NAV_HTML = """
<div class="topbar"><div class="nav"><div class="brand"><span class="icon">🎞️</span><div>Movie Library<small>Cinematic media manager</small></div></div><div class="navlinks">{% if not is_admin() %}<a class="btn {{ 'active' if active=='viewer_home' else '' }}" href="/home"><span>⌂</span><span class="txt">Home</span></a>{% endif %}<a class="btn {{ 'active' if active=='movies' else '' }}" href="/">"""+ICON_MOVIES+"""<span class="txt">Movies</span></a><a class="btn {{ 'active' if active=='tv' else '' }}" href="/tv">"""+ICON_TV+"""<span class="txt">TV Shows</span></a>{% if is_admin() %}<a class="btn {{ 'active' if active in ['dashboard','source_diagnostics','reports','server','settings','activity','health','mac_service'] else '' }}" href="/admin"><span>📊</span><span class="txt">Admin</span></a>{% endif %}<a class="btn" href="/logout">"""+ICON_LOGOUT+"""<span class="txt">Logout</span></a></div></div></div>
"""



VIEWER_HOME_HTML = """
<!doctype html>
<html>
<head>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Home</title>"""+BASE_STYLE+"""
<style>
.viewer-hero{padding:22px;display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:14px}.viewer-hero h1{margin:0 0 6px;font-size:34px}.viewer-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.viewer-card{position:relative;overflow:hidden;min-height:220px;padding:20px;border-radius:24px;border:1px solid rgba(148,163,184,.16);background:linear-gradient(135deg,rgba(15,23,42,.92),rgba(30,41,59,.55));box-shadow:var(--shadow)}.viewer-card .big-icon{font-size:48px;margin-bottom:10px}.viewer-card h2{margin:0 0 6px;font-size:30px}.viewer-card p{color:var(--muted);margin:0 0 18px}.viewer-stats{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.viewer-stat{padding:9px 11px;border-radius:14px;background:rgba(2,6,23,.36);border:1px solid rgba(148,163,184,.12)}.viewer-stat b{font-size:20px}.viewer-stat span{display:block;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}.viewer-card-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:auto}.viewer-recent{margin-top:14px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.recent-list{display:grid;gap:8px}.recent-item{padding:10px 12px;border-radius:14px;background:rgba(2,6,23,.26);border:1px solid rgba(148,163,184,.10);display:flex;justify-content:space-between;gap:10px}.recent-item span{color:var(--muted);font-size:12px}@media(max-width:850px){.viewer-hero{display:block}.viewer-grid,.viewer-recent{grid-template-columns:1fr}.viewer-card{min-height:0}.viewer-card-actions{display:grid}.viewer-card-actions .btn{justify-content:center}}
</style>
</head>
<body>"""+NAV_HTML+"""
<main class=shell>
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<section class="panel viewer-hero">
  <div><h1>Welcome{% if viewer_name %}, {{ viewer_name }}{% endif %}</h1><p class=muted style="margin:0">Choose what you want to browse. This account only has access to Movies and TV Shows.</p></div>
  <div class=viewer-stats>
    <div class=viewer-stat><b>{{ movie_count }}</b><span>Movies</span></div>
    <div class=viewer-stat><b>{{ tv_show_count }}</b><span>TV Shows</span></div>
    <div class=viewer-stat><b>{{ episode_count }}</b><span>Episodes</span></div>
  </div>
</section>
<section class=viewer-grid>
  <article class=viewer-card>
    <div class=big-icon>🎬</div><h2>Movies</h2><p>Browse the movie library, search titles, filter by resolution, collection, country, or year, and open movie details.</p>
    <div class=viewer-card-actions><a class="btn primary" href="/">Open Movies</a><a class=btn href="/?sort=year_desc">Recently released</a></div>
  </article>
  <article class=viewer-card>
    <div class=big-icon>📺</div><h2>TV Shows</h2><p>Browse TV shows, open seasons, and view episode lists without any admin or server controls.</p>
    <div class=viewer-card-actions><a class="btn primary" href="/tv">Open TV Shows</a></div>
  </article>
</section>
<section class=viewer-recent>
  <div class="panel panel-pad"><h2 style="margin-top:0">Recently added movies</h2><div class=recent-list>{% for item in recent_movies %}<a class=recent-item href="/movie/{{ item.id }}"><b>{{ item.title }}</b><span>{{ item.year or '' }}</span></a>{% else %}<p class=muted>No movies found yet.</p>{% endfor %}</div></div>
  <div class="panel panel-pad"><h2 style="margin-top:0">Recently added TV shows</h2><div class=recent-list>{% for item in recent_shows %}<a class=recent-item href="/tv/{{ item.id }}"><b>{{ item.title }}</b><span>{{ item.year or '' }}</span></a>{% else %}<p class=muted>No TV shows found yet.</p>{% endfor %}</div></div>
</section>
</main>
</body>
</html>
"""

LOGIN_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Movie Library Login</title>"""+BASE_STYLE+"""</head><body><main class="shell" style="max-width:460px;padding-top:8vh"><section class="panel panel-pad"><div class="brand" style="margin-bottom:18px"><span class="icon">🎞️</span><div>Movie Library<small>Sign in to continue</small></div></div><form method=post style="display:grid;gap:12px"><input name=username placeholder=Username autocomplete=username><input name=password type=password placeholder=Password autocomplete=current-password><button class="primary" type=submit>Login</button></form>{% if error %}<p style="color:#fecaca">{{ error }}</p>{% endif %}</section></main></body></html>
"""

INDEX_HTML = """
<!doctype html>
<html>
<head>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Movies</title>"""+BASE_STYLE+"""
</head>
<body>"""+NAV_HTML+"""
<main class="shell">
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<div class="grid-shell">
  <aside class="panel">
    <div class="panel-pad">
      <form method=get action="/" class="searchbar">
        <input name=q value="{{ q }}" type=search placeholder="Search title or collection" enterkeyhint="search">
        <button class="primary" type=submit>"""+ICON_SEARCH+"""</button>
        <input type=hidden name=sort value="{{ sort_value }}">
        <input type=hidden name=page_size value="{{ page_size }}">
      </form>

      <form method=get action="/">
        <input type=hidden name=q value="{{ q }}">
        <details class="filters movie-filter-accordion" {% if active_filter_count %}open{% endif %}>
          <summary>Movie filters{% if active_filter_count %} · {{ active_filter_count }} active{% endif %}</summary>
          <div class="filter-grid">
            <select name=sort onchange="this.form.submit()">
              {% for v,l in sort_options %}<option value="{{ v }}" {% if v==sort_value %}selected{% endif %}>Sort: {{ l }}</option>{% endfor %}
            </select>
            <select name=page_size onchange="this.form.submit()">
              {% for n in page_size_options %}<option value="{{ n }}" {% if n==page_size %}selected{% endif %}>{{ n }} per page</option>{% endfor %}
            </select>
            <div><b class=muted>Resolution</b><div class=filter-group>{% for r in resolutions %}<label class=check><input type=checkbox name=resolution value="{{ r }}" {% if r in resolutions_selected %}checked{% endif %}>{{ r }}</label>{% endfor %}</div></div>
            <div><b class=muted>File type</b><div class=filter-group>{% for ext in file_extensions %}<label class=check><input type=checkbox name=file_ext value="{{ ext }}" {% if ext in file_ext_selected %}checked{% endif %}>{{ ext }}</label>{% endfor %}</div></div>
            <div><b class=muted>Year</b><div class=filter-group>{% for y in years %}<label class=check><input type=checkbox name=year value="{{ y }}" {% if y in years_selected %}checked{% endif %}>{{ y }}</label>{% endfor %}</div></div>
            <div><b class=muted>Country</b><div class=filter-group>{% for c in countries %}<label class=check><input type=checkbox name=country value="{{ c }}" {% if c in countries_selected %}checked{% endif %}>{{ c }}</label>{% endfor %}</div></div>
            <div><b class=muted>Collection</b><div class=filter-group>{% for s in movie_sets %}<label class=check><input type=checkbox name=movie_set value="{{ s }}" {% if s in movie_sets_selected %}checked{% endif %}>{{ s }}</label>{% endfor %}</div></div>
            <div><b class=muted>Assets</b><div class=filter-group>{% for v,l in asset_status_options %}<label class=check><input type=checkbox name=asset_status value="{{ v }}" {% if v in asset_status_selected %}checked{% endif %}>{{ l }}</label>{% endfor %}</div></div>
            <div class=toolbar><button class="primary" type=submit>Apply filters</button><a class=btn href="/">Clear filters</a></div>
          </div>
        </details>
      </form>
    </div>
    <div class=muted style="padding:0 16px 8px">Showing {{ result_start }}–{{ result_end }} of {{ filtered_total }} / {{ total_all }}</div>
    <div class=list>
      {% for movie in movies %}
      <a class="item mobile-detail-link {% if selected and selected['id']==movie['id'] %}active{% endif %}" href="/?{{ build_query(filters, movie_id=movie['id']) }}" data-mobile-url="/movie/{{ movie['id'] }}?{{ build_query(filters) }}">
        <div class=thumb {% if movie['poster_path'] %}style="background-image:url('/thumb/movie/poster/{{ movie['id'] }}')"{% endif %}></div>
        <div><div class=item-title>{{ movie['title'] }}</div><div class=item-meta>{% if movie['year'] %}{{ movie['year'] }} · {% endif %}{{ movie['genres'] or movie['country'] or 'Movie' }}{% if file_size_label(movie) %} · {{ file_size_label(movie) }}{% endif %}</div><div class=item-tags>{% for t in media_tags(movie)[:4] %}<span class=chip>{{ t.text }}</span>{% endfor %}</div></div>
      </a>
      {% else %}<div class=empty>No movies found.</div>{% endfor %}
    </div>
    <div class=pagination><a class=btn href="/?{{ first_page_query }}">First</a>{% if page>1 %}<a class=btn href="/?{{ prev_page_query }}">Prev</a>{% endif %}<span class=muted>Page {{ page }} / {{ total_pages }}</span>{% if page<total_pages %}<a class=btn href="/?{{ next_page_query }}">Next</a>{% endif %}<a class=btn href="/?{{ last_page_query }}">Last</a></div>
  </aside>

  <section class="panel">
    {% if selected %}
    <div class=hero-art {% if selected['fanart_path'] %}style="background-image:url('/thumb/movie/fanart/{{ selected['id'] }}')"{% endif %}></div>
    <div class=detail>
      <div class=detail-head>
        <div class=poster {% if selected['poster_path'] %}style="background-image:url('/thumb/movie/poster/{{ selected['id'] }}')"{% endif %}></div>
        <div><h1>{{ selected['title'] }}</h1><div class=muted>{% if selected['year'] %}{{ selected['year'] }}{% endif %}{% if selected['country'] %} · {{ selected['country'] }}{% endif %}</div>{% if selected['movie_set'] %}<div class=collection-row><span class=muted>Collection</span><a class="btn collection-btn" href="/?{{ collection_query(selected['movie_set'], sort_value, page_size) }}">{{ selected['movie_set'] }}</a></div>{% endif %}<div class=chips>{% for t in selected_tags %}<span class=chip>{{ t.text }}</span>{% endfor %}{% for t in selected_asset_tags %}<span class=chip style="border-color:rgba(245,158,11,.4);color:#fde68a">{{ t.text }}</span>{% endfor %}</div><p class=plot>{{ selected['plot'] or 'No plot available.' }}</p></div>
      </div>
      <div class=info-grid><div class=info><b>Director</b>{{ selected['director'] or '—' }}</div><div class=info><b>Cast</b>{{ selected['actors'] or '—' }}</div><div class=info><b>File size</b>{{ file_size_label(selected) or '—' }}</div><div class=info><b>Media</b>{{ selected['video_codec'] or '—' }} / {{ selected['audio_codec'] or '—' }}</div><div class=info><b>Path</b>{{ selected['movie_path'] or '—' }}</div></div>
    </div>
    {% else %}<div class=empty>Select a movie to view details.</div>{% endif %}
  </section>
</div>
</main>
<script>
(function(){
  const mq = window.matchMedia('(max-width: 950px)');
  document.querySelectorAll('[data-mobile-url]').forEach(function(link){
    link.addEventListener('click', function(ev){
      if (mq.matches && link.dataset.mobileUrl) {
        ev.preventDefault();
        window.location.href = link.dataset.mobileUrl;
      }
    });
  });
})();
</script>
</body>
</html>
"""

MOVIE_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>{{ movie['title'] }}</title>"""+BASE_STYLE+"""</head><body>"""+NAV_HTML+"""<main class=shell><section class=panel><div class=hero-art {% if movie['fanart_path'] %}style="background-image:url('/image/fanart/{{ movie['id'] }}')"{% endif %}></div><div class=detail><a class="btn back-one" href="/?{{ build_query(filters) }}" aria-label="Back">"""+ICON_BACK+"""</a><div class=detail-head><div class=poster {% if movie['poster_path'] %}style="background-image:url('/thumb/movie/poster/{{ movie['id'] }}')"{% endif %}></div><div><h1>{{ movie['title'] }}</h1><div class=muted>{% if movie['year'] %}{{ movie['year'] }}{% endif %}{% if movie['country'] %} · {{ movie['country'] }}{% endif %}</div>{% if movie['movie_set'] %}<div class=collection-row><span class=muted>Collection</span><a class="btn collection-btn" href="/?{{ collection_query(movie['movie_set'], sort_value, page_size) }}">{{ movie['movie_set'] }}</a></div>{% endif %}<div class=chips>{% for t in tags %}<span class=chip>{{ t.text }}</span>{% endfor %}{% for t in asset_tags %}<span class=chip style="border-color:rgba(245,158,11,.4);color:#fde68a">{{ t.text }}</span>{% endfor %}</div><p class=plot>{{ movie['plot'] or 'No plot available.' }}</p></div></div><div class=info-grid><div class=info><b>Director</b>{{ movie['director'] or '—' }}</div><div class=info><b>Cast</b>{{ movie['actors'] or '—' }}</div><div class=info><b>File size</b>{{ file_size_label(movie) or '—' }}</div><div class=info><b>Media</b>{{ movie['video_codec'] or '—' }} / {{ movie['audio_codec'] or '—' }}</div><div class=info><b>Path</b>{{ movie['movie_path'] or '—' }}</div></div></div></section></main></body></html>
"""

TV_SEASON_SCRIPT = """
<script>
(function(){
  function activateSeason(card){
    const key = card.dataset.seasonKey;
    const label = card.dataset.seasonLabel || 'Episode guide';
    document.querySelectorAll('.season-switch').forEach(function(el){ el.classList.toggle('active', el === card); });
    document.querySelectorAll('.episode-pane').forEach(function(pane){ pane.classList.toggle('active', pane.dataset.seasonKey === key); });
    const title = document.getElementById('episode-title');
    if (title) title.textContent = label;
    const href = card.getAttribute('href');
    if (href && window.history && window.history.replaceState) window.history.replaceState(null, '', href);
  }
  document.querySelectorAll('.season-switch').forEach(function(card){
    card.addEventListener('click', function(ev){ ev.preventDefault(); activateSeason(card); });
  });
  const mq = window.matchMedia('(max-width: 950px)');
  document.querySelectorAll('[data-mobile-url]').forEach(function(link){
    link.addEventListener('click', function(ev){
      if (mq.matches && link.dataset.mobileUrl) {
        ev.preventDefault();
        window.location.href = link.dataset.mobileUrl;
      }
    });
  });
})();
</script>
"""

TV_DETAIL_BLOCK = """
{% if selected %}
<div class=hero-art {% if selected['fanart_path'] %}style="background-image:url('/thumb/tv/fanart/{{ selected['id'] }}')"{% endif %}></div>
<div class=detail>
  <a class="btn back-one" href="{{ back_url }}" aria-label="Back">"""+ICON_BACK+"""</a>
  <div class=detail-head>
    <div class=poster {% if selected['poster_path'] %}style="background-image:url('/thumb/tv/poster/{{ selected['id'] }}')"{% endif %}></div>
    <div>
      <h1>{{ selected['title'] }}</h1>
      <div class=muted>{% if selected['year'] %}{{ selected['year'] }}{% endif %}{% if selected['status'] %} · {{ selected['status'] }}{% endif %}{% if season_count %} · {{ season_count }} seasons{% endif %}{% if episode_count %} · {{ episode_count }} episodes{% endif %}</div>
      <div class=chips>{% for g in split_csv_values(selected['genres']) %}<span class=chip>{{ g }}</span>{% endfor %}{% for m in tv_missing_assets(selected) %}<span class=chip style="border-color:rgba(245,158,11,.4);color:#fde68a">{{ m }}</span>{% endfor %}</div>
      <p class=plot>{{ selected['plot'] or 'No plot available.' }}</p>
    </div>
  </div>
  <div class=season-layout>
    <aside>
      <h2>Seasons</h2>
      <div class=season-list>
        {% for season in season_blocks %}
        <a class="season-card season-switch {% if season.is_selected %}active{% endif %}" data-season-key="{{ loop.index0 }}" data-season-label="{{ season.label }}" href="{{ season_href_base }}{{ season.season_number if season.season_number is not none else -1 }}">
          <div class=season-poster {% if season.poster_available %}style="background-image:url('/thumb/tv/season-poster-{{ season.season_number }}/{{ selected['id'] }}')"{% elif selected['poster_path'] %}style="background-image:url('/thumb/tv/poster/{{ selected['id'] }}')"{% endif %}></div>
          <div><b>{{ season.label }}</b><div class=muted>{{ season.episode_count }} episodes</div></div>
        </a>
        {% else %}<div class=empty>No seasons found.</div>{% endfor %}
      </div>
    </aside>
    <section>
      <h2 id=episode-title>{{ selected_season_label or 'Episode guide' }}</h2>
      <div class=muted style="margin:-4px 0 10px">Scroll this episode guide within the selected season.</div>
      {% for season in season_blocks %}
      <div class="episodes episode-pane {% if season.is_selected %}active{% endif %}" data-season-key="{{ loop.index0 }}">
        {% for ep in season.episodes %}
        <article class=episode>
          <div class=epno>S{{ '%02d'|format(ep['season_number'] or 0) }}E{{ '%02d'|format(ep['episode_number'] or 0) }}</div>
          <div><b>{{ ep['title'] or 'Episode' }}</b><div class=episode-meta-tags>{% for t in media_tags(ep) %}<span class=chip>{{ t.text }}</span>{% endfor %}</div>{% if ep['plot'] %}<p>{{ ep['plot'] }}</p>{% endif %}</div>
          <div class=muted>{% if ep['air_date'] %}{{ ep['air_date'] }}{% endif %}</div>
        </article>
        {% else %}<div class=empty>No episodes found for this season.</div>{% endfor %}
      </div>
      {% else %}<div class=empty>No episode guide found.</div>{% endfor %}
    </section>
  </div>
</div>
{% else %}<div class=empty>Select a TV show to view seasons and episodes.</div>{% endif %}
"""

TV_HTML = """
<!doctype html>
<html>
<head>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>TV Shows</title>"""+BASE_STYLE+"""
</head>
<body>"""+NAV_HTML+"""
<main class=shell>
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<div class=grid-shell>
  <aside class=panel>
    <div class="panel-pad tv-intro">
      <div><h2 style="margin-bottom:4px">TV Shows</h2>
      <div class=muted>Browse your TV library. Select a show to view its seasons and episode guide.</div></div>
      <span class=count-pill>{{ total_shows }} shows</span>
    </div>
    <div class=panel-pad style="padding-top:0">
      <form method=get action="/tv" class="searchbar">
        <input name=q value="{{ q }}" type=search placeholder="Search TV show name" enterkeyhint="search">
        <button class="primary" type=submit>"""+ICON_SEARCH+"""</button>
      </form>
    </div>
    {% if q %}<div class=muted style="padding:0 16px 8px">Showing {{ shows|length }} of {{ total_shows }} TV shows for “{{ q }}”</div>{% endif %}
    <div class=list>
      {% for show in shows %}
      <a class="item mobile-detail-link {% if selected and selected['id']==show['id'] %}active{% endif %}" href="/tv{{ build_tv_query(show_id=show['id'], season=None) }}" data-mobile-url="/tv-show/{{ show['id'] }}{{ build_tv_query(show_id=None, season=None) }}">
        <div class=thumb {% if show['poster_path'] %}style="background-image:url('/thumb/tv/poster/{{ show['id'] }}')"{% endif %}></div>
        <div><div class=item-title>{{ show['title'] }}</div><div class=item-meta>{% if show['year'] %}{{ show['year'] }} · {% endif %}{{ show['genres'] or show['status'] or 'TV show' }}</div><div class=item-tags>{% for missing in tv_missing_assets(show)[:2] %}<span class=chip style="border-color:rgba(245,158,11,.4);color:#fde68a">{{ missing }}</span>{% endfor %}</div></div>
      </a>
      {% else %}<div class=empty>No TV shows found.</div>{% endfor %}
    </div>
  </aside>
  <section class=panel>
"""+TV_DETAIL_BLOCK+"""
  </section>
</div>
</main>
"""+TV_SEASON_SCRIPT+"""
</body>
</html>
"""

TV_SHOW_HTML = """
<!doctype html>
<html>
<head>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{{ selected['title'] if selected else 'TV Show' }}</title>"""+BASE_STYLE+"""
</head>
<body>"""+NAV_HTML+"""
<main class=shell>
  <section class=panel>
"""+TV_DETAIL_BLOCK+"""
  </section>
</main>
"""+TV_SEASON_SCRIPT+"""
</body>
</html>
"""


ADMIN_TABS_HTML = """
<nav class=admin-console-tabs aria-label="Admin sections">
  <a class="{{ 'active' if admin_section=='overview' else '' }}" href="/admin">Overview</a>
  <a class="{{ 'active' if admin_section=='libraries' else '' }}" href="/source-diagnostics">Libraries</a>
  <a class="{{ 'active' if admin_section=='alerts' else '' }}" href="/reports/alerts">Alerts</a>
  <a class="{{ 'active' if admin_section=='scans' else '' }}" href="/admin/scans">Scans</a>
  <a class="{{ 'active' if admin_section=='server' else '' }}" href="/server-control">Server</a>
  <a class="{{ 'active' if admin_section=='users' else '' }}" href="/admin/users">Users</a>
  <a class="{{ 'active' if admin_section=='settings' else '' }}" href="/settings">Settings</a>
</nav>
"""

ADMIN_USERS_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Admin Users</title>"""+BASE_STYLE+"""
<style>
.users-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,.7fr);gap:14px}.viewer-account-card{margin-top:12px;padding:14px;border:1px solid rgba(148,163,184,.18);border-radius:18px;background:rgba(2,6,23,.35)}.viewer-account-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.viewer-status{padding:5px 9px;border-radius:999px;border:1px solid rgba(34,197,94,.3);color:#bbf7d0;background:rgba(22,163,74,.10);font-size:12px}.viewer-status.off{border-color:rgba(248,113,113,.35);color:#fecaca;background:rgba(239,68,68,.10)}.users-note{padding:12px;border-radius:16px;background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.2);color:#bfdbfe}@media(max-width:900px){.users-grid{grid-template-columns:1fr}.viewer-account-head{display:block}}
</style>
</head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<section class="panel settings-hero">
  <div><h1>Users</h1><p class=muted style="margin:0">Manage who can administer the library and who can only browse Movies and TV Shows.</p></div>
  <div class=settings-hero-actions><a class=btn href="/admin">Admin overview</a></div>
</section>
<div class=users-grid>
  <section class="panel settings-card">
    <h2>Viewer accounts</h2>
    <p class=muted>Viewer usernames are not case-sensitive. Viewers can browse Movies and TV Shows only; they cannot access admin pages, scans, exports, reports, restarts, or settings.</p>
    {% for viewer in viewers %}
    <div class=viewer-account-card>
      <div class=viewer-account-head><div><h3 style="margin:0">{{ viewer.username }}</h3><p class=muted style="margin:4px 0 0">Viewer account #{{ viewer.index }}</p></div><span class="viewer-status {{ '' if viewer.enabled else 'off' }}">{{ 'Enabled' if viewer.enabled else 'Disabled' }}</span></div>
      <form method=post action="/settings/viewer/{{ viewer.index }}" style="margin-top:12px">
        <input name=viewer_username value="{{ viewer.username }}" placeholder="Viewer username">
        <input name=viewer_password type=password placeholder="New password (leave blank to keep current)">
        <label class=check><input type=checkbox name=enabled {% if viewer.enabled %}checked{% endif %}> Enabled</label>
        <button class=primary>Save viewer</button>
      </form>
      <form method=post action="/settings/viewer/{{ viewer.index }}/remove" onsubmit="return confirm('Remove this viewer account?');" style="margin-top:8px">
        <button class="btn danger">Remove viewer</button>
      </form>
    </div>
    {% else %}<p class=muted>No viewer accounts configured.</p>{% endfor %}

    <div style="margin-top:14px;padding:14px;border:1px solid rgba(34,197,94,.22);border-radius:18px;background:rgba(22,163,74,.08)">
      <h3 style="margin-top:0">+ Add viewer</h3>
      <form method=post action="/settings/viewer/add">
        <input name=viewer_username placeholder="Viewer username">
        <input name=viewer_password type=password placeholder="Viewer password">
        <label class=check><input type=checkbox name=enabled checked> Enabled</label>
        <button class=primary>Add viewer</button>
      </form>
    </div>
  </section>

  <aside class="panel settings-card">
    <h2>Admin account</h2>
    <p class=muted>The admin can manage folders, scans, scheduled tasks, alerts, users, and server controls.</p>
    <form method=post action="/settings/web">
      <input name=username value="{{ username }}" placeholder="Admin username">
      <input name=password type=password placeholder="New admin password (optional)">
      <h3>Server port</h3>
      <input name=port value="{{ port }}" placeholder="Port">
      <button class=primary>Save admin and web settings</button>
    </form>
    <div class=users-note style="margin-top:14px">
      Default viewer account remains <b>Laila</b>. Usernames are matched case-insensitively, so Laila, laila, and LAILA are treated as the same viewer name.
    </div>
  </aside>
</div>
</main></body></html>
"""


DASHBOARD_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Library Dashboard</title>"""+BASE_STYLE+"""
<script>
function refreshScanStatus(){
  fetch('/scan-status', {cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
    var box=document.getElementById('dashboard-scan-status'); if(!box) return;
    box.className='scan-state '+(s.running?'running':(s.ok?'done':'error'));
    box.innerHTML='<strong>'+escapeHtml(s.label || 'Scan status')+'</strong><div>'+escapeHtml(s.message || '')+'</div><div class="muted">Started: '+escapeHtml(s.started || '—')+' · Finished: '+escapeHtml(s.finished || '—')+'</div>';
  }).catch(function(){});
}
function escapeHtml(v){return String(v).replace(/[&<>'"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c];});}
setInterval(refreshScanStatus, 2500);
window.addEventListener('load', refreshScanStatus);
</script>
<style>
.dashboard-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:14px 0}.dashboard-main{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(340px,.8fr);gap:14px;align-items:start}.health-list{display:grid;gap:10px}.health-card{padding:13px;border:1px solid rgba(148,163,184,.16);border-radius:18px;background:rgba(2,6,23,.30)}.health-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.status-pill{display:inline-flex;align-items:center;border-radius:999px;padding:5px 9px;font-size:12px;font-weight:800;border:1px solid rgba(148,163,184,.20)}.status-pill.good{color:#bbf7d0;background:rgba(34,197,94,.11);border-color:rgba(34,197,94,.32)}.status-pill.warn{color:#fde68a;background:rgba(245,158,11,.10);border-color:rgba(245,158,11,.32)}.status-pill.danger{color:#fecaca;background:rgba(244,63,94,.12);border-color:rgba(244,63,94,.32)}.source-path{font-size:12px;color:var(--muted);word-break:break-all;margin:7px 0}.metric-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:10px 0}.metric{padding:9px;border-radius:13px;background:rgba(15,23,42,.48);border:1px solid rgba(148,163,184,.10)}.metric b{display:block;font-size:18px}.metric span{display:block;color:var(--muted);font-size:11px;margin-top:2px}.dashboard-side{display:grid;gap:14px}.history-compact{display:grid;gap:8px}.history-item{padding:10px;border-radius:14px;background:rgba(2,6,23,.28);border:1px solid rgba(148,163,184,.12)}.warning-list{display:grid;gap:8px;margin-top:10px}.warning-item{padding:9px 10px;border-radius:13px;border:1px solid rgba(245,158,11,.25);background:rgba(245,158,11,.08);color:#fde68a}@media(max-width:1050px){.dashboard-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.dashboard-main{grid-template-columns:1fr}}@media(max-width:650px){.dashboard-grid,.metric-row{grid-template-columns:1fr}.health-top{display:block}.health-card .toolbar{display:grid}.settings-hero-actions{display:grid;width:100%}}
</style>
</head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<section class="panel settings-hero">
  <div><h1>Admin</h1><p class=muted style="margin:0">One organised console for libraries, alerts, scans, users, server controls, and settings.</p></div>
  <div class=settings-hero-actions><a class="btn good" href="/scan-wait/refresh-all">Check all libraries</a><a class="btn warn" href="/reports/alerts">View alerts</a><a class=btn href="/missing-items">Review missing</a></div>
</section>

<div class=dashboard-grid>
  <section class="panel mini-stat"><b>{{ data.about.movie_count }}</b><span>Movies in database</span></section>
  <section class="panel mini-stat"><b>{{ data.about.tv_show_count }}</b><span>TV shows</span></section>
  <section class="panel mini-stat"><b>{{ data.about.episode_count }}</b><span>TV episodes</span></section>
  <section class="panel mini-stat"><b>{{ data.missing.movies + data.missing.episodes }}</b><span>Missing items awaiting review</span></section>
  <section class="panel mini-stat"><b>{{ data.alert_summary.visible_total }}</b><span>Visible alerts</span></section>
  <section class="panel mini-stat"><b>{{ data.alert_summary.review_total }}</b><span>Marked for review</span></section>
</div>


<section class=panel style="margin-top:14px">
  <h2>Admin sections</h2>
  <div class=admin-section-grid>
    <div class=admin-section-card><h3>Libraries</h3><p>Manage monitored movie and TV folders and view cached source health without slow folder walking.</p><div class=toolbar><a class="btn primary" href="/source-diagnostics">Open Libraries</a></div></div>
    <div class=admin-section-card><h3>Alerts</h3><p>Review current library issues in the browser. {{ data.alert_summary.visible_total }} visible, {{ data.alert_summary.review_total }} marked for review.</p><div class=toolbar><a class="btn primary" href="/reports/alerts">View alerts</a><a class="btn warn" href="/reports/alerts?queue=review">Review queue</a></div></div>
    <div class=admin-section-card><h3>Scans</h3><p>Run library checks, review scan history, scheduled refresh, and missing item cleanup in one place.</p><div class=toolbar><a class="btn primary" href="/admin/scans">Open Scans</a></div></div>
    <div class=admin-section-card><h3>Server</h3><p>Check local/remote URLs, Caddy status, and use the only visible restart controls.</p><div class=toolbar><a class="btn primary" href="/server-control">Open Server</a></div></div>
    <div class=admin-section-card><h3>Users</h3><p>Manage admin login and viewer accounts, including extra viewers.</p><div class=toolbar><a class="btn primary" href="/admin/users">Manage Users</a></div></div>
    <div class=admin-section-card><h3>Settings</h3><p>Keep advanced configuration, version/about details, and Mac app readiness separate from daily admin actions.</p><div class=toolbar><a class="btn primary" href="/settings">Open Settings</a><a class=btn href="/admin/runtime-paths">Runtime paths</a><a class=btn href="/admin/mac-app-readiness">Mac app readiness</a><a class=btn href="/admin/first-run-setup">First-run preview</a><a class=btn href="/admin/migration-safety">Migration safety</a><a class=btn href="/admin/migration-dry-run">Migration dry run</a><a class=btn href="/admin/migration-backup">Migration backup</a><a class=btn href="/admin/migration-backup-verify">Verify backup</a><a class=btn href="/admin/migration-stage">Migration stage</a><a class=btn href="/admin/migration-stage-verify">Verify stage</a><a class=btn href="/admin/migration-cutover-readiness">Cutover readiness</a><a class=btn href="/admin/migration-cutover-plan">Cutover plan</a><a class=btn href="/admin/dmg-packaging">DMG packaging</a><a class=btn href="/admin/package-manifest">Package manifest</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/admin/test-dmg-status">Test DMG status</a><a class=btn href="/admin/app-support-prep">App Support prep</a><a class=btn href="/admin/app-support-status">App Support status</a></div></div>
  </div>
</section>
<div class=dashboard-main>
  <section class="panel settings-card">
    <h2>🎬 Movies library sources</h2>
    <p class=muted>Each card is a monitored folder behind the single Movies library.</p>
    <div class=health-list>
      {% for source in data.movie_sources %}
      <article class=health-card>
        <div class=health-top><div><h3 style="margin:0">{{ source.name }}</h3><p class=source-path>{{ source.path }}</p></div><span class="status-pill {{ source.status_class }}">{{ source.status_text }}</span></div>
        <div class=metric-row><div class=metric><b>{{ source.total_items }}</b><span>Movies</span></div><div class=metric><b>{{ source.missing_items }}</b><span>Missing</span></div><div class=metric><b>{{ source.last_signature_update }}</b><span>Last folder check</span></div></div>
        <p class=muted>Configured last scan: {{ source.last_scanned or 'Never' }} · {{ 'Enabled' if source.enabled else 'Disabled' }}</p>
        <div class=toolbar><a class="btn good" href="{{ source.check_url }}">Check now</a><a class=btn href="{{ source.manage_url }}">Manage folders</a></div>
      </article>
      {% else %}<p class=muted>No movie folders configured yet.</p>{% endfor %}
    </div>
  </section>

  <aside class=dashboard-side>
    <section class="panel scan-card">
      <h2>⏱️ Current scan status</h2>
      <div id=dashboard-scan-status class="scan-state {{ 'running' if data.scan_status.running else ('done' if data.scan_status.ok else 'error') }}"><strong>{{ data.scan_status.label }}</strong><div>{{ data.scan_status.message }}</div><div class=muted>Started: {{ data.scan_status.started or '—' }} · Finished: {{ data.scan_status.finished or '—' }}</div></div>
    </section>
    <section class="panel settings-card">
      <h2>🕒 Scheduled task</h2>
      <div class=kv><div><b>Schedule</b><span>{{ data.scheduled.next_text }}</span></div><div><b>Last run</b><span>{{ data.scheduled.last_run_at }}</span></div><div><b>Last result</b><span>{{ data.scheduled.last_result }}</span></div></div>
      <div class=toolbar><a class=btn href="/settings#scheduled-tasks">Edit schedule</a></div>
    </section>
    <section class="panel settings-card">
      <h2>⚠️ Attention</h2>
      <div class=about-grid><div class=mini-stat><b>{{ data.alert_summary.visible_total }}</b><span>Open alerts</span></div><div class=mini-stat><b>{{ data.alert_summary.ignored_total }}</b><span>Ignored alerts</span></div><div class=mini-stat><b>{{ data.alert_summary.movie_total }}</b><span>Movie alerts</span></div><div class=mini-stat><b>{{ data.alert_summary.tv_total }}</b><span>TV alerts</span></div></div>
      <div class=warning-list>{% for warning in data.warnings %}<div class=warning-item>{{ warning }}</div>{% else %}<p class=muted>No source warnings right now.</p>{% endfor %}</div>
      <div class=toolbar><a class="btn warn" href="/missing-items">Review missing</a><a class=btn href="/reports/alerts">View alerts</a></div>
    </section>
  </aside>
</div>

<div class=dashboard-main style="margin-top:14px">
  <section class="panel settings-card">
    <h2>📺 TV Shows library sources</h2>
    <p class=muted>Each card is a monitored folder behind the single TV Shows library.</p>
    <div class=health-list>
      {% for source in data.tv_sources %}
      <article class=health-card>
        <div class=health-top><div><h3 style="margin:0">{{ source.name }}</h3><p class=source-path>{{ source.path }}</p></div><span class="status-pill {{ source.status_class }}">{{ source.status_text }}</span></div>
        <div class=metric-row><div class=metric><b>{{ source.show_count }}</b><span>Shows</span></div><div class=metric><b>{{ source.episode_count }}</b><span>Episodes</span></div><div class=metric><b>{{ source.missing_items }}</b><span>Missing episodes</span></div></div>
        <p class=muted>Configured last scan: {{ source.last_scanned or 'Never' }} · Last folder check: {{ source.last_signature_update }} · {{ 'Enabled' if source.enabled else 'Disabled' }}</p>
        <div class=toolbar><a class="btn good" href="{{ source.check_url }}">Check now</a><a class=btn href="{{ source.manage_url }}">Manage folders</a></div>
      </article>
      {% else %}<p class=muted>No TV folders configured yet.</p>{% endfor %}
    </div>
  </section>

  <aside class=dashboard-side>
    <section class="panel settings-card">
      <h2>🧾 Recent scan history</h2>
      <div class=history-compact>
        {% for scan in data.history %}
        <div class=history-item><b>{{ scan.label }}</b><p class=muted style="margin:4px 0 0">{{ scan.finished_at or scan.started_at }} · {{ 'OK' if scan.ok else 'Failed' }}</p></div>
        {% else %}<p class=muted>No scan history yet.</p>{% endfor %}
      </div>
      <div class=toolbar><a class=btn href="/scan-history">Full history</a></div>
    </section>
    <section class="panel settings-card">
      <h2>⚙️ Quick actions</h2>
      <div class=toolbar><a class="btn good" href="/scan-wait/refresh-movies">Check Movies</a><a class="btn good" href="/scan-wait/refresh-tv">Check TV Shows</a><a class=btn href="/source-diagnostics">Manage libraries</a><a class=btn href="/reports">Reports</a><a class=btn href="/server-control">Server</a><a class="btn danger" href="/missing-items">Missing items</a></div>
    </section>
  </aside>
</div>
</main></body></html>
"""


SOURCE_DIAGNOSTICS_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Source Diagnostics</title>"""+BASE_STYLE+"""
<style>
.diag-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:14px 0}.diag-grid{display:grid;grid-template-columns:1fr;gap:14px}.diag-card{padding:16px;border:1px solid rgba(148,163,184,.16);border-radius:20px;background:rgba(2,6,23,.30)}.diag-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.diag-path{font-size:12px;color:var(--muted);word-break:break-all;margin:7px 0}.diag-counts{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin:12px 0}.diag-count{padding:10px;border-radius:14px;background:rgba(15,23,42,.50);border:1px solid rgba(148,163,184,.10)}.diag-count b{display:block;font-size:20px}.diag-count span{display:block;color:var(--muted);font-size:11px;margin-top:2px}.status-pill{display:inline-flex;align-items:center;border-radius:999px;padding:5px 9px;font-size:12px;font-weight:800;border:1px solid rgba(148,163,184,.20)}.status-pill.good{color:#bbf7d0;background:rgba(34,197,94,.11);border-color:rgba(34,197,94,.32)}.status-pill.warn{color:#fde68a;background:rgba(245,158,11,.10);border-color:rgba(245,158,11,.32)}.status-pill.danger{color:#fecaca;background:rgba(244,63,94,.12);border-color:rgba(244,63,94,.32)}.diag-detail{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.diag-box{padding:11px;border-radius:15px;border:1px solid rgba(148,163,184,.12);background:rgba(2,6,23,.24)}.diag-box h4{margin:0 0 8px}.diag-box ul{margin:0;padding-left:18px;color:var(--muted);font-size:12px;line-height:1.45}.diag-box li{word-break:break-all;margin-bottom:4px}.diag-note{padding:10px 12px;border-radius:14px;border:1px solid rgba(56,189,248,.22);background:rgba(14,165,233,.08);color:#e0f2fe;margin-top:10px}@media(max-width:1050px){.diag-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.diag-counts{grid-template-columns:repeat(3,minmax(0,1fr))}.diag-detail{grid-template-columns:1fr}}@media(max-width:650px){.diag-summary,.diag-counts{grid-template-columns:1fr}.diag-head{display:block}.settings-hero-actions,.diag-card .toolbar{display:grid;width:100%}}
</style>
</head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<section class="panel settings-hero">
  <div><h1>Admin · Libraries</h1><p class=muted style="margin:0">Admin view of monitored folders, change-cache status, database counts, and folders waiting for the next scan.</p></div>
  <div class=settings-hero-actions><a class="btn good" href="/scan-wait/refresh-all">Check all now</a><a class=btn href="/source-diagnostics?deep=1">Run detailed comparison</a><a class=btn href="/settings">Folder settings</a></div>
</section>

<div class=diag-summary>
  <section class="panel mini-stat"><b>{{ data.total_sources }}</b><span>Total configured sources</span></section>
  <section class="panel mini-stat"><b>{{ data.healthy_sources }}</b><span>Healthy sources</span></section>
  <section class="panel mini-stat"><b>{{ data.attention_sources }}</b><span>Need attention</span></section>
  <section class="panel mini-stat"><b>{{ data.changed_sources if data.deep else "—" }}</b><span>{{ "Have folder changes" if data.deep else "Detailed check not run" }}</span></section>
</div>

<section class="panel settings-card">
  <h2>How change checks work</h2>
  <p class=muted>The scanner stores a lightweight signature for each media folder using filenames, modified times, and file sizes. This page now loads quickly using the database and cached scan signatures only. Press <b>Run detailed comparison</b> when you want to walk the folders and compare current files against the cache. Normal scheduled/manual scans still do the real change check.</p>
  <div class=diag-note>Deleted video files are still marked as missing first. They are only removed from the database if you later use the safe clean-missing option.</div>
</section>

<section class="panel settings-card" style="margin-top:14px"><h2>🎬 Movies sources</h2></section>
<div class=diag-grid>
{% for source in data.movie_sources %}
  <article class=diag-card>
    <div class=diag-head><div><h2 style="margin:0">{{ source.name }}</h2><p class=diag-path>{{ source.path }}</p></div><span class="status-pill {{ source.status_class }}">{{ source.status_text }}</span></div>
    <div class=diag-counts>
      <div class=diag-count><b>{{ source.database.primary_count }}</b><span>{{ source.database.primary_label }} in DB</span></div>
      <div class=diag-count><b>{{ source.media_file_count if source.deep else "—" }}</b><span>{{ "Video files currently seen" if source.deep else "Skipped for speed" }}</span></div>
      <div class=diag-count><b>{{ source.unchanged_folder_count if source.deep else "—" }}</b><span>{{ "Unchanged folders" if source.deep else "Detailed only" }}</span></div>
      <div class=diag-count><b>{{ source.changed_folder_count if source.deep else "—" }}</b><span>{{ "Changed folders" if source.deep else "Detailed only" }}</span></div>
      <div class=diag-count><b>{{ source.new_folder_count if source.deep else "—" }}</b><span>{{ "New folders" if source.deep else "Detailed only" }}</span></div>
      <div class=diag-count><b>{{ source.missing_cached_folder_count if source.deep else "—" }}</b><span>{{ "Missing cached folders" if source.deep else "Detailed only" }}</span></div>
    </div>
    <p class=muted>Enabled: {{ 'Yes' if source.enabled else 'No' }} · Config last scan: {{ source.last_scanned or 'Never' }} · Last signature update: {{ source.last_signature_update }} · Cached folders: {{ source.cached_folder_count }}</p>
    {% if source.deep %}<div class=diag-detail>
      <div class=diag-box><h4>Changed folders</h4><ul>{% for item in source.changed_samples %}<li>{{ item }}</li>{% else %}<li>None detected</li>{% endfor %}</ul></div>
      <div class=diag-box><h4>New folders</h4><ul>{% for item in source.new_samples %}<li>{{ item }}</li>{% else %}<li>None detected</li>{% endfor %}</ul></div>
      <div class=diag-box><h4>Missing cached folders</h4><ul>{% for item in source.missing_cached_samples %}<li>{{ item }}</li>{% else %}<li>None detected</li>{% endfor %}</ul></div>
    </div>{% else %}<div class=diag-note>Fast view: current folder walking is skipped so the Libraries tab opens quickly. Use <b>Run detailed comparison</b> or <b>Check this source</b> when needed.</div>{% endif %}
    <div class=toolbar><a class="btn good" href="{{ source.check_url }}">Check this source</a><a class=btn href="{{ source.manage_url }}">Manage folders</a><a class="btn danger" href="{{ source.rebuild_url }}">Clean rebuild this source</a></div>
  </article>
{% else %}<p class=muted>No movie sources configured.</p>{% endfor %}
</div>

<section class="panel settings-card" style="margin-top:14px"><h2>📺 TV Shows sources</h2></section>
<div class=diag-grid>
{% for source in data.tv_sources %}
  <article class=diag-card>
    <div class=diag-head><div><h2 style="margin:0">{{ source.name }}</h2><p class=diag-path>{{ source.path }}</p></div><span class="status-pill {{ source.status_class }}">{{ source.status_text }}</span></div>
    <div class=diag-counts>
      <div class=diag-count><b>{{ source.database.secondary_count }}</b><span>Shows in DB</span></div>
      <div class=diag-count><b>{{ source.database.primary_count }}</b><span>Episodes in DB</span></div>
      <div class=diag-count><b>{{ source.unchanged_folder_count if source.deep else "—" }}</b><span>{{ "Unchanged folders" if source.deep else "Detailed only" }}</span></div>
      <div class=diag-count><b>{{ source.changed_folder_count if source.deep else "—" }}</b><span>{{ "Changed folders" if source.deep else "Detailed only" }}</span></div>
      <div class=diag-count><b>{{ source.new_folder_count if source.deep else "—" }}</b><span>{{ "New folders" if source.deep else "Detailed only" }}</span></div>
      <div class=diag-count><b>{{ source.missing_cached_folder_count if source.deep else "—" }}</b><span>{{ "Missing cached folders" if source.deep else "Detailed only" }}</span></div>
    </div>
    <p class=muted>Enabled: {{ 'Yes' if source.enabled else 'No' }} · Config last scan: {{ source.last_scanned or 'Never' }} · Last signature update: {{ source.last_signature_update }} · Cached folders: {{ source.cached_folder_count }} · Current video files: {{ source.media_file_count if source.deep else "not checked on this fast page load" }}</p>
    {% if source.deep %}<div class=diag-detail>
      <div class=diag-box><h4>Changed folders</h4><ul>{% for item in source.changed_samples %}<li>{{ item }}</li>{% else %}<li>None detected</li>{% endfor %}</ul></div>
      <div class=diag-box><h4>New folders</h4><ul>{% for item in source.new_samples %}<li>{{ item }}</li>{% else %}<li>None detected</li>{% endfor %}</ul></div>
      <div class=diag-box><h4>Missing cached folders</h4><ul>{% for item in source.missing_cached_samples %}<li>{{ item }}</li>{% else %}<li>None detected</li>{% endfor %}</ul></div>
    </div>{% else %}<div class=diag-note>Fast view: current folder walking is skipped so the Libraries tab opens quickly. Use <b>Run detailed comparison</b> or <b>Check this source</b> when needed.</div>{% endif %}
    <div class=toolbar><a class="btn good" href="{{ source.check_url }}">Check this source</a><a class=btn href="{{ source.manage_url }}">Manage folders</a><a class="btn danger" href="{{ source.rebuild_url }}">Clean rebuild this source</a></div>
  </article>
{% else %}<p class=muted>No TV sources configured.</p>{% endfor %}
</div>
</main></body></html>
"""

SETTINGS_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Settings</title>"""+BASE_STYLE+"""
<script>
function refreshScanStatus(){
  fetch('/scan-status', {cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
    var box=document.getElementById('scan-status-box'); if(!box) return;
    box.className='scan-state '+(s.running?'running':(s.ok?'done':'error'));
    box.innerHTML='<strong>'+escapeHtml(s.label || 'Scan status')+'</strong><div>'+escapeHtml(s.message || '')+'</div><div class="muted">Started: '+escapeHtml(s.started || '—')+' · Finished: '+escapeHtml(s.finished || '—')+'</div>';
  }).catch(function(){});
}
function escapeHtml(v){return String(v).replace(/[&<>'"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c];});}
setInterval(refreshScanStatus, 2000);
window.addEventListener('load', refreshScanStatus);
</script>
<style>
.library-dialog{max-width:920px;width:min(920px,94vw);max-height:88vh;overflow:auto;border:1px solid rgba(148,163,184,.28);border-radius:22px;background:#0f172a;color:#e5e7eb;padding:22px;box-shadow:0 24px 90px rgba(0,0,0,.55)}
.library-dialog::backdrop{background:rgba(2,6,23,.72);backdrop-filter:blur(4px)}
.dialog-close{float:right}
.library-dialog form{margin-top:8px}
.library-dialog .toolbar form{display:inline}
</style>

</head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<section class="panel settings-hero">
  <div>
    <h1>Admin · Settings</h1>
    <p class=muted style="margin:0">Advanced configuration. Daily actions are organised under Libraries, Alerts, Scans, Server, and Users.</p>
  </div>
  <div class=settings-hero-actions>
    <a class="btn primary" href="/admin">Admin overview</a>
    <a class=btn href="/about">About / version</a>
    <a class=btn href="/admin/mac-app-readiness">Mac app readiness</a>
  </div>
</section>
<div class=settings-grid>
  <section class="panel settings-card" id="movies-library">
    <h2>🎬 Movies</h2>
    <p class=muted>One Movies library, with as many monitored folders as you need behind it.</p>
    <div class=about-grid>
      <div class=mini-stat><b>{{ movie_sources|length }}</b><span>folder{{ '' if movie_sources|length == 1 else 's' }} monitored</span></div>
      <div class=mini-stat><b>{{ movie_enabled_count }}</b><span>enabled</span></div>
      <div class=mini-stat><b>{{ movie_last_scanned }}</b><span>last scan</span></div>
    </div>
    <div class=toolbar><button class="btn primary" type=button onclick="document.getElementById('movies-dialog').showModal()">Manage folders</button><a class="btn good" href="/scan-wait/refresh-movies">"""+ICON_REFRESH+""" Check Movies for changes</a><a class="btn danger" href="/scan-wait/rebuild-movies">Clean rebuild Movies</a></div>
    <dialog id=movies-dialog class=library-dialog>
      <form method=dialog><button class="btn dialog-close">Close</button></form>
      <h2>Manage Movie Folders</h2>
      <p class=muted>These folders are all part of the single Movies library. Add another folder only when your films are stored in another location.</p>
      {% if not movie_sources %}<p class=muted>No movie folders have been added yet.</p>{% endif %}
      {% for source in movie_sources %}
      <div style="margin-top:12px;padding:12px;border:1px solid rgba(148,163,184,.18);border-radius:16px;background:rgba(2,6,23,.35)">
        <b>{{ source.name }}</b> <span class=muted>· {{ 'Enabled' if source.enabled else 'Disabled' }}</span>
        <p class=path>{{ source.path }}<br><span class=muted>Last checked: {{ source.last_scanned or 'Never' }}</span></p>
        <form method=post action="/settings/library-source/{{ source.index }}"><input name=name value="{{ source.name }}" placeholder="Folder name"><input name=folder value="{{ source.path }}" placeholder="/Volumes/.../Movies"><label class=check><input type=checkbox name=enabled {% if source.enabled %}checked{% endif %}> Enabled</label><button class=primary>Save</button></form>
        <div class=toolbar><a class="btn good" href="/scan-wait/refresh-movies-{{ source.index }}">Check now</a><a class="btn danger" href="/scan-wait/rebuild-movies-{{ source.index }}">Clean rebuild this folder</a><form method=post action="/settings/library-source/{{ source.index }}/remove" onsubmit="return confirm('Remove this movie folder from monitoring? The files are not deleted.');"><button class="btn danger">Remove</button></form></div>
      </div>
      {% endfor %}
      <div style="margin-top:14px;padding:12px;border:1px solid rgba(34,197,94,.22);border-radius:16px;background:rgba(22,163,74,.08)">
        <h3 style="margin-top:0">+ Add movie folder</h3>
        <form method=post action="/settings/library-source/add"><input name=name placeholder="Folder name, e.g. Main Movies"><input name=folder placeholder="/Volumes/D/Movies"><label class=check><input type=checkbox name=enabled checked> Enabled</label><button class=primary>Add folder</button></form>
      </div>
    </dialog>
    <div class=safe-note>Refresh checks all enabled Movies folders for new, changed, and missing files. Missing files are marked first, not deleted.</div>
  </section>
  <section class="panel settings-card" id="tv-library">
    <h2>📺 TV Shows</h2>
    <p class=muted>One TV Shows library, with multiple monitored folders behind it.</p>
    <div class=about-grid>
      <div class=mini-stat><b>{{ tv_sources|length }}</b><span>folder{{ '' if tv_sources|length == 1 else 's' }} monitored</span></div>
      <div class=mini-stat><b>{{ tv_enabled_count }}</b><span>enabled</span></div>
      <div class=mini-stat><b>{{ tv_last_scanned }}</b><span>last scan</span></div>
    </div>
    <div class=toolbar><button class="btn primary" type=button onclick="document.getElementById('tv-dialog').showModal()">Manage folders</button><a class="btn good" href="/scan-wait/refresh-tv">"""+ICON_REFRESH+""" Check TV Shows for changes</a><a class="btn danger" href="/scan-wait/rebuild-tv">Clean rebuild TV Shows</a></div>
    <dialog id=tv-dialog class=library-dialog>
      <form method=dialog><button class="btn dialog-close">Close</button></form>
      <h2>Manage TV Folders</h2>
      <p class=muted>These folders are all part of the single TV Shows library.</p>
      {% if not tv_sources %}<p class=muted>No TV folders have been added yet.</p>{% endif %}
      {% for source in tv_sources %}
      <div style="margin-top:12px;padding:12px;border:1px solid rgba(148,163,184,.18);border-radius:16px;background:rgba(2,6,23,.35)">
        <b>{{ source.name }}</b> <span class=muted>· {{ 'Enabled' if source.enabled else 'Disabled' }}</span>
        <p class=path>{{ source.path }}<br><span class=muted>Last checked: {{ source.last_scanned or 'Never' }}</span></p>
        <form method=post action="/settings/tv-library-source/{{ source.index }}"><input name=name value="{{ source.name }}" placeholder="Folder name"><input name=tv_folder value="{{ source.path }}" placeholder="/Volumes/.../TV Shows"><label class=check><input type=checkbox name=enabled {% if source.enabled %}checked{% endif %}> Enabled</label><button class=primary>Save</button></form>
        <div class=toolbar><a class="btn good" href="/scan-wait/refresh-tv-{{ source.index }}">Check now</a><a class="btn danger" href="/scan-wait/rebuild-tv-{{ source.index }}">Clean rebuild this folder</a><form method=post action="/settings/tv-library-source/{{ source.index }}/remove" onsubmit="return confirm('Remove this TV folder from monitoring? The files are not deleted.');"><button class="btn danger">Remove</button></form></div>
      </div>
      {% endfor %}
      <div style="margin-top:14px;padding:12px;border:1px solid rgba(34,197,94,.22);border-radius:16px;background:rgba(22,163,74,.08)">
        <h3 style="margin-top:0">+ Add TV folder</h3>
        <form method=post action="/settings/tv-library-source/add"><input name=name placeholder="Folder name, e.g. Main TV"><input name=tv_folder placeholder="/Volumes/D/TV Shows"><label class=check><input type=checkbox name=enabled checked> Enabled</label><button class=primary>Add folder</button></form>
      </div>
    </dialog>
    <div class=safe-note>Refresh checks all enabled TV folders for new, changed, and missing episodes. Missing files are marked first, not deleted.</div>
  </section>
  <section class="panel settings-card">
    <h2>📤 Reports and exports</h2>
    <p class=muted>Quick maintenance summary and CSV exports for checking missing artwork, missing NFOs, and duplicate groups.</p>
    <div class=about-grid>
      <div class=mini-stat><b>{{ report_summary.total_attention }}</b><span>Total checks needing attention</span></div>
      <div class=mini-stat><b>{{ report_summary.movie_attention }}</b><span>Movie report items</span></div>
      <div class=mini-stat><b>{{ report_summary.tv_attention }}</b><span>TV report items</span></div>
    </div>
    <div class=toolbar><a class="btn primary" href="/reports">Open report dashboard</a><a class="btn warn" href="/missing-items">Review missing items</a><a class="btn good" href="/scan-wait/refresh-all">Refresh movies & TV</a><a class=btn href="/export/library.csv">Export movies CSV</a><a class=btn href="/export/tv-library.csv">Export TV CSV</a></div>
  </section>
  <section class="panel scan-card">
    <h2>⏱️ Scan status</h2>
    <div id=scan-status-box class="scan-state {{ 'running' if scan_status.running else ('done' if scan_status.ok else 'error') }}"><strong>{{ scan_status.label }}</strong><div>{{ scan_status.message }}</div><div class=muted>Started: {{ scan_status.started or '—' }} · Finished: {{ scan_status.finished or '—' }}</div></div>
    <p class=muted style="margin-bottom:0">When a refresh or rebuild finishes, this status changes to completed and shows the scan report.</p>
  </section>
  <section class="panel settings-card" id="scheduled-tasks">
    <h2>⏱️ Scheduled Tasks</h2>
    <p class=muted>Automatically check your monitored Movie and TV folders for changes. Unchanged folders are skipped; new, changed, or missing files are processed.</p>
    <div class=mini-stat><b>{{ scheduled_scan.next_text }}</b><span>current schedule</span></div>
    <div class=kv>
      <div><b>Last run</b><span>{{ scheduled_scan.last_run_at }}</span></div>
      <div><b>Last result</b><span>{{ scheduled_scan.last_result }}</span></div>
    </div>
    <form method=post action="/settings/schedule" style="margin-top:12px">
      <label class=check><input type=checkbox name=enabled {% if scheduled_scan.enabled %}checked{% endif %}> Enable automatic library change checks</label>
      <select name=frequency>
        <option value="daily" {% if scheduled_scan.frequency == 'daily' %}selected{% endif %}>Daily at a set time</option>
        <option value="interval" {% if scheduled_scan.frequency == 'interval' %}selected{% endif %}>Every X minutes</option>
      </select>
      <input name=time_of_day type=time value="{{ scheduled_scan.time_of_day }}">
      <input name=interval_minutes type=number min=15 step=15 value="{{ scheduled_scan.interval_minutes }}" placeholder="Interval in minutes">
      <button class=primary>Save scheduled task</button>
    </form>
  </section>
  <section class="panel settings-card">
    <h2>👥 Users moved to Admin &gt; Users</h2>
    <p class=muted>Admin and viewer account management now has its own page, so Settings stays focused on library and schedule configuration.</p>
    <div class=toolbar><a class="btn primary" href="/admin/users">Manage users</a><a class=btn href="/server-control">Server controls</a></div>
  </section>
  <section class="panel settings-card">
    <h2>ℹ️ About / version</h2>
    <p class=muted>Use this card to confirm exactly which build is running.</p>
    <div class=about-grid>
      <div class=mini-stat><b>{{ about.movie_count }}</b><span>Movies</span></div>
      <div class=mini-stat><b>{{ about.tv_show_count }}</b><span>TV shows</span></div>
      <div class=mini-stat><b>{{ about.episode_count }}</b><span>Episodes</span></div>
    </div>
    <div class=kv>
      <div><b>Version</b><span>{{ about.version }}</span></div>
      <div><b>App path</b><span>{{ about.app_path }}</span></div>
      <div><b>Database</b><span>{{ about.db_path }} · {{ about.db_size }}</span></div>
      <div><b>Port</b><span>{{ about.port }}</span></div>
    </div>
  </section>
</div></main></body></html>
"""

SCAN_WAIT_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Scanning</title>"""+BASE_STYLE+"""
<script>
function escapeHtml(v){return String(v).replace(/[&<>'"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c];});}
function poll(){
  fetch('/scan-status', {cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
    var box=document.getElementById('status-box');
    box.className='scan-state '+(s.running?'running':(s.ok?'done':'error'));
    box.innerHTML='<strong>'+escapeHtml(s.label || 'Scan status')+'</strong><div>'+escapeHtml(s.message || '')+'</div><div class="muted">Started: '+escapeHtml(s.started || '—')+' · Finished: '+escapeHtml(s.finished || '—')+'</div>';
    var done=document.getElementById('done-actions');
    if(!s.running){ done.style.display='flex'; }
    else { setTimeout(poll, 1500); }
  }).catch(function(){ setTimeout(poll, 1500); });
}
window.addEventListener('load', poll);
</script></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:760px"><section class="panel scan-card"><h1>Scanning…</h1><p class=muted>This page will update automatically. When it says completed, the scan is finished.</p><div id=status-box class="scan-state running"><strong>{{ label }}</strong><div>Starting scan…</div></div><div id=done-actions class=toolbar style="display:none"><a class="btn primary" href="/settings">Back to settings</a><a class=btn href="/">Movies</a><a class=btn href="/tv">TV Shows</a><a class=btn href="/reports">Reports</a></div></section></main></body></html>
"""


@app.route("/home")
def viewer_home():
    if not require_login():
        return redirect(url_for("login"))
    if require_admin():
        return redirect(url_for("dashboard"))
    c = conn()
    try:
        movie_count = safe_table_count(c, "movies")
        tv_show_count = safe_table_count(c, "tv_shows")
        episode_count = safe_table_count(c, "tv_episodes")
        recent_movies = [dict(r) for r in c.execute("SELECT id, title, year FROM movies ORDER BY id DESC LIMIT 5").fetchall()]
        recent_shows = [dict(r) for r in c.execute("SELECT id, title, year FROM tv_shows ORDER BY id DESC LIMIT 5").fetchall()]
    finally:
        c.close()
    return render_template_string(
        VIEWER_HOME_HTML,
        active="viewer_home",
        viewer_name=session.get("viewer_username", ""),
        movie_count=movie_count,
        tv_show_count=tv_show_count,
        episode_count=episode_count,
        recent_movies=recent_movies,
        recent_shows=recent_shows,
    )

def _admin_credentials_match(username, password):
    web_cfg = cfg.get("web", {}) if isinstance(cfg, dict) else {}
    return username == web_cfg.get("username", "admin") and password == web_cfg.get("password", "change-me-now")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username") or ""
        password = request.form.get("password") or ""
        web_cfg = cfg.get("web", {})
        if _admin_credentials_match(username, password):
            session["logged_in"] = True
            session["role"] = "admin"
            record_activity("success", "auth", "login", f"Admin login: {username}", {"remote_addr": request.remote_addr or ""}, actor=f"admin:{username}")
            return redirect(url_for("dashboard"))
        for viewer in _normalise_viewers(web_cfg):
            if not viewer.get("enabled", True):
                continue
            if username.lower() == str(viewer.get("username", "")).lower() and password == str(viewer.get("password", "")):
                session["logged_in"] = True
                session["role"] = "viewer"
                session["viewer_username"] = viewer.get("username", "Viewer")
                record_activity("success", "auth", "login", f"Viewer login: {viewer.get('username', 'Viewer')}", {"remote_addr": request.remote_addr or ""}, actor=f"viewer:{viewer.get('username', 'Viewer')}")
                return redirect(url_for("viewer_home"))
        record_activity("warning", "auth", "login_failed", f"Failed login for username: {username}", {"remote_addr": request.remote_addr or ""}, actor="guest")
        error = "Invalid credentials"
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    data = request.get_json(silent=True) if request.is_json else None
    data = data or request.form or {}
    username = str(data.get("username") or "")
    password = str(data.get("password") or "")
    if _admin_credentials_match(username, password):
        session["logged_in"] = True
        session["role"] = "admin"
        record_activity("success", "auth", "api_login", f"Admin API login: {username}", {"remote_addr": request.remote_addr or ""}, actor=f"admin:{username}")
        return jsonify({"ok": True, "role": "admin", "version": UI_VERSION})
    for viewer in _normalise_viewers((cfg.get("web", {}) if isinstance(cfg, dict) else {})):
        if viewer.get("enabled", True) and username.lower() == str(viewer.get("username", "")).lower() and password == str(viewer.get("password", "")):
            record_activity("warning", "auth", "api_login_denied", f"Viewer tried admin API login: {viewer.get('username', 'Viewer')}", {"remote_addr": request.remote_addr or ""}, actor=f"viewer:{viewer.get('username', 'Viewer')}")
            return jsonify({"ok": False, "error": "admin_required", "detail": "The macOS control app must use the admin account, not a viewer account."}), 403
    record_activity("warning", "auth", "api_login_failed", f"Failed admin API login for username: {username}", {"remote_addr": request.remote_addr or ""}, actor="guest")
    return jsonify({"ok": False, "error": "invalid_credentials"}), 401


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/image/<kind>/<int:movie_id>")
def image(kind, movie_id):
    if not require_login():
        return redirect(url_for("login"))
    if kind not in {"poster", "fanart"}:
        return ("", 404)
    c = conn()
    row = c.execute("SELECT poster_path, fanart_path FROM movies WHERE id=?", (movie_id,)).fetchone()
    c.close()
    if not row:
        return ("", 404)
    path = row["poster_path"] if kind == "poster" else row["fanart_path"]
    return _send_artwork_file(path)


@app.route("/thumb/movie/<kind>/<int:movie_id>")
def movie_thumb(kind, movie_id):
    if not require_login():
        return redirect(url_for("login"))
    if kind not in {"poster", "fanart"}:
        return ("", 404)
    c = conn()
    row = c.execute("SELECT poster_path, fanart_path FROM movies WHERE id=?", (movie_id,)).fetchone()
    c.close()
    if not row:
        return ("", 404)
    path = row["poster_path"] if kind == "poster" else row["fanart_path"]
    width, height = (220, 330) if kind == "poster" else (900, 420)
    return _send_thumbnail(path, "movie", kind, movie_id, width, height)






def _tv_season_label(season_number):
    if season_number is None:
        return "Other episodes"
    try:
        season_number = int(season_number)
    except Exception:
        return "Other episodes"
    if season_number == 0:
        return "Specials"
    return f"Season {season_number:02d}"


def _tv_season_art_candidates(show_path, season_number, kind="poster"):
    if not show_path:
        return []
    try:
        root = Path(show_path)
    except Exception:
        return []
    if season_number is None:
        return []
    try:
        n = int(season_number)
    except Exception:
        return []
    suffixes = ["jpg", "jpeg", "png", "webp"]
    names = []
    if n == 0:
        if kind == "poster":
            names += ["season-specials-poster", "season00-poster", "season-specials", "season00"]
        else:
            names += ["season-specials-banner", "season00-banner"]
    else:
        if kind == "poster":
            names += [f"season{n:02d}-poster", f"season{n:02d}", f"season {n:02d}-poster", f"season {n:02d}"]
        else:
            names += [f"season{n:02d}-banner", f"season {n:02d}-banner"]
    candidates = []
    for base in names:
        for ext in suffixes:
            candidates.append(root / f"{base}.{ext}")
    return candidates


def find_tv_season_art(show_path, season_number, kind="poster"):
    for candidate in _tv_season_art_candidates(show_path, season_number, kind=kind):
        if candidate.exists():
            return str(candidate.resolve())
    return None

@app.route("/tv-image/<kind>/<int:show_id>")
def tv_image(kind, show_id):
    if not require_login():
        return redirect(url_for("login"))
    c = conn()
    row = c.execute("SELECT poster_path, fanart_path, show_path FROM tv_shows WHERE id=?", (show_id,)).fetchone()
    c.close()
    if not row:
        return ("", 404)
    path = None
    if kind == "poster":
        path = row["poster_path"]
    elif kind == "fanart":
        path = row["fanart_path"]
    elif kind.startswith("season-poster-"):
        try:
            season_number = int(kind.split("-")[-1])
        except Exception:
            season_number = None
        path = find_tv_season_art(row["show_path"], season_number, kind="poster")
    elif kind.startswith("season-banner-"):
        try:
            season_number = int(kind.split("-")[-1])
        except Exception:
            season_number = None
        path = find_tv_season_art(row["show_path"], season_number, kind="banner")
    return _send_artwork_file(path)


@app.route("/thumb/tv/<kind>/<int:show_id>")
def tv_thumb(kind, show_id):
    if not require_login():
        return redirect(url_for("login"))
    c = conn()
    row = c.execute("SELECT poster_path, fanart_path, show_path FROM tv_shows WHERE id=?", (show_id,)).fetchone()
    c.close()
    if not row:
        return ("", 404)
    path = None
    if kind == "poster":
        path = row["poster_path"]
    elif kind == "fanart":
        path = row["fanart_path"]
    elif kind.startswith("season-poster-"):
        try:
            season_number = int(kind.split("-")[-1])
        except Exception:
            season_number = None
        path = find_tv_season_art(row["show_path"], season_number, kind="poster")
    else:
        return ("", 404)
    width, height = (220, 330) if "poster" in kind else (900, 420)
    return _send_thumbnail(path, "tv", kind, show_id, width, height)


@app.route("/tv")
def tv_index():
    if not require_login():
        return redirect(url_for("login"))
    c = conn()
    q = request.args.get("q", "").strip()
    rows = [dict(r) for r in c.execute("SELECT * FROM tv_shows ORDER BY COALESCE(sort_title, title) COLLATE NOCASE, year, id").fetchall()]
    total_shows = len(rows)
    shows = [show for show in rows if text_search_matches(q, show.get("title"))]
    show_id = request.args.get("show_id", "")
    season_selected_raw = (request.args.get("season") or "").strip()
    selected = None
    if show_id:
        for show in shows:
            if str(show.get("id")) == str(show_id):
                selected = show
                break
    if selected is None and shows:
        selected = shows[0]
    episodes = []
    episodes_by_season = OrderedDict()
    season_count = 0
    episode_count = 0
    season_blocks = []
    selected_season_number = None
    selected_season_label = ""
    if selected is not None:
        episodes = [dict(r) for r in c.execute("SELECT * FROM tv_episodes WHERE show_id=? ORDER BY COALESCE(season_number, 0), COALESCE(episode_number, 0), id", (selected["id"],)).fetchall()]
        season_count = len({e.get("season_number") for e in episodes if e.get("season_number") is not None})
        episode_count = len(episodes)
        for episode in episodes:
            season_number = episode.get("season_number")
            season_label = _tv_season_label(season_number)
            episodes_by_season.setdefault(season_label, []).append(episode)
        season_blocks = []
        for season_label, season_episodes in episodes_by_season.items():
            season_number = season_episodes[0].get("season_number") if season_episodes else None
            is_selected = False
            if season_selected_raw != "":
                is_selected = str(season_number if season_number is not None else -1) == season_selected_raw
            season_blocks.append({
                "label": season_label,
                "season_number": season_number,
                "episodes": season_episodes,
                "episode_count": len(season_episodes),
                "poster_available": bool(find_tv_season_art(selected.get("show_path"), season_number, kind="poster")) if selected else False,
                "banner_available": bool(find_tv_season_art(selected.get("show_path"), season_number, kind="banner")) if selected else False,
                "is_selected": is_selected,
            })
        if season_blocks:
            if season_selected_raw != "":
                for season in season_blocks:
                    if season["is_selected"]:
                        selected_season_number = season.get("season_number")
                        selected_season_label = season.get("label") or ""
                        episodes = season.get("episodes") or []
                        break
            else:
                first_season = season_blocks[0]
                first_season["is_selected"] = True
                selected_season_number = first_season.get("season_number")
                selected_season_label = first_season.get("label") or ""
                episodes = first_season.get("episodes") or []
            if selected_season_label == "":
                episodes = []
    c.close()
    return render_template_string(
        TV_HTML,
        active="tv",
        media_tags=media_tags,
        shows=shows,
        total_shows=total_shows,
        q=q,
        selected=selected,
        episodes=episodes,
        episodes_by_season=episodes_by_season,
        season_count=season_count,
        episode_count=episode_count,
        build_tv_query=lambda **updates: build_tv_filters_query(request.args, **updates),
        split_csv_values=split_csv_values,
        tv_missing_assets=tv_missing_assets,
        season_blocks=season_blocks,
        selected_season_number=selected_season_number,
        selected_season_label=selected_season_label,
        back_url="/tv",
        season_href_base=(f"/tv?show_id={selected['id']}&season=" if selected else "/tv?season="),
    )


ISSUE_DEFINITIONS = {
    "movie-missing-nfo": {"title": "Movie missing NFO", "kind": "movie_asset", "asset": "NFO", "path_column": "nfo_path", "export": "/export/report/missing-nfo.csv"},
    "movie-missing-poster": {"title": "Movie missing posters", "kind": "movie_asset", "asset": "poster", "path_column": "poster_path", "export": "/export/report/missing-poster.csv"},
    "movie-missing-fanart": {"title": "Movie missing fanart", "kind": "movie_asset", "asset": "fanart", "path_column": "fanart_path", "export": "/export/report/missing-fanart.csv"},
    "movie-duplicates": {"title": "Duplicate movie groups", "kind": "movie_duplicates", "export": "/export/report/duplicates.csv"},
    "tv-missing-show-nfo": {"title": "TV shows missing NFO", "kind": "tv_show_asset", "asset": "show NFO", "path_column": "nfo_path", "export": "/export/tv-report/missing-tvshow-nfo.csv"},
    "tv-missing-episode-nfo": {"title": "TV episodes missing NFO", "kind": "tv_episode_nfo", "asset": "episode NFO", "export": "/export/tv-report/missing-episode-nfo.csv"},
    "tv-missing-poster": {"title": "TV shows missing posters", "kind": "tv_show_asset", "asset": "poster", "path_column": "poster_path", "export": "/export/tv-report/missing-poster.csv"},
    "tv-missing-fanart": {"title": "TV shows missing fanart", "kind": "tv_show_asset", "asset": "fanart", "path_column": "fanart_path", "export": "/export/tv-report/missing-fanart.csv"},
    "tv-duplicates": {"title": "Duplicate TV show groups", "kind": "tv_duplicates", "export": "/export/tv-report/duplicates.csv"},
}


def get_issue_rows(issue_key):
    definition = ISSUE_DEFINITIONS.get(issue_key)
    if not definition:
        return None, []
    c = conn()
    try:
        kind = definition.get("kind")
        if kind == "movie_asset":
            rows = export_missing_asset_rows(c, definition.get("path_column"), definition.get("asset"))
        elif kind == "movie_duplicates":
            rows = export_duplicates_rows(c)
        elif kind == "tv_show_asset":
            rows = export_tv_missing_show_asset_rows(c, definition.get("path_column"), definition.get("asset"))
        elif kind == "tv_episode_nfo":
            rows = export_tv_missing_episode_nfo_rows(c)
        elif kind == "tv_duplicates":
            rows = export_tv_duplicates_rows(c)
        else:
            rows = []
    finally:
        c.close()
    return definition, rows


def issue_row_title(row, definition):
    if not isinstance(row, dict):
        return "Issue"
    kind = definition.get("kind") if definition else ""
    if kind == "tv_episode_nfo":
        show = row.get("show_title") or "TV show"
        season = row.get("season_number")
        episode = row.get("episode_number")
        ep_title = row.get("title") or "Episode"
        return f"{show} - S{int(season or 0):02d}E{int(episode or 0):02d} - {ep_title}"
    if "duplicate" in str(kind):
        year = row.get("year") or ""
        return f"{row.get('title') or 'Duplicate group'} {('(' + str(year) + ')') if year else ''}".strip()
    year = row.get("year") or row.get("show_year") or ""
    return f"{row.get('title') or row.get('show_title') or 'Untitled'} {('(' + str(year) + ')') if year else ''}".strip()



def _alert_id(issue_key, title, path, expected):
    raw = "|".join(str(x or "").strip() for x in (issue_key, title, path, expected))
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:20]


def _ignored_alert_ids():
    ignored = cfg.setdefault("ignored_alerts", [])
    ids = set()
    if isinstance(ignored, list):
        for item in ignored:
            if isinstance(item, dict) and item.get("id"):
                ids.add(str(item.get("id")))
            elif isinstance(item, str):
                ids.add(item)
    return ids


def is_alert_ignored(alert_id):
    return str(alert_id or "") in _ignored_alert_ids()


def _review_alert_ids():
    review = cfg.setdefault("review_alerts", [])
    ids = set()
    if isinstance(review, list):
        for item in review:
            if isinstance(item, dict) and item.get("id"):
                ids.add(str(item.get("id")))
            elif isinstance(item, str):
                ids.add(item)
    return ids


def is_alert_in_review(alert_id):
    return str(alert_id or "") in _review_alert_ids()


def mark_alert_for_review(alert_id, issue_key="", title=""):
    alert_id = str(alert_id or "").strip()
    if not alert_id:
        return False
    review = cfg.setdefault("review_alerts", [])
    if not isinstance(review, list):
        cfg["review_alerts"] = review = []
    if not any((item.get("id") if isinstance(item, dict) else item) == alert_id for item in review):
        review.append({
            "id": alert_id,
            "issue_key": str(issue_key or ""),
            "title": str(title or ""),
            "marked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        save_config(cfg)
        return True
    return False


def unmark_alert_for_review(alert_id):
    alert_id = str(alert_id or "").strip()
    if not alert_id:
        return False
    review = cfg.setdefault("review_alerts", [])
    if not isinstance(review, list):
        cfg["review_alerts"] = []
        save_config(cfg)
        return False
    before = len(review)
    cfg["review_alerts"] = [item for item in review if (item.get("id") if isinstance(item, dict) else item) != alert_id]
    if len(cfg["review_alerts"]) != before:
        save_config(cfg)
        return True
    return False


def alert_note(alert_id):
    notes = cfg.setdefault("alert_notes", {})
    if not isinstance(notes, dict):
        cfg["alert_notes"] = {}
        save_config(cfg)
        return ""
    return str(notes.get(str(alert_id or "")) or "")


def save_alert_note(alert_id, note):
    alert_id = str(alert_id or "").strip()
    if not alert_id:
        return False
    notes = cfg.setdefault("alert_notes", {})
    if not isinstance(notes, dict):
        cfg["alert_notes"] = notes = {}
    note = str(note or "").strip()[:600]
    if note:
        notes[alert_id] = note
    else:
        notes.pop(alert_id, None)
    save_config(cfg)
    return True


def ignore_alert(alert_id, issue_key="", title=""):
    alert_id = str(alert_id or "").strip()
    if not alert_id:
        return False
    ignored = cfg.setdefault("ignored_alerts", [])
    if not isinstance(ignored, list):
        cfg["ignored_alerts"] = ignored = []
    if not any((item.get("id") if isinstance(item, dict) else item) == alert_id for item in ignored):
        ignored.append({
            "id": alert_id,
            "issue_key": str(issue_key or ""),
            "title": str(title or ""),
            "ignored_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        save_config(cfg)
        return True
    return False


def unignore_alert(alert_id):
    alert_id = str(alert_id or "").strip()
    if not alert_id:
        return False
    ignored = cfg.setdefault("ignored_alerts", [])
    if not isinstance(ignored, list):
        cfg["ignored_alerts"] = []
        save_config(cfg)
        return False
    before = len(ignored)
    cfg["ignored_alerts"] = [item for item in ignored if (item.get("id") if isinstance(item, dict) else item) != alert_id]
    if len(cfg["ignored_alerts"]) != before:
        save_config(cfg)
        return True
    return False


def issue_alert_id(issue_key, row, definition):
    return _alert_id(issue_key, issue_row_title(row, definition), issue_row_path(row), issue_row_expected(row, definition))

def issue_row_path(row):
    if not isinstance(row, dict):
        return ""
    return row.get("movie_path") or row.get("file_path") or row.get("folder_path") or ""


def issue_row_expected(row, definition):
    if not isinstance(row, dict) or not definition:
        return ""
    asset = definition.get("asset") or "asset"
    kind = definition.get("kind")
    if "duplicate" in str(kind):
        return "Review duplicate IDs: " + str(row.get("movie_ids") or row.get("show_ids") or "")
    folder = row.get("folder_path") or ""
    if not folder:
        return f"Expected {asset} path is not stored in the database."
    return f"Expected {asset} in folder: {folder}"




def _first_int_from_text(value):
    text = str(value or "")
    for part in re.split(r"[^0-9]+", text):
        if part.isdigit():
            return int(part)
    return None


def issue_item_href(row, definition):
    if not isinstance(row, dict) or not definition:
        return ""
    kind = str(definition.get("kind") or "")
    if kind.startswith("movie"):
        movie_id = row.get("id") or _first_int_from_text(row.get("movie_ids"))
        return f"/?movie_id={movie_id}" if movie_id else ""
    if kind == "tv_episode_nfo":
        show_id = row.get("show_id")
        return f"/tv?show_id={show_id}" if show_id else ""
    if kind.startswith("tv"):
        show_id = row.get("show_id") or row.get("id") or _first_int_from_text(row.get("show_ids"))
        return f"/tv?show_id={show_id}" if show_id else ""
    return ""

def issue_area(definition):
    kind = str((definition or {}).get("kind") or "")
    return "TV Shows" if kind.startswith("tv_") else "Movies"


def issue_severity(definition):
    kind = str((definition or {}).get("kind") or "")
    if "duplicate" in kind:
        return "Warning"
    if kind == "tv_episode_nfo":
        return "Metadata"
    return "Alert"


def get_all_issue_alerts(max_per_type=80, max_total=500):
    alerts = []
    for key, definition in ISSUE_DEFINITIONS.items():
        _definition, rows = get_issue_rows(key)
        for row in (rows or [])[:max_per_type]:
            title = issue_row_title(row, definition)
            path = issue_row_path(row)
            expected = issue_row_expected(row, definition)
            alert_id = _alert_id(key, title, path, expected)
            alerts.append({
                "id": alert_id,
                "key": key,
                "type_title": definition.get("title") or key,
                "area": issue_area(definition),
                "severity": issue_severity(definition),
                "title": title,
                "path": path,
                "expected": expected,
                "ignored": is_alert_ignored(alert_id),
                "review": is_alert_in_review(alert_id),
                "note": alert_note(alert_id),
                "href": f"/reports/issues/{key}",
                "item_href": issue_item_href(row, definition),
            })
            if len(alerts) >= max_total:
                return alerts
    return alerts


ISSUES_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>{{ definition.title }}</title>"""+BASE_STYLE+"""
<style>
.issue-list{display:grid;gap:10px}.issue-card{padding:14px;border:1px solid rgba(251,191,36,.22);border-radius:18px;background:rgba(251,191,36,.08)}.issue-card h3{margin:0 0 6px}.issue-card p{color:var(--muted);line-height:1.45;word-break:break-word;margin:5px 0}.issue-meta{display:grid;gap:4px;margin-top:8px}.issue-summary{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.issue-empty{padding:24px;border:1px solid rgba(52,211,153,.24);border-radius:18px;background:rgba(16,185,129,.10);color:#dcfce7}.copy-box{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:#cbd5e1;background:rgba(2,6,23,.46);border:1px solid rgba(148,163,184,.14);border-radius:12px;padding:9px;overflow:auto}.ignored-note{display:inline-flex;border:1px solid rgba(148,163,184,.18);background:rgba(148,163,184,.10);border-radius:999px;padding:5px 9px;font-size:12px;font-weight:850;color:#cbd5e1}
</style>
</head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>{{ definition.title }}</h1><p class=muted style="margin:0">{{ rows|length }} issue{{ '' if rows|length == 1 else 's' }} found. This view does not rescan folders; press Check libraries when you want fresh results.</p></div>
  <div class=settings-hero-actions><a class=btn href="/reports">Back to reports</a><a class="btn good" href="/scan-wait/refresh-all">Check libraries</a><a class=btn href="{{ definition.export }}">Export CSV</a></div>
</section>
<section class="panel report-section">
  {% if not rows %}
    <div class=issue-empty><b>No issues found.</b><br><span>Nothing currently needs attention for this report.</span></div>
  {% else %}
    <div class=issue-list>
      {% for row in rows %}
      <article class=issue-card>
        <h3>{{ issue_row_title(row, definition) }}</h3>
        <div class=issue-meta>
          <p><b>Issue:</b> {{ definition.title }}</p>
          {% set path = issue_row_path(row) %}{% if path %}<p><b>Path:</b></p><div class=copy-box>{{ path }}</div>{% endif %}
          <p><b>Expected:</b> {{ issue_row_expected(row, definition) }}</p>
        </div>
        {% set alert_id = issue_alert_id(issue_key, row, definition) %}
        <div class=toolbar>
          {% if row.get('id') and definition.kind.startswith('movie') and 'duplicate' not in definition.kind %}<a class=btn href="/?movie_id={{ row.get('id') }}">Open movie</a>{% endif %}
          {% if row.get('show_id') %}<a class=btn href="/tv?show_id={{ row.get('show_id') }}">Open TV show</a>{% elif row.get('id') and definition.kind.startswith('tv') and 'duplicate' not in definition.kind %}<a class=btn href="/tv?show_id={{ row.get('id') }}">Open TV show</a>{% endif %}
          {% if is_alert_ignored(alert_id) %}
            <span class=ignored-note>Ignored</span>
            <form method=post action="/reports/alerts/unignore" style="display:inline"><input type=hidden name=alert_id value="{{ alert_id }}"><input type=hidden name=next value="{{ request.full_path }}"><button class=btn type=submit>Show again</button></form>
          {% else %}
            <form method=post action="/reports/alerts/ignore" style="display:inline"><input type=hidden name=alert_id value="{{ alert_id }}"><input type=hidden name=issue_key value="{{ issue_key }}"><input type=hidden name=title value="{{ issue_row_title(row, definition) }}"><input type=hidden name=next value="{{ request.full_path }}"><button class=btn type=submit>Ignore</button></form>
          {% endif %}
        </div>
      </article>
      {% endfor %}
    </div>
  {% endif %}
</section>
</main></body></html>
"""



ALERTS_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Alerts</title>"""+BASE_STYLE+"""
<style>
.alert-tools{display:grid;grid-template-columns:1fr 180px 180px 180px 180px;gap:10px;margin-top:12px}.alert-list{display:grid;gap:10px}.alert-card{padding:14px;border:1px solid rgba(251,191,36,.22);border-radius:18px;background:rgba(251,191,36,.08)}.alert-card.review{border-color:rgba(96,165,250,.42);background:rgba(96,165,250,.10)}.alert-card h3{margin:0 0 6px}.alert-card p{color:var(--muted);line-height:1.45;word-break:break-word;margin:5px 0}.alert-meta{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}.badge{display:inline-flex;border:1px solid rgba(148,163,184,.18);background:rgba(15,23,42,.44);border-radius:999px;padding:5px 9px;font-size:12px;font-weight:850}.badge.alert{color:#fde68a;border-color:rgba(245,158,11,.32);background:rgba(245,158,11,.10)}.badge.warning{color:#fecaca;border-color:rgba(244,63,94,.30);background:rgba(244,63,94,.10)}.badge.metadata{color:#bfdbfe;border-color:rgba(96,165,250,.30);background:rgba(96,165,250,.10)}.badge.review{color:#bfdbfe;border-color:rgba(96,165,250,.42);background:rgba(96,165,250,.14)}.copy-box{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:#cbd5e1;background:rgba(2,6,23,.46);border:1px solid rgba(148,163,184,.14);border-radius:12px;padding:9px;overflow:auto}.ignored-note{display:inline-flex;border:1px solid rgba(148,163,184,.18);background:rgba(148,163,184,.10);border-radius:999px;padding:5px 9px;font-size:12px;font-weight:850;color:#cbd5e1}.empty-state{padding:24px;border:1px solid rgba(52,211,153,.24);border-radius:18px;background:rgba(16,185,129,.10);color:#dcfce7}.alert-summary{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:14px 0}.alert-summary .mini-stat{padding:14px}.note-form{margin-top:10px;display:grid;gap:8px}.note-form textarea{min-height:58px;resize:vertical}.alert-note{margin-top:8px;border:1px solid rgba(148,163,184,.16);background:rgba(15,23,42,.38);border-radius:14px;padding:10px;color:#e2e8f0}.bulk-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:12px;border:1px solid rgba(148,163,184,.14);border-radius:16px;background:rgba(15,23,42,.34);margin-top:10px}.select-line{display:flex;gap:10px;align-items:flex-start}.select-line input[type=checkbox]{margin-top:6px;transform:scale(1.15)}@media(max-width:850px){.alert-tools,.alert-summary{grid-template-columns:1fr}.alert-card .toolbar{display:grid}.bulk-bar{display:grid}}
</style>
<script>
function filterAlerts(){
  var q=(document.getElementById('alert-search').value||'').toLowerCase();
  var area=document.getElementById('alert-area').value;
  var sev=document.getElementById('alert-severity').value;
  var queue=document.getElementById('alert-queue').value;
  var type=document.getElementById('alert-type').value;
  var shown=0;
  document.querySelectorAll('.alert-card').forEach(function(card){
    var text=(card.getAttribute('data-search')||'').toLowerCase();
    var review=card.getAttribute('data-review')==='1';
    var queueOk=!queue || (queue==='review' && review) || (queue==='normal' && !review);
    var ok=(!q || text.indexOf(q)>=0) && (!area || card.getAttribute('data-area')===area) && (!sev || card.getAttribute('data-severity')===sev) && (!type || card.getAttribute('data-key')===type) && queueOk;
    card.style.display=ok?'block':'none'; if(ok) shown++;
  });
  var out=document.getElementById('shown-count'); if(out) out.textContent=shown;
}
function selectVisibleAlerts(checked){
  document.querySelectorAll('.alert-card').forEach(function(card){
    if(card.style.display !== 'none'){
      var cb=card.querySelector('.alert-select');
      if(cb){ cb.checked = checked; }
    }
  });
  updateBulkSelected();
}
function updateBulkSelected(){
  var ids=[];
  document.querySelectorAll('.alert-select:checked').forEach(function(cb){ids.push(cb.value);});
  var hidden=document.getElementById('bulk-alert-ids'); if(hidden){ hidden.value=ids.join(','); }
  var count=document.getElementById('bulk-count'); if(count){ count.textContent=ids.length; }
}
function confirmBulk(){
  updateBulkSelected();
  var hidden=document.getElementById('bulk-alert-ids');
  if(!hidden || !hidden.value){ alert('Select at least one visible alert first.'); return false; }
  return true;
}
window.addEventListener('load', function(){ filterAlerts(); updateBulkSelected(); });
</script>
</head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>Admin · Alerts</h1><p class=muted style="margin:0">One browser view for current issues. {{ alerts|length }} visible · {{ review_count }} in review · {{ ignored_count }} ignored.</p></div>
  <div class=settings-hero-actions><a class="btn good" href="/scan-wait/refresh-all">Check libraries first</a><a class=btn href="/reports/alerts?show_ignored=1">Show ignored</a><a class=btn href="/reports">Back to reports</a></div>
</section>
<section class=alert-summary>
  <div class="panel mini-stat"><b>{{ alerts|length }}</b><span>{{ "All alerts" if show_ignored else "Active alerts" }}</span></div>
  <div class="panel mini-stat"><b>{{ counts.movies }}</b><span>Movie alerts</span></div>
  <div class="panel mini-stat"><b>{{ counts.tv }}</b><span>TV alerts</span></div>
  <div class="panel mini-stat"><b>{{ ignored_count }}</b><span>Ignored alerts</span></div>
  <div class="panel mini-stat"><b>{{ review_count }}</b><span>Marked for review</span></div>
  <div class="panel mini-stat"><b><span id=shown-count>{{ alerts|length }}</span></b><span>Shown after filter</span></div>
</section>
<section class="panel settings-card">
  <h2>Filter alerts</h2>
  <div class=alert-tools>
    <input id=alert-search oninput="filterAlerts()" placeholder="Search title, path, issue type…">
    <select id=alert-area onchange="filterAlerts()"><option value="">All areas</option><option>Movies</option><option>TV Shows</option></select>
    <select id=alert-severity onchange="filterAlerts()"><option value="">All types</option><option>Alert</option><option>Warning</option><option>Metadata</option></select>
    <select id=alert-queue onchange="filterAlerts()"><option value="">All queues</option><option value="review">Review queue only</option><option value="normal">Not in review</option></select>
    <select id=alert-type onchange="filterAlerts()"><option value="">All issue types</option>{% for key, label in alert_types %}<option value="{{ key }}">{{ label }}</option>{% endfor %}</select>
  </div>
  <form class=bulk-bar method=post action="/reports/alerts/bulk" onsubmit="return confirmBulk();">
    <input type=hidden id=bulk-alert-ids name=alert_ids value="">
    <input type=hidden name=next value="{{ request.full_path }}">
    <b>Bulk actions:</b>
    <span class=muted><span id=bulk-count>0</span> selected</span>
    <button class=btn type=button onclick="selectVisibleAlerts(true)">Select visible</button>
    <button class=btn type=button onclick="selectVisibleAlerts(false)">Clear</button>
    <button class=btn name=bulk_action value=review type=submit>Mark for review</button>
    <button class=btn name=bulk_action value=unreview type=submit>Remove from review</button>
    <button class=btn name=bulk_action value=ignore type=submit>Ignore selected</button>
    <button class=btn name=bulk_action value=unignore type=submit>Show selected again</button>
  </form>
</section>
<section class="panel settings-card">
  <h2>Current issues</h2>
  {% if not alerts %}
    <div class=empty-state><b>No current report issues found.</b><br><span>Reports are clear based on the current database.</span></div>
  {% else %}
  <div class=alert-list>
    {% for alert in alerts %}
    <article class="alert-card {{ 'review' if alert.review else '' }}" data-area="{{ alert.area }}" data-severity="{{ alert.severity }}" data-key="{{ alert.key }}" data-review="{{ '1' if alert.review else '0' }}" data-search="{{ alert.area }} {{ alert.severity }} {{ alert.type_title }} {{ alert.title }} {{ alert.path }} {{ alert.expected }} {{ alert.note }}">
      <div class=select-line><input class=alert-select type=checkbox value="{{ alert.id }}" onchange="updateBulkSelected()"><div style="min-width:0;flex:1"><div class=alert-meta><span class="badge {{ alert.severity|lower }}">{{ alert.severity }}</span><span class=badge>{{ alert.area }}</span><span class=badge>{{ alert.type_title }}</span>{% if alert.review %}<span class="badge review">For review</span>{% endif %}</div>
      <h3>{{ alert.title }}</h3></div></div>
      {% if alert.path %}<p><b>Path:</b></p><div class=copy-box>{{ alert.path }}</div>{% endif %}
      {% if alert.expected %}<p><b>Expected:</b> {{ alert.expected }}</p>{% endif %}
      {% if alert.note %}<div class=alert-note><b>Note:</b> {{ alert.note }}</div>{% endif %}
      <div class=toolbar>{% if alert.item_href %}<a class="btn primary" href="{{ alert.item_href }}">Open item</a>{% endif %}<a class="btn" href="{{ alert.href }}">View group</a>
        {% if alert.review %}
          <form method=post action="/reports/alerts/unreview" style="display:inline"><input type=hidden name=alert_id value="{{ alert.id }}"><input type=hidden name=next value="{{ request.full_path }}"><button class=btn type=submit>Remove from review</button></form>
        {% else %}
          <form method=post action="/reports/alerts/review" style="display:inline"><input type=hidden name=alert_id value="{{ alert.id }}"><input type=hidden name=issue_key value="{{ alert.key }}"><input type=hidden name=title value="{{ alert.title }}"><input type=hidden name=next value="{{ request.full_path }}"><button class=btn type=submit>Mark for review</button></form>
        {% endif %}
        {% if alert.ignored %}
          <span class=badge>Ignored</span><form method=post action="/reports/alerts/unignore" style="display:inline"><input type=hidden name=alert_id value="{{ alert.id }}"><input type=hidden name=next value="{{ request.full_path }}"><button class=btn type=submit>Show again</button></form>
        {% else %}
          <form method=post action="/reports/alerts/ignore" style="display:inline"><input type=hidden name=alert_id value="{{ alert.id }}"><input type=hidden name=issue_key value="{{ alert.key }}"><input type=hidden name=title value="{{ alert.title }}"><input type=hidden name=next value="{{ request.full_path }}"><button class=btn type=submit>Ignore</button></form>
        {% endif %}</div>
      <form class=note-form method=post action="/reports/alerts/note"><input type=hidden name=alert_id value="{{ alert.id }}"><input type=hidden name=next value="{{ request.full_path }}"><textarea name=note placeholder="Add a private admin note for this alert…">{{ alert.note }}</textarea><div><button class=btn type=submit>Save note</button></div></form>
    </article>
    {% endfor %}
  </div>
  {% endif %}
</section>
</main></body></html>
"""

REPORTS_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Reports</title>"""+BASE_STYLE+"""
<style>
.report-section{margin-top:16px;padding:16px}.report-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;margin-bottom:12px}.report-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.report-row{display:grid;grid-template-columns:76px minmax(0,1fr) auto;gap:12px;align-items:center;padding:12px;border:1px solid rgba(148,163,184,.14);border-radius:16px;background:rgba(2,6,23,.26)}.report-count{font-size:24px;font-weight:950;letter-spacing:-.04em;color:#f8fafc}.report-label{font-weight:900}.report-note{color:var(--muted);font-size:12px;margin-top:3px}.report-ok{color:#86efac}.report-warn{color:#fde68a}.history-list{display:grid;gap:8px}.history-item{padding:12px;border:1px solid rgba(148,163,184,.14);border-radius:16px;background:rgba(2,6,23,.26)}.history-item summary{cursor:pointer}.history-item p{color:var(--muted);line-height:1.55;white-space:normal}.issue-actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.issue-actions .btn{min-width:86px}@media(max-width:900px){.report-head{display:block}.report-list{grid-template-columns:1fr}.report-row{grid-template-columns:58px minmax(0,1fr)}.report-row .btn{grid-column:1/-1;width:100%}}
</style>
</head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>Admin · Reports</h1><p class=muted style="margin:0">View issues directly in the browser first. Exports are still available as a secondary option.</p></div>
  <div class=settings-hero-actions><a class="btn primary" href="/reports/alerts">View all alerts</a><a class="btn good" href="/scan-wait/refresh-all">Check libraries first</a><a class=btn href="/scan-history">Scan history</a><a class=btn href="/missing-items">Missing items</a></div>
</section>
<section class="panel report-section">
  <div class=report-head><div><h2>🎬 Movies</h2><p class=muted>Open issue lists in the web app without exporting first.</p></div><a class=btn href="/export/library.csv">Export full movies CSV</a></div>
  <div class=report-list>
    {% for item in reports.movies %}
    <div class=report-row><div class="report-count {{ 'report-ok' if item.count == 0 else 'report-warn' }}">{{ item.count }}</div><div><div class=report-label>{{ item.label }}</div><div class=report-note>{{ item.note }}</div></div><div class=issue-actions><a class="btn primary" href="{{ item.href }}">View</a><a class=btn href="{{ item.export_href }}">Export</a></div></div>
    {% endfor %}
  </div>
</section>
<section class="panel report-section">
  <div class=report-head><div><h2>📺 TV Shows</h2><p class=muted>Open issue lists in the web app without exporting first.</p></div><a class=btn href="/export/tv-library.csv">Export full TV CSV</a></div>
  <div class=report-list>
    {% for item in reports.tv %}
    <div class=report-row><div class="report-count {{ 'report-ok' if item.count == 0 else 'report-warn' }}">{{ item.count }}</div><div><div class=report-label>{{ item.label }}</div><div class=report-note>{{ item.note }}</div></div><div class=issue-actions><a class="btn primary" href="{{ item.href }}">View</a><a class=btn href="{{ item.export_href }}">Export</a></div></div>
    {% endfor %}
  </div>
</section>
<section class="panel report-section">
  <div class=report-head><div><h2>🚨 Missing items review</h2><p class=muted>Files marked missing are not deleted automatically. Review them before cleaning anything.</p></div><a class="btn warn" href="/missing-items">Open missing items</a></div>
  <div class=report-list>
    <div class=report-row><div class="report-count {{ 'report-ok' if missing.movies == 0 else 'report-warn' }}">{{ missing.movies }}</div><div><div class=report-label>Missing movies</div><div class=report-note>Movies whose video file disappeared from a monitored folder.</div></div><a class=btn href="/missing-items#movies">Review</a></div>
    <div class=report-row><div class="report-count {{ 'report-ok' if missing.episodes == 0 else 'report-warn' }}">{{ missing.episodes }}</div><div><div class=report-label>Missing TV episodes</div><div class=report-note>Episodes whose video file disappeared from a monitored folder.</div></div><a class=btn href="/missing-items#episodes">Review</a></div>
  </div>
</section>
<section class="panel report-section">
  <div class=report-head><div><h2>🧹 Clean missing items</h2><p class=muted>Admin-only safety cleanup. This removes database records only; it never deletes video files from disk.</p></div></div>
  <form method=post action="/missing-items/clean" onsubmit="return confirm('Clean missing database records older than the selected age? This does not delete files from disk.');">
    <div class=form-grid>
      <label>Only clean items missing for at least
        <select name=days>
          <option value=7>7 days</option>
          <option value=14>14 days</option>
          <option value=30>30 days</option>
          <option value=60>60 days</option>
          <option value=90>90 days</option>
        </select>
      </label>
      <label>Safety rule
        <input value="Files are not deleted. Database rows only." readonly>
      </label>
    </div>
    <div class=toolbar><button class="btn danger">Clean missing items</button><a class=btn href="/missing-items">Review missing items first</a></div>
  </form>
</section>
<section class="panel report-section">
  <h2>⏱️ Latest scan summary</h2>
  <div class="scan-state {{ 'running' if scan_status.running else ('done' if scan_status.ok else 'error') }}"><strong>{{ scan_status.label }}</strong><div>{{ scan_status.message }}</div><div class=muted>Started: {{ scan_status.started or '—' }} · Finished: {{ scan_status.finished or '—' }}</div></div>
</section>
<section class="panel report-section">
  <div class=report-head><div><h2>📜 Scan history</h2><p class=muted>The last 50 scans are stored. This page shows the most recent 12.</p></div><a class=btn href="/scan-history">Open full history</a></div>
  <div class=history-list>
    {% for scan in scan_history %}
    <details class=history-item><summary><b>{{ scan.finished_at or scan.created_at }}</b> — {{ scan.label }} <span class="{{ 'report-ok' if scan.ok else 'report-warn' }}">{{ 'OK' if scan.ok else 'Failed' }}</span></summary><p>{{ scan.message }}</p></details>
    {% else %}<p class=muted>No scan history yet.</p>{% endfor %}
  </div>
</section>
</main></body></html>
"""

@app.route("/reports/alerts")
def report_alerts():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    all_alerts = get_all_issue_alerts()
    show_ignored = request.args.get("show_ignored") == "1"
    ignored_count = sum(1 for item in all_alerts if item.get("ignored"))
    review_count = sum(1 for item in all_alerts if item.get("review") and not item.get("ignored"))
    alerts = all_alerts if show_ignored else [item for item in all_alerts if not item.get("ignored")]
    counts = {
        "movies": sum(1 for item in alerts if item.get("area") == "Movies"),
        "tv": sum(1 for item in alerts if item.get("area") == "TV Shows"),
    }
    return render_template_string(
        ALERTS_HTML,
        active="reports",
        admin_section="alerts",
        alerts=alerts,
        counts=counts,
        ignored_count=ignored_count,
        review_count=review_count,
        show_ignored=show_ignored,
        alert_types=[(key, definition.get("title") or key) for key, definition in ISSUE_DEFINITIONS.items()],
    )


@app.route("/reports/alerts/review", methods=["POST"])
def reports_alert_review():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    alert_id = request.form.get("alert_id", "")
    issue_key = request.form.get("issue_key", "")
    title = request.form.get("title", "")
    if mark_alert_for_review(alert_id, issue_key, title):
        flash("Alert added to the review queue.")
    else:
        flash("Alert was already in the review queue.")
    return redirect(request.form.get("next") or url_for("report_alerts"))


@app.route("/reports/alerts/unreview", methods=["POST"])
def reports_alert_unreview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    alert_id = request.form.get("alert_id", "")
    if unmark_alert_for_review(alert_id):
        flash("Alert removed from the review queue.")
    else:
        flash("Alert was not in the review queue.")
    return redirect(request.form.get("next") or url_for("report_alerts"))


@app.route("/reports/alerts/bulk", methods=["POST"])
def reports_alert_bulk():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    raw_ids = request.form.get("alert_ids", "")
    alert_ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
    action = request.form.get("bulk_action", "").strip()
    changed = 0
    for alert_id in alert_ids:
        if action == "review":
            mark_alert_for_review(alert_id, "bulk", "Bulk selected alert")
            changed += 1
        elif action == "unreview":
            unmark_alert_for_review(alert_id)
            changed += 1
        elif action == "ignore":
            ignore_alert(alert_id, "bulk", "Bulk selected alert")
            changed += 1
        elif action == "unignore":
            unignore_alert(alert_id)
            changed += 1
    if changed:
        save_config(cfg)
        record_activity("info", "alerts", f"bulk-{action}", f"Bulk alert action '{action}' applied to {changed} alert(s).")
        flash(f"Bulk alert action applied to {changed} alert(s).")
    else:
        flash("No alert bulk action was applied.")
    return redirect(request.form.get("next") or url_for("report_alerts"))


@app.route("/reports/alerts/note", methods=["POST"])
def reports_alert_note():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    alert_id = request.form.get("alert_id", "")
    note = request.form.get("note", "")
    if save_alert_note(alert_id, note):
        flash("Alert note saved." if note.strip() else "Alert note cleared.")
    else:
        flash("Could not save that alert note.")
    return redirect(request.form.get("next") or url_for("report_alerts"))


@app.route("/reports/alerts/ignore", methods=["POST"])
def reports_alert_ignore():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    alert_id = request.form.get("alert_id", "")
    issue_key = request.form.get("issue_key", "")
    title = request.form.get("title", "")
    if ignore_alert(alert_id, issue_key, title):
        flash("Alert ignored. It will stay hidden until you show ignored alerts or the issue changes.")
    else:
        flash("Alert was already ignored.")
    return redirect(request.form.get("next") or url_for("report_alerts"))


@app.route("/reports/alerts/unignore", methods=["POST"])
def reports_alert_unignore():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    alert_id = request.form.get("alert_id", "")
    if unignore_alert(alert_id):
        flash("Alert restored.")
    else:
        flash("Alert was not ignored.")
    return redirect(request.form.get("next") or url_for("report_alerts", show_ignored="1"))


@app.route("/reports")
def reports():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(REPORTS_HTML, active="reports", admin_section="alerts", reports=get_report_summary(), scan_status=get_scan_status(), scan_history=get_scan_history(12), missing=missing_counts())


@app.route("/reports/issues/<issue_key>")
def report_issues(issue_key):
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    definition, rows = get_issue_rows(issue_key)
    if not definition:
        flash("Report issue type not found.")
        return redirect(url_for("reports"))
    return render_template_string(
        ISSUES_HTML,
        active="reports",
        admin_section="alerts",
        definition=definition,
        rows=rows,
        issue_row_title=issue_row_title,
        issue_row_path=issue_row_path,
        issue_row_expected=issue_row_expected,
        issue_alert_id=issue_alert_id,
        is_alert_ignored=is_alert_ignored,
        issue_key=issue_key,
    )


SCAN_HISTORY_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Scan History</title>"""+BASE_STYLE+"""
<style>.history-list{display:grid;gap:10px}.history-card{padding:14px;border:1px solid rgba(148,163,184,.14);border-radius:18px;background:rgba(2,6,23,.26)}.history-card h3{margin:0 0 6px}.history-card p{color:var(--muted);line-height:1.55}.ok{color:#86efac}.warn{color:#fde68a}</style>
</head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero"><div><h1>Scan History</h1><p class=muted style="margin:0">Recent manual and scheduled library refreshes.</p></div><div class=settings-hero-actions><a class=btn href="/admin/scans">Back to scans</a><a class="btn good" href="/scan-wait/refresh-all">Run check now</a></div></section>
<section class="panel report-section"><div class=history-list>{% for scan in history %}<article class=history-card><h3>{{ scan.label }} <span class="{{ 'ok' if scan.ok else 'warn' }}">{{ 'OK' if scan.ok else 'Failed' }}</span></h3><p class=muted>Action: {{ scan.action }} · Started: {{ scan.started_at or '—' }} · Finished: {{ scan.finished_at or '—' }}</p><p>{{ scan.message }}</p></article>{% else %}<p class=muted>No scan history yet.</p>{% endfor %}</div></section>
</main></body></html>
"""

MISSING_ITEMS_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Missing Items</title>"""+BASE_STYLE+"""
<style>.missing-list{display:grid;gap:10px}.missing-card{padding:14px;border:1px solid rgba(251,191,36,.22);border-radius:18px;background:rgba(251,191,36,.08)}.missing-card h3{margin:0 0 6px}.missing-card p{color:var(--muted);line-height:1.45;word-break:break-word}.inline-form{display:inline}.danger-note{padding:12px;border-radius:16px;border:1px solid rgba(251,113,133,.30);background:rgba(244,63,94,.10);color:#ffe4e6}</style>
</head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero"><div><h1>Missing Items</h1><p class=muted style="margin:0">Items stay in the database first. Remove them only after you are sure the files are really gone.</p></div><div class=settings-hero-actions><a class=btn href="/admin/scans">Back to scans</a><a class="btn good" href="/scan-wait/refresh-all">Check again</a></div></section>
<section class="panel report-section">
  <h2>🧹 Clean missing items</h2>
  <p class=danger-note>Use this only after reviewing the list below. It removes database records for items that have been missing long enough. It does not delete files from disk.</p>
  <form method=post action="/missing-items/clean" onsubmit="return confirm('Clean missing database records older than the selected age? This does not delete files from disk.');">
    <div class=form-grid>
      <label>Only clean items missing for at least
        <select name=days>
          <option value=7>7 days</option>
          <option value=14>14 days</option>
          <option value=30>30 days</option>
          <option value=60>60 days</option>
          <option value=90>90 days</option>
        </select>
      </label>
    </div>
    <div class=toolbar><button class="btn danger">Clean missing items</button></div>
  </form>
</section>
<section class="panel report-section" id=movies><h2>🎬 Missing Movies</h2><p class=danger-note>External drives can disconnect. Use “Restore if file returned” first if you think the file should exist again.</p><div class=missing-list>{% for item in items.movies %}<article class=missing-card><h3>{{ item.title }}{% if item.year %} ({{ item.year }}){% endif %}</h3><p>{{ item.path }}<br>Missing since: {{ item.missing_since or 'unknown' }}</p><div class=toolbar><form class=inline-form method=post action="/missing-items/movie/{{ item.id }}/restore"><button class="btn good">Restore if file returned</button></form><form class=inline-form method=post action="/missing-items/movie/{{ item.id }}/remove" onsubmit="return confirm('Remove this missing movie from the database? The file itself is not deleted.');"><button class="btn danger">Remove from database</button></form></div></article>{% else %}<p class=muted>No missing movies.</p>{% endfor %}</div></section>
<section class="panel report-section" id=episodes><h2>📺 Missing TV Episodes</h2><div class=missing-list>{% for item in items.episodes %}<article class=missing-card><h3>{{ item.show_title }} — S{{ '%02d'|format(item.season_number or 0) }}E{{ '%02d'|format(item.episode_number or 0) }}{% if item.title %} — {{ item.title }}{% endif %}</h3><p>{{ item.path }}<br>Missing since: {{ item.missing_since or 'unknown' }}</p><div class=toolbar><form class=inline-form method=post action="/missing-items/episode/{{ item.id }}/restore"><button class="btn good">Restore if file returned</button></form><form class=inline-form method=post action="/missing-items/episode/{{ item.id }}/remove" onsubmit="return confirm('Remove this missing TV episode from the database? The file itself is not deleted.');"><button class="btn danger">Remove from database</button></form></div></article>{% else %}<p class=muted>No missing TV episodes.</p>{% endfor %}</div></section>
</main></body></html>
"""

ADMIN_SCANS_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Scans</title>"""+BASE_STYLE+"""
<style>
.scan-console-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:14px 0}.scan-summary-card{min-width:0;padding:13px 14px;border-radius:18px}.scan-summary-card .summary-label{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:7px}.scan-summary-card .summary-value{display:block;font-size:clamp(16px,1.3vw,22px);font-weight:900;line-height:1.18;overflow-wrap:anywhere}.scan-summary-card .summary-detail{display:block;color:var(--muted);font-size:12px;line-height:1.35;margin-top:6px;overflow-wrap:anywhere}.scan-summary-card.running{border-color:rgba(56,189,248,.36);box-shadow:0 0 0 3px rgba(56,189,248,.08)}.scan-summary-card.error{border-color:rgba(251,113,133,.38)}.scan-console-main{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(340px,.85fr);gap:14px;align-items:start}.scan-action-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.scan-action{padding:14px;border-radius:18px;border:1px solid rgba(148,163,184,.14);background:rgba(2,6,23,.26)}.scan-action h3{margin:0 0 6px}.scan-action p{color:var(--muted);line-height:1.45;font-size:13px}.history-compact{display:grid;gap:8px}.history-item{padding:11px;border-radius:15px;border:1px solid rgba(148,163,184,.13);background:rgba(2,6,23,.25)}.history-item b{display:block}.history-item small{display:block;color:var(--muted);margin-top:3px}.missing-callout{padding:12px;border-radius:16px;border:1px solid rgba(251,191,36,.28);background:rgba(251,191,36,.08);color:#fde68a;line-height:1.45}.schedule-box{padding:12px;border-radius:16px;border:1px solid rgba(96,165,250,.24);background:rgba(96,165,250,.08)}@media(max-width:950px){.scan-console-grid,.scan-action-grid{grid-template-columns:1fr}.scan-console-main{grid-template-columns:1fr}.settings-hero-actions{display:grid;width:100%}}
</style>
<script>
function escapeHtmlScan(v){return String(v || '').replace(/[&<>'"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c];});}
function refreshAdminScanSummary(){
  fetch('/scan-status', {cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
    var card=document.getElementById('scan-current-card'); if(!card) return;
    card.className='panel scan-summary-card '+(s.running?'running':(s.ok?'':'error'));
    var label=document.getElementById('scan-current-label');
    var detail=document.getElementById('scan-current-detail');
    var time=document.getElementById('scan-current-time');
    if(label) label.textContent=s.label || 'Scan status';
    if(detail) detail.textContent=s.message || '';
    if(time) time.textContent='Started: '+(s.started || '—')+' · Finished: '+(s.finished || '—');
  }).catch(function(){});
}
setInterval(refreshAdminScanSummary, 2500);
window.addEventListener('load', refreshAdminScanSummary);
</script>
</head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<section class="panel settings-hero">
  <div><h1>Admin · Scans</h1><p class=muted style="margin:0">One place for library checks, scheduled refresh, scan history, and missing-item cleanup.</p></div>
  <div class=settings-hero-actions><a class="btn good" href="/scan-wait/refresh-all">Check all libraries</a><a class=btn href="/source-diagnostics">Library folders</a></div>
</section>
<section class=scan-console-grid>
  <div id=scan-current-card class="panel scan-summary-card {{ 'running' if data.status.running else ('' if data.status.ok else 'error') }}"><span class=summary-label>Current scan</span><span id=scan-current-label class=summary-value>{{ data.status.label }}</span><span id=scan-current-detail class=summary-detail>{{ data.status.message }}</span><span id=scan-current-time class=summary-detail>Started: {{ data.status.started or '—' }} · Finished: {{ data.status.finished or '—' }}</span></div>
  <div class="panel scan-summary-card"><span class=summary-label>Scheduled refresh</span><span class=summary-value>{{ data.scheduled.status }}</span><span class=summary-detail>{{ data.scheduled.detail }}</span></div>
  <div class="panel scan-summary-card"><span class=summary-label>Recent scan records</span><span class=summary-value>{{ data.history|length }}</span><span class=summary-detail>Showing latest records only</span></div>
  <div class="panel scan-summary-card"><span class=summary-label>Missing items</span><span class=summary-value>{{ data.missing.movies + data.missing.episodes }}</span><span class=summary-detail>{{ data.missing.movies }} movies · {{ data.missing.episodes }} TV episodes</span></div>
</section>
<div class=scan-console-main>
  <section class="panel settings-card">
    <h2>Run checks</h2>
    <div class=scan-action-grid>
      <article class=scan-action><h3>All libraries</h3><p>Checks Movies and TV Shows using the cached change-detection engine.</p><a class="btn good" href="/scan-wait/refresh-all">Check all now</a></article>
      <article class=scan-action><h3>Movies only</h3><p>Checks all enabled movie folders and skips unchanged folders.</p><a class="btn primary" href="/scan-wait/refresh-movies">Check Movies</a></article>
      <article class=scan-action><h3>TV Shows only</h3><p>Checks all enabled TV folders and skips unchanged folders.</p><a class="btn primary" href="/scan-wait/refresh-tv">Check TV Shows</a></article>
    </div>
  </section>
  <aside class="panel settings-card">
    <h2>Schedule</h2>
    <div class=schedule-box>
      <b>{{ data.scheduled.status }}</b><br>
      <span class=muted>{{ data.scheduled.detail }}</span><br>
      <span class=muted>Last run: {{ data.scheduled.last_run or '—' }}</span>
    </div>
    <div class=toolbar style="margin-top:10px"><a class=btn href="/settings#scheduled-tasks">Edit schedule</a></div>
  </aside>
</div>
<div class=scan-console-main style="margin-top:14px">
  <section class="panel settings-card">
    <h2>Recent scan history</h2>
    <div class=history-compact>
      {% for scan in data.history[:8] %}<article class=history-item><b>{{ scan.label }} · {{ 'OK' if scan.ok else 'Failed' }}</b><small>{{ scan.started_at or '—' }} → {{ scan.finished_at or '—' }}</small><div class=muted>{{ scan.message }}</div></article>{% else %}<p class=muted>No scan history yet.</p>{% endfor %}
    </div>
    <div class=toolbar><a class=btn href="/scan-history">Open full scan history</a></div>
  </section>
  <aside class="panel settings-card">
    <h2>Missing items</h2>
    <div class=missing-callout>Movies: {{ data.missing.movies }}<br>TV episodes: {{ data.missing.episodes }}<br><br>Missing items are marked first. They are not deleted from the database until you review and clean them.</div>
    <div class=toolbar style="margin-top:10px"><a class="btn warn" href="/missing-items">Review missing items</a></div>
  </aside>
</div>
</main></body></html>
"""

@app.route("/admin/scans")
def admin_scans():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    data = {
        "status": get_scan_status(),
        "scheduled": scheduled_scan_summary(),
        "history": get_scan_history(20),
        "missing": missing_counts(),
    }
    return render_template_string(ADMIN_SCANS_HTML, active="dashboard", admin_section="scans", data=data)

@app.route("/scan-history")
def scan_history_page():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(SCAN_HISTORY_HTML, active="reports", admin_section="scans", history=get_scan_history(50))

@app.route("/missing-items")
def missing_items_page():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(MISSING_ITEMS_HTML, active="reports", admin_section="scans", items=get_missing_items())

@app.route("/missing-items/movie/<int:item_id>/restore", methods=["POST"])
def missing_movie_restore(item_id):
    if not require_login():
        return redirect(url_for("login"))
    flash(mark_movie_restored_if_present(item_id))
    return redirect("/missing-items#movies")

@app.route("/missing-items/episode/<int:item_id>/restore", methods=["POST"])
def missing_episode_restore(item_id):
    if not require_login():
        return redirect(url_for("login"))
    flash(mark_episode_restored_if_present(item_id))
    return redirect("/missing-items#episodes")

@app.route("/missing-items/movie/<int:item_id>/remove", methods=["POST"])
def missing_movie_remove(item_id):
    if not require_login():
        return redirect(url_for("login"))
    c = conn()
    try:
        c.execute("DELETE FROM movies WHERE id=? AND COALESCE(missing,0)=1", (item_id,))
        c.commit()
        flash("Missing movie removed from the database.")
        record_activity("warning", "cleanup", "missing_movie_removed", f"Missing movie removed from database: id {item_id}")
    finally:
        c.close()
    return redirect("/missing-items#movies")

@app.route("/missing-items/episode/<int:item_id>/remove", methods=["POST"])
def missing_episode_remove(item_id):
    if not require_login():
        return redirect(url_for("login"))
    c = conn()
    try:
        c.execute("DELETE FROM tv_episodes WHERE id=? AND COALESCE(missing,0)=1", (item_id,))
        c.commit()
        flash("Missing TV episode removed from the database.")
        record_activity("warning", "cleanup", "missing_episode_removed", f"Missing TV episode removed from database: id {item_id}")
    finally:
        c.close()
    return redirect("/missing-items#episodes")

@app.route("/missing-items/clean", methods=["POST"])
def missing_items_clean():
    if not require_admin():
        return redirect(url_for("login"))
    result = clean_missing_items_older_than(request.form.get("days", 7))
    flash(
        f"Cleaned missing items older than {result['days']} days: "
        f"{result['removed_movies']} movie(s), {result['removed_episodes']} TV episode(s). "
        f"Kept newer missing items: {result['kept_movies']} movie(s), {result['kept_episodes']} TV episode(s)."
    )
    record_activity("warning", "cleanup", "clean_missing_items", "Cleaned missing database items", result)
    return redirect("/missing-items")

BACKUPS_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Backups</title>"""+BASE_STYLE+"""
<style>
.backup-list{display:grid;gap:10px}.backup-card{padding:14px;border:1px solid rgba(148,163,184,.15);border-radius:18px;background:rgba(2,6,23,.28)}.backup-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.backup-name{font-weight:950;word-break:break-all}.backup-meta{color:var(--muted);font-size:13px;line-height:1.45}.backup-note{padding:12px;border-radius:16px;border:1px solid rgba(56,189,248,.22);background:rgba(14,165,233,.08);color:#e0f2fe}.backup-danger{padding:12px;border-radius:16px;border:1px solid rgba(251,113,133,.30);background:rgba(244,63,94,.10);color:#ffe4e6}.inline-form{display:inline}.backup-card .toolbar form{display:inline}@media(max-width:750px){.backup-head{display:block}.backup-card .toolbar{display:grid}.backup-card .toolbar .btn,.backup-card .toolbar button{width:100%}}
</style>
</head><body>"""+NAV_HTML+"""
<main class=shell>
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<section class="panel settings-hero">
  <div><h1>Backups</h1><p class=muted style="margin:0">Create a safe copy of the database and settings before big scans, rebuilds, or cleanup.</p></div>
  <div class=settings-hero-actions><a class=btn href="/dashboard">Dashboard</a><a class=btn href="/settings">Settings</a><a class=btn href="/reports">Reports</a><a class=btn href="/activity-log">Activity log</a></div>
</section>

<section class="panel settings-card">
  <h2>💾 Create backup</h2>
  <p class=backup-note>A full backup contains <b>library.db</b> and <b>config.json</b>. This includes your admin/viewer accounts, monitored folders, schedule settings, scan history, missing-item status, and database contents.</p>
  <form method=post action="/backups/create"><button class="btn primary">Create full backup now</button></form>
</section>

<div class=settings-grid style="margin-top:14px">
  <section class="panel settings-card">
    <h2>Current app state</h2>
    <div class=kv>
      <div><b>Version</b><span>{{ about.version }}</span></div>
      <div><b>Database</b><span>{{ about.db_path }} · {{ about.db_size }}</span></div>
      <div><b>Config</b><span>{{ config_path }}</span></div>
      <div><b>Backup folder</b><span>{{ about.backup_path }}</span></div>
    </div>
  </section>
  <section class="panel settings-card">
    <h2>Restore safety</h2>
    <p class=backup-danger>Restore only when no scan is running. The app automatically creates a fresh safety backup before restoring. Restoring settings may require a web app restart to make every change visible.</p>
  </section>
</div>

<section class="panel settings-card" style="margin-top:14px">
  <h2>Available backups</h2>
  <div class=backup-list>
    {% for backup in backups %}
    <article class=backup-card>
      <div class=backup-head><div><div class=backup-name>{{ backup.name }}</div><div class=backup-meta>{{ backup.type }} · {{ backup.contains }} · {{ backup.size }} · Created: {{ backup.created }}</div></div></div>
      <div class=toolbar>
        <a class=btn href="/backups/download/{{ backup.name }}">Download</a>
        <form method=post action="/backups/restore/{{ backup.name }}" onsubmit="return confirm('Restore this backup? A safety backup will be created first.');"><button class="btn danger">Restore</button></form>
        <form method=post action="/backups/delete/{{ backup.name }}" onsubmit="return confirm('Delete this backup file? This does not affect the live database.');"><button class=btn>Delete backup</button></form>
      </div>
    </article>
    {% else %}<p class=muted>No backups yet. Create one above.</p>{% endfor %}
  </div>
</section>
</main></body></html>
"""

@app.route("/backups")
def backups_page():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    flash("Backup tools are hidden in this simplified admin layout.")
    return redirect(url_for("dashboard"))

@app.route("/backups/create", methods=["POST"])
def backups_create():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    flash("Backup tools are hidden in this simplified admin layout.")
    return redirect(url_for("dashboard"))

@app.route("/backups/download/<path:name>")
def backups_download(name):
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    flash("Backup tools are hidden in this simplified admin layout.")
    return redirect(url_for("dashboard"))

@app.route("/backups/restore/<path:name>", methods=["POST"])
def backups_restore(name):
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    flash("Backup tools are hidden in this simplified admin layout.")
    return redirect(url_for("dashboard"))

@app.route("/backups/delete/<path:name>", methods=["POST"])
def backups_delete(name):
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    flash("Backup tools are hidden in this simplified admin layout.")
    return redirect(url_for("dashboard"))

ACTIVITY_LOG_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Activity Log</title>"""+BASE_STYLE+"""
<style>
.activity-filters{display:grid;grid-template-columns:1fr 1fr 2fr auto;gap:10px;margin-top:12px}.activity-list{display:grid;gap:9px;margin-top:14px}.activity-item{padding:12px;border:1px solid rgba(148,163,184,.14);border-radius:16px;background:rgba(2,6,23,.26)}.activity-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.activity-meta{color:var(--muted);font-size:12px;margin-top:4px}.activity-details{white-space:pre-wrap;word-break:break-word;color:var(--muted);font-size:12px;margin-top:8px;padding:9px;border-radius:12px;background:rgba(15,23,42,.50);border:1px solid rgba(148,163,184,.10)}.level-pill{border-radius:999px;padding:4px 8px;font-size:11px;font-weight:900;text-transform:uppercase;border:1px solid rgba(148,163,184,.22)}.level-pill.success{color:#bbf7d0;background:rgba(34,197,94,.11);border-color:rgba(34,197,94,.32)}.level-pill.info{color:#bae6fd;background:rgba(14,165,233,.10);border-color:rgba(14,165,233,.28)}.level-pill.warning{color:#fde68a;background:rgba(245,158,11,.10);border-color:rgba(245,158,11,.32)}.level-pill.error{color:#fecaca;background:rgba(244,63,94,.12);border-color:rgba(244,63,94,.32)}@media(max-width:850px){.activity-filters{grid-template-columns:1fr}.activity-top{display:block}.activity-top .level-pill{margin-top:8px;display:inline-flex}}
</style>
</head><body>"""+NAV_HTML+"""
<main class=shell>
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<section class="panel settings-hero">
  <div><h1>Activity Log</h1><p class=muted style="margin:0">Admin audit trail for scans, scheduled checks, backups, settings changes, viewer management, cleanup, logins, and errors.</p></div>
  <div class=settings-hero-actions><a class="btn good" href="/scan-wait/refresh-all">Check all libraries</a><a class=btn href="/dashboard">Dashboard</a><a class=btn href="/reports">Reports</a><a class=btn href="/settings">Settings</a><a class=btn href="/admin/migration-safety">Migration safety</a><a class=btn href="/admin/migration-dry-run">Migration dry run</a><a class=btn href="/admin/migration-backup">Migration backup</a><a class=btn href="/admin/migration-backup-verify">Verify backup</a><a class=btn href="/admin/migration-stage">Migration stage</a><a class=btn href="/admin/migration-stage-verify">Verify stage</a><a class=btn href="/admin/migration-cutover-readiness">Cutover readiness</a><a class=btn href="/admin/migration-cutover-plan">Cutover plan</a><a class=btn href="/admin/dmg-packaging">DMG packaging</a><a class=btn href="/admin/package-manifest">Package manifest</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/admin/test-dmg-status">Test DMG status</a><a class=btn href="/admin/app-support-prep">App Support prep</a><a class=btn href="/admin/app-support-status">App Support status</a></div>
</section>

<div class=about-grid>
  <section class="panel mini-stat"><b>{{ stats.total }}</b><span>Total retained events</span></section>
  <section class="panel mini-stat"><b>{{ stats.errors }}</b><span>Error events</span></section>
  <section class="panel mini-stat"><b>{{ stats.warnings }}</b><span>Warning events</span></section>
  <section class="panel mini-stat"><b>{{ stats.last }}</b><span>Latest event</span></section>
</div>

<section class="panel settings-card" style="margin-top:14px">
  <h2>Filter activity</h2>
  <form method=get class=activity-filters>
    <select name=level>
      <option value="" {% if not level %}selected{% endif %}>All levels</option>
      {% for item in ['success','info','warning','error'] %}<option value="{{ item }}" {% if level==item %}selected{% endif %}>{{ item|title }}</option>{% endfor %}
    </select>
    <select name=area>
      <option value="" {% if not area %}selected{% endif %}>All areas</option>
      {% for item in areas %}<option value="{{ item }}" {% if area==item %}selected{% endif %}>{{ item|title }}</option>{% endfor %}
    </select>
    <input name=q value="{{ q }}" placeholder="Search message, actor, action, details">
    <button class=primary>Filter</button>
  </form>
</section>

<section class="panel settings-card" style="margin-top:14px">
  <h2>Recent events</h2>
  <div class=activity-list>
    {% for row in rows %}
    <article class=activity-item>
      <div class=activity-top><div><b>{{ row.message or row.action }}</b><div class=activity-meta>{{ row.created_at }} · {{ row.area }} / {{ row.action }} · {{ row.actor }}</div></div><span class="level-pill {{ row.level }}">{{ row.level }}</span></div>
      {% if row.details %}<div class=activity-details>{{ row.details }}</div>{% endif %}
    </article>
    {% else %}<p class=muted>No activity events match this filter yet.</p>{% endfor %}
  </div>
</section>
</main></body></html>
"""

@app.route("/activity-log")
def activity_log_page():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    flash("Activity log is now hidden from the normal admin console. Use Alerts, Scans, or Server for daily admin work.")
    return redirect(url_for("dashboard"))
    level = (request.args.get("level") or "").strip().lower()
    area = (request.args.get("area") or "").strip().lower()
    q = (request.args.get("q") or "").strip()
    if level not in {"", "success", "info", "warning", "error"}:
        level = ""
    allowed_areas = ["auth", "scan", "settings", "source", "viewer", "backup", "cleanup", "server", "system"]
    if area not in ["", *allowed_areas]:
        area = ""
    return render_template_string(
        ACTIVITY_LOG_HTML,
        active="activity",
        rows=get_activity_log(120, level=level, area=area, q=q),
        stats=get_activity_stats(),
        level=level,
        area=area,
        q=q,
        areas=allowed_areas,
    )



SYSTEM_HEALTH_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>System Health</title>"""+BASE_STYLE+"""
<style>
.health-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:14px 0}.health-main{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(360px,.7fr);gap:14px;align-items:start}.check-list{display:grid;gap:10px}.check-row{display:grid;grid-template-columns:minmax(160px,.32fr) minmax(110px,.18fr) minmax(0,1fr);gap:10px;align-items:start;padding:12px;border:1px solid rgba(148,163,184,.14);background:rgba(2,6,23,.28);border-radius:16px}.check-row b{font-size:14px}.status-pill{display:inline-flex;align-items:center;justify-content:center;border-radius:999px;padding:5px 9px;font-size:12px;font-weight:850;border:1px solid rgba(148,163,184,.20)}.status-pill.good{color:#bbf7d0;background:rgba(34,197,94,.11);border-color:rgba(34,197,94,.32)}.status-pill.warn{color:#fde68a;background:rgba(245,158,11,.10);border-color:rgba(245,158,11,.32)}.status-pill.danger{color:#fecaca;background:rgba(244,63,94,.12);border-color:rgba(244,63,94,.32)}.health-table{width:100%;border-collapse:collapse;margin-top:10px}.health-table th,.health-table td{text-align:left;padding:9px 8px;border-bottom:1px solid rgba(148,163,184,.12);font-size:13px}.health-table th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}.maintenance-actions{display:grid;gap:10px}.maintenance-actions form{margin:0}.note-box{padding:11px 12px;border-radius:15px;border:1px solid rgba(56,189,248,.22);background:rgba(14,165,233,.08);color:#e0f2fe;line-height:1.45}.error-item{padding:10px;border:1px solid rgba(251,113,133,.20);background:rgba(244,63,94,.08);border-radius:14px;margin-top:8px}.error-item b{display:block}.error-item span{display:block;color:var(--muted);font-size:12px;margin-top:4px}.source-health-list{display:grid;gap:8px}.source-health-row{padding:10px;border-radius:14px;background:rgba(2,6,23,.24);border:1px solid rgba(148,163,184,.12)}.source-health-row .path{margin-top:6px}.system-path{word-break:break-all;color:var(--muted);font-size:12px}@media(max-width:1050px){.health-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.health-main{grid-template-columns:1fr}.check-row{grid-template-columns:1fr}}@media(max-width:650px){.health-grid{grid-template-columns:1fr}.settings-hero-actions,.maintenance-actions{display:grid;width:100%}.health-table{display:block;overflow:auto}}
</style>
</head><body>"""+NAV_HTML+"""
<main class=shell>
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<section class="panel settings-hero">
  <div><h1>System Health</h1><p class=muted style="margin:0">Advanced maintenance view for database, cache, sources, scheduled checks, and recent errors.</p></div>
  <div class=settings-hero-actions><a class=btn href="/dashboard">Admin dashboard</a><a class=btn href="/source-diagnostics">Libraries</a><a class=btn href="/server-control">Server</a><a class=btn href="/admin/first-run-setup">First-run preview</a><a class=btn href="/admin/migration-safety">Migration safety</a><a class=btn href="/admin/migration-dry-run">Migration dry run</a><a class=btn href="/admin/migration-backup">Migration backup</a><a class=btn href="/admin/migration-backup-verify">Verify backup</a><a class=btn href="/admin/migration-stage">Migration stage</a><a class=btn href="/admin/migration-stage-verify">Verify stage</a><a class=btn href="/admin/migration-cutover-readiness">Cutover readiness</a><a class=btn href="/admin/migration-cutover-plan">Cutover plan</a><a class=btn href="/admin/dmg-packaging">DMG packaging</a><a class=btn href="/admin/package-manifest">Package manifest</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/admin/test-dmg-status">Test DMG status</a><a class=btn href="/admin/app-support-prep">App Support prep</a><a class=btn href="/admin/app-support-status">App Support status</a></div>
</section>

<div class=health-grid>
  <section class="panel mini-stat"><b>{{ data.about.db_size }}</b><span>Database size</span></section>
  <section class="panel mini-stat"><b>{{ data.cache.size }}</b><span>Thumbnail cache</span></section>
  
  <section class="panel mini-stat"><b>{{ data.activity.errors }}</b><span>Logged errors</span></section>
</div>

<div class=health-main>
  <section class="panel settings-card">
    <h2>🩺 Health checks</h2>
    <div class=check-list>
      {% for check in data.checks %}
      <article class=check-row><b>{{ check.label }}</b><span class="status-pill {{ check.class }}">{{ check.status }}</span><span class=system-path>{{ check.message }}</span></article>
      {% endfor %}
    </div>
  </section>

  <aside class="panel settings-card">
    <h2>🧰 Maintenance actions</h2>
    <div class=maintenance-actions>
      
      <form method=post action="/maintenance/clear-artwork-cache" onsubmit="return confirm('Clear generated thumbnails? Original poster and fanart files will not be touched.');"><button class="danger" type=submit>Clear thumbnail cache</button></form>
      <a class=btn href="/scan-wait/refresh-all">Check all libraries now</a>
      <a class=btn href="/missing-items">Review missing items</a>
    </div>
    <div class=note-box style="margin-top:12px">Thumbnail cache clearing is safe: it only deletes generated files under <span class=system-path>{{ data.artwork_cache_path }}</span>. Artwork will regenerate when pages are opened.</div>
  </aside>
</div>

<div class=health-main style="margin-top:14px">
  <section class="panel settings-card">
    <h2>📁 Source folder status</h2>
    <div class=source-health-list>
      {% for source in data.source_rows %}
      <article class=source-health-row><div style="display:flex;justify-content:space-between;gap:10px"><b>{{ source.kind }} · {{ source.name }}</b><span class="status-pill {{ source.status_class }}">{{ source.status_text }}</span></div><div class=path>{{ source.path or 'No path set' }}</div><p class=muted style="margin:8px 0 0">Last scan: {{ source.last_scanned }} · Last folder signature: {{ source.last_signature_update }} · {{ 'Enabled' if source.enabled else 'Disabled' }}</p></article>
      {% else %}<p class=muted>No source folders configured yet.</p>{% endfor %}
    </div>
    <div class=toolbar><a class=btn href="/source-diagnostics">Open detailed diagnostics</a><a class=btn href="/settings#movies-library">Manage folders</a></div>
  </section>

  <aside class="panel settings-card">
    <h2>⚙️ Runtime summary</h2>
    <div class=kv>
      <div><b>Version</b><span>{{ data.about.version }}</span></div>
      <div><b>Port</b><span>{{ data.about.port }}</span></div>
      <div><b>Admin</b><span>{{ data.admin_username }}</span></div>
      <div><b>Viewers</b><span>{{ data.enabled_viewer_count }} enabled / {{ data.viewer_count }} total</span></div>
      <div><b>Scheduled scan</b><span>{{ data.scheduled.next_text }}</span></div>
      <div><b>Current scan</b><span>{{ data.scan_status.label }} — {{ data.scan_status.message }}</span></div>
      <div><b>Config</b><span>{{ data.config_path }}</span></div>
      <div><b>Caddyfile</b><span>{{ data.caddyfile_path }}</span></div>
    </div>
  </aside>
</div>

<div class=health-main style="margin-top:14px">
  <section class="panel settings-card">
    <h2>🗄️ Database tables</h2>
    <table class=health-table><thead><tr><th>Table</th><th>Rows</th></tr></thead><tbody>{% for table in data.tables %}<tr><td>{{ table.name }}</td><td>{{ table.count }}</td></tr>{% endfor %}</tbody></table>
  </section>

  <aside class="panel settings-card">
    <h2>🚨 Recent errors</h2>
    {% for row in data.recent_errors %}
    <article class=error-item><b>{{ row.message or row.action }}</b><span>{{ row.created_at }} · {{ row.area }} / {{ row.action }}</span></article>
    {% else %}<p class=muted>No recent error events.</p>{% endfor %}
    <div class=toolbar><a class=btn href="/activity-log?level=error">Open error log</a></div>
  </aside>
</div>
</main></body></html>
"""


SERVER_CONTROL_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Server Control</title>"""+BASE_STYLE+"""
<style>
.server-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:14px 0}.server-main{display:grid;grid-template-columns:minmax(0,1fr) minmax(340px,.8fr);gap:14px;align-items:start}.server-card h2{margin-bottom:8px}.server-status{display:grid;gap:10px}.server-row{display:grid;grid-template-columns:150px auto minmax(0,1fr);gap:10px;align-items:center;padding:12px;border:1px solid rgba(148,163,184,.14);background:rgba(2,6,23,.24);border-radius:16px}.server-row b{font-size:14px}.status-dot{display:inline-block;width:10px;height:10px;border-radius:99px;margin-right:8px;background:#f59e0b}.status-dot.good{background:#22c55e}.status-dot.warn{background:#f59e0b}.status-dot.bad{background:#ef4444}.urlbox{display:grid;gap:9px}.urlbox a{display:block;word-break:break-all;padding:12px;border-radius:15px;border:1px solid rgba(148,163,184,.16);background:rgba(15,23,42,.55);color:#dbeafe;text-decoration:none}.safe-note{padding:12px;border-radius:16px;border:1px solid rgba(245,158,11,.28);background:rgba(245,158,11,.09);color:#fde68a;line-height:1.45}.server-actions form{display:inline-block;margin:4px 6px 4px 0}.activity-list{display:grid;gap:8px}.activity-item{padding:10px 12px;border:1px solid rgba(148,163,184,.13);background:rgba(2,6,23,.20);border-radius:14px}.activity-item small{display:block;color:var(--muted);margin-top:3px}.details-box{margin-top:12px}.details-box summary{cursor:pointer;color:#bfdbfe}.logbox{white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:1.45;max-height:260px;overflow:auto;background:rgba(2,6,23,.55);border:1px solid rgba(148,163,184,.18);border-radius:14px;padding:12px;margin-top:10px}@media(max-width:1050px){.server-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.server-main{grid-template-columns:1fr}.server-row{grid-template-columns:1fr}}@media(max-width:650px){.server-summary{grid-template-columns:1fr}.settings-hero-actions{display:grid;width:100%}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<section class="settings-hero panel">
  <div><h1>Admin · Server</h1><p class=muted style="margin:0">Simple admin status for the Movie Library server. Use this to check whether the app is running, open the right URLs, and access safe maintenance actions.</p></div>
  <div class=settings-hero-actions><a class=btn href="/admin">Admin overview</a></div>
</section>

<div class=server-summary>
  <section class="panel mini-stat"><b>{{ 'Running' if data.pid else 'Unknown' }}</b><span>Flask server</span></section>
  <section class="panel mini-stat"><b>{{ data.service.port_check.status }}</b><span>Port {{ data.port }}</span></section>
  <section class="panel mini-stat"><b>{{ 'Running' if data.is_caddy_running else 'Not detected' }}</b><span>Shared Caddy</span></section>
  <section class="panel mini-stat"><b>{{ 'Running' if data.scan_status.running else 'Idle' }}</b><span>Scanner</span></section>
</div>

<div class=server-main>
  <section class="panel server-card">
    <h2>Current status</h2>
    <div class=server-status>
      <article class=server-row><b>Movie Library</b><span class="status-pill good">Running</span><span class=muted>Process ID {{ data.pid }} using {{ data.python }}</span></article>
      <article class=server-row><b>Web port</b><span class="status-pill {{ data.service.port_check.class }}">{{ data.service.port_check.status }}</span><span class=muted>{{ data.service.port_check.message }}</span></article>
      <article class=server-row><b>Caddy</b><span class="status-pill {{ 'good' if data.is_caddy_running else 'warn' }}">{{ 'Running' if data.is_caddy_running else 'Not detected' }}</span><span class=muted>Shared Caddyfile: {{ data.caddyfile_path }}</span></article>
      <article class=server-row><b>Scanner</b><span class="status-pill {{ 'warn' if data.scan_status.running else 'good' }}">{{ 'Running' if data.scan_status.running else 'Idle' }}</span><span class=muted>{{ data.scan_status.label or data.scan_status.message or 'No active scan.' }}</span></article>
    </div>
  </section>

  <aside class="panel server-card">
    <h2>Open the app</h2>
    <div class=urlbox>
      <a href="{{ data.urls.local }}"><b>Local</b><br>{{ data.urls.local }}</a>
      <a href="{{ data.urls.remote }}"><b>Remote</b><br>{{ data.urls.remote }}</a>
    </div>
    
  </aside>
</div>

<div class=server-main style="margin-top:14px">
  <section class="panel server-card">
    <h2>Restart controls</h2>
    <p class=muted>Use these only when the server is stuck or after copying in a new build.</p>
    <div class=safe-note>These buttons are admin-only. They do not overwrite the shared Caddyfile. The Caddy option validates/reloads the existing shared config.</div>
    <div class="toolbar server-actions" style="margin-top:12px">
      <form method=get action="/restart-web"><button class="btn danger" type=submit>Restart Movie Library only</button></form>
      <form method=get action="/restart-web-and-caddy"><button class="btn danger" type=submit>Restart Movie Library + reload Caddy</button></form>
    </div>
  </section>

  <aside class="panel server-card">
    <h2>Recent server activity</h2>
    {% if data.recent_server_events %}<div class=activity-list>{% for row in data.recent_server_events[:5] %}<div class=activity-item><b>{{ row.action }}</b><small>{{ row.created_at }} · {{ row.level }}</small><div class=muted>{{ row.message }}</div></div>{% endfor %}</div>{% else %}<p class=muted>No recent server activity recorded.</p>{% endif %}
    <div class=toolbar><a class=btn href="/reports/alerts">View alerts</a></div>
  </aside>
</div>

<section class="panel server-card" style="margin-top:14px">
  <h2>Technical details</h2>
  <p class=muted>These details are hidden by default so the page stays clean.</p>
  <details class=details-box><summary>Show process paths and log tails</summary>
    <div class=kv style="margin-top:12px">
      <div><b>App folder</b><span>{{ data.app_dir }}</span></div>
      <div><b>Python</b><span>{{ data.python }}</span></div>
      <div><b>Caddyfile</b><span>{{ data.caddyfile_path }}</span></div>
    </div>
    <h3>restart.log</h3><p class=muted>{{ data.logs.restart.path }} · {{ data.logs.restart.size }}</p><div class=logbox>{{ data.logs.restart.text or 'No restart log yet.' }}</div>
    <h3>movie_library_server.log</h3><p class=muted>{{ data.logs.runner.path }} · {{ data.logs.runner.size }}</p><div class=logbox>{{ data.logs.runner.text or 'No server runner log yet.' }}</div>
    <h3>caddy.log</h3><p class=muted>{{ data.logs.caddy.path }} · {{ data.logs.caddy.size }}</p><div class=logbox>{{ data.logs.caddy.text or 'No Caddy log found beside the shared Caddyfile.' }}</div>
  </details>
</section>
</main></body></html>
"""

MAC_SERVICE_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Mac Service</title>"""+BASE_STYLE+"""
<style>
.service-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:14px 0}.service-main{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(360px,.85fr);gap:14px;align-items:start}.readiness-list{display:grid;gap:9px}.readiness-row{display:grid;grid-template-columns:minmax(160px,.55fr) auto minmax(0,1fr);gap:10px;align-items:start;padding:11px 12px;border:1px solid rgba(148,163,184,.13);background:rgba(2,6,23,.25);border-radius:15px}.readiness-row b{font-size:14px}.codebox{white-space:pre-wrap;word-break:break-word;padding:12px;border-radius:15px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.50);color:#dbeafe;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.45}.service-note{padding:12px;border-radius:16px;border:1px solid rgba(52,211,153,.22);background:rgba(16,185,129,.10);color:#d1fae5;line-height:1.45}.warning-note{padding:12px;border-radius:16px;border:1px solid rgba(245,158,11,.28);background:rgba(245,158,11,.09);color:#fde68a;line-height:1.45}.step-list{display:grid;gap:10px}.step-list article{padding:12px;border-radius:16px;border:1px solid rgba(148,163,184,.13);background:rgba(2,6,23,.24)}.step-list h3{margin-bottom:6px}.service-path{font-size:12px;color:var(--muted);word-break:break-all;margin-top:4px}@media(max-width:1050px){.service-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.service-main{grid-template-columns:1fr}.readiness-row{grid-template-columns:1fr}}@media(max-width:650px){.service-grid{grid-template-columns:1fr}.settings-hero-actions{display:grid;width:100%}}
</style>
</head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=flash>{{ m }}</div>{% endfor %}{% endwith %}
<section class="panel settings-hero">
  <div><h1>Admin · Server · Mac Service</h1><p class=muted style="margin:0">Startup readiness for running Movie Library like a small local server. This does not overwrite or simplify your shared Caddyfile.</p></div>
  <div class=settings-hero-actions><a class=btn href="/server-control">Back to Server</a></div>
</section>

<div class=service-grid>
  <section class="panel mini-stat"><b>{{ data.port }}</b><span>Movie Library port</span></section>
  <section class="panel mini-stat"><b>{{ data.launchd_status.status }}</b><span>LaunchAgent status</span></section>
  <section class="panel mini-stat"><b>{{ data.caddy_status.status }}</b><span>Shared Caddy status</span></section>
</div>

<div class=service-main>
  <section class="panel settings-card">
    <h2>🖥️ Service readiness</h2>
    <div class=readiness-list>
      {% for row in data.readiness %}
      <article class=readiness-row><b>{{ row.label }}<div class=service-path>{{ row.path }}</div></b><span class="status-pill {{ row.class }}">{{ row.status }}</span><span class=muted>{{ row.message }}</span></article>
      {% endfor %}
    </div>
  </section>

  <aside class="panel settings-card">
    <h2>🔗 URLs</h2>
    <div class=kv>
      <div><b>Local</b><span>{{ data.urls.local }}</span></div>
      <div><b>Remote</b><span>{{ data.urls.remote }}</span></div>
      <div><b>Runner</b><span>{{ data.runner_path }}</span></div>
      <div><b>LaunchAgent</b><span>{{ data.launch_agent_path }}</span></div>
    </div>
    <div class=service-note style="margin-top:12px">The LaunchAgent starts only the Flask Movie Library server. Caddy remains shared infrastructure and should continue to be managed separately with Homebrew/Caddy commands.</div>
  </aside>
</div>

<div class=service-main style="margin-top:14px">
  <section class="panel settings-card">
    <h2>🚀 Install automatic startup</h2>
    <p class=muted>Run this once in Terminal on the Mac. It creates <b>~/Library/LaunchAgents/com.jay.movie-library.plist</b>, points it at this app folder, and starts it for the signed-in user.</p>
    <div class=codebox>{{ data.commands.install }}</div>
    <div class=warning-note style="margin-top:12px">Do not run this from a copied location unless that copied location is the real app folder you want launchd to start.</div>
  </section>

  <aside class="panel settings-card">
    <h2>🧹 Remove automatic startup</h2>
    <p class=muted>Run this if you later want to stop Movie Library from starting at login.</p>
    <div class=codebox>{{ data.commands.uninstall }}</div>
  </aside>
</div>

<div class=service-main style="margin-top:14px">
  <section class="panel settings-card">
    <h2>🧪 Manual server run</h2>
    <p class=muted>This runs the same server script without installing launchd. Useful for troubleshooting before enabling automatic startup.</p>
    <div class=codebox>{{ data.commands.manual_run }}</div>
  </section>

  <aside class="panel settings-card">
    <h2>🌐 Shared Caddy checks</h2>
    <p class=muted>These are the safe Caddy commands for your shared Caddyfile. They preserve both Movie Library and Tutor app routes.</p>
    <div class=codebox>{{ data.commands.caddy_validate }}</div>
    <div class=codebox style="margin-top:10px">{{ data.commands.caddy_reload }}</div>
  </aside>
</div>

<section class="panel settings-card" style="margin-top:14px">
  <h2>Recommended order</h2>
  <div class=step-list>
    <article><h3>1. Check this page after copying the build to the Mac</h3><p class=muted>Confirm the runner and installer files are present in <b>/Volumes/D/Webserver/movie library/app</b>.</p></article>
    <article><h3>2. Run the manual server command once</h3><p class=muted>Confirm the web app starts normally on port {{ data.port }} before installing launchd.</p></article>
    <article><h3>3. Install the LaunchAgent</h3><p class=muted>After installing, restart the Mac or log out/in and check this page again.</p></article>
    <article><h3>4. Keep Caddy separate</h3><p class=muted>Caddy should continue to use the shared <b>/Volumes/D/Webserver/Caddyfile</b>. This build does not replace it.</p></article>
  </div>
</section>
</main></body></html>
"""


MAC_APP_READINESS_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Mac App Readiness</title>"""+BASE_STYLE+"""
<style>
.readiness-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.path-list{display:grid;gap:10px}.path-card{padding:12px;border-radius:16px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.32)}.path-card b{display:block}.path-card code{display:block;margin-top:6px;white-space:pre-wrap;word-break:break-word;color:#bfdbfe}.step-list article{display:grid;grid-template-columns:46px 1fr;gap:12px;align-items:start}.step-badge{width:36px;height:36px;border-radius:999px;background:rgba(59,130,246,.18);border:1px solid rgba(59,130,246,.32);display:flex;align-items:center;justify-content:center;font-weight:900;color:#bfdbfe}.warning-note{padding:12px;border-radius:16px;background:rgba(245,158,11,.10);border:1px solid rgba(245,158,11,.30);color:#fde68a}@media(max-width:900px){.readiness-grid{grid-template-columns:1fr}.step-list article{grid-template-columns:1fr}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>Mac App Readiness</h1><p class=muted style="margin:0">Planning page for the cleaner future Movie Library.app layout. This page does not move files or change Caddy.</p></div>
  <div class=settings-hero-actions><a class="btn primary" href="/settings">Back to settings</a><a class=btn href="/server-control">Server</a><a class=btn href="/admin/first-run-setup">First-run preview</a><a class=btn href="/admin/migration-safety">Migration safety</a><a class=btn href="/admin/migration-dry-run">Migration dry run</a><a class=btn href="/admin/migration-backup">Migration backup</a><a class=btn href="/admin/migration-backup-verify">Verify backup</a><a class=btn href="/admin/migration-stage">Migration stage</a><a class=btn href="/admin/migration-stage-verify">Verify stage</a><a class=btn href="/admin/migration-cutover-readiness">Cutover readiness</a><a class=btn href="/admin/migration-cutover-plan">Cutover plan</a><a class=btn href="/admin/dmg-packaging">DMG packaging</a><a class=btn href="/admin/package-manifest">Package manifest</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/admin/test-dmg-status">Test DMG status</a><a class=btn href="/admin/app-support-prep">App Support prep</a><a class=btn href="/admin/app-support-status">App Support status</a></div>
</section>
<section class="panel settings-card">
  <h2>Recommended final layout</h2>
  <p class=muted>The clean Mac design is a normal app in Applications, with changing data stored outside the app bundle.</p>
  <div class=about-grid>
    <div class=mini-stat><b>{{ data.port }}</b><span>Flask port kept</span></div>
    <div class=mini-stat><b>{{ data.movie_sources|length }}</b><span>movie source folders</span></div>
    <div class=mini-stat><b>{{ data.tv_sources|length }}</b><span>TV source folders</span></div>
  </div>
  <div class=warning-note style="margin-top:12px">Caddy should remain separate and shared. It should keep routing <b>mjeromem75.dyndns.org</b> to Movie Library and <b>jeromem75.dyndns.org</b> to Tutor.</div>
</section>
<div class=readiness-grid style="margin-top:14px">
  <section class="panel settings-card"><h2>Current developer-style layout</h2><div class=path-list>{% for item in data.current %}<div class=path-card><b>{{ item.label }}</b><span class=muted>{{ item.status }}</span><code>{{ item.path }}</code></div>{% endfor %}</div></section>
  <section class="panel settings-card"><h2>Future Mac-style layout</h2><div class=path-list>{% for item in data.target %}<div class=path-card><b>{{ item.label }}</b><span class=muted>{{ item.status }}</span><code>{{ item.path }}</code></div>{% endfor %}</div></section>
</div>
<section class="panel settings-card" style="margin-top:14px">
  <h2>First-run setup wizard plan</h2>
  <div class=step-list>{% for item in data.checklist %}<article><div class=step-badge>{{ item.step }}</div><div><h3 style="margin:0 0 4px">{{ item.title }}</h3><p class=muted style="margin:0">{{ item.text }}</p></div></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>URLs that should remain</h2>
  <div class=kv><div><b>Local</b><span>{{ data.local_url }}</span></div><div><b>Remote HTTPS</b><span>{{ data.remote_url }}</span></div><div><b>Shared Caddyfile</b><span>{{ data.caddyfile_path }}</span></div><div><b>Future data folder exists now?</b><span>{{ 'Yes' if data.app_support_exists else 'No, not created yet' }}</span></div></div>
</section>
</main></body></html>
"""


FIRST_RUN_SETUP_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>First-run Setup Preview</title>"""+BASE_STYLE+"""
<style>
.setup-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.setup-card{padding:15px;border-radius:20px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.32)}.setup-card h3{margin:0 0 6px}.setup-status{display:inline-flex;margin-bottom:8px;padding:5px 9px;border-radius:999px;border:1px solid rgba(125,211,252,.26);background:rgba(14,165,233,.10);color:#dff7ff;font-size:12px;font-weight:850}.config-list{display:grid;gap:8px}.config-row{display:grid;grid-template-columns:180px minmax(0,1fr);gap:10px;padding:10px;border-radius:15px;border:1px solid rgba(148,163,184,.12);background:rgba(2,6,23,.24)}.config-row b{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em}.config-row span{word-break:break-word}.plan-list li{margin-bottom:8px;color:var(--muted);line-height:1.45}.preview-note{padding:12px;border-radius:16px;border:1px solid rgba(245,158,11,.30);background:rgba(245,158,11,.10);color:#fde68a}@media(max-width:900px){.setup-grid{grid-template-columns:1fr}.config-row{grid-template-columns:1fr;gap:4px}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>First-run Setup Preview</h1><p class=muted style="margin:0">Read-only preview of the future setup wizard for the packaged Movie Library.app.</p></div>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/mac-app-readiness">Mac app readiness</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card">
  <h2>What this will become</h2>
  <p class=muted>The final Mac app should guide a clean install through data folder, media folders, admin/viewer accounts, port 8765, and first scan. This page is deliberately read-only: it does not move files, edit Caddy, change accounts, or rescan anything.</p>
  <div class=preview-note>Caddy remains separate and shared with Tutor. The future wizard should confirm the remote URL but should not overwrite <b>/Volumes/D/Webserver/Caddyfile</b>.</div>
</section>
<div class=setup-grid style="margin-top:14px">
{% for card in data.setup_cards %}
  <article class="panel setup-card"><span class=setup-status>{{ card.status }}</span><h3>{{ loop.index }}. {{ card.title }}</h3><p class=muted>{{ card.text }}</p></article>
{% endfor %}
</div>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Current values the wizard would pre-fill</h2>
  <div class=config-list>{% for item in data.current_config %}<div class=config-row><b>{{ item.label }}</b><span>{{ item.value }}</span></div>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Next implementation steps</h2>
  <ol class=plan-list>{% for item in data.next_build_plan %}<li>{{ item }}</li>{% endfor %}</ol>
</section>
</main></body></html>
"""


MIGRATION_SAFETY_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Migration Safety Preview</title>"""+BASE_STYLE+"""
<style>
.migration-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.path-row{display:grid;grid-template-columns:1fr;gap:6px;padding:12px;border:1px solid rgba(15,23,42,.12);border-radius:14px;background:#fff}.path-row code{white-space:normal;word-break:break-word}.guard-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}.guard-list li{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}.safe-pill{display:inline-block;padding:5px 10px;border-radius:999px;background:#e8f5e9;color:#1b5e20;font-weight:800;font-size:12px}.warn-pill{display:inline-block;padding:5px 10px;border-radius:999px;background:#fff3cd;color:#7a4b00;font-weight:800;font-size:12px}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
<section class="panel settings-card">
  <span class=safe-pill>Read-only preview</span>
  <h1>Migration Safety Preview</h1>
  <p class=muted>This page prepares the future move from the current developer-style folder into a clean Mac app layout. It does not move, copy, delete, or edit anything.</p>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/first-run-setup">First-run preview</a><a class=btn href="/admin/mac-app-readiness">Mac app readiness</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Current → future data paths</h2>
  <p class=muted>Future packaged builds should keep the program in the .app bundle and keep changing data in Application Support.</p>
  <div class=migration-grid>{% for item in data.path_pairs %}<article class=path-row><b>{{ item.label }}</b><span class=warn-pill>{{ item.status }}</span><small class=muted>From</small><code>{{ item.source }}</code><small class=muted>To</small><code>{{ item.target }}</code></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Safe migration sequence</h2>
  <div class=step-list>{% for step in data.migration_steps %}<article><h3>{{ step.title }}</h3><p class=muted>{{ step.text }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Guardrails</h2>
  <ul class=guard-list>{% for item in data.guardrails %}<li>{{ item }}</li>{% endfor %}</ul>
  <p class=muted style="margin-top:12px">Shared Caddyfile: <b>{{ data.caddyfile_path }}</b></p>
</section>
</main></body></html>
"""


MIGRATION_DRY_RUN_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Migration Dry Run</title>"""+BASE_STYLE+"""
<style>
.dry-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.dry-card{padding:12px;border-radius:16px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.28)}.dry-card h3{margin:0 0 6px}.dry-path{font-size:12px;color:var(--muted);word-break:break-all}.log-box{white-space:pre-wrap;word-break:break-word;padding:12px;border-radius:15px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.50);color:#dbeafe;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.45}.warning-note{padding:12px;border-radius:16px;background:rgba(245,158,11,.10);border:1px solid rgba(245,158,11,.30);color:#fde68a}.ok-note{padding:12px;border-radius:16px;background:rgba(16,185,129,.10);border:1px solid rgba(16,185,129,.28);color:#d1fae5}@media(max-width:900px){.dry-grid{grid-template-columns:1fr}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>Migration Dry Run</h1><p class=muted style="margin:0">Read-only dry-run dashboard for the future move from the developer folder to Application Support.</p></div>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/app-support-status">App Support status</a><a class=btn href="/admin/app-support-prep">App Support prep</a><a class=btn href="/admin/migration-safety">Migration safety</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card">
  <h2>Overall dry-run readiness</h2>
  <div class=kv>
    <div><b>Status</b><span class="status-{{ data.overall_class }}">{{ data.overall_status }}</span></div>
    <div><b>Future app</b><span>{{ data.future_app }}</span></div>
    <div><b>Future data folder</b><span>{{ data.future_data }}</span></div>
    <div><b>Dry-run helper</b><span>{{ data.helper_path }}</span></div>
  </div>
  <div class=warning-note style="margin-top:12px">This is still not the migration wizard. It checks and reports only; it does not copy or move config.json, library.db, cache, media folders, Tutor files, or Caddy.</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Source files/folders that a later wizard would migrate</h2>
  <div class=dry-grid>{% for item in data.source_items %}<article class=dry-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }} · {{ item.size }}</p><p class=dry-path><b>From:</b> {{ item.source }}</p><p class=dry-path><b>To:</b> {{ item.target }}</p><p class=muted>{{ item.note }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Target Application Support skeleton</h2>
  <div class=dry-grid>{% for item in data.target_items %}<article class=dry-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=dry-path>{{ item.path }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Helper / generated notes</h2>
  <div class=dry-grid>{% for item in data.helper_items %}<article class=dry-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=dry-path>{{ item.path }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Next safe sequence</h2>
  <ol>{% for item in data.planned_sequence %}<li>{{ item }}</li>{% endfor %}</ol>
  <div class=ok-note>{% for item in data.safety %}<div>{{ item }}</div>{% endfor %}</div>
</section>
{% if data.summary or data.log_tail %}
<section class="panel settings-card" style="margin-top:14px">
  <h2>Dry-run report/log</h2>
  {% if data.summary %}<h3>Summary</h3><pre class=log-box>{{ data.summary }}</pre>{% endif %}
  {% if data.log_tail %}<h3>Log tail</h3><pre class=log-box>{{ data.log_tail }}</pre>{% endif %}
</section>
{% endif %}
</main></body></html>
"""


MIGRATION_BACKUP_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Migration Backup</title>"""+BASE_STYLE+"""
<style>
.backup-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}.backup-card{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:16px;padding:14px}.backup-card h3{margin:4px 0 8px}.backup-path{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;color:#334155;word-break:break-word;white-space:normal}.status-good{color:#166534;font-weight:800}.status-warn{color:#92400e;font-weight:800}.status-bad{color:#991b1b;font-weight:800}.backup-list{display:grid;gap:8px}.backup-list li{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}.log-box{white-space:pre-wrap;background:#0f172a;color:#e5e7eb;border-radius:14px;padding:12px;max-height:420px;overflow:auto;font-size:12px}.backup-table{display:grid;gap:8px}.backup-row{display:grid;grid-template-columns:1fr 120px 120px 120px;gap:8px;align-items:start;background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px}.backup-row b{word-break:break-word}@media(max-width:800px){.backup-row{grid-template-columns:1fr}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
<section class="panel settings-card">
  <span class="status-{{ data.overall_class }}">{{ data.overall_status }}</span>
  <h1>Migration Backup</h1>
  <p class=muted>This is the next safety step after the dry run. It prepares timestamped backup copies before any future migration wizard is allowed to switch live data locations.</p>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/migration-dry-run">Migration dry run</a><a class=btn href="/admin/migration-backup-verify">Verify backup</a><a class=btn href="/admin/migration-stage">Migration stage</a><a class=btn href="/admin/migration-stage-verify">Verify stage</a><a class=btn href="/admin/migration-cutover-readiness">Cutover readiness</a><a class=btn href="/admin/migration-cutover-plan">Cutover plan</a><a class=btn href="/admin/app-support-status">App Support status</a><a class=btn href="/admin/migration-safety">Migration safety</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Backup readiness</h2>
  <div class=backup-grid>{% for item in data.checks %}<article class=backup-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=backup-path>{{ item.path }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>What the helper creates</h2>
  <p class=muted>Target folder: <code>{{ data.backup_root }}</code></p>
  <ol class=backup-list>{% for item in data.sequence %}<li>{{ item }}</li>{% endfor %}</ol>
  <div class=backup-grid style="margin-top:12px">{% for item in data.safety %}<article class=backup-card>{{ item }}</article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Recent migration backups</h2>
  {% if data.latest_backups %}<div class=backup-table>{% for item in data.latest_backups %}<div class=backup-row><b>{{ item.name }}<br><span class=muted>{{ item.modified }}</span><br><span class=backup-path>{{ item.path }}</span></b><span>Config: {{ item.config }}</span><span>DB: {{ item.database }}</span><span>Checksums: {{ item.checksum }}</span></div>{% endfor %}</div>{% else %}<p class=muted>No timestamped migration backups found yet.</p>{% endif %}
</section>
{% if data.summary or data.log_tail %}
<section class="panel settings-card" style="margin-top:14px">
  <h2>Latest backup report/log</h2>
  {% if data.summary %}<h3>Summary</h3><pre class=log-box>{{ data.summary }}</pre>{% endif %}
  {% if data.log_tail %}<h3>Log tail</h3><pre class=log-box>{{ data.log_tail }}</pre>{% endif %}
</section>
{% endif %}
</main></body></html>
"""


MIGRATION_BACKUP_VERIFY_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Migration Backup Verification</title>"""+BASE_STYLE+"""
<style>
.verify-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}.verify-card{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:16px;padding:14px}.verify-card h3{margin:4px 0 8px}.verify-path{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;color:#334155;word-break:break-word;white-space:normal}.status-good{color:#166534;font-weight:800}.status-warn{color:#92400e;font-weight:800}.status-bad{color:#991b1b;font-weight:800}.verify-list{display:grid;gap:8px}.verify-list li{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}.log-box{white-space:pre-wrap;background:#0f172a;color:#e5e7eb;border-radius:14px;padding:12px;max-height:420px;overflow:auto;font-size:12px}.verify-row{display:grid;grid-template-columns:1fr 120px 120px 120px;gap:8px;align-items:start;background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px}.verify-row b{word-break:break-word}@media(max-width:800px){.verify-row{grid-template-columns:1fr}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
<section class="panel settings-card">
  <span class="status-{{ data.overall_class }}">{{ data.overall_status }}</span>
  <h1>Migration Backup Verification</h1>
  <p class=muted>This checks that the latest timestamped migration safety backup can be read and checksum-verified before any later migration step is considered.</p>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/migration-backup">Migration backup</a><a class=btn href="/admin/migration-stage">Migration stage</a><a class=btn href="/admin/migration-stage-verify">Verify stage</a><a class=btn href="/admin/migration-cutover-readiness">Cutover readiness</a><a class=btn href="/admin/migration-dry-run">Migration dry run</a><a class=btn href="/admin/app-support-status">App Support status</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Verification readiness</h2>
  <div class=verify-grid>{% for item in data.checks %}<article class=verify-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=verify-path>{{ item.path }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Verification sequence</h2>
  <p class=muted>Backup root: <code>{{ data.backup_root }}</code></p>
  <ol class=verify-list>{% for item in data.sequence %}<li>{{ item }}</li>{% endfor %}</ol>
  <div class=verify-grid style="margin-top:12px">{% for item in data.safety %}<article class=verify-card>{{ item }}</article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Recent migration backups</h2>
  {% if data.latest_backups %}<div class=verify-list>{% for item in data.latest_backups %}<div class=verify-row><b>{{ item.name }}<br><span class=muted>{{ item.modified }}</span><br><span class=verify-path>{{ item.path }}</span></b><span>Config: {{ item.config }}</span><span>DB: {{ item.database }}</span><span>Checksums: {{ item.checksum }}</span></div>{% endfor %}</div>{% else %}<p class=muted>No timestamped migration backups found yet.</p>{% endif %}
</section>
{% if data.summary or data.log_tail %}
<section class="panel settings-card" style="margin-top:14px">
  <h2>Latest verification report/log</h2>
  {% if data.summary %}<h3>Summary</h3><pre class=log-box>{{ data.summary }}</pre>{% endif %}
  {% if data.log_tail %}<h3>Log tail</h3><pre class=log-box>{{ data.log_tail }}</pre>{% endif %}
</section>
{% endif %}
</main></body></html>
"""



MIGRATION_STAGE_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Migration Stage</title>"""+BASE_STYLE+"""
<style>
.stage-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}.stage-card{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:16px;padding:14px}.stage-card h3{margin:4px 0 8px}.stage-path{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;color:#334155;word-break:break-word;white-space:normal}.status-good{color:#166534;font-weight:800}.status-warn{color:#92400e;font-weight:800}.status-bad{color:#991b1b;font-weight:800}.stage-list{display:grid;gap:8px}.stage-list li{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}.log-box{white-space:pre-wrap;background:#0f172a;color:#e5e7eb;border-radius:14px;padding:12px;max-height:420px;overflow:auto;font-size:12px}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
<section class="panel settings-card">
  <span class="status-{{ data.overall_class }}">{{ data.overall_status }}</span>
  <h1>Migration Stage</h1>
  <p class=muted>This is a non-live staging step. It copies config.json and library.db into Application Support/migration-staging/current so the future Mac app data location can be inspected before any live switch exists.</p>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/migration-backup-verify">Verify backup</a><a class=btn href="/admin/migration-backup">Migration backup</a><a class=btn href="/admin/migration-dry-run">Migration dry run</a><a class=btn href="/admin/app-support-status">App Support status</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Staging readiness</h2>
  <div class=stage-grid>{% for item in data.checks %}<article class=stage-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=stage-path>{{ item.path }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Staged files</h2>
  <p class=muted>Stage folder: <code>{{ data.current_stage_dir }}</code></p>
  <div class=stage-grid>{% for item in data.staged_files %}<article class=stage-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=stage-path>{{ item.path }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Safe sequence</h2>
  <ol class=stage-list>{% for item in data.sequence %}<li>{{ item }}</li>{% endfor %}</ol>
  <div class=stage-grid style="margin-top:12px">{% for item in data.safety %}<article class=stage-card>{{ item }}</article>{% endfor %}</div>
</section>
{% if data.summary or data.log_tail %}
<section class="panel settings-card" style="margin-top:14px">
  <h2>Latest staging report/log</h2>
  {% if data.summary %}<h3>Summary</h3><pre class=log-box>{{ data.summary }}</pre>{% endif %}
  {% if data.log_tail %}<h3>Log tail</h3><pre class=log-box>{{ data.log_tail }}</pre>{% endif %}
</section>
{% endif %}
</main></body></html>
"""


MIGRATION_STAGE_VERIFY_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Migration Stage Verification</title>"""+BASE_STYLE+"""
<style>
.stage-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}.stage-card{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:16px;padding:14px}.stage-card h3{margin:4px 0 8px}.stage-path{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;color:#334155;word-break:break-word;white-space:normal}.status-good{color:#166534;font-weight:800}.status-warn{color:#92400e;font-weight:800}.status-bad{color:#991b1b;font-weight:800}.stage-list{display:grid;gap:8px}.stage-list li{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}.log-box{white-space:pre-wrap;background:#0f172a;color:#e5e7eb;border-radius:14px;padding:12px;max-height:420px;overflow:auto;font-size:12px}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
<section class="panel settings-card">
  <span class="status-{{ data.overall_class }}">{{ data.overall_status }}</span>
  <h1>Migration Stage Verification</h1>
  <p class=muted>This checks the non-live staged copy in Application Support/migration-staging/current. It verifies the staged config/database files and their checksum metadata before any later live-data switch is considered.</p>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/migration-stage">Migration stage</a><a class=btn href="/admin/migration-backup-verify">Verify backup</a><a class=btn href="/admin/migration-dry-run">Migration dry run</a><a class=btn href="/admin/app-support-status">App Support status</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Verification readiness</h2>
  <p class=muted>Stage folder: <code>{{ data.current_stage_dir }}</code></p>
  <div class=stage-grid>{% for item in data.checks %}<article class=stage-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=stage-path>{{ item.path }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Safe sequence</h2>
  <ol class=stage-list>{% for item in data.sequence %}<li>{{ item }}</li>{% endfor %}</ol>
  <div class=stage-grid style="margin-top:12px">{% for item in data.safety %}<article class=stage-card>{{ item }}</article>{% endfor %}</div>
</section>
{% if data.summary or data.log_tail %}
<section class="panel settings-card" style="margin-top:14px">
  <h2>Latest stage verification report/log</h2>
  {% if data.summary %}<h3>Summary</h3><pre class=log-box>{{ data.summary }}</pre>{% endif %}
  {% if data.log_tail %}<h3>Log tail</h3><pre class=log-box>{{ data.log_tail }}</pre>{% endif %}
</section>
{% endif %}
</main></body></html>
"""



MIGRATION_CUTOVER_READINESS_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Migration Cutover Readiness</title>"""+BASE_STYLE+"""
<style>
.cutover-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}.cutover-card{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:16px;padding:14px}.cutover-card h3{margin:4px 0 8px}.cutover-path{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;color:#334155;word-break:break-word;white-space:normal}.status-good{color:#166534;font-weight:800}.status-warn{color:#92400e;font-weight:800}.status-bad{color:#991b1b;font-weight:800}.cutover-list{display:grid;gap:8px}.cutover-list li{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}.log-box{white-space:pre-wrap;background:#0f172a;color:#e5e7eb;border-radius:14px;padding:12px;max-height:420px;overflow:auto;font-size:12px}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
<section class="panel settings-card">
  <span class="status-{{ data.overall_class }}">{{ data.overall_status }}</span>
  <h1>Migration Cutover Readiness</h1>
  <p class=muted>This is the final read-only safety check before we later build any explicit live-data switch. It confirms the staged config/database still match the current live files and that the previous prep, backup, and stage verification summaries exist.</p>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/migration-stage-verify">Verify stage</a><a class=btn href="/admin/migration-cutover-plan">Cutover plan</a><a class=btn href="/admin/migration-stage">Migration stage</a><a class=btn href="/admin/migration-backup-verify">Verify backup</a><a class=btn href="/admin/app-support-status">App Support status</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Cutover readiness inputs</h2>
  <p class=muted>Staged folder: <code>{{ data.current_stage_dir }}</code></p>
  <div class=cutover-grid>{% for item in data.checks %}<article class=cutover-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=cutover-path>{{ item.path }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Safe sequence before any future live switch</h2>
  <ol class=cutover-list>{% for item in data.sequence %}<li>{{ item }}</li>{% endfor %}</ol>
  <div class=cutover-grid style="margin-top:12px">{% for item in data.safety %}<article class=cutover-card>{{ item }}</article>{% endfor %}</div>
</section>
{% if data.summary or data.log_tail %}
<section class="panel settings-card" style="margin-top:14px">
  <h2>Latest cutover readiness report/log</h2>
  {% if data.summary %}<h3>Summary</h3><pre class=log-box>{{ data.summary }}</pre>{% endif %}
  {% if data.log_tail %}<h3>Log tail</h3><pre class=log-box>{{ data.log_tail }}</pre>{% endif %}
</section>
{% endif %}
</main></body></html>
"""



MIGRATION_CUTOVER_PLAN_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Migration Cutover Plan</title>"""+BASE_STYLE+"""
<style>
.plan-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}.plan-card{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:16px;padding:14px}.plan-card h3{margin:4px 0 8px}.plan-path{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;color:#334155;word-break:break-word;white-space:normal}.status-good{color:#166534;font-weight:800}.status-warn{color:#92400e;font-weight:800}.status-bad{color:#991b1b;font-weight:800}.plan-list{display:grid;gap:8px}.plan-list li{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}.log-box{white-space:pre-wrap;background:#0f172a;color:#e5e7eb;border-radius:14px;padding:12px;max-height:420px;overflow:auto;font-size:12px}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
<section class="panel settings-card">
  <span class="status-{{ data.overall_class }}">{{ data.overall_status }}</span>
  <h1>Migration Cutover Plan</h1>
  <p class=muted>This creates/reads a human-readable plan for the later live-data switch to Application Support. It is still not the switch itself.</p>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/migration-cutover-readiness">Cutover readiness</a><a class=btn href="/admin/app-support-readiness">App Support readiness</a><a class=btn href="/admin/app-support-switch-preview">Switch preview</a><a class=btn href="/admin/migration-stage-verify">Verify stage</a><a class=btn href="/admin/migration-backup-verify">Verify backup</a><a class=btn href="/admin/app-support-status">App Support status</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Planned path split</h2>
  <div class=plan-grid>
    <article class=plan-card><h3>Current developer app folder</h3><p class=plan-path>{{ data.current_app_dir }}</p></article>
    <article class=plan-card><h3>Future app</h3><p class=plan-path>{{ data.future_app }}</p></article>
    <article class=plan-card><h3>Future data folder</h3><p class=plan-path>{{ data.future_data }}</p></article>
  </div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Cutover plan inputs</h2>
  <div class=plan-grid>{% for item in data.checks %}<article class=plan-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=plan-path>{{ item.path }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Future live-data targets</h2>
  <div class=plan-grid>
    <article class=plan-card><h3>Config</h3><p class=muted>Current</p><p class=plan-path>{{ data.current_config }}</p><p class=muted>Future</p><p class=plan-path>{{ data.future_config }}</p></article>
    <article class=plan-card><h3>Database</h3><p class=muted>Current</p><p class=plan-path>{{ data.current_database }}</p><p class=muted>Future</p><p class=plan-path>{{ data.future_database }}</p></article>
  </div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Planned switch sequence</h2>
  <ol class=plan-list>{% for item in data.plan_steps %}<li>{{ item }}</li>{% endfor %}</ol>
  <div class=plan-grid style="margin-top:12px">{% for item in data.safety %}<article class=plan-card>{{ item }}</article>{% endfor %}</div>
</section>
{% if data.summary or data.log_tail %}
<section class="panel settings-card" style="margin-top:14px">
  <h2>Latest cutover plan report/log</h2>
  {% if data.summary %}<h3>Summary</h3><pre class=log-box>{{ data.summary }}</pre>{% endif %}
  {% if data.log_tail %}<h3>Log tail</h3><pre class=log-box>{{ data.log_tail }}</pre>{% endif %}
</section>
{% endif %}
</main></body></html>
"""



DMG_PACKAGING_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>DMG Packaging Preview</title>"""+BASE_STYLE+"""
<style>
.package-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}.package-card{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:16px;padding:14px}.package-card h3{margin:6px 0}.badge-soft{display:inline-block;padding:5px 10px;border-radius:999px;background:#eef2ff;color:#3730a3;font-weight:800;font-size:12px}.package-list{display:grid;gap:8px}.package-list li{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}.check-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}.check-list div{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}.check-list span{display:block;color:#64748b;font-size:12px;margin-bottom:4px}.check-list code{white-space:normal;word-break:break-word}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
<section class="panel settings-card">
  <span class=badge-soft>Read-only packaging plan</span>
  <h1>DMG Packaging Preview</h1>
  <p class=muted>This page defines how the future clean Mac package should be built. It does not build a DMG, move files, edit Caddy, or change runtime paths.</p>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/first-run-setup">First-run preview</a><a class=btn href="/admin/migration-safety">Migration safety</a><a class=btn href="/admin/migration-dry-run">Migration dry run</a><a class=btn href="/admin/migration-backup">Migration backup</a><a class=btn href="/admin/migration-backup-verify">Verify backup</a><a class=btn href="/admin/migration-stage">Migration stage</a><a class=btn href="/admin/migration-stage-verify">Verify stage</a><a class=btn href="/admin/migration-cutover-readiness">Cutover readiness</a><a class=btn href="/admin/migration-cutover-plan">Cutover plan</a><a class=btn href="/admin/dmg-packaging">DMG packaging</a><a class=btn href="/admin/package-manifest">Package manifest</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/admin/test-dmg-status">Test DMG status</a><a class=btn href="/admin/app-support-prep">App Support prep</a><a class=btn href="/admin/app-support-status">App Support status</a><a class=btn href="/admin/mac-app-readiness">Mac app readiness</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Packaging shape</h2>
  <div class=package-grid>{% for item in data.package_items %}<article class=package-card><span class=badge-soft>{{ item.status }}</span><h3>{{ item.title }}</h3><p class=muted>{{ item.text }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Build sequence before creating the DMG</h2>
  <ol class=package-list>{% for item in data.dmg_sequence %}<li>{{ item }}</li>{% endfor %}</ol>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Guardrails</h2>
  <ul class=package-list>{% for item in data.package_guardrails %}<li>{{ item }}</li>{% endfor %}</ul>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Current and future paths</h2>
  <div class=check-list>{% for item in data.checks %}<div><span>{{ item.label }}</span><code>{{ item.value }}</code></div>{% endfor %}</div>
</section>
</main></body></html>
"""

PACKAGE_MANIFEST_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Package Manifest Preview</title>"""+BASE_STYLE+"""
<style>
.manifest-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.manifest-card{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:16px;padding:14px}.manifest-card h3{margin:6px 0}.manifest-pill{display:inline-block;padding:5px 10px;border-radius:999px;background:#ecfeff;color:#155e75;font-weight:800;font-size:12px}.manifest-list{display:grid;gap:8px}.manifest-list li{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}.manifest-path{display:grid;gap:6px}.manifest-path code{white-space:normal;word-break:break-word}.exists-good{color:#166534;font-weight:800}.exists-warn{color:#92400e;font-weight:800}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
<section class="panel settings-card">
  <span class=manifest-pill>Read-only manifest</span>
  <h1>Package Manifest Preview</h1>
  <p class=muted>This page defines what should go inside the future Movie Library.app, what should stay in Application Support, and what must be excluded from the DMG. It does not move files or build the DMG.</p>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/package-preflight">Package preflight</a><a class=btn href="/admin/dmg-packaging">DMG packaging</a><a class=btn href="/admin/migration-safety">Migration safety</a><a class=btn href="/admin/first-run-setup">First-run preview</a><a class=btn href="/admin/mac-app-readiness">Mac app readiness</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Future layout</h2>
  <div class=manifest-grid><article class=manifest-card><span class=manifest-pill>Program</span><h3>Movie Library.app</h3><div class=manifest-path><code>{{ data.future_app }}</code></div></article><article class=manifest-card><span class=manifest-pill>Data</span><h3>Application Support</h3><div class=manifest-path><code>{{ data.future_data }}</code></div></article></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Include inside Movie Library.app</h2>
  <div class=manifest-grid>{% for item in data.include_items %}<article class=manifest-card><h3>{{ item.label }}</h3><p class=muted>{{ item.reason }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Keep outside the app bundle</h2>
  <div class=manifest-grid>{% for item in data.external_items %}<article class=manifest-card><h3>{{ item.label }}</h3><div class=manifest-path><code>{{ item.target }}</code></div><p class=muted>{{ item.reason }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Exclude from the DMG/app bundle</h2>
  <ul class=manifest-list>{% for item in data.exclude_items %}<li>{{ item }}</li>{% endfor %}</ul>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Current readiness checks</h2>
  <div class=manifest-grid>{% for item in data.readiness_checks %}<article class=manifest-card><h3>{{ item.label }}</h3><div class=manifest-path><code>{{ item.value }}</code></div><p class="{% if item.exists %}exists-good{% else %}exists-warn{% endif %}">{% if item.exists %}Exists{% else %}Not found yet{% endif %}</p></article>{% endfor %}</div>
</section>
</main></body></html>
"""


PACKAGE_PREFLIGHT_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Package Preflight Preview</title>"""+BASE_STYLE+"""
<style>
.preflight-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.preflight-card{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:16px;padding:14px}.preflight-card h3{margin:6px 0}.preflight-pill{display:inline-block;padding:5px 10px;border-radius:999px;background:#eef2ff;color:#3730a3;font-weight:800;font-size:12px}.preflight-list{display:grid;gap:8px}.preflight-list li{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}.preflight-path{display:grid;gap:6px}.preflight-path code{white-space:normal;word-break:break-word}.status-good{color:#166534;font-weight:800}.status-warn{color:#92400e;font-weight:800}.small-muted{color:#64748b;font-size:12px}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
<section class="panel settings-card">
  <span class=preflight-pill>Read-only preflight</span>
  <h1>Package Preflight Preview</h1>
  <p class=muted>This page checks the pieces needed before moving from the test .app bundle stage to a cleaner packaged Mac app and later DMG. It does not build, copy, move, delete, or edit anything. The separate test DMG helper is local-only and creates packaging output in a build folder when you choose to run it on the Mac.</p>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/package-manifest">Package manifest</a><a class=btn href="/admin/runtime-paths">Runtime paths</a><a class=btn href="/admin/dmg-packaging">DMG packaging</a><a class=btn href="/admin/migration-safety">Migration safety</a><a class=btn href="/admin/first-run-setup">First-run preview</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Target layout</h2>
  <div class=preflight-grid><article class=preflight-card><span class=preflight-pill>Program</span><h3>Future app</h3><div class=preflight-path><code>{{ data.future_app }}</code></div></article><article class=preflight-card><span class=preflight-pill>Data</span><h3>Future data folder</h3><div class=preflight-path><code>{{ data.future_data }}</code></div></article><article class=preflight-card><span class=preflight-pill>Remote HTTPS</span><h3>Shared Caddy</h3><div class=preflight-path><code>{{ data.caddy.path }}</code></div><p class="{% if data.caddy.exists %}status-good{% else %}status-warn{% endif %}">{% if data.caddy.exists %}Caddyfile found{% else %}Caddyfile not found in this environment{% endif %}</p></article></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Required program files</h2>
  <div class=preflight-grid>{% for item in data.required_program_files %}<article class=preflight-card><h3>{{ item.label }}</h3><div class=preflight-path><code>{{ item.path }}</code></div><p class="{% if item.exists %}status-good{% else %}status-warn{% endif %}">{{ item.status }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Launcher permissions</h2>
  <p class=muted>After unzipping or copying the app, macOS may require execute permission on command files and app-bundle launchers. This section only checks permissions; use <code>Fix Movie Library Permissions.command</code> from the app folder if anything shows as needing attention.</p>
  <div class=preflight-grid>{% for item in data.permission_checks %}<article class=preflight-card><h3>{{ item.label }}</h3><div class=preflight-path><code>{{ item.path }}</code></div><p class="{% if item.executable %}status-good{% else %}status-warn{% endif %}">{{ item.status }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Data that must stay outside the app bundle</h2>
  <div class=preflight-grid>{% for item in data.data_files %}<article class=preflight-card><h3>{{ item.label }}</h3><div class=preflight-path><span class=small-muted>Current</span><code>{{ item.current }}</code><span class=small-muted>Future</span><code>{{ item.future }}</code></div><p class=muted>{{ item.message }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Exclude / protect</h2>
  <div class=preflight-grid>{% for item in data.exclude_checks %}<article class=preflight-card><span class=preflight-pill>{{ item.status }}</span><h3>{{ item.label }}</h3><p class=muted>{{ item.reason }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Admin API routes needed by the control app</h2>
  <ul class=preflight-list>{% for item in data.api_routes %}<li><code>{{ item }}</code></li>{% endfor %}</ul>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Next packaging sequence</h2>
  <ol class=preflight-list>{% for item in data.package_steps %}<li>{{ item }}</li>{% endfor %}</ol>
</section>
</main></body></html>
"""


TEST_DMG_STATUS_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Test DMG Status</title>"""+BASE_STYLE+"""
<style>
.dmg-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.dmg-card{border:1px solid rgba(15,23,42,.12);border-radius:16px;background:#fff;padding:14px;box-shadow:0 8px 22px rgba(15,23,42,.05)}.dmg-card h3{margin:0 0 8px}.dmg-pill{display:inline-flex;border-radius:999px;padding:4px 9px;background:#eef2ff;color:#1e3a8a;font-size:12px;font-weight:800}.status-good{color:#166534;font-weight:800}.status-warn{color:#92400e;font-weight:800}.dmg-path{white-space:normal;word-break:break-word}.log-box{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;border-radius:14px;padding:14px;overflow:auto;max-height:360px;font-size:12px}.small-muted{color:#64748b;font-size:12px}.dmg-list{display:grid;gap:8px}.dmg-list li{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:12px;padding:10px 12px}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
<section class="panel settings-card">
  <span class=dmg-pill>Read-only packaging status</span>
  <h1>Test DMG Status</h1>
  <p class=muted>This page shows whether the local test DMG packaging helpers have created their output. It does not build, mount, install, copy, move, delete, or edit anything.</p>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/package-preflight">Package preflight</a><a class=btn href="/admin/dmg-packaging">DMG packaging</a><a class=btn href="/admin/package-manifest">Package manifest</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Current test DMG</h2>
  <div class=dmg-grid>
    <article class=dmg-card><h3>DMG file</h3><p class="{% if data.dmg_info.exists %}status-good{% else %}status-warn{% endif %}">{% if data.dmg_info.exists %}Built{% else %}Not built yet{% endif %}</p><p class=dmg-path><code>{{ data.dmg_info.path }}</code></p><p class=small-muted>Size: {{ data.dmg_info.size }} · Modified: {{ data.dmg_info.modified }}</p></article>
    <article class=dmg-card><h3>Checksum</h3><p class="status-{{ data.dmg_info.checksum.class }}">{{ data.dmg_info.checksum.status }}</p><p class=small-muted>{{ data.dmg_info.checksum.message }}</p></article>
  </div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Artifacts</h2>
  <div class=dmg-grid>{% for item in data.artifacts %}<article class=dmg-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=dmg-path><code>{{ item.path }}</code></p><p class=small-muted>{{ item.size }} · {{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Expected Finder layout</h2>
  <p class=muted>The test image now checks the shape of a normal Mac installer window: app bundle, Applications alias, and clear notes. This still does not install or move your live data.</p>
  <div class=dmg-grid>{% for item in data.finder_layout %}<article class=dmg-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=small-muted>{{ item.message }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Next safe sequence</h2>
  <ol class=dmg-list>{% for item in data.next_steps %}<li>{{ item }}</li>{% endfor %}</ol>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Safety reminders</h2>
  <ul class=dmg-list>{% for item in data.safety %}<li>{{ item }}</li>{% endfor %}</ul>
</section>
{% if data.build_summary or data.verify_summary or data.install_notes or data.manifest_tail %}
<section class="panel settings-card" style="margin-top:14px"><h2>Generated summaries / manifest</h2>{% if data.build_summary %}<h3>Build summary</h3><pre class=log-box>{{ data.build_summary }}</pre>{% endif %}{% if data.verify_summary %}<h3>Verify summary</h3><pre class=log-box>{{ data.verify_summary }}</pre>{% endif %}{% if data.install_notes %}<h3>Install notes</h3><pre class=log-box>{{ data.install_notes }}</pre>{% endif %}{% if data.manifest_tail %}<h3>Contents manifest</h3><pre class=log-box>{{ data.manifest_tail }}</pre>{% endif %}</section>
{% endif %}
<section class="panel settings-card" style="margin-top:14px">
  <h2>Helper log tails</h2>
  <h3>Build log</h3><pre class=log-box>{{ data.build_log_tail or "No build log yet." }}</pre>
  <h3>Verify log</h3><pre class=log-box>{{ data.verify_log_tail or "No verify log yet." }}</pre>
</section>
</main></body></html>
"""



RUNTIME_PATHS_ADMIN_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Runtime Paths</title>"""+BASE_STYLE+"""
<style>
.runtime-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.runtime-card{padding:14px;border-radius:16px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.28)}.runtime-card h3{margin:0 0 7px}.runtime-path{font-size:12px;color:var(--muted);word-break:break-all}.status-good{color:#bbf7d0;font-weight:800}.status-warn{color:#fde68a;font-weight:800}.safe-note{padding:12px;border-radius:16px;border:1px solid rgba(52,211,153,.22);background:rgba(16,185,129,.10);color:#d1fae5;line-height:1.45}.warning-note{padding:12px;border-radius:16px;border:1px solid rgba(245,158,11,.28);background:rgba(245,158,11,.09);color:#fde68a;line-height:1.45}.runtime-kv{display:grid;gap:8px;margin-top:12px}.runtime-kv div{display:grid;grid-template-columns:170px minmax(0,1fr);gap:10px;padding:10px;border-radius:14px;background:rgba(2,6,23,.26);border:1px solid rgba(148,163,184,.12)}.runtime-kv b{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}.runtime-kv span{word-break:break-all}@media(max-width:750px){.runtime-kv div{grid-template-columns:1fr}.settings-hero-actions{display:grid;width:100%}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>Runtime Paths</h1><p class=muted style="margin:0">Read-only view of the resolved data paths used by the running app.</p></div>
  <div class=settings-hero-actions><a class=btn href="/admin/app-support-readiness">App Support readiness</a><a class=btn href="/admin/app-support-marker-preview">Marker preview</a><a class=btn href="/admin/app-support-switch-preview">Switch preview</a><a class=btn href="/admin/app-support-status">App Support status</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card">
  <h2>Runtime mode</h2>
  {% if data.warning %}<div class=warning-note>{{ data.warning }}</div>{% else %}<div class=safe-note>{{ data.safe_default_note }}</div>{% endif %}
  <div class=runtime-kv>
    <div><b>Mode</b><span>{{ data.runtime.mode }}</span></div>
    <div><b>Data folder</b><span>{{ data.runtime.data_dir }}</span></div>
    <div><b>Marker file</b><span>{{ data.runtime.marker_path }}</span></div>
  </div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Resolved paths</h2>
  <div class=runtime-grid>{% for item in data.path_items %}<article class=runtime-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }} · {{ item.kind }}</p><p class=runtime-path>{{ item.path }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Safety</h2>
  <div class=safe-note>This page and its API only read path metadata. They do not switch runtime paths, create folders, copy data, move data, delete data, migrate the database, edit Caddy, or change scanner logic.</div>
</section>
</main></body></html>
"""


APP_SUPPORT_SWITCH_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>App Support Switch Preview</title>"""+BASE_STYLE+"""
<style>
.switch-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.switch-card{padding:14px;border-radius:16px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.28)}.switch-card h3{margin:0 0 7px}.switch-path{font-size:12px;color:var(--muted);word-break:break-all}.switch-list{display:grid;gap:8px}.switch-list li{padding:10px 12px;border-radius:14px;border:1px solid rgba(148,163,184,.14);background:rgba(2,6,23,.24)}.warning-note{padding:12px;border-radius:16px;border:1px solid rgba(245,158,11,.28);background:rgba(245,158,11,.09);color:#fde68a;line-height:1.45}.safe-note{padding:12px;border-radius:16px;border:1px solid rgba(52,211,153,.22);background:rgba(16,185,129,.10);color:#d1fae5;line-height:1.45}@media(max-width:750px){.settings-hero-actions{display:grid;width:100%}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>App Support Switch Preview</h1><p class=muted style="margin:0">Read-only plan for a later explicit runtime mode switch.</p></div>
  <div class=settings-hero-actions><a class=btn href="/admin/app-support-readiness">App Support readiness</a><a class=btn href="/admin/app-support-marker-preview">Marker preview</a><a class=btn href="/admin/runtime-paths">Runtime paths</a><a class=btn href="/admin/migration-cutover-plan">Cutover plan</a><a class=btn href="/admin/app-support-status">App Support status</a></div>
</section>
<section class="panel settings-card">
  <h2>Current mode</h2>
  {% if data.app_support_active_warning %}<div class=warning-note>{{ data.app_support_active_warning }}</div>{% else %}<div class=safe-note>legacy-app-local remains the active safe default. This page does not enable app-support mode.</div>{% endif %}
  <div class=switch-grid style="margin-top:12px">{% for item in data.current_paths %}<article class=switch-card><h3>{{ item.label }}</h3><p class=switch-path>{{ item.path }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Future App Support targets</h2>
  <div class=switch-grid>{% for item in data.future_paths %}<article class=switch-card><h3>{{ item.label }}</h3><p class=switch-path>{{ item.path }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Future opt-in mechanism</h2>
  <div class=switch-grid>{% for item in data.marker_options %}<article class=switch-card><h3>{{ item.label }}</h3><p class=switch-path>{{ item.value }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Safety checklist before switching</h2>
  <ol class=switch-list>{% for item in data.checklist %}<li>{{ item }}</li>{% endfor %}</ol>
  <div class=warning-note style="margin-top:12px">{{ data.warning }}</div>
</section>
</main></body></html>
"""


APP_SUPPORT_READINESS_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>App Support Readiness</title>"""+BASE_STYLE+"""
<style>
.readiness-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}.readiness-card{padding:14px;border-radius:16px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.28)}.readiness-card h3{margin:0 0 7px}.readiness-path{font-size:12px;color:var(--muted);word-break:break-all}.status-good{color:#bbf7d0;font-weight:800}.status-warn{color:#fde68a;font-weight:800}.status-bad{color:#fecaca;font-weight:800}.warning-note{padding:12px;border-radius:16px;border:1px solid rgba(245,158,11,.28);background:rgba(245,158,11,.09);color:#fde68a;line-height:1.45}.safe-note{padding:12px;border-radius:16px;border:1px solid rgba(52,211,153,.22);background:rgba(16,185,129,.10);color:#d1fae5;line-height:1.45}.readiness-kv{display:grid;gap:8px;margin-top:12px}.readiness-kv div{display:grid;grid-template-columns:180px minmax(0,1fr);gap:10px;padding:10px;border-radius:14px;background:rgba(2,6,23,.26);border:1px solid rgba(148,163,184,.12)}.readiness-kv b{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}.readiness-kv span{word-break:break-all}@media(max-width:750px){.readiness-kv div{grid-template-columns:1fr}.settings-hero-actions{display:grid;width:100%}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>App Support Readiness</h1><p class=muted style="margin:0">Read-only validator for later manual cutover planning.</p></div>
  <div class=settings-hero-actions><a class=btn href="/admin/app-support-marker-preview">Marker preview</a><a class=btn href="/admin/app-support-switch-preview">Switch preview</a><a class=btn href="/admin/runtime-paths">Runtime paths</a><a class=btn href="/admin/migration-cutover-plan">Cutover plan</a></div>
</section>
<section class="panel settings-card">
  <h2>Readiness status</h2>
  {% if data.warning %}<div class=warning-note>{{ data.warning }}</div>{% else %}<div class=safe-note>{{ data.note }}</div>{% endif %}
  <div class=readiness-kv>
    <div><b>Status</b><span class="status-{{ data.readiness_class }}">{{ data.readiness_status }}</span></div>
    <div><b>Runtime mode</b><span>{{ data.runtime.mode }}</span></div>
    <div><b>Still legacy safe</b><span class="status-{{ data.legacy_safe_class }}">{{ data.legacy_safe_status }}</span></div>
    <div><b>Checks present</b><span>{{ data.ready_count }} / {{ data.total_count }}</span></div>
  </div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Validator checks</h2>
  <div class=readiness-grid>{% for item in data.checks %}<article class=readiness-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=readiness-path>{{ item.path }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Safety</h2>
  <div class=warning-note>This page must remain read-only. It does not perform cutover or prepare live data.</div>
</section>
</main></body></html>
"""


APP_SUPPORT_MARKER_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>App Support Marker Preview</title>"""+BASE_STYLE+"""
<style>
.marker-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.marker-card{padding:14px;border-radius:16px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.28)}.marker-card h3{margin:0 0 7px}.marker-path{font-size:12px;color:var(--muted);word-break:break-all}.marker-code{white-space:pre-wrap;word-break:break-word;padding:12px;border-radius:15px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.54);color:#dbeafe;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.45}.warning-note{padding:12px;border-radius:16px;border:1px solid rgba(245,158,11,.28);background:rgba(245,158,11,.09);color:#fde68a;line-height:1.45}.safe-note{padding:12px;border-radius:16px;border:1px solid rgba(52,211,153,.22);background:rgba(16,185,129,.10);color:#d1fae5;line-height:1.45}@media(max-width:750px){.settings-hero-actions{display:grid;width:100%}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:1100px">
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>App Support Marker Preview</h1><p class=muted style="margin:0">Read-only preview of the future runtime mode marker.</p></div>
  <div class=settings-hero-actions><a class=btn href="/admin/app-support-readiness">App Support readiness</a><a class=btn href="/admin/app-support-switch-preview">Switch preview</a><a class=btn href="/admin/runtime-paths">Runtime paths</a></div>
</section>
<section class="panel settings-card">
  <h2>Current runtime</h2>
  {% if data.app_support_active_warning %}<div class=warning-note>{{ data.app_support_active_warning }}</div>{% else %}<div class=safe-note>Current runtime mode: {{ data.runtime.mode }}. legacy-app-local remains the active safe default.</div>{% endif %}
  <div class=marker-grid style="margin-top:12px">
    <article class=marker-card><h3>App folder</h3><p class=marker-path>{{ data.runtime.app_dir }}</p></article>
    <article class=marker-card><h3>Data folder</h3><p class=marker-path>{{ data.runtime.data_dir }}</p></article>
    <article class=marker-card><h3>Cache folder</h3><p class=marker-path>{{ data.runtime.cache_dir }}</p></article>
    <article class=marker-card><h3>Logs folder</h3><p class=marker-path>{{ data.runtime.logs_dir }}</p></article>
  </div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Future marker</h2>
  <div class=marker-grid><article class=marker-card><h3>Marker file path</h3><p class=marker-path>{{ data.marker_path }}</p></article><article class=marker-card><h3>Config flag preview</h3><p class=marker-path>{{ data.config_flag_text }}</p></article></div>
  <h3>Preview JSON only</h3>
  <pre class=marker-code>{{ data.marker_json }}</pre>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Current legacy paths</h2>
  <div class=marker-grid>{% for item in data.current_paths %}<article class=marker-card><h3>{{ item.label }}</h3><p class=marker-path>{{ item.path }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Future App Support paths</h2>
  <div class=marker-grid>{% for item in data.future_paths %}<article class=marker-card><h3>{{ item.label }}</h3><p class=marker-path>{{ item.path }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Warnings</h2>
  <div class=warning-note>{{ data.warning }}</div>
</section>
</main></body></html>
"""


APP_SUPPORT_STATUS_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Application Support Status</title>"""+BASE_STYLE+"""
<style>
.support-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.support-card{padding:12px;border-radius:16px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.28)}.support-card h3{margin:0 0 6px}.support-path{font-size:12px;color:var(--muted);word-break:break-all}.support-list{display:grid;gap:10px}.log-box{white-space:pre-wrap;word-break:break-word;padding:12px;border-radius:15px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.50);color:#dbeafe;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.45}.warning-note{padding:12px;border-radius:16px;background:rgba(245,158,11,.10);border:1px solid rgba(245,158,11,.30);color:#fde68a}.ok-note{padding:12px;border-radius:16px;background:rgba(16,185,129,.10);border:1px solid rgba(16,185,129,.28);color:#d1fae5}@media(max-width:950px){.support-grid{grid-template-columns:1fr}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>Application Support Status</h1><p class=muted style="margin:0">Read-only verification dashboard for the future macOS data folder skeleton.</p></div>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/app-support-prep">App Support prep</a><a class=btn href="/admin/runtime-paths">Runtime paths</a><a class=btn href="/admin/app-support-switch-preview">Switch preview</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/admin/migration-safety">Migration safety</a><a class=btn href="/admin/migration-dry-run">Migration dry run</a><a class=btn href="/admin/migration-backup">Migration backup</a><a class=btn href="/admin/migration-backup-verify">Verify backup</a><a class=btn href="/admin/migration-stage">Migration stage</a><a class=btn href="/admin/migration-stage-verify">Verify stage</a><a class=btn href="/admin/migration-cutover-readiness">Cutover readiness</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card">
  <h2>Overall readiness</h2>
  <div class=kv>
    <div><b>Status</b><span class="status-{{ data.overall_class }}">{{ data.overall_status }}</span></div>
    <div><b>Future app</b><span>{{ data.future_app }}</span></div>
    <div><b>Future data folder</b><span>{{ data.future_data }}</span></div>
    <div><b>Verify helper</b><span>{{ data.verify_helper }}</span></div>
  </div>
  <div class=warning-note style="margin-top:12px">This is only a verification step. It checks the future data folder skeleton and writes notes; it does not migrate your live database/config/cache.</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Required skeleton items</h2>
  <div class=support-grid>{% for item in data.required_items %}<article class=support-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=support-path>{{ item.path }}</p><p class=muted>{{ item.message }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Optional/future migration items</h2>
  <div class=support-grid>{% for item in data.optional_items %}<article class=support-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=support-path>{{ item.path }}</p><p class=muted>{{ item.message }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Helpers and logs</h2>
  <div class=support-grid>{% for item in data.helper_items %}<article class=support-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=support-path>{{ item.path }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Current live data still stays put</h2>
  <div class=support-grid>{% for item in data.current_live_items %}<article class=support-card><h3>{{ item.label }}</h3><p class="status-{{ 'good' if item.exists else 'warn' }}">{{ 'Present' if item.exists else 'Missing' }}</p><p class=support-path>{{ item.path }}</p><p class=muted>{{ item.size }} · {{ item.message }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Next safe sequence</h2>
  <ol>{% for item in data.next_steps %}<li>{{ item }}</li>{% endfor %}</ol>
  <div class=ok-note>{% for item in data.safety %}<div>{{ item }}</div>{% endfor %}</div>
</section>
{% if data.verify_summary or data.prep_summary or data.readme or data.verify_log_tail or data.prep_log_tail %}
<section class="panel settings-card" style="margin-top:14px">
  <h2>Generated notes / logs</h2>
  {% if data.verify_summary %}<h3>Verify summary</h3><pre class=log-box>{{ data.verify_summary }}</pre>{% endif %}
  {% if data.prep_summary %}<h3>Prep summary</h3><pre class=log-box>{{ data.prep_summary }}</pre>{% endif %}
  {% if data.verify_summary %}<h3>Verify summary</h3><pre class=log-box>{{ data.verify_summary }}</pre>{% endif %}
  {% if data.readme %}<h3>Data-folder README</h3><pre class=log-box>{{ data.readme }}</pre>{% endif %}
  {% if data.verify_log_tail %}<h3>Verify helper log</h3><pre class=log-box>{{ data.verify_log_tail }}</pre>{% endif %}
  {% if data.prep_log_tail %}<h3>Prep helper log</h3><pre class=log-box>{{ data.prep_log_tail }}</pre>{% endif %}
  {% if data.verify_log_tail %}<h3>Verify helper log</h3><pre class=log-box>{{ data.verify_log_tail }}</pre>{% endif %}
</section>
{% endif %}
</main></body></html>
"""


APP_SUPPORT_PREP_PREVIEW_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>Application Support Prep</title>"""+BASE_STYLE+"""
<style>
.support-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.support-card{padding:12px;border-radius:16px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.28)}.support-card h3{margin:0 0 6px}.support-path{font-size:12px;color:var(--muted);word-break:break-all}.support-list{display:grid;gap:10px}.log-box{white-space:pre-wrap;word-break:break-word;padding:12px;border-radius:15px;border:1px solid rgba(148,163,184,.16);background:rgba(2,6,23,.50);color:#dbeafe;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:1.45}.warning-note{padding:12px;border-radius:16px;background:rgba(245,158,11,.10);border:1px solid rgba(245,158,11,.30);color:#fde68a}.ok-note{padding:12px;border-radius:16px;background:rgba(16,185,129,.10);border:1px solid rgba(16,185,129,.28);color:#d1fae5}@media(max-width:950px){.support-grid{grid-template-columns:1fr}}
</style></head><body>"""+NAV_HTML+"""
<main class=shell>
"""+ADMIN_TABS_HTML+"""
<section class="panel settings-hero">
  <div><h1>Application Support Prep</h1><p class=muted style="margin:0">Readiness page for the future macOS data folder. This page does not create folders by itself and does not move live data.</p></div>
  <div class=settings-hero-actions><a class="btn primary" href="/admin/app-support-status">App Support status</a><a class=btn href="/admin/package-preflight">Package preflight</a><a class=btn href="/admin/package-manifest">Package manifest</a><a class=btn href="/admin/migration-safety">Migration safety</a><a class=btn href="/admin/migration-dry-run">Migration dry run</a><a class=btn href="/admin/test-dmg-status">Test DMG status</a><a class=btn href="/settings">Settings</a></div>
</section>
<section class="panel settings-card">
  <h2>Future data location</h2>
  <p class=muted>The final Mac design should keep program files in <b>Movie Library.app</b> and changing data in Application Support.</p>
  <div class=kv>
    <div><b>Future app</b><span>{{ data.future_app }}</span></div>
    <div><b>Future data folder</b><span>{{ data.future_data }}</span></div>
    <div><b>Prep helper</b><span>{{ data.helper_path }}</span></div>
    <div><b>Current database</b><span>{{ data.current_database }}</span></div>
    <div><b>Future database</b><span>{{ data.future_database }}</span></div>
  </div>
  <div class=warning-note style="margin-top:12px">The prep helper creates the folder skeleton only. It does not copy <b>library.db</b>, <b>config.json</b>, artwork cache, media folders, Tutor files, or the shared Caddyfile.</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Folder skeleton</h2>
  <div class=support-grid>{% for item in data.folders %}<article class=support-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=support-path>{{ item.path }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Helper / notes</h2>
  <div class=support-grid>{% for item in data.files %}<article class=support-card><h3>{{ item.label }}</h3><p class="status-{{ item.class }}">{{ item.status }}</p><p class=support-path>{{ item.path }}</p><p class=muted>{{ item.modified }}</p></article>{% endfor %}</div>
</section>
<section class="panel settings-card" style="margin-top:14px">
  <h2>Next safe sequence</h2>
  <ol>{% for item in data.next_steps %}<li>{{ item }}</li>{% endfor %}</ol>
  <div class=ok-note>{% for item in data.safety %}<div>{{ item }}</div>{% endfor %}</div>
</section>
{% if data.prep_summary or data.verify_summary or data.readme or data.prep_log_tail or data.verify_log_tail %}
<section class="panel settings-card" style="margin-top:14px">
  <h2>Generated prep files / logs</h2>
  {% if data.prep_summary %}<h3>Prep summary</h3><pre class=log-box>{{ data.prep_summary }}</pre>{% endif %}
  {% if data.verify_summary %}<h3>Verify summary</h3><pre class=log-box>{{ data.verify_summary }}</pre>{% endif %}
  {% if data.readme %}<h3>Data-folder README</h3><pre class=log-box>{{ data.readme }}</pre>{% endif %}
  {% if data.prep_log_tail %}<h3>Prep helper log</h3><pre class=log-box>{{ data.prep_log_tail }}</pre>{% endif %}
  {% if data.verify_log_tail %}<h3>Verify helper log</h3><pre class=log-box>{{ data.verify_log_tail }}</pre>{% endif %}
</section>
{% endif %}
</main></body></html>
"""


ABOUT_HTML = """
<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1"><title>About</title>"""+BASE_STYLE+"""</head><body>"""+NAV_HTML+"""
<main class=shell style="max-width:980px">
<section class="panel settings-card">
  <h1>About / Version</h1>
  <p class=muted>This confirms which Movie Library build is currently running.</p>
  <div class=about-grid>
    <div class=mini-stat><b>{{ about.movie_count }}</b><span>Movies</span></div>
    <div class=mini-stat><b>{{ about.tv_show_count }}</b><span>TV shows</span></div>
    <div class=mini-stat><b>{{ about.episode_count }}</b><span>Episodes</span></div>
  </div>
  <div class=kv>
    <div><b>Version</b><span>{{ about.version }}</span></div>
    <div><b>App path</b><span>{{ about.app_path }}</span></div>
    <div><b>Movie root</b><span>{{ about.movie_root }}</span></div>
    <div><b>Web root</b><span>{{ about.web_root }}</span></div>
    <div><b>Database</b><span>{{ about.db_path }} · {{ about.db_size }}</span></div>
    <div><b>Port</b><span>{{ about.port }}</span></div>
  </div>
  <div class=toolbar><a class="btn primary" href="/settings">Back to settings</a><a class=btn href="/_ui-version">Plain version check</a></div>
</section>
</main></body></html>
"""





@app.route("/admin/mac-app-readiness")
def mac_app_readiness():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(MAC_APP_READINESS_HTML, active="settings", admin_section="settings", data=get_mac_app_readiness_data())

@app.route("/api/admin/mac-app-readiness")
def api_admin_mac_app_readiness():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_mac_app_readiness_data()})


@app.route("/admin/first-run-setup")
def first_run_setup_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(FIRST_RUN_SETUP_PREVIEW_HTML, active="settings", admin_section="settings", data=get_first_run_setup_preview_data())

@app.route("/api/admin/first-run-setup-preview")
def api_admin_first_run_setup_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_first_run_setup_preview_data()})


@app.route("/admin/migration-safety")
def migration_safety_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(MIGRATION_SAFETY_PREVIEW_HTML, active="settings", admin_section="settings", data=get_migration_safety_preview_data())

@app.route("/api/admin/migration-safety-preview")
def api_admin_migration_safety_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_migration_safety_preview_data()})


@app.route("/admin/migration-dry-run")
def migration_dry_run_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(MIGRATION_DRY_RUN_PREVIEW_HTML, active="settings", admin_section="settings", data=get_migration_dry_run_preview_data())

@app.route("/api/admin/migration-dry-run-preview")
def api_admin_migration_dry_run_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_migration_dry_run_preview_data()})



@app.route("/admin/migration-backup")
def migration_backup_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(MIGRATION_BACKUP_PREVIEW_HTML, active="settings", admin_section="settings", data=get_migration_backup_preview_data())

@app.route("/api/admin/migration-backup-preview")
def api_admin_migration_backup_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_migration_backup_preview_data()})

@app.route("/admin/migration-backup-verify")
def migration_backup_verify_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(MIGRATION_BACKUP_VERIFY_PREVIEW_HTML, active="settings", admin_section="settings", data=get_migration_backup_verify_preview_data())

@app.route("/api/admin/migration-backup-verify-preview")
def api_admin_migration_backup_verify_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_migration_backup_verify_preview_data()})

@app.route("/admin/migration-stage")
def migration_stage_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(MIGRATION_STAGE_PREVIEW_HTML, active="settings", admin_section="settings", data=get_migration_stage_preview_data())

@app.route("/api/admin/migration-stage-preview")
def api_admin_migration_stage_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_migration_stage_preview_data()})

@app.route("/admin/migration-stage-verify")
def migration_stage_verify_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(MIGRATION_STAGE_VERIFY_PREVIEW_HTML, active="settings", admin_section="settings", data=get_migration_stage_verify_preview_data())

@app.route("/api/admin/migration-stage-verify-preview")
def api_admin_migration_stage_verify_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_migration_stage_verify_preview_data()})


@app.route("/admin/migration-cutover-readiness")
def migration_cutover_readiness_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(MIGRATION_CUTOVER_READINESS_PREVIEW_HTML, active="settings", admin_section="settings", data=get_migration_cutover_readiness_preview_data())

@app.route("/api/admin/migration-cutover-readiness-preview")
def api_admin_migration_cutover_readiness_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_migration_cutover_readiness_preview_data()})


@app.route("/admin/migration-cutover-plan")
def migration_cutover_plan_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(MIGRATION_CUTOVER_PLAN_PREVIEW_HTML, active="settings", admin_section="settings", data=get_migration_cutover_plan_preview_data())

@app.route("/api/admin/migration-cutover-plan-preview")
def api_admin_migration_cutover_plan_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_migration_cutover_plan_preview_data()})


@app.route("/admin/dmg-packaging")
def dmg_packaging_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(DMG_PACKAGING_PREVIEW_HTML, active="settings", admin_section="settings", data=get_dmg_packaging_preview_data())

@app.route("/api/admin/dmg-packaging-preview")
def api_admin_dmg_packaging_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_dmg_packaging_preview_data()})



@app.route("/admin/package-manifest")
def package_manifest_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(PACKAGE_MANIFEST_PREVIEW_HTML, active="settings", admin_section="settings", data=get_package_manifest_preview_data())

@app.route("/api/admin/package-manifest-preview")
def api_admin_package_manifest_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_package_manifest_preview_data()})


@app.route("/admin/package-preflight")
def package_preflight_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(PACKAGE_PREFLIGHT_PREVIEW_HTML, active="settings", admin_section="settings", data=get_package_preflight_preview_data())

@app.route("/api/admin/package-preflight-preview")
def api_admin_package_preflight_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_package_preflight_preview_data()})


@app.route("/admin/test-dmg-status")
def test_dmg_status_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(TEST_DMG_STATUS_PREVIEW_HTML, active="settings", admin_section="settings", data=get_test_dmg_status_preview_data())

@app.route("/api/admin/test-dmg-status-preview")
def api_admin_test_dmg_status_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_test_dmg_status_preview_data()})


@app.route("/admin/runtime-paths")
def runtime_paths_admin():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(RUNTIME_PATHS_ADMIN_HTML, active="settings", admin_section="settings", data=get_runtime_paths_admin_data())


@app.route("/api/admin/runtime-paths")
def api_admin_runtime_paths():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_runtime_paths_admin_data()})


@app.route("/admin/app-support-switch-preview")
def app_support_switch_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(APP_SUPPORT_SWITCH_PREVIEW_HTML, active="settings", admin_section="settings", data=get_app_support_switch_preview_data())


@app.route("/api/admin/app-support-switch-preview")
def api_admin_app_support_switch_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_app_support_switch_preview_data()})


@app.route("/admin/app-support-readiness")
def app_support_readiness():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(APP_SUPPORT_READINESS_HTML, active="settings", admin_section="settings", data=get_app_support_readiness_data())


@app.route("/api/admin/app-support-readiness")
def api_admin_app_support_readiness():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_app_support_readiness_data()})


@app.route("/admin/app-support-marker-preview")
def app_support_marker_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(APP_SUPPORT_MARKER_PREVIEW_HTML, active="settings", admin_section="settings", data=get_app_support_marker_preview_data())


@app.route("/api/admin/app-support-marker-preview")
def api_admin_app_support_marker_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_app_support_marker_preview_data()})


@app.route("/admin/app-support-prep")
def app_support_prep_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(APP_SUPPORT_PREP_PREVIEW_HTML, active="settings", admin_section="settings", data=get_app_support_prep_preview_data())

@app.route("/api/admin/app-support-prep-preview")
def api_admin_app_support_prep_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_app_support_prep_preview_data()})


@app.route("/admin/app-support-status")
def app_support_status_preview():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(APP_SUPPORT_STATUS_PREVIEW_HTML, active="settings", admin_section="settings", data=get_app_support_status_preview_data())

@app.route("/api/admin/app-support-status-preview")
def api_admin_app_support_status_preview():
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return jsonify({"ok": True, "data": get_app_support_status_preview_data()})


@app.route("/server-control")
def server_control():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(SERVER_CONTROL_HTML, active="server", admin_section="server", data=get_server_control_data())


@app.route("/mac-service")
def mac_service():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    flash("Mac service details are hidden from the simplified admin console. Use Server for day-to-day status and restart controls.")
    return redirect(url_for("server_control"))


@app.route("/system-health")
def system_health():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    flash("System Health is now hidden from the normal admin console. Use Server, Libraries, Alerts, and Scans instead.")
    return redirect(url_for("dashboard"))


@app.route("/maintenance/clear-artwork-cache", methods=["POST"])
def maintenance_clear_artwork_cache():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    if get_scan_status().get("running"):
        flash("A scan is currently running. Wait for it to finish before clearing the cache.")
        return redirect(url_for("server_control"))
    removed = clear_artwork_cache()
    flash(f"Thumbnail cache cleared: {removed['files']} generated files removed ({removed['size']}).")
    record_activity("success", "system", "artwork_cache_cleared", f"Thumbnail cache cleared: {removed['files']} files removed ({removed['size']}).", removed)
    return redirect(url_for("server_control"))


@app.route("/about")
def about():
    if not require_login():
        return redirect(url_for("login"))
    return render_template_string(ABOUT_HTML, active="settings", about=get_about_info())

@app.route("/admin")
@app.route("/dashboard")
def dashboard():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(DASHBOARD_HTML, active="dashboard", admin_section="overview", data=get_library_dashboard_data())


@app.route("/source-diagnostics")
def source_diagnostics():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(SOURCE_DIAGNOSTICS_HTML, active="source_diagnostics", admin_section="libraries", data=get_source_diagnostics_data(deep=(request.args.get("deep") == "1")))


@app.route("/settings/schedule", methods=["POST"])
def settings_schedule():
    if not require_login():
        return redirect(url_for("login"))
    settings = get_scheduled_scan_settings()
    settings["enabled"] = request.form.get("enabled") == "on"
    settings["frequency"] = request.form.get("frequency") if request.form.get("frequency") in {"daily", "interval"} else "daily"
    time_text = (request.form.get("time_of_day") or "03:00").strip()
    if not re.match(r"^\d{2}:\d{2}$", time_text):
        time_text = "03:00"
    settings["time_of_day"] = time_text
    try:
        settings["interval_minutes"] = max(15, int(request.form.get("interval_minutes") or settings.get("interval_minutes") or 360))
    except Exception:
        settings["interval_minutes"] = 360
    save_scheduled_scan_settings(settings)
    flash("Scheduled library change check saved.")
    record_activity("success", "settings", "schedule_saved", "Scheduled library change check saved", settings)
    return redirect(url_for("settings"))



@app.route("/admin/users")
def admin_users():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    return render_template_string(
        ADMIN_USERS_HTML,
        active="dashboard",
        admin_section="users",
        username=cfg.get("web", {}).get("username", "admin"),
        viewers=[dict(viewer, index=i + 1) for i, viewer in enumerate(_normalise_viewers(cfg.get("web", {})))],
        port=cfg.get("web", {}).get("port", 8765),
    )

@app.route("/settings")
def settings():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    movie_source_values = get_movie_sources()
    tv_source_values = get_tv_sources()
    movie_enabled_count, movie_last_scanned = _source_summary(movie_source_values)
    tv_enabled_count, tv_last_scanned = _source_summary(tv_source_values)
    return render_template_string(
        SETTINGS_HTML,
        active="settings",
        admin_section="settings",
        folder=movie_source_values[0].get("path", "") if movie_source_values else "",
        tv_folder=tv_source_values[0].get("path", "") if tv_source_values else "",
        movie_sources=[dict(source, index=i + 1) for i, source in enumerate(movie_source_values)],
        tv_sources=[dict(source, index=i + 1) for i, source in enumerate(tv_source_values)],
        movie_enabled_count=movie_enabled_count,
        tv_enabled_count=tv_enabled_count,
        movie_last_scanned=movie_last_scanned,
        tv_last_scanned=tv_last_scanned,
        current_folder_display=(movie_source_values[0].get("path", "") if movie_source_values else "") or "Not set",
        current_tv_folder_display=(tv_source_values[0].get("path", "") if tv_source_values else "") or "Not set",
        username=cfg.get("web", {}).get("username", "admin"),
        viewers=[dict(viewer, index=i + 1) for i, viewer in enumerate(_normalise_viewers(cfg.get("web", {})))],
        port=cfg.get("web", {}).get("port", 8765),
        scan_status=get_scan_status(),
        about=get_about_info(),
        report_summary=get_report_summary(),
        scheduled_scan=scheduled_scan_summary()
    )


@app.route("/settings/library", methods=["POST"])
def settings_library():
    return settings_library_source(1)

@app.route("/settings/library-source/<int:index>", methods=["POST"])
def settings_library_source(index):
    if not require_login():
        return redirect(url_for("login"))
    sources = get_movie_sources()
    if index < 1 or index > len(sources):
        flash("Movie folder not found.")
        return redirect(url_for("settings"))

    folder_input = (request.form.get("folder") or "").strip()
    if folder_input:
        folder_path, error = validate_library_folder(folder_input)
        if error:
            flash(error)
            return redirect(url_for("settings"))
        folder_value = str(folder_path)
    else:
        folder_value = ""

    sources[index - 1]["name"] = (request.form.get("name") or f"Movie folder {index}").strip()
    sources[index - 1]["path"] = folder_value
    sources[index - 1]["enabled"] = request.form.get("enabled") == "on"
    save_movie_sources(sources)
    flash(f"Movie folder saved: {sources[index - 1]['name']} — {folder_value or 'Not set'}")
    record_activity("success", "source", "movie_source_saved", f"Movie source saved: {sources[index - 1]['name']}", {"path": folder_value, "enabled": sources[index - 1]["enabled"]})
    return redirect(url_for("settings"))


@app.route("/settings/library-source/add", methods=["POST"])
def settings_library_source_add():
    if not require_login():
        return redirect(url_for("login"))
    folder_input = (request.form.get("folder") or "").strip()
    folder_path, error = validate_library_folder(folder_input)
    if error:
        flash(error)
        return redirect(url_for("settings"))
    sources = get_movie_sources()
    source = _source_defaults("movies", len(sources) + 1, str(folder_path))
    source["name"] = (request.form.get("name") or source["name"]).strip()
    source["enabled"] = request.form.get("enabled") == "on"
    sources.append(source)
    save_movie_sources(sources)
    flash(f"Movie folder added: {source['name']} — {source['path']}")
    record_activity("success", "source", "movie_source_added", f"Movie source added: {source['name']}", {"path": source["path"], "enabled": source["enabled"]})
    return redirect(url_for("settings"))


@app.route("/settings/library-source/<int:index>/remove", methods=["POST"])
def settings_library_source_remove(index):
    if not require_login():
        return redirect(url_for("login"))
    sources = get_movie_sources()
    if index < 1 or index > len(sources):
        flash("Movie folder not found.")
        return redirect(url_for("settings"))
    removed = sources.pop(index - 1)
    save_movie_sources(sources)
    flash(f"Removed movie folder from monitoring: {removed.get('name') or removed.get('path')}")
    record_activity("warning", "source", "movie_source_removed", f"Movie source removed: {removed.get('name') or removed.get('path')}", {"path": removed.get("path", "")})
    return redirect(url_for("settings"))



@app.route("/settings/scan", methods=["POST"])
def settings_scan():
    if not require_login():
        return redirect(url_for("login"))

    folder_input = (request.form.get("folder") or "").strip()
    folder_path, error = validate_library_folder(folder_input)
    if error:
        flash(error)
        return redirect(url_for("settings"))

    cfg["folder"] = str(folder_path)
    save_config(cfg)

    backup_path = backup_database("movies_clean_rebuild")
    if backup_path:
        flash(f"Database backup created: {backup_path.name}")
    c = conn()
    scanned, saved, report = scan_movies_folder(folder_path, c, clean=True, return_report=True)
    c.close()
    flash(format_scan_report_message("Library folder saved and clean scan completed.", report))
    return redirect(url_for("index"))





@app.route("/settings/tv-library", methods=["POST"])
def settings_tv_library():
    return settings_tv_library_source(1)

@app.route("/settings/tv-library-source/<int:index>", methods=["POST"])
def settings_tv_library_source(index):
    if not require_login():
        return redirect(url_for("login"))
    sources = get_tv_sources()
    if index < 1 or index > len(sources):
        flash("TV folder not found.")
        return redirect(url_for("settings"))
    folder_input = (request.form.get("tv_folder") or "").strip()
    if folder_input:
        folder_path, error = validate_tv_folder(folder_input)
        if error:
            flash(error)
            return redirect(url_for("settings"))
        folder_value = str(folder_path)
    else:
        folder_value = ""
    sources[index - 1]["name"] = (request.form.get("name") or f"TV folder {index}").strip()
    sources[index - 1]["path"] = folder_value
    sources[index - 1]["enabled"] = request.form.get("enabled") == "on"
    save_tv_sources(sources)
    flash(f"TV folder saved: {sources[index - 1]['name']} — {folder_value or 'Not set'}")
    record_activity("success", "source", "tv_source_saved", f"TV source saved: {sources[index - 1]['name']}", {"path": folder_value, "enabled": sources[index - 1]["enabled"]})
    return redirect(url_for("settings"))


@app.route("/settings/tv-library-source/add", methods=["POST"])
def settings_tv_library_source_add():
    if not require_login():
        return redirect(url_for("login"))
    folder_input = (request.form.get("tv_folder") or "").strip()
    folder_path, error = validate_tv_folder(folder_input)
    if error:
        flash(error)
        return redirect(url_for("settings"))
    sources = get_tv_sources()
    source = _source_defaults("tv", len(sources) + 1, str(folder_path))
    source["name"] = (request.form.get("name") or source["name"]).strip()
    source["enabled"] = request.form.get("enabled") == "on"
    sources.append(source)
    save_tv_sources(sources)
    flash(f"TV folder added: {source['name']} — {source['path']}")
    record_activity("success", "source", "tv_source_added", f"TV source added: {source['name']}", {"path": source["path"], "enabled": source["enabled"]})
    return redirect(url_for("settings"))


@app.route("/settings/tv-library-source/<int:index>/remove", methods=["POST"])
def settings_tv_library_source_remove(index):
    if not require_login():
        return redirect(url_for("login"))
    sources = get_tv_sources()
    if index < 1 or index > len(sources):
        flash("TV folder not found.")
        return redirect(url_for("settings"))
    removed = sources.pop(index - 1)
    save_tv_sources(sources)
    flash(f"Removed TV folder from monitoring: {removed.get('name') or removed.get('path')}")
    record_activity("warning", "source", "tv_source_removed", f"TV source removed: {removed.get('name') or removed.get('path')}", {"path": removed.get("path", "")})
    return redirect(url_for("settings"))


@app.route("/settings/tv-scan", methods=["POST"])
def settings_tv_scan():
    if not require_login():
        return redirect(url_for("login"))
    folder_input = (request.form.get("tv_folder") or "").strip()
    folder_path, error = validate_tv_folder(folder_input)
    if error:
        flash(error)
        return redirect(url_for("settings"))
    cfg["tv_folder"] = str(folder_path)
    save_config(cfg)
    backup_path = backup_database("tv_clean_rebuild")
    if backup_path:
        flash(f"Database backup created: {backup_path.name}")
    c = conn()
    _scanned, _saved, report = scan_tv_folder(folder_path, c, clean=True, return_report=True)
    c.close()
    flash(format_tv_scan_report_message("TV folder saved and clean scan completed.", report))
    return redirect(url_for("tv_index"))


@app.route("/settings/refresh-movies")
def settings_refresh_movies():
    if not require_login():
        return redirect(url_for("login"))
    return redirect("/scan-wait/refresh-movies")


@app.route("/settings/refresh-tv")
def settings_refresh_tv():
    if not require_login():
        return redirect(url_for("login"))
    return redirect("/scan-wait/refresh-tv")


@app.route("/settings/refresh-all")
def settings_refresh_all():
    if not require_login():
        return redirect(url_for("login"))
    return redirect("/scan-wait/refresh-all")


@app.route("/settings/web", methods=["POST"])
def settings_web():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))

    username = (request.form.get("username") or "").strip() or "admin"
    password = (request.form.get("password") or "").strip()
    port_text = (request.form.get("port") or "").strip()

    cfg.setdefault("web", {})
    viewers = _normalise_viewers(cfg["web"])
    cfg.setdefault("ignored_alerts", [])
    if any(username.lower() == str(v.get("username", "")).lower() for v in viewers):
        flash("Admin username must be different from every viewer username.")
        return redirect(_users_return_url())

    cfg["web"]["username"] = username
    if password:
        cfg["web"]["password"] = password

    port_changed = False
    if port_text:
        if port_text.isdigit() and 1 <= int(port_text) <= 65535:
            new_port = int(port_text)
            port_changed = cfg["web"].get("port", 8765) != new_port
            cfg["web"]["port"] = new_port
        else:
            flash("Port must be a whole number between 1 and 65535.")
            return redirect(url_for("settings"))

    save_config(cfg)

    message = f"Web settings saved. Admin: '{username}'."
    if password:
        message += " Admin password updated."
    if port_changed:
        message += f" Port changed to {cfg['web']['port']}. Restart the web app to use the new port."
    else:
        message += f" Current port: {cfg['web'].get('port', 8765)}."

    flash(message)
    record_activity("success", "settings", "web_settings_saved", "Web/admin settings saved", {"username": username, "port": cfg["web"].get("port", 8765), "password_changed": bool(password)})
    return redirect(url_for("settings"))


def _users_return_url():
    ref = request.headers.get("Referer", "")
    return url_for("admin_users") if "/admin/users" in ref else url_for("settings")


@app.route("/settings/viewer/add", methods=["POST"])
def settings_viewer_add():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    cfg.setdefault("web", {})
    viewers = _normalise_viewers(cfg["web"])
    cfg.setdefault("ignored_alerts", [])
    username = (request.form.get("viewer_username") or "").strip()
    password = (request.form.get("viewer_password") or "").strip()
    enabled = request.form.get("enabled") == "on"
    if not username or not password:
        flash("Viewer username and password are required.")
        return redirect(_users_return_url())
    if username.lower() == str(cfg["web"].get("username", "admin")).lower():
        flash("Viewer username must be different from the admin username.")
        return redirect(_users_return_url())
    if any(username.lower() == str(v.get("username", "")).lower() for v in viewers):
        flash("That viewer username already exists.")
        return redirect(_users_return_url())
    viewers.append({"username": username, "password": password, "enabled": enabled})
    cfg["web"]["viewers"] = viewers
    save_config(cfg)
    flash(f"Viewer '{username}' added.")
    record_activity("success", "viewer", "viewer_added", f"Viewer added: {username}", {"enabled": enabled})
    return redirect(_users_return_url())


@app.route("/settings/viewer/<int:index>", methods=["POST"])
def settings_viewer_update(index):
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    cfg.setdefault("web", {})
    viewers = _normalise_viewers(cfg["web"])
    cfg.setdefault("ignored_alerts", [])
    if index < 1 or index > len(viewers):
        flash("Viewer account not found.")
        return redirect(_users_return_url())
    username = (request.form.get("viewer_username") or "").strip()
    password = (request.form.get("viewer_password") or "").strip()
    enabled = request.form.get("enabled") == "on"
    if not username:
        flash("Viewer username is required.")
        return redirect(_users_return_url())
    if username.lower() == str(cfg["web"].get("username", "admin")).lower():
        flash("Viewer username must be different from the admin username.")
        return redirect(_users_return_url())
    if any(i != index - 1 and username.lower() == str(v.get("username", "")).lower() for i, v in enumerate(viewers)):
        flash("That viewer username already exists.")
        return redirect(_users_return_url())
    viewers[index - 1]["username"] = username
    viewers[index - 1]["enabled"] = enabled
    if password:
        viewers[index - 1]["password"] = password
    cfg["web"]["viewers"] = viewers
    save_config(cfg)
    flash(f"Viewer '{username}' saved.")
    record_activity("success", "viewer", "viewer_saved", f"Viewer saved: {username}", {"enabled": enabled, "password_changed": bool(password)})
    return redirect(_users_return_url())


@app.route("/settings/viewer/<int:index>/remove", methods=["POST"])
def settings_viewer_remove(index):
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    cfg.setdefault("web", {})
    viewers = _normalise_viewers(cfg["web"])
    cfg.setdefault("ignored_alerts", [])
    if index < 1 or index > len(viewers):
        flash("Viewer account not found.")
        return redirect(_users_return_url())
    removed = viewers.pop(index - 1)
    if not viewers:
        flash("At least one viewer should exist. The default Laila viewer was restored.")
        viewers.append({"username": "Laila", "password": "La1laz3b3st", "enabled": True})
    cfg["web"]["viewers"] = viewers
    save_config(cfg)
    flash(f"Viewer '{removed.get('username', 'Viewer')}' removed.")
    record_activity("warning", "viewer", "viewer_removed", f"Viewer removed: {removed.get('username', 'Viewer')}")
    return redirect(_users_return_url())


@app.route("/restart-web")
def restart_web():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    record_activity("warning", "server", "restart_web", "Web server restart requested")
    response = restart_wait_page("Web server restart requested. Waiting to reconnect…")
    restart_web_process()
    return response


@app.route("/restart-web-and-caddy")
def restart_web_and_caddy():
    if not require_admin():
        return redirect(url_for("login") if not require_login() else url_for("index"))
    try:
        ensure_caddy_paths()
    except Exception as exc:
        flash(f"Could not restart Caddy: {exc}")
        record_activity("error", "server", "restart_caddy_failed", f"Could not restart Caddy: {exc}")
        return redirect(url_for("settings"))
    record_activity("warning", "server", "restart_web_and_caddy", "Web server and Caddy restart requested")
    response = restart_wait_page("Web server and Caddy restart requested. Waiting to reconnect…")
    restart_web_and_caddy_process()
    return response


@app.route("/tv-refresh")
def tv_refresh():
    if not require_login():
        return redirect(url_for("login"))
    return redirect("/scan-wait/refresh-tv")


@app.route("/refresh")
def refresh():
    if not require_login():
        return redirect(url_for("login"))
    return redirect("/scan-wait/refresh-movies")


@app.route("/rebuild")
def rebuild():
    if not require_login():
        return redirect(url_for("login"))
    return redirect("/scan-wait/rebuild-movies")


@app.route("/")
def index():
    if not require_login():
        return redirect(url_for("login"))

    c = conn()
    q = request.args.get("q", "").strip().lower()

    years_selected = request.args.getlist("year")
    countries_selected = request.args.getlist("country")
    movie_sets_selected = request.args.getlist("movie_set")
    resolutions_selected = request.args.getlist("resolution")
    file_ext_selected = request.args.getlist("file_ext")
    asset_status_selected = request.args.getlist("asset_status")
    sort_value = normalize_sort(request.args.get("sort", "title_asc"))

    page = request.args.get("page", "1")
    try:
        page = max(1, int(page))
    except Exception:
        page = 1

    page_size = request.args.get("page_size", "150")
    try:
        page_size = int(page_size)
    except Exception:
        page_size = 150
    if page_size not in [50, 100, 150, 300]:
        page_size = 150

    total_all = get_total_movie_count(c)
    filtered_total = get_filtered_movie_count(
        c,
        q,
        years_selected,
        countries_selected,
        movie_sets_selected,
        resolutions_selected,
        file_ext_selected,
        asset_status_selected
    )

    total_pages = max(1, (filtered_total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    start = (page - 1) * page_size
    end = start + page_size
    page_movies = get_page_movies(
        c,
        q,
        years_selected,
        countries_selected,
        movie_sets_selected,
        resolutions_selected,
        file_ext_selected,
        asset_status_selected,
        sort_value,
        page,
        page_size
    )

    years = [str(r[0]) for r in c.execute("SELECT DISTINCT year FROM movies WHERE year IS NOT NULL ORDER BY year").fetchall()]
    country_values = set()
    for row in c.execute("SELECT country FROM movies WHERE country IS NOT NULL AND TRIM(country)<>''"):
        for part in str(row["country"]).split(","):
            part = part.strip()
            if part:
                country_values.add(part)
    countries = sorted(country_values, key=str.lower)
    movie_sets = [str(r[0]) for r in c.execute("SELECT DISTINCT movie_set FROM movies WHERE movie_set IS NOT NULL AND TRIM(movie_set)<>'' ORDER BY movie_set").fetchall()]

    resolution_rows = c.execute("""
        SELECT DISTINCT video_width, video_height
        FROM movies
        WHERE video_width IS NOT NULL OR video_height IS NOT NULL
        ORDER BY video_width DESC, video_height DESC
    """).fetchall()

    seen = set()
    resolutions = []
    for row in resolution_rows:
        r = normalize_resolution(row["video_width"], row["video_height"])
        if r and r not in seen:
            seen.add(r)
            resolutions.append(r)

    file_extensions = []
    for row in c.execute("SELECT movie_path FROM movies WHERE movie_path IS NOT NULL AND TRIM(movie_path)<>''").fetchall():
        ext = media_file_extension(row["movie_path"])
        if ext and ext not in file_extensions:
            file_extensions.append(ext)
    file_extensions = sorted(file_extensions, key=lambda value: value.lower())

    selected = None
    movie_id = request.args.get("movie_id")
    if movie_id:
        selected = get_selected_movie(
            c,
            movie_id,
            q,
            years_selected,
            countries_selected,
            movie_sets_selected,
            resolutions_selected,
            file_ext_selected,
            asset_status_selected
        )

    if selected is None and page_movies:
        selected = page_movies[0]
    selected_tags = media_tags(selected) if selected else []
    selected_asset_tags = missing_asset_tags(selected) if selected else []

    filters = {
        "q": q,
        "sort": sort_value,
        "year": years_selected,
        "country": countries_selected,
        "movie_set": movie_sets_selected,
        "resolution": resolutions_selected,
        "file_ext": file_ext_selected,
        "asset_status": asset_status_selected,
        "page": page,
        "page_size": page_size,
    }
    active_filter_count = sum(len(v) for v in [years_selected, countries_selected, movie_sets_selected, resolutions_selected, file_ext_selected, asset_status_selected])
    if q:
        active_filter_count += 1

    first_page_query = build_query(filters, page=1)
    prev_page_query = build_query(filters, page=page - 1)
    next_page_query = build_query(filters, page=page + 1)
    last_page_query = build_query(filters, page=total_pages)

    result_start = start + 1 if filtered_total else 0
    result_end = min(end, filtered_total) if filtered_total else 0
    page_size_options = [50, 100, 150, 300]

    c.close()

    return render_template_string(
        INDEX_HTML,
        active="movies",
        media_tags=media_tags,
        file_size_label=file_size_label,
        collection_query=collection_query,
        movies=page_movies,
        selected=selected,
        years=years,
        countries=countries,
        movie_sets=movie_sets,
        resolutions=resolutions,
        file_extensions=file_extensions,
        years_selected=years_selected,
        countries_selected=countries_selected,
        movie_sets_selected=movie_sets_selected,
        resolutions_selected=resolutions_selected,
        file_ext_selected=file_ext_selected,
        years_summary=selected_summary(years_selected),
        countries_summary=selected_summary(countries_selected),
        movie_sets_summary=selected_summary(movie_sets_selected),
        resolutions_summary=selected_summary(resolutions_selected),
        total_all=total_all,
        filtered_total=filtered_total,
        selected_tags=selected_tags,
        filters=filters,
        build_query=build_query,
        normalize_resolution=normalize_resolution,
        asset_status_options=[("needs_attention", "Needs attention"), ("missing_nfo", "Missing NFO"), ("missing_poster", "Missing poster"), ("missing_fanart", "Missing fanart")],
        asset_status_labels={"needs_attention": "Needs attention", "missing_nfo": "Missing NFO", "missing_poster": "Missing poster", "missing_fanart": "Missing fanart"},
        asset_status_summary=selected_summary([{"needs_attention": "Needs attention", "missing_nfo": "Missing NFO", "missing_poster": "Missing poster", "missing_fanart": "Missing fanart"}.get(v, v) for v in asset_status_selected]),
        selected_asset_tags=selected_asset_tags,
        page=page,
        total_pages=total_pages,
        first_page_query=first_page_query,
        prev_page_query=prev_page_query,
        next_page_query=next_page_query,
        last_page_query=last_page_query,
        result_start=result_start,
        result_end=result_end,
        page_size=page_size,
        page_size_options=page_size_options,
        sort_options=SORT_OPTIONS,
        sort_value=sort_value,
        sort_label=get_sort_label(sort_value)
    )


@app.route("/movie/<int:movie_id>")
def movie_detail(movie_id):
    if not require_login():
        return redirect(url_for("login"))

    c = conn()
    q = request.args.get("q", "").strip().lower()
    years_selected = request.args.getlist("year")
    countries_selected = request.args.getlist("country")
    movie_sets_selected = request.args.getlist("movie_set")
    resolutions_selected = request.args.getlist("resolution")
    file_ext_selected = request.args.getlist("file_ext")
    asset_status_selected = request.args.getlist("asset_status")
    sort_value = normalize_sort(request.args.get("sort", "title_asc"))
    page = request.args.get("page", "1")
    try:
        page = max(1, int(page))
    except Exception:
        page = 1

    movie = row_to_dict(c.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone())
    c.close()
    if not movie:
        return redirect(url_for("index"))

    page_size = request.args.get("page_size", "150")
    try:
        page_size = int(page_size)
    except Exception:
        page_size = 150
    if page_size not in [50, 100, 150, 300]:
        page_size = 150

    filters = {
        "q": q,
        "sort": sort_value,
        "year": years_selected,
        "country": countries_selected,
        "movie_set": movie_sets_selected,
        "resolution": resolutions_selected,
        "file_ext": file_ext_selected,
        "asset_status": asset_status_selected,
        "page": page,
        "page_size": page_size,
    }
    active_filter_count = sum(len(v) for v in [years_selected, countries_selected, movie_sets_selected, resolutions_selected, file_ext_selected, asset_status_selected])
    if q:
        active_filter_count += 1

    movie = dict(movie)
    return render_template_string(
        MOVIE_HTML,
        active="movies",
        movie=movie,
        tags=media_tags(movie),
        asset_tags=missing_asset_tags(movie),
        filters=filters,
        build_query=build_query,
        normalize_resolution=normalize_resolution,
        file_size_label=file_size_label,
        collection_query=collection_query,
        sort_value=sort_value,
        page_size=page_size
    )




def build_tv_detail_context(c, show_id, season_selected_raw=""):
    selected = row_to_dict(c.execute("SELECT * FROM tv_shows WHERE id=?", (show_id,)).fetchone())
    episodes = []
    episodes_by_season = OrderedDict()
    season_count = 0
    episode_count = 0
    season_blocks = []
    selected_season_number = None
    selected_season_label = ""
    if selected is not None:
        episodes = [dict(r) for r in c.execute("SELECT * FROM tv_episodes WHERE show_id=? ORDER BY COALESCE(season_number, 0), COALESCE(episode_number, 0), id", (selected["id"],)).fetchall()]
        season_count = len({e.get("season_number") for e in episodes if e.get("season_number") is not None})
        episode_count = len(episodes)
        for episode in episodes:
            season_number = episode.get("season_number")
            season_label = _tv_season_label(season_number)
            episodes_by_season.setdefault(season_label, []).append(episode)
        for season_label, season_episodes in episodes_by_season.items():
            season_number = season_episodes[0].get("season_number") if season_episodes else None
            is_selected = False
            if season_selected_raw != "":
                is_selected = str(season_number if season_number is not None else -1) == str(season_selected_raw)
            season_blocks.append({
                "label": season_label,
                "season_number": season_number,
                "episodes": season_episodes,
                "episode_count": len(season_episodes),
                "poster_available": bool(find_tv_season_art(selected.get("show_path"), season_number, kind="poster")) if selected else False,
                "banner_available": bool(find_tv_season_art(selected.get("show_path"), season_number, kind="banner")) if selected else False,
                "is_selected": is_selected,
            })
        if season_blocks:
            if season_selected_raw != "":
                found = False
                for season in season_blocks:
                    if season["is_selected"]:
                        selected_season_number = season.get("season_number")
                        selected_season_label = season.get("label") or ""
                        found = True
                        break
                if not found:
                    season_blocks[0]["is_selected"] = True
                    selected_season_number = season_blocks[0].get("season_number")
                    selected_season_label = season_blocks[0].get("label") or ""
            else:
                season_blocks[0]["is_selected"] = True
                selected_season_number = season_blocks[0].get("season_number")
                selected_season_label = season_blocks[0].get("label") or ""
    return {
        "selected": selected,
        "episodes": episodes,
        "episodes_by_season": episodes_by_season,
        "season_count": season_count,
        "episode_count": episode_count,
        "season_blocks": season_blocks,
        "selected_season_number": selected_season_number,
        "selected_season_label": selected_season_label,
    }


@app.route("/tv-show/<int:show_id>")
def tv_show_detail(show_id):
    if not require_login():
        return redirect(url_for("login"))
    c = conn()
    q = (request.args.get("q") or "").strip()
    ctx = build_tv_detail_context(c, show_id, (request.args.get("season") or "").strip())
    c.close()
    if not ctx.get("selected"):
        return redirect(url_for("tv_index"))
    tv_search_query = urlencode({"q": q}) if q else ""
    back_url = f"/tv?{tv_search_query}" if tv_search_query else "/tv"
    season_href_base = f"/tv-show/{show_id}?{tv_search_query}&season=" if tv_search_query else f"/tv-show/{show_id}?season="
    return render_template_string(
        TV_SHOW_HTML,
        active="tv",
        media_tags=media_tags,
        back_url=back_url,
        season_href_base=season_href_base,
        split_csv_values=split_csv_values,
        tv_missing_assets=tv_missing_assets,
        **ctx,
    )


def _scan_action_label(action):
    m = re.match(r'^(refresh|rebuild)-(movies|tv)-(\d+)$', action)
    if m:
        verb, kind, index = m.groups()
        label_kind = "movie folder" if kind == "movies" else "TV folder"
        return f"{'Refreshing' if verb == 'refresh' else 'Clean rebuilding'} {label_kind} {index}"
    return {
        "refresh-movies": "Checking Movies for changes",
        "rebuild-movies": "Clean rebuilding Movies",
        "refresh-tv": "Checking TV Shows for changes",
        "rebuild-tv": "Clean rebuilding TV Shows",
        "refresh-all": "Checking Movies and TV Shows for changes",
    }.get(action, "Scanning")


def _scan_one_movie_source(index, source, clean=False):
    path_text = _source_path(source)
    folder_path, error = validate_library_folder(path_text)
    if error:
        return f"Movie folder {index}: {error}"
    c = conn()
    try:
        _scanned, _saved, report = scan_movies_folder(folder_path, c, clean=clean, return_report=True)
    finally:
        c.close()
    _mark_source_scanned("movies", index)
    name = source.get("name", f"Movie folder {index}") if isinstance(source, dict) else f"Movie folder {index}"
    prefix = f"{name} clean rebuild completed." if clean else f"{name} refresh completed."
    return format_scan_report_message(prefix, report)


def _scan_one_tv_source(index, source, clean=False):
    path_text = _source_path(source)
    folder_path, error = validate_tv_folder(path_text)
    if error:
        return f"TV folder {index}: {error}"
    c = conn()
    try:
        _scanned, _saved, report = scan_tv_folder(folder_path, c, clean=clean, return_report=True)
    finally:
        c.close()
    _mark_source_scanned("tv", index)
    name = source.get("name", f"TV folder {index}") if isinstance(source, dict) else f"TV folder {index}"
    prefix = f"{name} clean rebuild completed." if clean else f"{name} refresh completed."
    return format_tv_scan_report_message(prefix, report)


def _run_scan_action(action):
    label = _scan_action_label(action)
    set_scan_status(running=True, label=label, message="Scan started. Please wait…", ok=True, started=now_label(), finished=None, report_url="/reports")
    try:
        messages = []
        single_match = re.match(r'^(refresh|rebuild)-(movies|tv)-(\d+)$', action)
        if single_match:
            verb, kind, index_text = single_match.groups()
            index = int(index_text)
            clean = verb == "rebuild"
            if clean:
                backup_path = backup_database(action)
                if backup_path:
                    messages.append(f"Database backup created before clean rebuild: {backup_path.name}")
            if kind == "movies":
                sources = get_movie_sources()
                if index < 1 or index > len(sources):
                    messages.append("Movie folder not found.")
                else:
                    messages.append(_scan_one_movie_source(index, sources[index - 1], clean=clean))
            else:
                sources = get_tv_sources()
                if index < 1 or index > len(sources):
                    messages.append("TV folder not found.")
                else:
                    messages.append(_scan_one_tv_source(index, sources[index - 1], clean=clean))
        else:
            if action in ("rebuild-movies", "rebuild-tv"):
                backup_path = backup_database(action)
                if backup_path:
                    messages.append(f"Database backup created before clean rebuild: {backup_path.name}")
            if action in ("refresh-movies", "rebuild-movies", "refresh-all"):
                movie_sources = _configured_sources("movies")
                if not movie_sources:
                    messages.append("Please enter a movie folder first.")
                else:
                    if action == "rebuild-movies":
                        c = conn()
                        try:
                            clear_movie_library(c)
                        finally:
                            c.close()
                    for index, source in movie_sources:
                        messages.append(_scan_one_movie_source(index, source, clean=False))
            if action in ("refresh-tv", "rebuild-tv", "refresh-all"):
                tv_sources = _configured_sources("tv")
                if not tv_sources:
                    messages.append("Please enter a TV folder first.")
                else:
                    if action == "rebuild-tv":
                        c = conn()
                        try:
                            clear_tv_library(c)
                        finally:
                            c.close()
                    for index, source in tv_sources:
                        messages.append(_scan_one_tv_source(index, source, clean=False))
        message = " | ".join(messages) if messages else "Nothing was scanned."
        message = message + " | Reports are available from /reports."
        ok = not any("does not exist" in m or "not a folder" in m or "enter" in m.lower() for m in messages)
        finished = now_label()
        finished_label = label.replace("Refreshing", "Completed refresh of").replace("Clean rebuilding", "Completed clean rebuild of")
        set_scan_status(running=False, label=finished_label, message=message, ok=ok, finished=finished, report_url="/reports")
        record_scan_history(action, finished_label, ok, message, get_scan_status().get("started"), finished)
    except Exception as exc:
        finished = now_label()
        message = f"Scan failed: {exc}"
        set_scan_status(running=False, label=label, message=message, ok=False, finished=finished)
        record_scan_history(action, label, False, message, get_scan_status().get("started"), finished)





def _api_admin_error():
    if not require_login():
        return jsonify({"ok": False, "error": "login_required"}), 401
    if not require_admin():
        return jsonify({"ok": False, "error": "admin_required"}), 403
    return None


def _latest_scan_history_item():
    try:
        rows = get_scan_history(1)
        return rows[0] if rows else None
    except Exception:
        return None


def _admin_status_payload():
    about = get_about_info()
    runtime_status = get_runtime_path_status()
    scan = get_scan_status()
    scheduled = scheduled_scan_summary()
    missing = missing_counts()
    try:
        alerts = get_all_issue_alerts(max_per_type=40, max_total=300)
    except Exception:
        alerts = []
    visible_alerts = [a for a in alerts if not a.get("ignored")]
    web_cfg = cfg.get("web", {}) if isinstance(cfg, dict) else {}
    port = int(web_cfg.get("port") or 8765)
    return {
        "ok": True,
        "version": UI_VERSION,
        "server_time": now_label(),
        "app": {
            "app_dir": str(APP_DIR),
            "data_dir": runtime_status["data_dir"],
            "config": runtime_status["config_path"],
            "database": str(DB_PATH),
            "cache_dir": runtime_status["cache_dir"],
            "logs_dir": runtime_status["logs_dir"],
            "backups_dir": runtime_status["backups_dir"],
            "runtime_mode": runtime_status["mode"],
            "port": port,
            "local_url": f"http://127.0.0.1:{port}",
            "remote_url": "https://mjeromem75.dyndns.org",
        },
        "runtime_paths": runtime_status,
        "library": {
            "movies": about.get("movie_count", 0),
            "tv_shows": about.get("tv_show_count", 0),
            "tv_episodes": about.get("episode_count", 0),
            "missing_movies": missing.get("movies", 0),
            "missing_episodes": missing.get("episodes", 0),
        },
        "scan": scan,
        "scheduled_scan": scheduled,
        "alerts": {
            "visible": len(visible_alerts),
            "ignored": sum(1 for a in alerts if a.get("ignored")),
            "review": sum(1 for a in visible_alerts if a.get("review")),
            "movies": sum(1 for a in visible_alerts if a.get("area") == "Movies"),
            "tv": sum(1 for a in visible_alerts if a.get("area") == "TV Shows"),
        },
        "server": {
            "pid": os.getpid(),
            "python": sys.executable,
            "caddy_running": is_caddy_running(),
        },
    }


@app.route("/api/admin/status")
def api_admin_status():
    err = _api_admin_error()
    if err:
        return err
    return jsonify(_admin_status_payload())


@app.route("/api/admin/scan-status")
def api_admin_scan_status():
    err = _api_admin_error()
    if err:
        return err
    return jsonify({
        "ok": True,
        "scan": get_scan_status(),
        "scheduled_scan": scheduled_scan_summary(),
        "latest_scan": _latest_scan_history_item(),
        "missing": missing_counts(),
    })


@app.route("/api/admin/server-status")
def api_admin_server_status():
    err = _api_admin_error()
    if err:
        return err
    service = get_mac_service_data()
    control = get_server_control_data()
    return jsonify({
        "ok": True,
        "version": UI_VERSION,
        "server_time": now_label(),
        "pid": os.getpid(),
        "python": sys.executable,
        "app_dir": str(APP_DIR),
        "port": control.get("port"),
        "urls": control.get("urls"),
        "scan_status": control.get("scan_status"),
        "caddy_running": control.get("is_caddy_running"),
        "port_check": service.get("port_check"),
        "launchd_status": service.get("launchd_status"),
        "launch_agent_path": service.get("launch_agent_path"),
        "runner_path": service.get("runner_path"),
    })


@app.route("/api/admin/recent-events")
def api_admin_recent_events():
    err = _api_admin_error()
    if err:
        return err
    try:
        limit = min(max(int(request.args.get("limit", 25)), 1), 100)
    except Exception:
        limit = 25
    level = request.args.get("level", "").strip().lower()
    area = request.args.get("area", "").strip().lower()
    q = request.args.get("q", "").strip()
    return jsonify({"ok": True, "items": get_activity_log(limit=limit, level=level, area=area, q=q)})


@app.route("/api/admin/run-scan", methods=["POST"])
def api_admin_run_scan():
    err = _api_admin_error()
    if err:
        return err
    action = (request.form.get("action") or "").strip() or None
    if request.is_json:
        try:
            action = (request.get_json(silent=True) or {}).get("action") or action
        except Exception:
            pass
    action = str(action or "refresh-all")
    valid_actions = {"refresh-all", "refresh-movies", "refresh-tv"}
    if action not in valid_actions:
        return jsonify({"ok": False, "error": "unsupported_action", "allowed": sorted(valid_actions)}), 400
    status = get_scan_status()
    if status.get("running"):
        return jsonify({"ok": False, "error": "scan_already_running", "scan": status}), 409
    threading.Thread(target=_run_scan_action, args=(action,), daemon=True).start()
    return jsonify({"ok": True, "started": True, "action": action, "scan": get_scan_status()}), 202


@app.route("/api/movies")
def api_movies():
    if not require_login():
        return jsonify({"error": "login_required"}), 401
    c = conn()
    try:
        limit = min(max(int(request.args.get("limit", 100)), 1), 500)
    except Exception:
        limit = 100
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except Exception:
        offset = 0
    q = request.args.get("q", "").strip().lower()
    where = ""
    params = []
    if q:
        where = "WHERE lower(title) LIKE ? OR lower(COALESCE(movie_set,'')) LIKE ?"
        params.extend([f"%{q}%", f"%{q}%"])
    total = c.execute(f"SELECT COUNT(*) AS n FROM movies {where}", params).fetchone()["n"]
    rows = c.execute(f"""
        SELECT id, title, year, country, movie_set, genres, director, video_width, video_height,
               video_codec, audio_codec, audio_channels, file_size_gb, poster_path, fanart_path
        FROM movies {where}
        ORDER BY sort_title COLLATE NOCASE, title COLLATE NOCASE
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()
    c.close()
    items = []
    for row in rows:
        item = _row_to_public_dict(row)
        item["poster_thumb"] = url_for("movie_thumb", kind="poster", movie_id=row["id"]) if row["poster_path"] else None
        item["fanart_thumb"] = url_for("movie_thumb", kind="fanart", movie_id=row["id"]) if row["fanart_path"] else None
        items.append(item)
    return jsonify({"total": total, "limit": limit, "offset": offset, "items": items})


@app.route("/api/movies/<int:movie_id>")
def api_movie_detail(movie_id):
    if not require_login():
        return jsonify({"error": "login_required"}), 401
    c = conn()
    row = c.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
    c.close()
    if not row:
        return jsonify({"error": "not_found"}), 404
    item = _row_to_public_dict(row)
    item["poster_thumb"] = url_for("movie_thumb", kind="poster", movie_id=movie_id) if row["poster_path"] else None
    item["fanart_thumb"] = url_for("movie_thumb", kind="fanart", movie_id=movie_id) if row["fanart_path"] else None
    return jsonify(item)


@app.route("/api/tv")
def api_tv():
    if not require_login():
        return jsonify({"error": "login_required"}), 401
    c = conn()
    try:
        limit = min(max(int(request.args.get("limit", 100)), 1), 500)
    except Exception:
        limit = 100
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except Exception:
        offset = 0
    q = request.args.get("q", "").strip().lower()
    where = ""
    params = []
    if q:
        where = "WHERE lower(title) LIKE ?"
        params.append(f"%{q}%")
    total = c.execute(f"SELECT COUNT(*) AS n FROM tv_shows {where}", params).fetchone()["n"]
    rows = c.execute(f"""
        SELECT id, title, year, country, genres, studio, status, poster_path, fanart_path
        FROM tv_shows {where}
        ORDER BY title COLLATE NOCASE
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()
    c.close()
    items = []
    for row in rows:
        item = _row_to_public_dict(row)
        item["poster_thumb"] = url_for("tv_thumb", kind="poster", show_id=row["id"]) if row["poster_path"] else None
        item["fanart_thumb"] = url_for("tv_thumb", kind="fanart", show_id=row["id"]) if row["fanart_path"] else None
        items.append(item)
    return jsonify({"total": total, "limit": limit, "offset": offset, "items": items})


@app.route("/api/tv/<int:show_id>")
def api_tv_detail(show_id):
    if not require_login():
        return jsonify({"error": "login_required"}), 401
    c = conn()
    show = c.execute("SELECT * FROM tv_shows WHERE id=?", (show_id,)).fetchone()
    if not show:
        c.close()
        return jsonify({"error": "not_found"}), 404
    seasons = c.execute("""
        SELECT season_number, COUNT(*) AS episode_count
        FROM tv_episodes
        WHERE show_id=?
        GROUP BY season_number
        ORDER BY season_number
    """, (show_id,)).fetchall()
    c.close()
    item = _row_to_public_dict(show)
    item["poster_thumb"] = url_for("tv_thumb", kind="poster", show_id=show_id) if show["poster_path"] else None
    item["fanart_thumb"] = url_for("tv_thumb", kind="fanart", show_id=show_id) if show["fanart_path"] else None
    item["seasons"] = [dict(r) for r in seasons]
    return jsonify(item)


@app.route("/api/tv/<int:show_id>/season/<int:season_number>")
def api_tv_season(show_id, season_number):
    if not require_login():
        return jsonify({"error": "login_required"}), 401
    c = conn()
    rows = c.execute("""
        SELECT id, season_number, episode_number, title, plot, video_width, video_height,
               video_codec, audio_codec, audio_channels, file_size_gb
        FROM tv_episodes
        WHERE show_id=? AND season_number=?
        ORDER BY episode_number
    """, (show_id, season_number)).fetchall()
    c.close()
    return jsonify({"show_id": show_id, "season_number": season_number, "items": [_row_to_public_dict(r) for r in rows]})

@app.route("/scan-wait/<action>")
def scan_wait(action):
    if not require_login():
        return redirect(url_for("login"))
    valid_actions = {"refresh-movies", "rebuild-movies", "refresh-tv", "rebuild-tv", "refresh-all"}
    if action not in valid_actions and not re.match(r'^(refresh|rebuild)-(movies|tv)-(\d+)$', action):
        flash("Unknown scan action.")
        return redirect(url_for("settings"))
    status = get_scan_status()
    if not status.get("running"):
        threading.Thread(target=_run_scan_action, args=(action,), daemon=True).start()
    return render_template_string(SCAN_WAIT_HTML, active="settings", label=_scan_action_label(action))


@app.route("/scan-status")
def scan_status():
    if not require_login():
        return jsonify({"running": False, "label": "Not signed in", "message": "Please sign in again.", "ok": False, "started": None, "finished": None})
    return jsonify(get_scan_status())

@app.route("/export/library.csv")
def export_library_csv():
    if not require_login():
        return redirect(url_for("login"))
    c = conn()
    rows = export_library_rows(c)
    c.close()
    fieldnames = [
        "id", "title", "sort_title", "year", "country", "movie_set", "plot", "genres", "director", "actors",
        "movie_path", "folder_path", "poster_path", "fanart_path", "nfo_path",
        "video_width", "video_height", "resolution_tag", "video_codec", "audio_codec", "audio_channels",
        "audio_channels_display", "file_size_gb", "favorite", "missing_nfo", "missing_poster", "missing_fanart"
    ]
    return csv_download_response(f"movie_library_export_{export_timestamp()}.csv", fieldnames, rows)


@app.route("/export/report/<report_name>.csv")
def export_report_csv(report_name):
    if not require_login():
        return redirect(url_for("login"))

    c = conn()
    try:
        if report_name == "missing-nfo":
            rows = export_missing_asset_rows(c, "nfo_path", "NFO")
            fieldnames = ["id", "title", "year", "country", "movie_set", "resolution_tag", "movie_path", "folder_path", "missing_asset"]
            filename = f"movie_library_missing_nfo_{export_timestamp()}.csv"
        elif report_name == "missing-poster":
            rows = export_missing_asset_rows(c, "poster_path", "poster")
            fieldnames = ["id", "title", "year", "country", "movie_set", "resolution_tag", "movie_path", "folder_path", "missing_asset"]
            filename = f"movie_library_missing_poster_{export_timestamp()}.csv"
        elif report_name == "missing-fanart":
            rows = export_missing_asset_rows(c, "fanart_path", "fanart")
            fieldnames = ["id", "title", "year", "country", "movie_set", "resolution_tag", "movie_path", "folder_path", "missing_asset"]
            filename = f"movie_library_missing_fanart_{export_timestamp()}.csv"
        elif report_name == "duplicates":
            rows = export_duplicates_rows(c)
            fieldnames = ["title", "year", "duplicate_count", "movie_ids"]
            filename = f"movie_library_duplicates_{export_timestamp()}.csv"
        else:
            c.close()
            return ("", 404)
    finally:
        c.close()

    return csv_download_response(filename, fieldnames, rows)


@app.route("/export/tv-library.csv")
def export_tv_library_csv():
    if not require_login():
        return redirect(url_for("login"))

    c = conn()
    rows = export_tv_library_rows(c)
    c.close()

    fieldnames = [
        "id", "title", "sort_title", "year", "country", "genres", "studio", "status", "premiered",
        "show_path", "poster_path", "fanart_path", "nfo_path", "season_count", "episode_count",
        "missing_tvshow_nfo", "missing_poster", "missing_fanart"
    ]
    return csv_download_response(f"tv_library_export_{export_timestamp()}.csv", fieldnames, rows)


@app.route("/export/tv-report/<report_name>.csv")
def export_tv_report_csv(report_name):
    if not require_login():
        return redirect(url_for("login"))

    c = conn()
    try:
        if report_name == "missing-tvshow-nfo":
            rows = export_tv_missing_show_asset_rows(c, "nfo_path", "tvshow_nfo")
            fieldnames = ["id", "title", "year", "country", "genres", "studio", "status", "show_path", "nfo_path", "missing_asset"]
            filename = f"tv_library_missing_tvshow_nfo_{export_timestamp()}.csv"
        elif report_name == "missing-episode-nfo":
            rows = export_tv_missing_episode_nfo_rows(c)
            fieldnames = ["id", "show_id", "show_title", "show_year", "season_number", "episode_number", "title", "air_date", "file_path", "folder_path", "nfo_path", "missing_asset"]
            filename = f"tv_library_missing_episode_nfo_{export_timestamp()}.csv"
        elif report_name == "missing-poster":
            rows = export_tv_missing_show_asset_rows(c, "poster_path", "poster")
            fieldnames = ["id", "title", "year", "country", "genres", "studio", "status", "show_path", "poster_path", "missing_asset"]
            filename = f"tv_library_missing_poster_{export_timestamp()}.csv"
        elif report_name == "missing-fanart":
            rows = export_tv_missing_show_asset_rows(c, "fanart_path", "fanart")
            fieldnames = ["id", "title", "year", "country", "genres", "studio", "status", "show_path", "fanart_path", "missing_asset"]
            filename = f"tv_library_missing_fanart_{export_timestamp()}.csv"
        elif report_name == "duplicates":
            rows = export_tv_duplicates_rows(c)
            fieldnames = ["title", "year", "duplicate_count", "show_ids"]
            filename = f"tv_library_duplicates_{export_timestamp()}.csv"
        else:
            c.close()
            return ("", 404)
    finally:
        c.close()

    return csv_download_response(filename, fieldnames, rows)


@app.route("/_ui-version")
def ui_version():
    return UI_VERSION


if __name__ == "__main__":
    c = conn()
    c.close()
    port = int(cfg.get("web", {}).get("port", 8765))
    app.run(host="0.0.0.0", port=port, debug=False)
