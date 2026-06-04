#!/bin/bash
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
APP_SUPPORT_DIR="$HOME/Library/Application Support/Movie Library"
STAGING_ROOT="$APP_SUPPORT_DIR/migration-staging"
STAGING_CURRENT="$STAGING_ROOT/current"
SUMMARY_PATH="$STAGING_ROOT/Movie Library Migration STAGE VERIFY SUMMARY.txt"
LOG_FILE="$LOG_DIR/movie_library_migration_stage_verify.log"
CONFIG_TARGET="$STAGING_CURRENT/config.json"
DB_TARGET="$STAGING_CURRENT/library.db"
CHECKSUM_TARGET="$STAGING_CURRENT/SHA256SUMS.txt"
DETAIL_SUMMARY="$STAGING_CURRENT/STAGE SUMMARY.txt"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

status=0
checksum_status="not checked"
db_integrity_status="not checked"

file_size() {
  local path="$1"
  if [ -f "$path" ]; then
    /usr/bin/stat -f%z "$path" 2>/dev/null || /usr/bin/stat -c%s "$path" 2>/dev/null || echo "unknown"
  else
    echo "missing"
  fi
}

sha_value() {
  local path="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  else
    return 1
  fi
}

expected_hash_for() {
  local needle="$1"
  if [ -f "$CHECKSUM_TARGET" ]; then
    grep -F "$needle" "$CHECKSUM_TARGET" 2>/dev/null | tail -1 | awk '{print $1}'
  fi
}

write_summary() {
  local result="$1"
  mkdir -p "$STAGING_ROOT" 2>/dev/null || true
  {
    echo "Movie Library migration staging verification summary"
    echo "Generated: $(date)"
    echo "Result: $result"
    echo "App folder: $APP_DIR"
    echo "Application Support folder: $APP_SUPPORT_DIR"
    echo "Staging folder: $STAGING_CURRENT"
    echo ""
    echo "Staged files checked:"
    echo "- config.json: $CONFIG_TARGET ($(file_size "$CONFIG_TARGET") bytes)"
    echo "- library.db: $DB_TARGET ($(file_size "$DB_TARGET") bytes)"
    echo "- SHA256SUMS.txt: $CHECKSUM_TARGET ($(file_size "$CHECKSUM_TARGET") bytes)"
    echo "- STAGE SUMMARY.txt: $DETAIL_SUMMARY ($(file_size "$DETAIL_SUMMARY") bytes)"
    echo ""
    echo "Checksum status: $checksum_status"
    echo "SQLite integrity status: $db_integrity_status"
    echo ""
    echo "Safety: this verification helper only reads the non-live staged copy."
    echo "It does not switch the running app to Application Support."
    echo "It does not copy, restore, move, edit, delete, or replace the current config/database."
    echo "It does not touch media folders, Tutor files, or the shared Caddyfile."
  } > "$SUMMARY_PATH"
}

check_hash() {
  local path="$1"
  local needle="$2"
  local label="$3"
  local expected=""
  local actual=""
  expected="$(expected_hash_for "$needle" || true)"
  if [ -z "$expected" ]; then
    echo "ERROR: No checksum entry found for $label in $CHECKSUM_TARGET"
    status=1
    return
  fi
  if ! actual="$(sha_value "$path")"; then
    echo "ERROR: No SHA-256 tool found to verify $label."
    status=1
    return
  fi
  if [ "$actual" = "$expected" ]; then
    echo "OK: $label checksum matches."
  else
    echo "ERROR: $label checksum mismatch."
    echo "Expected: $expected"
    echo "Actual:   $actual"
    status=1
  fi
}

echo ""
echo "=== Movie Library migration staging verification ==="
echo "Started: $(date)"
echo "App folder: $APP_DIR"
echo "Staging folder: $STAGING_CURRENT"
echo ""

if [ "$(uname)" != "Darwin" ]; then
  echo "ERROR: This helper is intended for macOS."
  write_summary "SKIPPED - not macOS"
  exit 2
fi

if [ ! -d "$STAGING_CURRENT" ]; then
  echo "ERROR: Staging folder is missing: $STAGING_CURRENT"
  echo "Run Stage Movie Library Migration Data.command first."
  write_summary "FAILED - staging folder missing"
  exit 1
fi

for required in "$CONFIG_TARGET" "$DB_TARGET" "$CHECKSUM_TARGET" "$DETAIL_SUMMARY"; do
  if [ ! -f "$required" ]; then
    echo "ERROR: Missing staged file: $required"
    status=1
  else
    echo "OK: $required ($(file_size "$required") bytes)"
  fi
done

if [ "$status" -ne 0 ]; then
  checksum_status="failed - required staged files missing"
  write_summary "FAILED - required staged files missing"
  exit "$status"
fi

echo ""
echo "Checksum verification:"
checksum_status="checking"
check_hash "$CONFIG_TARGET" "config.json" "config.json"
check_hash "$DB_TARGET" "library.db" "library.db"
if [ "$status" -eq 0 ]; then
  checksum_status="PASS - staged checksums match"
else
  checksum_status="FAILED - staged checksum mismatch or missing entry"
fi

echo ""
echo "SQLite integrity check:"
if command -v sqlite3 >/dev/null 2>&1; then
  db_check="$(sqlite3 "$DB_TARGET" 'PRAGMA integrity_check;' 2>&1 || true)"
  echo "$db_check"
  if [ "$db_check" = "ok" ]; then
    db_integrity_status="PASS - sqlite integrity_check returned ok"
  else
    db_integrity_status="WARN - sqlite integrity_check did not return ok"
    status=1
  fi
else
  db_integrity_status="WARN - sqlite3 command not found, database integrity was not checked"
  echo "$db_integrity_status"
fi

echo ""
if [ "$status" -eq 0 ]; then
  write_summary "PASS - staged migration copy verified"
  echo "PASS: staged migration copy verified."
else
  write_summary "FAILED - staged migration copy did not verify cleanly"
  echo "FAILED: staged migration copy did not verify cleanly."
fi

echo "Summary: $SUMMARY_PATH"
echo "Finished: $(date)"
echo "Log: $LOG_FILE"
echo ""
exit "$status"
