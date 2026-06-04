#!/bin/bash
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
APP_SUPPORT_DIR="$HOME/Library/Application Support/Movie Library"
CACHE_DIR="$APP_SUPPORT_DIR/cache"
ARTWORK_DIR="$CACHE_DIR/artwork"
POSTERS_DIR="$CACHE_DIR/posters"
FANART_DIR="$CACHE_DIR/fanart"
THUMBNAILS_DIR="$CACHE_DIR/thumbnails"
LOGS_TARGET="$APP_SUPPORT_DIR/logs"
EXPORTS_DIR="$APP_SUPPORT_DIR/exports"
MIGRATION_BACKUPS_DIR="$APP_SUPPORT_DIR/migration-backups"
README_PATH="$APP_SUPPORT_DIR/README - Movie Library Data Folder.txt"
PREP_SUMMARY_PATH="$APP_SUPPORT_DIR/Movie Library App Support PREP SUMMARY.txt"
VERIFY_SUMMARY_PATH="$APP_SUPPORT_DIR/Movie Library App Support VERIFY SUMMARY.txt"
LOG_FILE="$LOG_DIR/movie_library_app_support_verify.log"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

status=0
missing=()

check_dir() {
  local label="$1"
  local path="$2"
  if [ -d "$path" ]; then
    echo "OK directory: $label -> $path"
  else
    echo "MISSING directory: $label -> $path"
    missing+=("directory: $label -> $path")
    status=1
  fi
}

check_file() {
  local label="$1"
  local path="$2"
  if [ -f "$path" ]; then
    echo "OK file: $label -> $path"
  else
    echo "MISSING file: $label -> $path"
    missing+=("file: $label -> $path")
    status=1
  fi
}

echo ""
echo "=== Movie Library Application Support verification ==="
echo "Started: $(date)"
echo "App folder: $APP_DIR"
echo "Application Support folder: $APP_SUPPORT_DIR"
echo ""

if [ "$(uname)" != "Darwin" ]; then
  echo "ERROR: This helper is intended for macOS because it verifies ~/Library/Application Support/Movie Library."
  exit 2
fi

check_dir "Application Support root" "$APP_SUPPORT_DIR"
check_dir "cache" "$CACHE_DIR"
check_dir "cache/artwork" "$ARTWORK_DIR"
check_dir "cache/posters" "$POSTERS_DIR"
check_dir "cache/fanart" "$FANART_DIR"
check_dir "cache/thumbnails" "$THUMBNAILS_DIR"
check_dir "logs" "$LOGS_TARGET"
check_dir "exports" "$EXPORTS_DIR"
check_dir "migration-backups" "$MIGRATION_BACKUPS_DIR"
check_file "data-folder README" "$README_PATH"

echo ""
echo "Optional migration-time files, not required yet:"
for optional in \
  "$PREP_SUMMARY_PATH" \
  "$APP_SUPPORT_DIR/config.json" \
  "$APP_SUPPORT_DIR/library.db"
do
  if [ -e "$optional" ]; then
    echo "FOUND optional: $optional"
  else
    echo "Not present yet: $optional"
  fi
done

{
  echo "Movie Library Application Support verification summary"
  echo "Generated: $(date)"
  echo "App folder: $APP_DIR"
  echo "Application Support folder: $APP_SUPPORT_DIR"
  echo ""
  if [ "$status" -eq 0 ]; then
    echo "Result: PASS"
    echo "Required folder skeleton is present. This is ready for later migration planning."
  else
    echo "Result: NEEDS ATTENTION"
    echo "Run Prepare Movie Library App Support.command, then run this verifier again."
    echo ""
    echo "Missing required items:"
    for item in "${missing[@]}"; do
      echo "- $item"
    done
  fi
  echo ""
  echo "Safety:"
  echo "- No database copied."
  echo "- No config copied."
  echo "- No cache copied."
  echo "- No media folders touched."
  echo "- No Tutor files touched."
  echo "- No Caddyfile touched."
} > "$VERIFY_SUMMARY_PATH"

echo ""
echo "Verify summary: $VERIFY_SUMMARY_PATH"
echo "Finished: $(date)"
echo "Log: $LOG_FILE"
echo ""

exit "$status"
