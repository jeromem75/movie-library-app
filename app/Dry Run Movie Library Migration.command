#!/bin/bash
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
APP_SUPPORT_DIR="$HOME/Library/Application Support/Movie Library"
CACHE_TARGET="$APP_SUPPORT_DIR/cache"
ARTWORK_TARGET="$CACHE_TARGET/artwork"
LOGS_TARGET="$APP_SUPPORT_DIR/logs"
EXPORTS_TARGET="$APP_SUPPORT_DIR/exports"
MIGRATION_BACKUPS_TARGET="$APP_SUPPORT_DIR/migration-backups"
CONFIG_SOURCE="$APP_DIR/config.json"
DB_SOURCE="$APP_DIR/library.db"
ARTWORK_SOURCE="$APP_DIR/.cache/artwork"
LOGS_SOURCE="$APP_DIR/logs"
SUMMARY_PATH="$LOG_DIR/movie_library_migration_dry_run_summary.txt"
LOG_FILE="$LOG_DIR/movie_library_migration_dry_run.log"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

status=0

file_size() {
  local path="$1"
  if [ -f "$path" ]; then
    /usr/bin/stat -f%z "$path" 2>/dev/null || /usr/bin/stat -c%s "$path" 2>/dev/null || echo "unknown"
  else
    echo "missing"
  fi
}

dir_status() {
  local path="$1"
  if [ -d "$path" ]; then
    echo "present"
  else
    echo "missing"
  fi
}

check_required_file() {
  local label="$1"
  local path="$2"
  if [ -f "$path" ]; then
    echo "OK required file: $label -> $path ($(file_size "$path") bytes)"
  else
    echo "MISSING required file: $label -> $path"
    status=1
  fi
}

check_required_dir() {
  local label="$1"
  local path="$2"
  if [ -d "$path" ]; then
    echo "OK required directory: $label -> $path"
  else
    echo "MISSING required directory: $label -> $path"
    status=1
  fi
}

check_optional_dir() {
  local label="$1"
  local path="$2"
  if [ -d "$path" ]; then
    echo "FOUND optional directory: $label -> $path"
  else
    echo "Optional directory not present: $label -> $path"
  fi
}

write_summary() {
  {
    echo "Movie Library migration dry-run summary"
    echo "Generated: $(date)"
    echo "App folder: $APP_DIR"
    echo "Future Application Support folder: $APP_SUPPORT_DIR"
    echo ""
    echo "Result: $1"
    echo ""
    echo "Source items a later migration wizard would consider:"
    echo "- config.json: $CONFIG_SOURCE ($(file_size "$CONFIG_SOURCE") bytes)"
    echo "- library.db: $DB_SOURCE ($(file_size "$DB_SOURCE") bytes)"
    echo "- artwork cache: $ARTWORK_SOURCE ($(dir_status "$ARTWORK_SOURCE"))"
    echo "- logs: $LOGS_SOURCE ($(dir_status "$LOGS_SOURCE"))"
    echo ""
    echo "Future target items checked:"
    echo "- $APP_SUPPORT_DIR ($(dir_status "$APP_SUPPORT_DIR"))"
    echo "- $CACHE_TARGET ($(dir_status "$CACHE_TARGET"))"
    echo "- $ARTWORK_TARGET ($(dir_status "$ARTWORK_TARGET"))"
    echo "- $LOGS_TARGET ($(dir_status "$LOGS_TARGET"))"
    echo "- $EXPORTS_TARGET ($(dir_status "$EXPORTS_TARGET"))"
    echo "- $MIGRATION_BACKUPS_TARGET ($(dir_status "$MIGRATION_BACKUPS_TARGET"))"
    echo ""
    echo "Planned future migration only, not performed by this helper:"
    echo "- Copy config.json to $APP_SUPPORT_DIR/config.json after explicit confirmation."
    echo "- Copy library.db to $APP_SUPPORT_DIR/library.db after explicit confirmation and safety copy."
    echo "- Optionally copy/rebuild artwork cache outside the .app bundle."
    echo ""
    echo "Safety:"
    echo "- No database copied."
    echo "- No config copied."
    echo "- No cache copied."
    echo "- No media folders touched."
    echo "- No Tutor files touched."
    echo "- No Caddyfile touched."
  } > "$SUMMARY_PATH"
}

echo ""
echo "=== Movie Library migration dry run ==="
echo "Started: $(date)"
echo "App folder: $APP_DIR"
echo "Future Application Support folder: $APP_SUPPORT_DIR"
echo ""

if [ "$(uname)" != "Darwin" ]; then
  echo "ERROR: This helper is intended for macOS."
  write_summary "SKIPPED - not macOS"
  exit 2
fi

check_required_file "Current config" "$CONFIG_SOURCE"
check_required_file "Current database" "$DB_SOURCE"
check_required_dir "Application Support root" "$APP_SUPPORT_DIR"
check_required_dir "Application Support cache" "$CACHE_TARGET"
check_required_dir "Application Support logs" "$LOGS_TARGET"
check_required_dir "Application Support exports" "$EXPORTS_TARGET"
check_required_dir "Application Support migration-backups" "$MIGRATION_BACKUPS_TARGET"
check_optional_dir "Current artwork cache" "$ARTWORK_SOURCE"
check_optional_dir "Current logs" "$LOGS_SOURCE"

echo ""
if [ "$status" -eq 0 ]; then
  echo "PASS: dry-run prerequisites are present. A later migration wizard can be designed from these paths."
  write_summary "PASS - dry-run prerequisites present"
else
  echo "NEEDS ATTENTION: missing required source or target items. Prepare/verify Application Support before migration."
  write_summary "NEEDS ATTENTION - missing required source or target items"
fi

echo "Summary: $SUMMARY_PATH"
echo "Finished: $(date)"
echo "Log: $LOG_FILE"
echo ""

exit "$status"
