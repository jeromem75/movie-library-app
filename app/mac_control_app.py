#!/usr/bin/env python3
"""Small macOS companion/control app for the Movie Library Flask server.

This is intentionally lightweight and dependency-free. It does not replace the
web UI and it does not edit the shared Caddyfile. It reads the local config,
uses the admin API added in v28.3.30, and offers safe buttons to open the web UI,
trigger scans, and start/restart the existing Flask server. It prefers the
JSON admin-login endpoint added in v28.4.2 and falls back to the HTML login
form only for older local builds. v28.4.3 adds safer disabled/enabled
button states and clearer server/API diagnostics. v28.4.6 makes the
status panel easier to read on smaller screens and adds a direct admin-login
test button. v28.4.7 adds a gentle auto-refresh loop and clearer scan
detail text without changing server/Caddy behaviour. v28.4.8 makes
the auto-refresh scheduler single-instance, adds a small Last refreshed
line, and prevents queued duplicate refresh timers. v28.4.9 adds a
Copy Status button with a clean diagnostic summary for support/troubleshooting.
v28.4.10 adds a clearer operation line, safer scan-request locking, and
copy/clear controls for the message log. v28.4.11 adds safer window
closing, caps the in-app message log so it stays responsive, and tidies
operation-state recovery after login/start/server-action errors.
v28.4.12 adds safer start-server progress polling so the control app reports
whether Flask actually starts responding after the runner is launched.
v28.4.13 tidies the control-window action layout into clearer grouped rows so
buttons do not feel cramped or run into each other on smaller Mac screens.
v28.4.14 adds a server-action lock and clearer shared-Caddy restart warnings so
restart requests cannot be double-clicked accidentally. v28.4.15 adds gentle
post-restart polling so the control app reports when the web server comes back.
v28.4.16 adds safe support shortcuts for opening the app folder/config file
and copying the key local paths without changing server or Caddy behaviour.
v28.5.7 adds a Launch Library shortcut and a safe command launcher that behaves
more like the future proper Movie Library.app entry point. v28.5.8 adds a
double-clickable test Movie Library.app bundle and makes Launch Library prefer
that bundle on macOS when it is present. v28.5.9 adds launcher/log
diagnostics shortcuts and aligns the test app launcher with a dedicated logs
folder so testing issues are easier to capture. v28.5.10 fixes the test .app bundle
path resolution used by both Movie Library.app and Movie Library Control.app so
double-click launchers find the server files beside the bundle. v28.5.11 makes the test app and command launchers read the configured web.port from config.json, using 8765 only as a fallback. v28.5.12 adds a permission repair helper so copied/unzipped macOS launchers can be checked and fixed before DMG packaging. v28.5.13 adds a Mac-only test DMG helper and control-app shortcuts for building/opening the local packaging output without moving app data. v28.5.14 adds a matching test-DMG verification helper so the generated image can be checked before moving toward a final installer. v28.5.15 adds a read-only test-DMG status dashboard plus quick shortcuts for opening the generated image and copying DMG-specific logs. v28.5.16 adds Finder-layout prep for the test DMG, including an Applications alias and install notes, while keeping it clearly marked as not final. v28.5.17 adds an Application Support prep helper so the future data-folder skeleton can be created safely before any migration step. v28.5.19 adds a migration dry-run helper and reorganises planning shortcuts into their own row. v28.5.20 adds a migration backup helper that creates timestamped safety copies before any future live-data switch. v28.5.21 adds a matching migration backup verification helper to check the latest timestamped backup and checksums before any future migration step. v28.5.22 adds a non-live migration staging helper that copies config.json and library.db into Application Support/migration-staging/current without switching live data paths. v28.5.23 adds a matching migration staging verification helper to check the staged files and checksum metadata before any future live-data switch. v28.5.24 adds a cutover readiness check that compares staged config/database with the current live files before any later explicit switch step is built. v28.5.25 adds a cutover plan helper/page that generates a readable switch plan only, without changing the live data path.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext
except Exception as exc:  # pragma: no cover - only shown on systems without Tk
    print(f"Tkinter is required for the control app: {exc}", file=sys.stderr)
    raise

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
RUNNER = APP_DIR / "run_movie_library_server.command"
APP_LAUNCHER = APP_DIR / "Movie Library.command"
APP_BUNDLE_LAUNCHER = APP_DIR / "Movie Library.app"
PERMISSIONS_HELPER = APP_DIR / "Fix Movie Library Permissions.command"
DMG_HELPER = APP_DIR / "Build Test Movie Library DMG.command"
DMG_VERIFY_HELPER = APP_DIR / "Verify Test Movie Library DMG.command"
APP_SUPPORT_PREP_HELPER = APP_DIR / "Prepare Movie Library App Support.command"
APP_SUPPORT_VERIFY_HELPER = APP_DIR / "Verify Movie Library App Support.command"
MIGRATION_DRY_RUN_HELPER = APP_DIR / "Dry Run Movie Library Migration.command"
MIGRATION_BACKUP_HELPER = APP_DIR / "Backup Movie Library Migration Data.command"
MIGRATION_BACKUP_VERIFY_HELPER = APP_DIR / "Verify Movie Library Migration Backup.command"
MIGRATION_STAGE_HELPER = APP_DIR / "Stage Movie Library Migration Data.command"
MIGRATION_STAGE_VERIFY_HELPER = APP_DIR / "Verify Movie Library Migration Stage.command"
MIGRATION_CUTOVER_READINESS_HELPER = APP_DIR / "Check Movie Library Migration Cutover.command"
MIGRATION_CUTOVER_PLAN_HELPER = APP_DIR / "Plan Movie Library Migration Cutover.command"
APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "Movie Library"
APP_SUPPORT_PREP_SUMMARY_PATH = APP_SUPPORT_DIR / "Movie Library App Support PREP SUMMARY.txt"
APP_SUPPORT_VERIFY_SUMMARY_PATH = APP_SUPPORT_DIR / "Movie Library App Support VERIFY SUMMARY.txt"
APP_SUPPORT_README_PATH = APP_SUPPORT_DIR / "README - Movie Library Data Folder.txt"
BUILD_DIR = APP_DIR / "build"
TEST_DMG_PATH = BUILD_DIR / "Movie Library Test.dmg"
TEST_DMG_CHECKSUM_PATH = BUILD_DIR / "Movie Library Test.sha256.txt"
TEST_DMG_SUMMARY_PATH = BUILD_DIR / "Movie Library Test DMG SUMMARY.txt"
TEST_DMG_VERIFY_SUMMARY_PATH = BUILD_DIR / "Movie Library Test DMG VERIFY SUMMARY.txt"
TEST_DMG_MANIFEST_PATH = BUILD_DIR / "Movie Library Test DMG CONTENTS MANIFEST.txt"
TEST_DMG_INSTALL_NOTES_PATH = BUILD_DIR / "Movie Library Test DMG INSTALL NOTES.txt"
LOG_DIR = APP_DIR / "logs"
MIGRATION_DRY_RUN_SUMMARY_PATH = LOG_DIR / "movie_library_migration_dry_run_summary.txt"
MIGRATION_DRY_RUN_LOG_PATH = LOG_DIR / "movie_library_migration_dry_run.log"
MIGRATION_BACKUP_LOG_PATH = LOG_DIR / "movie_library_migration_backup.log"
MIGRATION_BACKUP_VERIFY_LOG_PATH = LOG_DIR / "movie_library_migration_backup_verify.log"
MIGRATION_STAGE_LOG_PATH = LOG_DIR / "movie_library_migration_stage.log"
MIGRATION_STAGE_VERIFY_LOG_PATH = LOG_DIR / "movie_library_migration_stage_verify.log"
MIGRATION_CUTOVER_READINESS_LOG_PATH = LOG_DIR / "movie_library_migration_cutover_readiness.log"
MIGRATION_CUTOVER_PLAN_LOG_PATH = LOG_DIR / "movie_library_migration_cutover_plan.log"
MIGRATION_BACKUP_SUMMARY_PATH = APP_SUPPORT_DIR / "migration-backups" / "Movie Library Migration BACKUP SUMMARY.txt"
MIGRATION_BACKUP_VERIFY_SUMMARY_PATH = APP_SUPPORT_DIR / "migration-backups" / "Movie Library Migration BACKUP VERIFY SUMMARY.txt"
MIGRATION_STAGE_SUMMARY_PATH = APP_SUPPORT_DIR / "migration-staging" / "Movie Library Migration STAGE SUMMARY.txt"
MIGRATION_STAGE_VERIFY_SUMMARY_PATH = APP_SUPPORT_DIR / "migration-staging" / "Movie Library Migration STAGE VERIFY SUMMARY.txt"
MIGRATION_CUTOVER_READINESS_SUMMARY_PATH = APP_SUPPORT_DIR / "migration-staging" / "Movie Library Migration CUTOVER READINESS SUMMARY.txt"
MIGRATION_CUTOVER_PLAN_SUMMARY_PATH = APP_SUPPORT_DIR / "migration-staging" / "Movie Library Migration CUTOVER PLAN SUMMARY.txt"
LAUNCHER_LOG_PATHS = [
    LOG_DIR / "movie_library_app_launcher.log",
    LOG_DIR / "movie_library_launcher.log",
    LOG_DIR / "movie_library_server.log",
    LOG_DIR / "movie_library_permissions.log",
    LOG_DIR / "movie_library_dmg_build.log",
    LOG_DIR / "movie_library_dmg_verify.log",
    LOG_DIR / "movie_library_app_support_prep.log",
    LOG_DIR / "movie_library_app_support_verify.log",
    LOG_DIR / "movie_library_migration_dry_run.log",
    LOG_DIR / "movie_library_migration_dry_run_summary.txt",
    LOG_DIR / "movie_library_migration_backup.log",
    LOG_DIR / "movie_library_migration_backup_verify.log",
    MIGRATION_BACKUP_SUMMARY_PATH,
    MIGRATION_BACKUP_VERIFY_SUMMARY_PATH,
    MIGRATION_STAGE_SUMMARY_PATH,
    MIGRATION_STAGE_VERIFY_SUMMARY_PATH,
    MIGRATION_STAGE_VERIFY_LOG_PATH,
    MIGRATION_CUTOVER_READINESS_SUMMARY_PATH,
    MIGRATION_CUTOVER_READINESS_LOG_PATH,
    MIGRATION_CUTOVER_PLAN_SUMMARY_PATH,
    MIGRATION_CUTOVER_PLAN_LOG_PATH,
    APP_DIR / "movie_library_app_launcher.log",
    APP_DIR / "movie_library_launcher.log",
    APP_DIR / "movie_library_server.log",
    APP_DIR / "restart.log",
]
REMOTE_URL = "https://mjeromem75.dyndns.org"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def web_config() -> dict:
    return load_config().get("web", {})


def port() -> int:
    try:
        return int(web_config().get("port", 8765))
    except Exception:
        return 8765


def local_url() -> str:
    return f"http://127.0.0.1:{port()}"


def is_port_open(host: str = "127.0.0.1", check_port: int | None = None) -> bool:
    check_port = check_port or port()
    try:
        with socket.create_connection((host, check_port), timeout=0.8):
            return True
    except OSError:
        return False


class AdminApi:
    def __init__(self) -> None:
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))
        self.logged_in = False
        self.last_url = ""
        self.auth_message = "Not signed in"

    def _request(self, path: str, data: dict | None = None, method: str | None = None) -> tuple[int, str]:
        url = local_url() + path
        payload = None
        headers = {}
        if data is not None:
            payload = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=5) as response:
                self.last_url = response.geturl()
                return response.status, response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return 0, str(exc)

    def login(self) -> bool:
        cfg = web_config()
        username = cfg.get("username", "")
        password = cfg.get("password", "")
        if not username or not password:
            self.auth_message = "Missing admin username/password in config.json"
            self.logged_in = False
            return False

        # Prefer the explicit JSON login endpoint. It avoids guessing whether an
        # HTML /login response was the form page or a real authenticated redirect.
        status, body = self._request("/api/admin/login", {"username": username, "password": password})
        if status == 200:
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            if data.get("ok") and data.get("role") == "admin":
                self.logged_in = True
                self.auth_message = f"Signed in as admin: {username}"
                return True
        elif status in {401, 403}:
            detail = ""
            try:
                detail = (json.loads(body).get("detail") or json.loads(body).get("error") or "")
            except Exception:
                detail = body[:120]
            self.auth_message = detail or "Admin login failed"
            self.logged_in = False
            return False

        # Fallback for older builds that do not yet have /api/admin/login.
        status, body = self._request("/login", {"username": username, "password": password})
        final_path = urllib.parse.urlparse(self.last_url or "").path.rstrip("/") or "/"
        self.logged_in = status in {200, 302} and final_path not in {"/login", "/"} and "Invalid credentials" not in body
        self.auth_message = f"Signed in as admin: {username}" if self.logged_in else "Admin login failed"
        return self.logged_in

    def json_get(self, path: str) -> dict:
        if not self.logged_in and not self.login():
            return {"ok": False, "error": "login_failed", "detail": "Could not log in with the admin username/password from config.json."}
        status, body = self._request(path)
        if status == 401:
            self.logged_in = False
            if not self.login():
                return {"ok": False, "error": "login_required", "detail": "Admin API rejected the saved login. Check config.json admin credentials."}
            status, body = self._request(path)
        if status != 200:
            return {"ok": False, "error": f"HTTP {status}", "detail": body[:300]}
        try:
            return json.loads(body)
        except Exception as exc:
            return {"ok": False, "error": "invalid_json", "detail": str(exc)}

    def post_scan(self, action: str) -> dict:
        if not self.logged_in and not self.login():
            return {"ok": False, "error": "login_failed", "detail": "Could not log in with the admin username/password from config.json."}
        status, body = self._request("/api/admin/run-scan", {"action": action}, method="POST")
        if status == 401:
            self.logged_in = False
            if self.login():
                status, body = self._request("/api/admin/run-scan", {"action": action}, method="POST")
        try:
            data = json.loads(body)
        except Exception:
            data = {"ok": False, "detail": body[:300]}
        data["http_status"] = status
        return data

    def get_action(self, path: str) -> tuple[int, str]:
        if not self.logged_in and not self.login():
            return 401, "Could not log in with the admin username/password from config.json."
        return self._request(path)


class ControlApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Movie Library Server")
        self.geometry("880x740")
        self.minsize(780, 640)
        self.api = AdminApi()
        self.scan_buttons: list[tk.Button] = []
        self.restart_buttons: list[tk.Button] = []
        self.admin_buttons: list[tk.Button] = []
        self.start_button: tk.Button | None = None
        self.last_server_running = False
        self.last_api_ok = False
        self.last_scan_running = False
        self.status_label: tk.Label | None = None
        self.counts_label: tk.Label | None = None
        self.refresh_button: tk.Button | None = None
        self.test_login_button: tk.Button | None = None
        self.auto_refresh_var = tk.BooleanVar(value=True)
        self.auto_refresh_after_id: str | None = None
        self.last_refresh_text_var = tk.StringVar(value="Last refreshed: not yet")
        self.last_status_snapshot = "Status has not been refreshed yet."
        self.refresh_in_progress = False
        self.scan_request_in_progress = False
        self.start_request_in_progress = False
        self.server_action_in_progress = False
        self.operation_var = tk.StringVar(value="Operation: idle")
        self.max_log_lines = 300
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        self.auto_refresh_var.trace_add("write", self._auto_refresh_changed)
        self.bind("<Configure>", self._update_wrap_lengths)
        self.after(300, self.refresh_status)

    def _build_ui(self) -> None:
        pad = {"padx": 14, "pady": 8}
        header = tk.Frame(self)
        header.pack(fill="x", **pad)
        tk.Label(header, text="Movie Library Server", font=("Helvetica", 22, "bold")).pack(anchor="w")
        tk.Label(header, text="Small macOS control app for the existing Flask web server", fg="#555").pack(anchor="w")

        self.status_var = tk.StringVar(value="Checking…")
        self.counts_var = tk.StringVar(value="")
        status_frame = tk.LabelFrame(self, text="Status")
        status_frame.pack(fill="x", **pad)
        self.status_label = tk.Label(status_frame, textvariable=self.status_var, font=("Helvetica", 14, "bold"), justify="left", anchor="w")
        self.status_label.pack(anchor="w", fill="x", padx=12, pady=6)
        self.counts_label = tk.Label(status_frame, textvariable=self.counts_var, justify="left", anchor="w", fg="#333")
        self.counts_label.pack(anchor="w", fill="x", padx=12, pady=(0, 4))
        tk.Label(status_frame, textvariable=self.last_refresh_text_var, justify="left", anchor="w", fg="#666").pack(anchor="w", fill="x", padx=12, pady=(0, 2))
        tk.Label(status_frame, textvariable=self.operation_var, justify="left", anchor="w", fg="#666").pack(anchor="w", fill="x", padx=12, pady=(0, 8))

        buttons = tk.LabelFrame(self, text="Actions")
        buttons.pack(fill="x", **pad)

        # Keep related controls together so the window remains readable on
        # smaller Mac screens. Avoid one very long row of buttons.
        row1 = tk.Frame(buttons)
        row1.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(row1, text="Status:", width=10, anchor="w", fg="#555").pack(side="left", padx=(0, 4))
        self.refresh_button = tk.Button(row1, text="Refresh Status", width=16, command=self.refresh_status)
        self.refresh_button.pack(side="left", padx=4)
        self.test_login_button = tk.Button(row1, text="Test Admin Login", width=18, command=self.test_admin_login)
        self.test_login_button.pack(side="left", padx=4)
        tk.Button(row1, text="Copy Status", width=14, command=self.copy_status).pack(side="left", padx=4)
        tk.Checkbutton(row1, text="Auto-refresh every 10 seconds", variable=self.auto_refresh_var).pack(side="left", padx=12)

        row2 = tk.Frame(buttons)
        row2.pack(fill="x", padx=10, pady=4)
        tk.Label(row2, text="Open:", width=10, anchor="w", fg="#555").pack(side="left", padx=(0, 4))
        tk.Button(row2, text="Launch Library", width=16, command=self.launch_library).pack(side="left", padx=4)
        tk.Button(row2, text="Local Library", width=16, command=lambda: webbrowser.open(local_url())).pack(side="left", padx=4)
        tk.Button(row2, text="Remote Library", width=16, command=lambda: webbrowser.open(REMOTE_URL)).pack(side="left", padx=4)
        open_admin_btn = tk.Button(row2, text="Admin Console", width=16, command=lambda: webbrowser.open(local_url() + "/admin"))
        open_admin_btn.pack(side="left", padx=4)
        self.admin_buttons.append(open_admin_btn)
        server_page_btn = tk.Button(row2, text="Server Page", width=16, command=lambda: webbrowser.open(local_url() + "/server-control"))
        server_page_btn.pack(side="left", padx=4)
        self.admin_buttons.append(server_page_btn)

        row_planning = tk.Frame(buttons)
        row_planning.pack(fill="x", padx=10, pady=4)
        tk.Label(row_planning, text="Planning:", width=10, anchor="w", fg="#555").pack(side="left", padx=(0, 4))
        planning_buttons = [
            ("First-run", "/admin/first-run-setup", 14),
            ("Migration Safety", "/admin/migration-safety", 18),
            ("Migration Dry Run", "/admin/migration-dry-run", 20),
            ("Migration Backup", "/admin/migration-backup", 20),
            ("Verify Backup", "/admin/migration-backup-verify", 18),
            ("Migration Stage", "/admin/migration-stage", 18),
            ("Verify Stage", "/admin/migration-stage-verify", 18),
            ("Cutover Ready", "/admin/migration-cutover-readiness", 18),
            ("Cutover Plan", "/admin/migration-cutover-plan", 18),
            ("DMG Packaging", "/admin/dmg-packaging", 18),
            ("Manifest", "/admin/package-manifest", 14),
            ("Preflight", "/admin/package-preflight", 14),
            ("Test DMG Status", "/admin/test-dmg-status", 18),
            ("App Support Prep", "/admin/app-support-prep", 18),
            ("App Support Status", "/admin/app-support-status", 18),
        ]
        for label, path, width in planning_buttons:
            btn = tk.Button(row_planning, text=label, width=width, command=lambda p=path: webbrowser.open(local_url() + p))
            btn.pack(side="left", padx=4)
            self.admin_buttons.append(btn)

        row3 = tk.Frame(buttons)
        row3.pack(fill="x", padx=10, pady=4)
        tk.Label(row3, text="Library:", width=10, anchor="w", fg="#555").pack(side="left", padx=(0, 4))
        for label, action in (("Check All", "refresh-all"), ("Check Movies", "refresh-movies"), ("Check TV Shows", "refresh-tv")):
            btn = tk.Button(row3, text=label, width=16, command=lambda a=action: self.run_scan(a))
            btn.pack(side="left", padx=4)
            self.scan_buttons.append(btn)
        self.start_button = tk.Button(row3, text="Start Server", width=16, command=self.start_server)
        self.start_button.pack(side="left", padx=4)

        row4 = tk.Frame(buttons)
        row4.pack(fill="x", padx=10, pady=(4, 8))
        tk.Label(row4, text="Server:", width=10, anchor="w", fg="#555").pack(side="left", padx=(0, 4))
        restart_web_btn = tk.Button(row4, text="Restart Web App", width=18, command=lambda: self.confirm_web_action("/restart-web", "Restart the Movie Library Flask web app?" ))
        restart_web_btn.pack(side="left", padx=4)
        restart_caddy_btn = tk.Button(row4, text="Restart Web + Caddy", width=20, command=lambda: self.confirm_web_action("/restart-web-and-caddy", "Restart the Movie Library Flask web app and reload the shared Caddy config?\n\nThis should preserve the Tutor route, but only use it when you really need the reverse proxy reloaded." ))
        restart_caddy_btn.pack(side="left", padx=4)
        self.restart_buttons.extend([restart_web_btn, restart_caddy_btn])

        url_frame = tk.LabelFrame(self, text="URLs / support")
        url_frame.pack(fill="x", **pad)
        self.urls_var = tk.StringVar(value=f"Local: {local_url()}\nRemote: {REMOTE_URL}")
        tk.Label(url_frame, textvariable=self.urls_var, justify="left").pack(anchor="w", padx=12, pady=(8, 4))
        support_row = tk.Frame(url_frame)
        support_row.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(support_row, text="Open App Folder", command=lambda: self.open_path(APP_DIR)).pack(side="left", padx=4)
        tk.Button(support_row, text="Open Mac App", command=lambda: self.open_path(APP_BUNDLE_LAUNCHER)).pack(side="left", padx=4)
        tk.Button(support_row, text="Open Config", command=lambda: self.open_path(CONFIG_PATH)).pack(side="left", padx=4)
        tk.Button(support_row, text="Open Logs", command=self.open_logs_folder).pack(side="left", padx=4)
        tk.Button(support_row, text="Fix Permissions", command=self.fix_permissions).pack(side="left", padx=4)
        tk.Button(support_row, text="Copy Paths", command=self.copy_paths).pack(side="left", padx=4)
        tk.Button(support_row, text="Copy Launcher Logs", command=self.copy_launcher_logs).pack(side="left", padx=4)
        support_row2 = tk.Frame(url_frame)
        support_row2.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(support_row2, text="Build Test DMG", command=self.build_test_dmg).pack(side="left", padx=4)
        tk.Button(support_row2, text="Verify Test DMG", command=self.verify_test_dmg).pack(side="left", padx=4)
        tk.Button(support_row2, text="Open Test DMG", command=self.open_test_dmg).pack(side="left", padx=4)
        tk.Button(support_row2, text="Open Build Folder", command=self.open_build_folder).pack(side="left", padx=4)
        tk.Button(support_row2, text="Copy DMG Logs", command=self.copy_dmg_logs).pack(side="left", padx=4)
        support_row3 = tk.Frame(url_frame)
        support_row3.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(support_row3, text="Prepare App Support", command=self.prepare_app_support).pack(side="left", padx=4)
        tk.Button(support_row3, text="Verify App Support", command=self.verify_app_support).pack(side="left", padx=4)
        tk.Button(support_row3, text="Migration Dry Run", command=self.run_migration_dry_run).pack(side="left", padx=4)
        tk.Button(support_row3, text="Migration Backup", command=self.run_migration_backup).pack(side="left", padx=4)
        tk.Button(support_row3, text="Verify Backup", command=self.run_migration_backup_verify).pack(side="left", padx=4)
        tk.Button(support_row3, text="Migration Stage", command=self.run_migration_stage).pack(side="left", padx=4)
        tk.Button(support_row3, text="Verify Stage", command=self.run_migration_stage_verify).pack(side="left", padx=4)
        tk.Button(support_row3, text="Cutover Check", command=self.run_migration_cutover_readiness).pack(side="left", padx=4)
        tk.Button(support_row3, text="Cutover Plan", command=self.run_migration_cutover_plan).pack(side="left", padx=4)
        support_row4 = tk.Frame(url_frame)
        support_row4.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(support_row4, text="Open App Support", command=lambda: self.open_path(APP_SUPPORT_DIR)).pack(side="left", padx=4)
        tk.Button(support_row4, text="Copy App Support Notes", command=self.copy_app_support_notes).pack(side="left", padx=4)
        tk.Button(support_row4, text="Copy Migration Notes", command=self.copy_migration_notes).pack(side="left", padx=4)

        log_frame = tk.LabelFrame(self, text="Recent events / messages")
        log_frame.pack(fill="both", expand=True, **pad)
        log_tools = tk.Frame(log_frame)
        log_tools.pack(fill="x", padx=10, pady=(8, 0))
        tk.Button(log_tools, text="Copy Messages", command=self.copy_messages).pack(side="left", padx=4)
        tk.Button(log_tools, text="Clear Messages", command=self.clear_messages).pack(side="left", padx=4)
        self.log = scrolledtext.ScrolledText(log_frame, height=12, wrap="word")
        self.log.pack(fill="both", expand=True, padx=10, pady=10)
        self._log("Ready.")

    def launch_library(self) -> None:
        """Use the lightweight command launcher that will become the basis of the
        future proper Movie Library.app entry point.

        This is intentionally safe: it opens the local library if Flask is
        already responding, otherwise it runs Movie Library.command, which
        checks the configured Movie Library port before starting the existing server runner.
        """
        try:
            if is_port_open():
                webbrowser.open(local_url())
                self._log("Launch Library: server already responding, opened local library.")
                return
            if sys.platform == "darwin" and APP_BUNDLE_LAUNCHER.exists():
                subprocess.Popen(["open", str(APP_BUNDLE_LAUNCHER)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._log("Launch Library: opened test Movie Library.app bundle.")
                self.after(1000, self._poll_after_launch_library)
            elif APP_LAUNCHER.exists():
                if sys.platform == "darwin":
                    subprocess.Popen(["open", str(APP_LAUNCHER)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen([str(APP_LAUNCHER)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._log("Launch Library: started safe command launcher.")
                self.after(1000, self._poll_after_launch_library)
            else:
                messagebox.showwarning("Launcher missing", f"Cannot find:\n{APP_LAUNCHER}\n\nExpected test app bundle:\n{APP_BUNDLE_LAUNCHER}")
                self._log(f"Launch Library failed, launcher missing: {APP_LAUNCHER}")
        except Exception as exc:
            messagebox.showerror("Launch failed", str(exc))
            self._log(f"Launch Library failed: {exc}")

    def _poll_after_launch_library(self, attempt: int = 1) -> None:
        if is_port_open():
            webbrowser.open(local_url())
            self._log("Launch Library: server responded, opened local library.")
            self.refresh_status()
            return
        if attempt in {5, 10, 15}:
            self._log(f"Launch Library: waiting for server... {attempt}s")
        if attempt >= 20:
            self._log("Launch Library: server did not respond within 20 seconds. Check the command window/log for errors.")
            self.refresh_status()
            return
        self.after(1000, lambda: self._poll_after_launch_library(attempt + 1))

    def open_path(self, path: Path) -> None:
        try:
            if not path.exists():
                messagebox.showwarning("Not found", f"Cannot find:\n{path}")
                self._log(f"Open path failed, not found: {path}")
                return
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._log(f"Opened in Finder: {path}")
            else:
                messagebox.showinfo("Path", str(path))
                self._log(f"Path shown: {path}")
        except Exception as exc:
            messagebox.showerror("Open failed", str(exc))
            self._log(f"Open path failed: {exc}")

    def copy_paths(self) -> None:
        text = "\n".join([
            "Movie Library local paths",
            f"App folder: {APP_DIR}",
            f"Config file: {CONFIG_PATH}",
            f"Runner: {RUNNER}",
            f"App launcher: {APP_LAUNCHER}",
            f"Test app bundle: {APP_BUNDLE_LAUNCHER}",
            f"Permissions helper: {PERMISSIONS_HELPER}",
            f"Test DMG helper: {DMG_HELPER}",
            f"Build folder: {BUILD_DIR}",
            f"Test DMG: {TEST_DMG_PATH}",
            f"Test DMG checksum: {TEST_DMG_CHECKSUM_PATH}",
            f"Logs folder: {LOG_DIR}",
            f"Local URL: {local_url()}",
            f"Remote URL: {REMOTE_URL}",
        ])
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._log("Copied local paths to clipboard.")
            messagebox.showinfo("Copied", "Local paths copied to clipboard.")
        except Exception as exc:
            messagebox.showerror("Copy failed", str(exc))

    def open_logs_folder(self) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Logs folder", f"Could not create/open logs folder:\n{exc}")
            self._log(f"Open logs failed: {exc}")
            return
        self.open_path(LOG_DIR)

    def open_build_folder(self) -> None:
        try:
            BUILD_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Build folder", f"Could not create/open build folder:\n{exc}")
            self._log(f"Open build folder failed: {exc}")
            return
        self.open_path(BUILD_DIR)

    def open_test_dmg(self) -> None:
        """Open/reveal the generated test DMG if it exists."""
        if not TEST_DMG_PATH.exists():
            messagebox.showinfo("Test DMG not found", "No test DMG has been built yet.\n\nRun Build Test DMG first, then Verify Test DMG.")
            self._log(f"Open test DMG skipped, file not found: {TEST_DMG_PATH}")
            return
        self.open_path(TEST_DMG_PATH)

    def copy_dmg_logs(self) -> None:
        """Copy only the DMG packaging/verification diagnostics."""
        candidates = [
            LOG_DIR / "movie_library_dmg_build.log",
            LOG_DIR / "movie_library_dmg_verify.log",
            TEST_DMG_SUMMARY_PATH,
            TEST_DMG_VERIFY_SUMMARY_PATH,
            TEST_DMG_MANIFEST_PATH,
            TEST_DMG_INSTALL_NOTES_PATH,
            TEST_DMG_CHECKSUM_PATH,
        ]
        sections: list[str] = []
        for path in candidates:
            try:
                if not path.exists() or not path.is_file():
                    continue
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = "\n".join(lines[-120:])
                sections.append(f"--- {path} ---\n{tail}")
            except Exception as exc:
                sections.append(f"--- {path} ---\nCould not read: {exc}")
        if not sections:
            messagebox.showinfo("No DMG logs", "No test DMG build/verify logs were found yet.\n\nRun Build Test DMG and Verify Test DMG first.")
            self._log("Copy DMG logs: no DMG logs or summaries found yet.")
            return
        payload = "Movie Library test DMG diagnostics\nGenerated: " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n\n" + "\n\n".join(sections)
        try:
            self.clipboard_clear()
            self.clipboard_append(payload)
            self._log("Copied test DMG logs/summaries to clipboard.")
            messagebox.showinfo("Copied", "Test DMG logs and summaries copied to clipboard.")
        except Exception as exc:
            messagebox.showerror("Copy DMG logs failed", str(exc))
            self._log(f"Copy DMG logs failed: {exc}")

    def copy_app_support_notes(self) -> None:
        sections: list[str] = []
        for path in [APP_SUPPORT_PREP_SUMMARY_PATH, APP_SUPPORT_VERIFY_SUMMARY_PATH, APP_SUPPORT_README_PATH, LOG_DIR / "movie_library_app_support_prep.log", LOG_DIR / "movie_library_app_support_verify.log"]:
            try:
                if not path.exists() or not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if not text:
                    continue
                sections.append(f"--- {path} ---\n" + "\n".join(text.splitlines()[-100:]))
            except Exception as exc:
                sections.append(f"--- {path} ---\nCould not read: {exc}")
        if not sections:
            messagebox.showinfo("No App Support notes", "No Application Support prep notes were found yet.\n\nRun Prepare App Support first, then copy notes again.")
            self._log("Copy App Support notes: no notes/logs found yet.")
            return
        payload = "Movie Library Application Support prep/verify diagnostics\nGenerated: " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n\n" + "\n\n".join(sections)
        try:
            self.clipboard_clear()
            self.clipboard_append(payload)
            self._log("Copied Application Support prep/verify notes to clipboard.")
            messagebox.showinfo("Copied", "Application Support prep/verify notes copied to clipboard.")
        except Exception as exc:
            messagebox.showerror("Copy App Support notes failed", str(exc))
            self._log(f"Copy App Support notes failed: {exc}")

    def copy_migration_notes(self) -> None:
        sections: list[str] = []
        for path in [MIGRATION_DRY_RUN_SUMMARY_PATH, MIGRATION_DRY_RUN_LOG_PATH, MIGRATION_BACKUP_SUMMARY_PATH, MIGRATION_BACKUP_LOG_PATH, MIGRATION_BACKUP_VERIFY_SUMMARY_PATH, MIGRATION_BACKUP_VERIFY_LOG_PATH, MIGRATION_STAGE_SUMMARY_PATH, MIGRATION_STAGE_LOG_PATH, MIGRATION_STAGE_VERIFY_SUMMARY_PATH, MIGRATION_STAGE_VERIFY_LOG_PATH, MIGRATION_CUTOVER_READINESS_SUMMARY_PATH, MIGRATION_CUTOVER_READINESS_LOG_PATH, MIGRATION_CUTOVER_PLAN_SUMMARY_PATH, MIGRATION_CUTOVER_PLAN_LOG_PATH]:
            try:
                if not path.exists() or not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if not text:
                    continue
                sections.append(f"--- {path} ---\n" + "\n".join(text.splitlines()[-140:]))
            except Exception as exc:
                sections.append(f"--- {path} ---\nCould not read: {exc}")
        if not sections:
            messagebox.showinfo("No migration notes", "No migration dry-run notes were found yet.\n\nRun Migration Dry Run first, then copy notes again.")
            self._log("Copy migration notes: no dry-run notes/logs found yet.")
            return
        payload = "Movie Library migration dry-run diagnostics\nGenerated: " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n\n" + "\n\n".join(sections)
        try:
            self.clipboard_clear()
            self.clipboard_append(payload)
            self._log("Copied migration notes to clipboard.")
            messagebox.showinfo("Copied", "Migration notes copied to clipboard.")
        except Exception as exc:
            messagebox.showerror("Copy migration notes failed", str(exc))
            self._log(f"Copy migration notes failed: {exc}")

    def run_migration_dry_run(self) -> None:
        """Run the future-migration dry-run helper.

        This writes a report/log only. It does not copy or move live data.
        """
        if not MIGRATION_DRY_RUN_HELPER.exists():
            messagebox.showwarning("Migration dry-run helper missing", f"Cannot find:\n{MIGRATION_DRY_RUN_HELPER}")
            self._log(f"Migration dry run failed, helper missing: {MIGRATION_DRY_RUN_HELPER}")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("Mac only", "The migration dry-run helper is intended for macOS.")
            self._log("Migration dry run skipped: macOS-only helper.")
            return
        if not messagebox.askyesno(
            "Run migration dry run?",
            "This will write a dry-run report showing what a later migration wizard would copy.\n\n"
            "It will not copy, move, delete, or edit library.db, config.json, cache, media folders, Tutor files, or the shared Caddyfile. Continue?",
        ):
            self._log("Migration dry run cancelled.")
            return

        self._set_operation("running migration dry run…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(MIGRATION_DRY_RUN_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-16:]) if output else "No output returned."
                self.after(0, lambda: self._log(f"Migration dry run finished with exit code {result.returncode}."))
                self.after(0, lambda: self._log(tail))
                if result.returncode == 0:
                    self.after(0, lambda: messagebox.showinfo("Migration dry run complete", "The dry-run report was created.\n\nOpen Migration Dry Run in the admin planning pages or copy migration notes to review it."))
                else:
                    self.after(0, lambda: messagebox.showwarning("Migration dry run warning", "The helper found missing required items. Prepare/verify Application Support, then run the dry run again."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Migration dry run failed", err))
                self.after(0, lambda err=err: self._log(f"Migration dry run failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)

    def run_migration_backup(self) -> None:
        """Create timestamped safety copies of config.json and library.db.

        This does not switch the app to Application Support and does not touch
        media folders, Tutor files, or the shared Caddyfile.
        """
        if not MIGRATION_BACKUP_HELPER.exists():
            messagebox.showwarning("Migration backup helper missing", f"Cannot find:\n{MIGRATION_BACKUP_HELPER}")
            self._log(f"Migration backup failed, helper missing: {MIGRATION_BACKUP_HELPER}")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("Mac only", "The migration backup helper is intended for macOS.")
            self._log("Migration backup skipped: macOS-only helper.")
            return
        if not messagebox.askyesno(
            "Create migration safety backup?",
            "This will copy config.json and library.db into a timestamped folder under Application Support/migration-backups.\n\n"
            "It will not delete, move, edit, or switch the live database/config, and it will not touch media folders, Tutor files, or the shared Caddyfile. Continue?",
        ):
            self._log("Migration backup cancelled.")
            return

        self._set_operation("creating migration backup…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(MIGRATION_BACKUP_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-18:]) if output else "No output returned."
                self.after(0, lambda: self._log(f"Migration backup finished with exit code {result.returncode}."))
                self.after(0, lambda: self._log(tail))
                if result.returncode == 0:
                    self.after(0, lambda: messagebox.showinfo("Migration backup complete", "Timestamped safety copies were created.\n\nOpen Migration Backup in the admin planning pages or copy migration notes to review the summary."))
                else:
                    self.after(0, lambda: messagebox.showwarning("Migration backup warning", "The backup helper found a problem. Check the migration notes/logs for details."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Migration backup failed", err))
                self.after(0, lambda err=err: self._log(f"Migration backup failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)

    def run_migration_backup_verify(self) -> None:
        """Verify the latest timestamped migration safety backup.

        This reads backup files and checksum metadata only. It does not restore,
        copy, move, delete, or switch live data paths.
        """
        if not MIGRATION_BACKUP_VERIFY_HELPER.exists():
            messagebox.showwarning("Migration backup verify helper missing", f"Cannot find:\n{MIGRATION_BACKUP_VERIFY_HELPER}")
            self._log(f"Migration backup verification failed, helper missing: {MIGRATION_BACKUP_VERIFY_HELPER}")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("Mac only", "The migration backup verification helper is intended for macOS.")
            self._log("Migration backup verification skipped: macOS-only helper.")
            return
        if not messagebox.askyesno(
            "Verify migration backup?",
            "This will inspect the latest timestamped backup under Application Support/migration-backups and verify its checksum file.\n\n"
            "It will not restore, delete, move, edit, copy, or switch the live database/config, and it will not touch media folders, Tutor files, or the shared Caddyfile. Continue?",
        ):
            self._log("Migration backup verification cancelled.")
            return

        self._set_operation("verifying migration backup…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(MIGRATION_BACKUP_VERIFY_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-18:]) if output else "No output returned."
                self.after(0, lambda: self._log(f"Migration backup verification finished with exit code {result.returncode}."))
                self.after(0, lambda: self._log(tail))
                if result.returncode == 0:
                    self.after(0, lambda: messagebox.showinfo("Migration backup verified", "The latest timestamped migration backup was verified.\n\nOpen Verify Backup in the admin planning pages or copy migration notes to review the summary."))
                else:
                    self.after(0, lambda: messagebox.showwarning("Migration backup verification warning", "The verification helper found a problem. Check the migration notes/logs before any future migration step."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Migration backup verification failed", err))
                self.after(0, lambda err=err: self._log(f"Migration backup verification failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)


    def run_migration_stage(self) -> None:
        """Copy config/database into a non-live Application Support staging folder.

        This does not switch live data paths and does not touch media folders,
        Tutor files, or the shared Caddyfile.
        """
        if not MIGRATION_STAGE_HELPER.exists():
            messagebox.showwarning("Migration staging helper missing", f"Cannot find:\n{MIGRATION_STAGE_HELPER}")
            self._log(f"Migration staging failed, helper missing: {MIGRATION_STAGE_HELPER}")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("Mac only", "The migration staging helper is intended for macOS.")
            self._log("Migration staging skipped: macOS-only helper.")
            return
        if not messagebox.askyesno(
            "Stage migration data?",
            "This will copy config.json and library.db into Application Support/migration-staging/current for inspection.\n\n"
            "It will not switch the live app to that folder, and it will not delete, move, edit, or replace the current database/config. Continue?",
        ):
            self._log("Migration staging cancelled.")
            return

        self._set_operation("staging migration data…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(MIGRATION_STAGE_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-18:]) if output else "No output returned."
                self.after(0, lambda: self._log(f"Migration staging finished with exit code {result.returncode}."))
                self.after(0, lambda: self._log(tail))
                if result.returncode == 0:
                    self.after(0, lambda: messagebox.showinfo("Migration data staged", "A non-live staged copy was created in Application Support/migration-staging/current.\n\nOpen Migration Stage in the admin planning pages or copy migration notes to review it."))
                else:
                    self.after(0, lambda: messagebox.showwarning("Migration staging warning", "The staging helper found a problem. Check the migration notes/logs before any future migration step."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Migration staging failed", err))
                self.after(0, lambda err=err: self._log(f"Migration staging failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)

    def run_migration_stage_verify(self) -> None:
        """Verify the non-live staged migration copy.

        This only reads Application Support/migration-staging/current and does not
        switch live data paths or touch media folders, Tutor files, or Caddy.
        """
        if not MIGRATION_STAGE_VERIFY_HELPER.exists():
            messagebox.showwarning("Migration stage verify helper missing", f"Cannot find:\n{MIGRATION_STAGE_VERIFY_HELPER}")
            self._log(f"Migration stage verification failed, helper missing: {MIGRATION_STAGE_VERIFY_HELPER}")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("Mac only", "The migration stage verification helper is intended for macOS.")
            self._log("Migration stage verification skipped: macOS-only helper.")
            return
        if not messagebox.askyesno(
            "Verify staged migration data?",
            "This will inspect the non-live staged copy under Application Support/migration-staging/current and verify staged checksums.\n\n"
            "It will not switch the live app to that folder, and it will not copy, delete, move, edit, or replace the current database/config. Continue?",
        ):
            self._log("Migration stage verification cancelled.")
            return

        self._set_operation("verifying staged migration data…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(MIGRATION_STAGE_VERIFY_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-18:]) if output else "No output returned."
                self.after(0, lambda: self._log(f"Migration stage verification finished with exit code {result.returncode}."))
                self.after(0, lambda: self._log(tail))
                if result.returncode == 0:
                    self.after(0, lambda: messagebox.showinfo("Staged migration data verified", "The non-live staged config/database copy was verified.\n\nOpen Verify Stage in the admin planning pages or copy migration notes to review it."))
                else:
                    self.after(0, lambda: messagebox.showwarning("Migration stage verification warning", "The stage verification helper found a problem. Check migration notes/logs before any future migration step."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Migration stage verification failed", err))
                self.after(0, lambda err=err: self._log(f"Migration stage verification failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)

    def run_migration_cutover_readiness(self) -> None:
        """Run the safe cutover readiness check.

        This compares the staged config/database with the current live files and
        writes a report only. It does not switch live data paths.
        """
        if not MIGRATION_CUTOVER_READINESS_HELPER.exists():
            messagebox.showwarning("Cutover readiness helper missing", f"Cannot find:\n{MIGRATION_CUTOVER_READINESS_HELPER}")
            self._log(f"Cutover readiness check failed, helper missing: {MIGRATION_CUTOVER_READINESS_HELPER}")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("Mac only", "The cutover readiness helper is intended for macOS.")
            self._log("Cutover readiness skipped: macOS-only helper.")
            return
        if not messagebox.askyesno(
            "Run cutover readiness check?",
            "This checks whether the staged config/database still match the current live config/database and whether the previous prep/backup/stage verification summaries exist.\n\n"
            "It will not switch the live app to Application Support, and it will not copy, delete, move, edit, or replace the current database/config. Continue?",
        ):
            self._log("Cutover readiness check cancelled.")
            return

        self._set_operation("checking migration cutover readiness…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(MIGRATION_CUTOVER_READINESS_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-20:]) if output else "No output returned."
                self.after(0, lambda: self._log(f"Cutover readiness finished with exit code {result.returncode}."))
                self.after(0, lambda: self._log(tail))
                if result.returncode == 0:
                    self.after(0, lambda: messagebox.showinfo("Cutover readiness passed", "The staged config/database still match the current live files.\n\nNo live-data switch was performed. Open Cutover Ready or copy migration notes to review the report."))
                else:
                    self.after(0, lambda: messagebox.showwarning("Cutover readiness not ready", "The readiness helper found an issue. Do not switch live data paths yet. Check the migration notes/logs."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Cutover readiness failed", err))
                self.after(0, lambda err=err: self._log(f"Cutover readiness failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)

    def run_migration_cutover_plan(self) -> None:
        """Generate a readable migration cutover plan only.

        This does not switch live data paths and does not copy staged files into
        the final Application Support live targets.
        """
        if not MIGRATION_CUTOVER_PLAN_HELPER.exists():
            messagebox.showwarning("Cutover plan helper missing", f"Cannot find:\n{MIGRATION_CUTOVER_PLAN_HELPER}")
            self._log(f"Cutover plan failed, helper missing: {MIGRATION_CUTOVER_PLAN_HELPER}")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("Mac only", "The cutover plan helper is intended for macOS.")
            self._log("Cutover plan skipped: macOS-only helper.")
            return
        if not messagebox.askyesno(
            "Generate cutover plan?",
            "This writes a readable plan for a later live-data switch to Application Support.\n\n"
            "It will not switch live data paths, copy staged files into the live target, delete, move, edit, or replace the current database/config. Continue?",
        ):
            self._log("Cutover plan generation cancelled.")
            return

        self._set_operation("generating migration cutover plan…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(MIGRATION_CUTOVER_PLAN_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-20:]) if output else "No output returned."
                self.after(0, lambda: self._log(f"Cutover plan finished with exit code {result.returncode}."))
                self.after(0, lambda: self._log(tail))
                if result.returncode == 0:
                    self.after(0, lambda: messagebox.showinfo("Cutover plan generated", "The cutover plan report was generated.\n\nNo live-data switch was performed. Open Cutover Plan or copy migration notes to review it."))
                else:
                    self.after(0, lambda: messagebox.showwarning("Cutover plan warning", "The helper could not complete the plan. Run/check cutover readiness first, then review migration notes."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Cutover plan failed", err))
                self.after(0, lambda err=err: self._log(f"Cutover plan failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)

    def verify_app_support(self) -> None:
        """Verify the future Application Support folder skeleton only.

        This writes a verification summary/log but does not copy or move live data.
        """
        if not APP_SUPPORT_VERIFY_HELPER.exists():
            messagebox.showwarning("App Support verify helper missing", f"Cannot find:\n{APP_SUPPORT_VERIFY_HELPER}")
            self._log(f"Verify App Support failed, helper missing: {APP_SUPPORT_VERIFY_HELPER}")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("Mac only", "The Application Support verify helper is intended for macOS.")
            self._log("Verify App Support skipped: macOS-only helper.")
            return

        self._set_operation("verifying Application Support…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(APP_SUPPORT_VERIFY_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-14:]) if output else "No output returned."
                self.after(0, lambda: self._log(f"Verify App Support finished with exit code {result.returncode}."))
                self.after(0, lambda: self._log(tail))
                if result.returncode == 0:
                    self.after(0, lambda: messagebox.showinfo("Application Support verified", "The future Movie Library data-folder skeleton passed verification.\n\nUse App Support Status in the admin planning pages to review it."))
                else:
                    self.after(0, lambda: messagebox.showwarning("Application Support verify warning", "The helper found missing items. Run Prepare App Support, then verify again."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Verify App Support failed", err))
                self.after(0, lambda err=err: self._log(f"Verify App Support failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)

    def prepare_app_support(self) -> None:
        """Create the future Application Support folder skeleton only.

        This does not copy config, database, cache, media folders, Caddy, or Tutor files.
        """
        if not APP_SUPPORT_PREP_HELPER.exists():
            messagebox.showwarning("App Support helper missing", f"Cannot find:\n{APP_SUPPORT_PREP_HELPER}")
            self._log(f"Prepare App Support failed, helper missing: {APP_SUPPORT_PREP_HELPER}")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("Mac only", "The Application Support prep helper is intended for macOS.")
            self._log("Prepare App Support skipped: macOS-only helper.")
            return
        if not messagebox.askyesno(
            "Prepare Application Support?",
            "This will create the future Movie Library data-folder skeleton in:\n\n"
            f"{APP_SUPPORT_DIR}\n\n"
            "It will not copy or move library.db, config.json, artwork cache, media folders, Tutor files, or the shared Caddyfile. Continue?",
        ):
            self._log("Prepare App Support cancelled.")
            return

        self._set_operation("preparing Application Support…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(APP_SUPPORT_PREP_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-12:]) if output else "No output returned."
                self.after(0, lambda: self._log(f"Prepare App Support finished with exit code {result.returncode}."))
                self.after(0, lambda: self._log(tail))
                if result.returncode == 0:
                    self.after(0, lambda: messagebox.showinfo("Application Support prepared", "The future Movie Library data-folder skeleton was created.\n\nUse App Support Prep in the admin planning pages to review it."))
                else:
                    self.after(0, lambda: messagebox.showwarning("Application Support prep warning", "The helper returned a non-zero exit code. Copy App Support notes or launcher logs for details."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Prepare App Support failed", err))
                self.after(0, lambda err=err: self._log(f"Prepare App Support failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)

    def build_test_dmg(self) -> None:
        """Run the local Mac-only test DMG helper.

        This creates packaging output only. It does not move config, database,
        artwork cache, Caddy, Tutor files, or media folders.
        """
        if not DMG_HELPER.exists():
            messagebox.showwarning("DMG helper missing", f"Cannot find:\n{DMG_HELPER}")
            self._log(f"Build test DMG failed, helper missing: {DMG_HELPER}")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("Mac only", "The test DMG helper uses macOS hdiutil and can only run on the Mac.")
            self._log("Build test DMG skipped: hdiutil is Mac-only.")
            return
        if not messagebox.askyesno(
            "Build test DMG?",
            "This will create a local test DMG in the app folder's build directory.\n\n"
            "It will not move your database, cache, config, media folders, Tutor app, or Caddyfile.\n\n"
            "This is a packaging test, not the final installable Movie Library DMG. Continue?",
        ):
            self._log("Build test DMG cancelled.")
            return

        self._set_operation("building test DMG…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(DMG_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-10:]) if output else "No output returned."
                self.after(0, lambda: self._log(f"Build test DMG finished with exit code {result.returncode}."))
                self.after(0, lambda: self._log(tail))
                if result.returncode == 0:
                    self.after(0, lambda: messagebox.showinfo("Test DMG built", "Test DMG created in the build folder.\n\nUse Open Build Folder to view it."))
                else:
                    self.after(0, lambda: messagebox.showwarning("DMG build finished with warnings", "The helper returned a non-zero exit code. Copy launcher logs for details."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Build test DMG failed", err))
                self.after(0, lambda err=err: self._log(f"Build test DMG failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)

    def verify_test_dmg(self) -> None:
        """Run the local Mac-only test DMG verification helper.

        This only inspects the generated test DMG. It does not install the app,
        start Flask, edit Caddy, move database/cache files, or touch media folders.
        """
        if not DMG_VERIFY_HELPER.exists():
            messagebox.showwarning("DMG verify helper missing", f"Cannot find:\n{DMG_VERIFY_HELPER}")
            self._log(f"Verify test DMG failed, helper missing: {DMG_VERIFY_HELPER}")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("Mac only", "The test DMG verification helper uses macOS hdiutil and can only run on the Mac.")
            self._log("Verify test DMG skipped: hdiutil is Mac-only.")
            return
        if not messagebox.askyesno(
            "Verify test DMG?",
            "This will inspect the generated test DMG in the build folder.\n\n"
            "It mounts the image read-only, checks for the expected files, then detaches it.\n\n"
            "It will not install the app, start the server, edit Caddy, or change your data. Continue?",
        ):
            self._log("Verify test DMG cancelled.")
            return

        self._set_operation("verifying test DMG…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(DMG_VERIFY_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-12:]) if output else "No output returned."
                self.after(0, lambda: self._log(f"Verify test DMG finished with exit code {result.returncode}."))
                self.after(0, lambda: self._log(tail))
                if result.returncode == 0:
                    self.after(0, lambda: messagebox.showinfo("Test DMG verified", "The test DMG mounted/read successfully and contains the expected lightweight files and Finder-style Applications alias."))
                else:
                    self.after(0, lambda: messagebox.showwarning("DMG verification failed", "The verify helper returned a non-zero exit code. Copy launcher logs for details."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Verify test DMG failed", err))
                self.after(0, lambda err=err: self._log(f"Verify test DMG failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)

    def fix_permissions(self) -> None:
        """Run the local permission repair helper.

        This is intentionally local-only: it does not start Flask, does not touch
        Caddy, and does not change media folders or database content.
        """
        if not PERMISSIONS_HELPER.exists():
            messagebox.showwarning("Permission helper missing", f"Cannot find:\n{PERMISSIONS_HELPER}")
            self._log(f"Fix permissions failed, helper missing: {PERMISSIONS_HELPER}")
            return
        if not messagebox.askyesno(
            "Fix launcher permissions?",
            "This will run the local permission helper for Movie Library launchers.\n\n"
            "It applies chmod +x to command/app launcher files and clears macOS quarantine attributes if present.\n\n"
            "It will not start the server, edit Caddy, move the database, or change media folders. Continue?",
        ):
            self._log("Fix permissions cancelled.")
            return

        self._set_operation("fixing launcher permissions…")

        def work() -> None:
            try:
                result = subprocess.run(
                    ["/bin/bash", str(PERMISSIONS_HELPER)],
                    cwd=str(APP_DIR),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-8:]) if output else "No output returned."
                self.after(0, lambda: self._log("Fix permissions finished."))
                self.after(0, lambda: self._log(tail))
                self.after(0, lambda: messagebox.showinfo("Permissions checked", "Launcher permissions have been checked/fixed.\n\nUse Package Preflight or Copy Launcher Logs if anything still fails."))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda err=err: messagebox.showerror("Fix permissions failed", err))
                self.after(0, lambda err=err: self._log(f"Fix permissions failed: {err}"))
            finally:
                self.after(0, lambda: self._set_operation("idle"))

        self._thread(work)

    def copy_launcher_logs(self) -> None:
        sections: list[str] = []
        for path in LAUNCHER_LOG_PATHS:
            try:
                if not path.exists() or not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if not text:
                    continue
                lines = text.splitlines()[-80:]
                sections.append(f"--- {path} ---\n" + "\n".join(lines))
            except Exception as exc:
                sections.append(f"--- {path} ---\nCould not read log: {exc}")
        if not sections:
            messagebox.showinfo("No launcher logs", "No launcher/server logs were found yet. Try Launch Library first, then copy logs again.")
            self._log("Copy launcher logs: no launcher/server logs found yet.")
            return
        payload = "Movie Library launcher diagnostics\nGenerated: " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n\n" + "\n\n".join(sections)
        try:
            self.clipboard_clear()
            self.clipboard_append(payload)
            self._log("Copied launcher/server logs to clipboard.")
            messagebox.showinfo("Copied", "Launcher/server logs copied to clipboard.")
        except Exception as exc:
            messagebox.showerror("Copy logs failed", str(exc))
            self._log(f"Copy launcher logs failed: {exc}")

    def _update_wrap_lengths(self, event=None) -> None:
        wrap = max(420, self.winfo_width() - 80)
        if self.status_label is not None:
            self.status_label.configure(wraplength=wrap)
        if self.counts_label is not None:
            self.counts_label.configure(wraplength=wrap)

    def _log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{stamp}] {text}\n")
        self._trim_log()
        self.log.see("end")

    def _trim_log(self) -> None:
        try:
            line_count = int(self.log.index("end-1c").split(".")[0])
        except Exception:
            return
        if line_count > self.max_log_lines:
            remove_to = max(1, line_count - self.max_log_lines + 1)
            self.log.delete("1.0", f"{remove_to}.0")

    def on_close(self) -> None:
        self._cancel_auto_refresh()
        self.destroy()

    def clear_messages(self) -> None:
        self.log.delete("1.0", "end")
        self._log("Messages cleared.")

    def copy_messages(self) -> None:
        text = self.log.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("No messages", "There are no messages to copy yet.")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._log("Copied recent messages to clipboard.")
        except Exception as exc:
            messagebox.showerror("Copy failed", str(exc))

    def _set_operation(self, text: str) -> None:
        self.operation_var.set("Operation: " + text)

    def _thread(self, func) -> None:
        threading.Thread(target=func, daemon=True).start()

    def _set_button_group(self, buttons: list[tk.Button], enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in buttons:
            button.configure(state=state)

    def _update_button_states(self) -> None:
        server_ready = self.last_server_running and self.last_api_ok
        scans_available = server_ready and not self.last_scan_running and not self.scan_request_in_progress and not self.server_action_in_progress
        self._set_button_group(self.admin_buttons, self.last_server_running)
        self._set_button_group(self.restart_buttons, server_ready and not self.server_action_in_progress)
        self._set_button_group(self.scan_buttons, scans_available)
        if self.start_button is not None:
            self.start_button.configure(state=("disabled" if (self.last_server_running or self.start_request_in_progress) else "normal"))
        if self.test_login_button is not None:
            self.test_login_button.configure(state=("normal" if self.last_server_running else "disabled"))

    def _cancel_auto_refresh(self) -> None:
        if self.auto_refresh_after_id is not None:
            try:
                self.after_cancel(self.auto_refresh_after_id)
            except Exception:
                pass
            self.auto_refresh_after_id = None

    def _schedule_auto_refresh(self) -> None:
        self._cancel_auto_refresh()
        if self.auto_refresh_var.get():
            self.auto_refresh_after_id = self.after(10000, self.refresh_status)

    def _auto_refresh_changed(self, *args) -> None:
        if self.auto_refresh_var.get():
            self._schedule_auto_refresh()
            self._log("Auto-refresh enabled.")
        else:
            self._cancel_auto_refresh()
            self._log("Auto-refresh paused.")

    def refresh_status(self) -> None:
        self._cancel_auto_refresh()
        if self.refresh_in_progress:
            self._schedule_auto_refresh()
            return
        self.refresh_in_progress = True
        self._set_operation("refreshing status…")
        if self.refresh_button is not None:
            self.refresh_button.configure(state="disabled")
        self.last_server_running = is_port_open()
        self.last_api_ok = False
        self.last_scan_running = False
        self._update_button_states()

        def work() -> None:
            port_open = is_port_open()
            self.last_server_running = port_open
            if not port_open:
                self.last_api_ok = False
                self.last_scan_running = False
                not_running_status = f"Server: not responding on port {port()}"
                not_running_counts = "Use Start Server, then refresh status."
                self.last_status_snapshot = self._make_status_snapshot(not_running_status, not_running_counts)
                self.after(0, lambda: self.status_var.set(not_running_status))
                self.after(0, lambda: self.counts_var.set(not_running_counts))
                self.after(0, self._update_button_states)
                self.after(0, self._refresh_finished)
                return

            data = self.api.json_get("/api/admin/status")
            if not data.get("ok"):
                self.last_api_ok = False
                self.last_scan_running = False
                auth_text = self.api.auth_message or "Admin API unavailable"
                detail = str(data.get("detail") or data.get("error") or data)
                status_text = f"Server responding, but admin API unavailable\nAuth: {auth_text}"
                self.last_status_snapshot = self._make_status_snapshot(status_text, detail)
                self.after(0, lambda: self.status_var.set(status_text))
                self.after(0, lambda: self.counts_var.set(detail))
                self.after(0, lambda: self._log(f"Status/API problem: {detail}"))
                self.after(0, self._update_button_states)
                self.after(0, self._refresh_finished)
                return

            server = data.get("server", {})
            library = data.get("library") or data.get("counts", {})
            scan = data.get("scan", {})
            alerts = data.get("alerts", {})
            scheduled = data.get("scheduled_scan", {})
            self.last_api_ok = True
            self.last_scan_running = bool(scan.get("running"))

            missing_total = int(library.get("missing_movies", 0) or 0) + int(library.get("missing_episodes", 0) or 0)
            auth_text = self.api.auth_message or ("Signed in" if self.api.logged_in else "Not signed in")
            scan_state = "running" if scan.get("running") else "idle"
            scan_bits = []
            for key in ("current_scan", "label", "message", "started_at"):
                value = scan.get(key)
                if value:
                    scan_bits.append(str(value))
            scan_detail = (" — " + " | ".join(scan_bits[:3])) if scan_bits else ""
            status_text = (
                f"Server: running on port {port()}\n"
                f"Scan: {scan_state}{scan_detail}\n"
                f"Auth: {auth_text}\n"
                f"Caddy: {'running' if server.get('caddy_running') else 'not detected'}"
            )
            counts_text = (
                f"Movies: {library.get('movies', 0)}    TV shows: {library.get('tv_shows', 0)}    "
                f"Episodes: {library.get('tv_episodes', 0)}\n"
                f"Alerts: {alerts.get('visible', alerts.get('total', 0))}    Missing: {missing_total}    "
                f"Scheduled scan: {'enabled' if scheduled.get('enabled') else 'disabled'}"
            )
            self.last_status_snapshot = self._make_status_snapshot(status_text, counts_text)
            self.after(0, lambda: self.status_var.set(status_text))
            self.after(0, lambda: self.counts_var.set(counts_text))
            self.after(0, lambda: self.urls_var.set(f"Local: {local_url()}\nRemote: {REMOTE_URL}"))
            self.after(0, self._update_button_states)

            server_status = self.api.json_get("/api/admin/server-status")
            if server_status.get("ok"):
                diag = (
                    f"Diagnostics: PID {server_status.get('pid')} | "
                    f"Python {server_status.get('python')} | "
                    f"Caddy {'running' if server_status.get('caddy_running') else 'not detected'}"
                )
            else:
                diag = f"Diagnostics unavailable: {server_status.get('detail') or server_status.get('error')}"

            events = self.api.json_get("/api/admin/recent-events?limit=5")
            latest = []
            if events.get("ok"):
                for item in (events.get("items") or [])[:3]:
                    label = item.get("message") or item.get("event") or item.get("action") or str(item)
                    latest.append(str(label))
            message = "Status refreshed. " + diag + ((" | Latest: " + " | ".join(latest)) if latest else "")
            self.after(0, lambda m=message: self._log(m))
            self.after(0, self._refresh_finished)
        self._thread(work)

    def _refresh_finished(self) -> None:
        self.refresh_in_progress = False
        if self.refresh_button is not None:
            self.refresh_button.configure(state="normal")
        self.last_refresh_text_var.set("Last refreshed: " + time.strftime("%H:%M:%S"))
        self._set_operation("idle")
        self._update_button_states()
        self._schedule_auto_refresh()

    def _make_status_snapshot(self, status_text: str, counts_text: str) -> str:
        cfg = web_config()
        username = cfg.get("username", "") or "not set"
        lines = [
            "Movie Library Control status",
            "Generated: " + time.strftime("%Y-%m-%d %H:%M:%S"),
            f"Local URL: {local_url()}",
            f"Remote URL: {REMOTE_URL}",
            f"Configured admin username: {username}",
            "",
            status_text,
            "",
            counts_text,
        ]
        return "\n".join(lines).strip()

    def copy_status(self) -> None:
        text = self.last_status_snapshot or self._make_status_snapshot(self.status_var.get(), self.counts_var.get())
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._log("Copied current status summary to clipboard.")
            messagebox.showinfo("Copied", "Current status summary copied to clipboard.")
        except Exception as exc:
            messagebox.showerror("Copy failed", str(exc))

    def test_admin_login(self) -> None:
        if self.test_login_button is not None:
            self.test_login_button.configure(state="disabled")

        def finish() -> None:
            self._set_operation("idle")
            self._update_button_states()

        def work() -> None:
            self.after(0, lambda: self._set_operation("testing admin login…"))
            try:
                if not is_port_open():
                    self.after(0, lambda: messagebox.showwarning("Server not running", "Start the server first, then test admin login."))
                    self.after(0, lambda: self._log("Admin login test skipped: server is not responding."))
                    return
                self.api.logged_in = False
                ok = self.api.login()
                message = self.api.auth_message or ("Admin login OK" if ok else "Admin login failed")
                self.after(0, lambda: self._log(f"Admin login test: {message}"))
                if ok:
                    self.after(0, lambda: messagebox.showinfo("Admin login OK", message))
                else:
                    self.after(0, lambda: messagebox.showerror("Admin login failed", message))
                self.after(300, self.refresh_status)
            finally:
                self.after(0, finish)
        self._thread(work)

    def run_scan(self, action: str) -> None:
        if self.scan_request_in_progress:
            self._log("Scan request ignored because another request is already being sent.")
            return
        self.scan_request_in_progress = True
        self.last_scan_running = True
        self._set_operation(f"requesting scan: {action}…")
        self._update_button_states()

        def work() -> None:
            try:
                if not is_port_open():
                    self.after(0, lambda: messagebox.showwarning("Server not running", "Start the server first."))
                    self.after(0, lambda: self._log("Scan request cancelled: server is not responding."))
                    return
                result = self.api.post_scan(action)
                self.after(0, lambda: self._log(f"Scan request {action}: {result}"))
            finally:
                def finish() -> None:
                    self.scan_request_in_progress = False
                    self._set_operation("waiting for scan status…")
                    self._update_button_states()
                    self.refresh_status()
                self.after(600, finish)
        self._thread(work)

    def start_server(self) -> None:
        if self.start_request_in_progress:
            self._log("Start server ignored because a start request is already in progress.")
            return
        if is_port_open():
            messagebox.showinfo("Already running", f"The server is already responding on port {port()}.")
            return
        if not RUNNER.exists():
            messagebox.showerror("Missing runner", f"Cannot find:\n{RUNNER}")
            return
        self.start_request_in_progress = True
        self._set_operation("starting server…")
        self._update_button_states()

        def work() -> None:
            try:
                subprocess.Popen(["/bin/bash", str(RUNNER)], cwd=str(APP_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.after(0, lambda: self._log("Start server requested using run_movie_library_server.command"))

                # Poll gently so the user gets a clear result instead of a silent wait.
                # This does not start a second Flask process; it only checks whether the
                # existing port begins responding after the runner was launched.
                for attempt in range(1, 16):
                    time.sleep(1)
                    if is_port_open():
                        self.after(0, lambda a=attempt: self._log(f"Server started and responded on port {port()} after {a} second(s)."))
                        self.after(0, lambda: self._set_operation("server started; refreshing status…"))
                        self.after(0, self.refresh_status)
                        return
                    if attempt in {5, 10}:
                        self.after(0, lambda a=attempt: self._log(f"Still waiting for server to respond on port {port()} ({a}s)…"))

                self.after(0, lambda: self._log(f"Start requested, but port {port()} did not respond within 15 seconds."))
                self.after(0, lambda: messagebox.showwarning("Server not responding yet", f"The start command was sent, but port {port()} did not respond within 15 seconds. Check the terminal/logs, then refresh status."))
                self.after(0, self.refresh_status)
            except Exception as exc:
                self.after(0, lambda e=exc: messagebox.showerror("Could not start server", str(e)))
                self.after(0, lambda e=exc: self._log(f"Could not start server: {e}"))
            finally:
                def finish() -> None:
                    self.start_request_in_progress = False
                    if not self.refresh_in_progress:
                        self._set_operation("idle")
                    self._update_button_states()
                self.after(0, finish)
        self._thread(work)

    def confirm_web_action(self, path: str, question: str) -> None:
        if self.server_action_in_progress:
            self._log("Server action ignored because another server action is already in progress.")
            return
        if not messagebox.askyesno("Confirm", question):
            return
        self.server_action_in_progress = True
        self._set_operation("requesting server action…")
        self._update_button_states()

        def work() -> None:
            try:
                if not is_port_open():
                    self.after(0, lambda: messagebox.showwarning("Server not running", "The server is not responding."))
                    self.after(0, lambda: self._log(f"Server action skipped: {path} because the server is not responding."))
                    return

                status, body = self.api.get_action(path)
                self.after(0, lambda: self._log(f"Requested {path}: HTTP {status} {body[:160]}"))
                if status not in {200, 202, 204, 302}:
                    self.after(0, lambda: messagebox.showwarning("Server action response", f"Request returned HTTP {status}. Check the message log for details."))
                    self.after(0, self.refresh_status)
                    return

                # Restart endpoints can briefly drop the Flask connection. Poll the
                # port so the user gets a clear 'back online' message instead of
                # guessing when to press Refresh Status. This only checks port 8765;
                # it never starts another Flask process and never edits Caddy.
                self.after(0, lambda: self._set_operation("restart requested; waiting for server…"))
                saw_down = False
                for attempt in range(1, 26):
                    time.sleep(1)
                    running = is_port_open()
                    if not running:
                        saw_down = True
                        if attempt in {3, 8, 15}:
                            self.after(0, lambda a=attempt: self._log(f"Waiting for server to come back on port {port()} ({a}s)…"))
                        continue
                    if running and (saw_down or attempt >= 3):
                        self.after(0, lambda a=attempt: self._log(f"Server is responding again on port {port()} after {a} second(s)."))
                        self.after(0, lambda: self._set_operation("server back online; refreshing status…"))
                        self.after(0, self.refresh_status)
                        return

                self.after(0, lambda: self._log(f"Restart request sent, but port {port()} was not confirmed healthy within 25 seconds."))
                self.after(0, lambda: messagebox.showwarning("Server not confirmed", f"The restart request was sent, but port {port()} was not confirmed healthy within 25 seconds. Check the message log, then refresh status."))
                self.after(0, self.refresh_status)
            finally:
                def finish() -> None:
                    self.server_action_in_progress = False
                    if not self.refresh_in_progress:
                        self._set_operation("idle")
                    self._update_button_states()
                self.after(0, finish)
        self._thread(work)


if __name__ == "__main__":
    ControlApp().mainloop()
