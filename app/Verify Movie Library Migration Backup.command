#!/bin/bash
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
APP_SUPPORT_DIR="$HOME/Library/Application Support/Movie Library"
BACKUP_ROOT="$APP_SUPPORT_DIR/migration-backups"
SUMMARY_PATH="$BACKUP_ROOT/Movie Library Migration BACKUP VERIFY SUMMARY.txt"
LOG_FILE="$LOG_DIR/movie_library_migration_backup_verify.log"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

status=0
LATEST_BACKUP=""

file_size() {
  local path="$1"
  if [ -f "$path" ]; then
    /usr/bin/stat -f%z "$path" 2>/dev/null || /usr/bin/stat -c%s "$path" 2>/dev/null || echo "unknown"
  else
    echo "missing"
  fi
}

write_summary() {
  local result="$1"
  {
    echo "Movie Library migration backup verification summary"
    echo "Generated: $(date)"
    echo "Result: $result"
    echo "App folder: $APP_DIR"
    echo "Backup root: $BACKUP_ROOT"
    echo "Latest backup checked: ${LATEST_BACKUP:-not found}"
    echo ""
    if [ -n "${LATEST_BACKUP:-}" ]; then
      echo "Latest backup files:"
      echo "- config.json: $LATEST_BACKUP/config.json ($(file_size "$LATEST_BACKUP/config.json") bytes)"
      echo "- library.db: $LATEST_BACKUP/library.db ($(file_size "$LATEST_BACKUP/library.db") bytes)"
      echo "- SHA256SUMS.txt: $LATEST_BACKUP/SHA256SUMS.txt ($(file_size "$LATEST_BACKUP/SHA256SUMS.txt") bytes)"
      echo "- BACKUP SUMMARY.txt: $LATEST_BACKUP/BACKUP SUMMARY.txt ($(file_size "$LATEST_BACKUP/BACKUP SUMMARY.txt") bytes)"
    fi
    echo ""
    echo "Safety: this verification helper only reads backup files and checksum metadata."
    echo "It does not copy, move, edit, delete, restore, or switch live data paths."
  } > "$SUMMARY_PATH"
}

echo ""
echo "=== Movie Library migration backup verification ==="
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

if [ ! -d "$BACKUP_ROOT" ]; then
  echo "ERROR: Backup root is missing: $BACKUP_ROOT"
  mkdir -p "$BACKUP_ROOT" 2>/dev/null || true
  write_summary "FAILED - backup root missing"
  exit 1
fi

LATEST_BACKUP="$(find "$BACKUP_ROOT" -maxdepth 1 -type d -name 'backup-*' -print 2>/dev/null | sort | tail -1)"
if [ -z "$LATEST_BACKUP" ]; then
  echo "ERROR: No timestamped backup folder found under: $BACKUP_ROOT"
  write_summary "FAILED - no timestamped backup found"
  exit 1
fi

echo "Latest backup folder: $LATEST_BACKUP"
echo ""

for required in "config.json" "library.db" "SHA256SUMS.txt" "BACKUP SUMMARY.txt"; do
  if [ ! -f "$LATEST_BACKUP/$required" ]; then
    echo "ERROR: Missing required backup file: $LATEST_BACKUP/$required"
    status=1
  else
    echo "OK: $required ($(file_size "$LATEST_BACKUP/$required") bytes)"
  fi
done

echo ""
echo "Checksum verification:"
if [ -f "$LATEST_BACKUP/SHA256SUMS.txt" ]; then
  if command -v shasum >/dev/null 2>&1; then
    if (cd "$LATEST_BACKUP" && shasum -a 256 -c "SHA256SUMS.txt"); then
      echo "OK: checksum verification passed."
    else
      echo "ERROR: checksum verification failed."
      status=1
    fi
  elif command -v sha256sum >/dev/null 2>&1; then
    if (cd "$LATEST_BACKUP" && sha256sum -c "SHA256SUMS.txt"); then
      echo "OK: checksum verification passed."
    else
      echo "ERROR: checksum verification failed."
      status=1
    fi
  else
    echo "WARN: No checksum tool found, could not verify SHA-256 entries."
    status=1
  fi
fi

echo ""
if [ "$status" -eq 0 ]; then
  write_summary "PASS - latest migration backup verified"
  echo "PASS: latest migration backup verified."
else
  write_summary "FAILED - latest migration backup did not verify cleanly"
  echo "FAILED: latest migration backup did not verify cleanly."
fi

echo "Summary: $SUMMARY_PATH"
echo "Finished: $(date)"
echo "Log: $LOG_FILE"
echo ""
exit "$status"
