#!/bin/bash
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
APP_SUPPORT_DIR="$HOME/Library/Application Support/Movie Library"
STAGING_ROOT="$APP_SUPPORT_DIR/migration-staging"
STAGING_CURRENT="$STAGING_ROOT/current"
SUMMARY_PATH="$STAGING_ROOT/Movie Library Migration STAGE SUMMARY.txt"
LOG_FILE="$LOG_DIR/movie_library_migration_stage.log"
CONFIG_SOURCE="$APP_DIR/config.json"
DB_SOURCE="$APP_DIR/library.db"
CONFIG_TARGET="$STAGING_CURRENT/config.json"
DB_TARGET="$STAGING_CURRENT/library.db"
CHECKSUM_TARGET="$STAGING_CURRENT/SHA256SUMS.txt"
DETAIL_SUMMARY="$STAGING_CURRENT/STAGE SUMMARY.txt"

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
  mkdir -p "$STAGING_ROOT" 2>/dev/null || true
  {
    echo "Movie Library migration staging summary"
    echo "Generated: $(date)"
    echo "Result: $result"
    echo "App folder: $APP_DIR"
    echo "Application Support folder: $APP_SUPPORT_DIR"
    echo "Staging folder: $STAGING_CURRENT"
    echo ""
    echo "Staged files:"
    echo "- config.json: $CONFIG_TARGET ($(file_size "$CONFIG_TARGET") bytes)"
    echo "- library.db: $DB_TARGET ($(file_size "$DB_TARGET") bytes)"
    echo "- checksums: $CHECKSUM_TARGET"
    echo ""
    echo "Safety: this helper creates a non-live staged copy only."
    echo "It does not switch the running app to Application Support."
    echo "It does not delete, move, edit, or replace the current config/database."
    echo "It does not touch media folders, Tutor files, or the shared Caddyfile."
  } > "$SUMMARY_PATH"
}

echo ""
echo "=== Movie Library migration staging ==="
echo "Started: $(date)"
echo "App folder: $APP_DIR"
echo "Staging folder: $STAGING_CURRENT"
echo ""

if [ "$(uname)" != "Darwin" ]; then
  echo "ERROR: This helper is intended for macOS."
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
if [ ! -d "$APP_SUPPORT_DIR" ]; then
  echo "ERROR: Missing Application Support folder: $APP_SUPPORT_DIR"
  echo "Run Prepare Movie Library App Support.command first."
  status=1
fi
if [ "$status" -ne 0 ]; then
  write_summary "FAILED - missing required source/target"
  exit "$status"
fi

mkdir -p "$STAGING_CURRENT"

cp -p "$CONFIG_SOURCE" "$CONFIG_TARGET"
cp -p "$DB_SOURCE" "$DB_TARGET"

{
  sha_file "$CONFIG_TARGET" || true
  sha_file "$DB_TARGET" || true
} > "$CHECKSUM_TARGET"

{
  echo "Movie Library migration staging detail"
  echo "Generated: $(date)"
  echo "Source app folder: $APP_DIR"
  echo "Staging folder: $STAGING_CURRENT"
  echo ""
  echo "Source files:"
  echo "- $CONFIG_SOURCE ($(file_size "$CONFIG_SOURCE") bytes)"
  echo "- $DB_SOURCE ($(file_size "$DB_SOURCE") bytes)"
  echo ""
  echo "Staged files:"
  echo "- $CONFIG_TARGET ($(file_size "$CONFIG_TARGET") bytes)"
  echo "- $DB_TARGET ($(file_size "$DB_TARGET") bytes)"
  echo "- $CHECKSUM_TARGET"
  echo ""
  echo "Not staged by this helper:"
  echo "- media folders"
  echo "- artwork cache"
  echo "- video files"
  echo "- Tutor app files"
  echo "- shared Caddyfile"
  echo "- live app data path switch"
} > "$DETAIL_SUMMARY"

write_summary "PASS - non-live staged copy created"

echo "PASS: migration data staged safely."
echo "Staging folder: $STAGING_CURRENT"
echo "Summary: $SUMMARY_PATH"
echo "Checksums: $CHECKSUM_TARGET"
echo "Finished: $(date)"
echo "Log: $LOG_FILE"
echo ""
