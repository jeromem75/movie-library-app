#!/bin/bash
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
APP_SUPPORT_DIR="$HOME/Library/Application Support/Movie Library"
STAGING_ROOT="$APP_SUPPORT_DIR/migration-staging"
STAGING_CURRENT="$STAGING_ROOT/current"
SUMMARY_PATH="$STAGING_ROOT/Movie Library Migration CUTOVER READINESS SUMMARY.txt"
LOG_FILE="$LOG_DIR/movie_library_migration_cutover_readiness.log"

CURRENT_CONFIG="$APP_DIR/config.json"
CURRENT_DB="$APP_DIR/library.db"
STAGED_CONFIG="$STAGING_CURRENT/config.json"
STAGED_DB="$STAGING_CURRENT/library.db"
STAGED_CHECKSUMS="$STAGING_CURRENT/SHA256SUMS.txt"
APP_SUPPORT_VERIFY_SUMMARY="$APP_SUPPORT_DIR/Movie Library App Support VERIFY SUMMARY.txt"
BACKUP_VERIFY_SUMMARY="$APP_SUPPORT_DIR/migration-backups/Movie Library Migration BACKUP VERIFY SUMMARY.txt"
STAGE_VERIFY_SUMMARY="$STAGING_ROOT/Movie Library Migration STAGE VERIFY SUMMARY.txt"

mkdir -p "$LOG_DIR"
mkdir -p "$STAGING_ROOT" 2>/dev/null || true
exec > >(tee -a "$LOG_FILE") 2>&1

status=0
result="NOT READY"
current_config_hash="not checked"
staged_config_hash="not checked"
current_db_hash="not checked"
staged_db_hash="not checked"
config_match="not checked"
db_match="not checked"
staged_db_integrity="not checked"

file_size() {
  local path="$1"
  if [ -f "$path" ]; then
    /usr/bin/stat -f%z "$path" 2>/dev/null || /usr/bin/stat -c%s "$path" 2>/dev/null || echo "unknown"
  else
    echo "missing"
  fi
}

file_mtime() {
  local path="$1"
  if [ -e "$path" ]; then
    /usr/bin/stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" "$path" 2>/dev/null || /usr/bin/stat -c "%y" "$path" 2>/dev/null || echo "unknown"
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

mark_error() {
  echo "ERROR: $1"
  status=1
}

check_file() {
  local label="$1"
  local path="$2"
  if [ -f "$path" ]; then
    echo "OK: $label exists: $path ($(file_size "$path") bytes, modified $(file_mtime "$path"))"
  else
    mark_error "$label missing: $path"
  fi
}

check_summary() {
  local label="$1"
  local path="$2"
  if [ -f "$path" ]; then
    echo "OK: $label summary exists: $path"
  else
    mark_error "$label summary missing: $path"
  fi
}

write_summary() {
  local final_result="$1"
  {
    echo "Movie Library migration cutover readiness summary"
    echo "Generated: $(date)"
    echo "Result: $final_result"
    echo ""
    echo "Important safety note: no live-data switch was performed."
    echo "This helper only reads current and staged files, compares checksums, and writes this report/log."
    echo ""
    echo "Current developer app folder: $APP_DIR"
    echo "Future Application Support folder: $APP_SUPPORT_DIR"
    echo "Staging folder: $STAGING_CURRENT"
    echo ""
    echo "Required summaries:"
    echo "- App Support verify summary: $APP_SUPPORT_VERIFY_SUMMARY"
    echo "- Backup verify summary: $BACKUP_VERIFY_SUMMARY"
    echo "- Stage verify summary: $STAGE_VERIFY_SUMMARY"
    echo ""
    echo "Current files:"
    echo "- config.json: $CURRENT_CONFIG ($(file_size "$CURRENT_CONFIG") bytes, modified $(file_mtime "$CURRENT_CONFIG"))"
    echo "- library.db: $CURRENT_DB ($(file_size "$CURRENT_DB") bytes, modified $(file_mtime "$CURRENT_DB"))"
    echo ""
    echo "Staged files:"
    echo "- config.json: $STAGED_CONFIG ($(file_size "$STAGED_CONFIG") bytes, modified $(file_mtime "$STAGED_CONFIG"))"
    echo "- library.db: $STAGED_DB ($(file_size "$STAGED_DB") bytes, modified $(file_mtime "$STAGED_DB"))"
    echo "- SHA256SUMS.txt: $STAGED_CHECKSUMS ($(file_size "$STAGED_CHECKSUMS") bytes, modified $(file_mtime "$STAGED_CHECKSUMS"))"
    echo ""
    echo "Checksum comparison:"
    echo "- current config: $current_config_hash"
    echo "- staged config:  $staged_config_hash"
    echo "- config match:   $config_match"
    echo "- current db:     $current_db_hash"
    echo "- staged db:      $staged_db_hash"
    echo "- database match: $db_match"
    echo ""
    echo "Staged database integrity: $staged_db_integrity"
    echo ""
    echo "Next action guidance:"
    if [ "$final_result" = "READY - staged data matches current live data" ]; then
      echo "- The checked staged copy appears aligned with the current config/database at the time of this report."
      echo "- A later explicit migration switch helper/page can be built after this, but should still require a final confirmation."
    else
      echo "- Do not switch live data paths yet."
      echo "- Re-run App Support prep/verify, migration backup/verify, migration stage, and stage verify as needed."
      echo "- If current library.db changed after staging, stage the data again before considering a future switch."
    fi
    echo ""
    echo "Guardrails preserved:"
    echo "- No live path switch."
    echo "- No database/config overwrite."
    echo "- No media folder changes."
    echo "- No Tutor changes."
    echo "- No shared Caddyfile changes."
  } > "$SUMMARY_PATH"
}

echo ""
echo "=== Movie Library migration cutover readiness check ==="
echo "Started: $(date)"
echo "App folder: $APP_DIR"
echo "Application Support: $APP_SUPPORT_DIR"
echo "Staging folder: $STAGING_CURRENT"
echo ""

if [ "$(uname)" != "Darwin" ]; then
  mark_error "This helper is intended for macOS."
fi

if [ ! -d "$APP_SUPPORT_DIR" ]; then
  mark_error "Application Support folder missing: $APP_SUPPORT_DIR"
else
  echo "OK: Application Support folder exists: $APP_SUPPORT_DIR"
fi

check_file "Current config.json" "$CURRENT_CONFIG"
check_file "Current library.db" "$CURRENT_DB"
check_file "Staged config.json" "$STAGED_CONFIG"
check_file "Staged library.db" "$STAGED_DB"
check_file "Staged SHA256SUMS.txt" "$STAGED_CHECKSUMS"
check_summary "App Support verification" "$APP_SUPPORT_VERIFY_SUMMARY"
check_summary "Migration backup verification" "$BACKUP_VERIFY_SUMMARY"
check_summary "Migration stage verification" "$STAGE_VERIFY_SUMMARY"

if [ "$status" -eq 0 ]; then
  echo ""
  echo "Comparing current live config/database with staged copies:"
  if current_config_hash="$(sha_value "$CURRENT_CONFIG")" && staged_config_hash="$(sha_value "$STAGED_CONFIG")"; then
    if [ "$current_config_hash" = "$staged_config_hash" ]; then
      config_match="PASS - staged config matches current config"
      echo "OK: config.json staged copy matches current config."
    else
      config_match="FAILED - staged config differs from current config"
      mark_error "Staged config.json differs from current config.json. Re-stage before switching."
    fi
  else
    config_match="FAILED - SHA-256 tool unavailable or config hash failed"
    mark_error "Could not compute config hashes."
  fi

  if current_db_hash="$(sha_value "$CURRENT_DB")" && staged_db_hash="$(sha_value "$STAGED_DB")"; then
    if [ "$current_db_hash" = "$staged_db_hash" ]; then
      db_match="PASS - staged database matches current database"
      echo "OK: library.db staged copy matches current database."
    else
      db_match="FAILED - staged database differs from current database"
      mark_error "Staged library.db differs from current library.db. If scans changed the DB after staging, run staging again."
    fi
  else
    db_match="FAILED - SHA-256 tool unavailable or database hash failed"
    mark_error "Could not compute database hashes."
  fi
fi

if [ -f "$STAGED_DB" ]; then
  echo ""
  echo "Staged SQLite integrity check:"
  if command -v sqlite3 >/dev/null 2>&1; then
    db_check="$(sqlite3 "$STAGED_DB" 'PRAGMA integrity_check;' 2>&1 || true)"
    echo "$db_check"
    if [ "$db_check" = "ok" ]; then
      staged_db_integrity="PASS - sqlite integrity_check returned ok"
    else
      staged_db_integrity="FAILED - sqlite integrity_check did not return ok"
      mark_error "Staged database integrity check failed or returned a warning."
    fi
  else
    staged_db_integrity="WARN - sqlite3 command not found, staged database integrity was not checked"
    echo "$staged_db_integrity"
  fi
fi

echo ""
if [ "$status" -eq 0 ]; then
  result="READY - staged data matches current live data"
  echo "PASS: cutover readiness check passed. No live switch was performed."
else
  result="NOT READY - review errors and re-run the safe prep/stage checks"
  echo "NOT READY: cutover readiness check found issues. No live switch was performed."
fi

write_summary "$result"
echo "Summary: $SUMMARY_PATH"
echo "Finished: $(date)"
echo "Log: $LOG_FILE"
echo ""
exit "$status"
