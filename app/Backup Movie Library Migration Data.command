#!/bin/bash
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
APP_SUPPORT_DIR="$HOME/Library/Application Support/Movie Library"
BACKUP_ROOT="$APP_SUPPORT_DIR/migration-backups"
CONFIG_SOURCE="$APP_DIR/config.json"
DB_SOURCE="$APP_DIR/library.db"
ARTWORK_SOURCE="$APP_DIR/.cache/artwork"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/backup-$STAMP"
SUMMARY_PATH="$BACKUP_ROOT/Movie Library Migration BACKUP SUMMARY.txt"
LOG_FILE="$LOG_DIR/movie_library_migration_backup.log"

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

sha_file() {
  local path="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path"
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path"
  else
    echo "checksum tool not found: $path"
    return 1
  fi
}

write_summary() {
  local result="$1"
  {
    echo "Movie Library migration safety backup summary"
    echo "Generated: $(date)"
    echo "Result: $result"
    echo "App folder: $APP_DIR"
    echo "Backup folder: $BACKUP_DIR"
    echo "Application Support folder: $APP_SUPPORT_DIR"
    echo ""
    echo "Copied safety backups:"
    echo "- config.json: $BACKUP_DIR/config.json ($(file_size "$BACKUP_DIR/config.json") bytes)"
    echo "- library.db: $BACKUP_DIR/library.db ($(file_size "$BACKUP_DIR/library.db") bytes)"
    echo "- checksums: $BACKUP_DIR/SHA256SUMS.txt"
    echo ""
    echo "Not copied by this helper:"
    echo "- media folders"
    echo "- video files"
    echo "- Tutor app files"
    echo "- shared Caddyfile"
    echo "- live data path switch"
    echo ""
    echo "Safety: this backup helper does not delete, move, edit, or replace the current config/database."
  } > "$SUMMARY_PATH"
}

echo ""
echo "=== Movie Library migration safety backup ==="
echo "Started: $(date)"
echo "App folder: $APP_DIR"
echo "Backup root: $BACKUP_ROOT"
echo ""

if [ "$(uname)" != "Darwin" ]; then
  echo "ERROR: This helper is intended for macOS."
  mkdir -p "$BACKUP_ROOT" 2>/dev/null || true
  write_summary "SKIPPED - not macOS"
  exit 2
fi

if [ ! -f "$CONFIG_SOURCE" ]; then
  echo "ERROR: Missing config source: $CONFIG_SOURCE"
  status=1
fi
if [ ! -f "$DB_SOURCE" ]; then
  echo "ERROR: Missing database source: $DB_SOURCE"
  status=1
fi
if [ "$status" -ne 0 ]; then
  mkdir -p "$BACKUP_ROOT" 2>/dev/null || true
  write_summary "FAILED - missing required source file"
  exit "$status"
fi

mkdir -p "$BACKUP_DIR"

cp -p "$CONFIG_SOURCE" "$BACKUP_DIR/config.json"
cp -p "$DB_SOURCE" "$BACKUP_DIR/library.db"

{
  sha_file "$BACKUP_DIR/config.json" || true
  sha_file "$BACKUP_DIR/library.db" || true
} > "$BACKUP_DIR/SHA256SUMS.txt"

{
  echo "Movie Library migration backup detail"
  echo "Generated: $(date)"
  echo "Source app folder: $APP_DIR"
  echo "Backup folder: $BACKUP_DIR"
  echo ""
  echo "Source files:"
  echo "- $CONFIG_SOURCE ($(file_size "$CONFIG_SOURCE") bytes)"
  echo "- $DB_SOURCE ($(file_size "$DB_SOURCE") bytes)"
  if [ -d "$ARTWORK_SOURCE" ]; then
    echo "- Artwork cache present, not copied by this helper: $ARTWORK_SOURCE"
  else
    echo "- Artwork cache not present: $ARTWORK_SOURCE"
  fi
  echo ""
  echo "Backed up files:"
  echo "- $BACKUP_DIR/config.json ($(file_size "$BACKUP_DIR/config.json") bytes)"
  echo "- $BACKUP_DIR/library.db ($(file_size "$BACKUP_DIR/library.db") bytes)"
  echo "- $BACKUP_DIR/SHA256SUMS.txt"
  echo ""
  echo "This helper did not switch the app to Application Support."
} > "$BACKUP_DIR/BACKUP SUMMARY.txt"

write_summary "PASS - timestamped safety backup created"

echo "PASS: migration safety backup created."
echo "Backup folder: $BACKUP_DIR"
echo "Summary: $SUMMARY_PATH"
echo "Checksums: $BACKUP_DIR/SHA256SUMS.txt"
echo "Finished: $(date)"
echo "Log: $LOG_FILE"
echo ""
