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
SUMMARY_PATH="$APP_SUPPORT_DIR/Movie Library App Support PREP SUMMARY.txt"
LOG_FILE="$LOG_DIR/movie_library_app_support_prep.log"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "=== Movie Library Application Support prep ==="
echo "Started: $(date)"
echo "App folder: $APP_DIR"
echo "Application Support folder: $APP_SUPPORT_DIR"
echo ""

if [ "$(uname)" != "Darwin" ]; then
  echo "ERROR: This helper is intended for macOS because it prepares ~/Library/Application Support/Movie Library."
  exit 2
fi

for dir in \
  "$APP_SUPPORT_DIR" \
  "$CACHE_DIR" \
  "$ARTWORK_DIR" \
  "$POSTERS_DIR" \
  "$FANART_DIR" \
  "$THUMBNAILS_DIR" \
  "$LOGS_TARGET" \
  "$EXPORTS_DIR" \
  "$MIGRATION_BACKUPS_DIR"
do
  mkdir -p "$dir"
  echo "OK directory: $dir"
done

cat > "$README_PATH" <<README
Movie Library data folder

This folder is for the future packaged Movie Library.app layout.

The clean final design is:
- /Applications/Movie Library.app contains the program/control launcher.
- ~/Library/Application Support/Movie Library/ contains changing user data.

This folder may contain:
- config.json
- library.db
- cache/
- logs/
- exports/
- migration-backups/

This prep helper does not copy or move your live database, config, artwork cache, media folders, Tutor files, or Caddyfile.
It only creates the safe folder structure for the future migration/first-run setup step.

Caddy remains separate and shared with the Tutor app.
README

cat > "$SUMMARY_PATH" <<SUMMARY
Movie Library Application Support prep summary
Generated: $(date)
App folder: $APP_DIR
Application Support folder: $APP_SUPPORT_DIR

Created/checked:
- $APP_SUPPORT_DIR
- $CACHE_DIR
- $ARTWORK_DIR
- $POSTERS_DIR
- $FANART_DIR
- $THUMBNAILS_DIR
- $LOGS_TARGET
- $EXPORTS_DIR
- $MIGRATION_BACKUPS_DIR

Safety:
- No database copied.
- No config copied.
- No cache copied.
- No media folders touched.
- No Tutor files touched.
- No Caddyfile touched.
SUMMARY

echo ""
echo "README: $README_PATH"
echo "Summary: $SUMMARY_PATH"
echo "Finished: $(date)"
echo "Log: $LOG_FILE"
echo ""

open -R "$APP_SUPPORT_DIR" >/dev/null 2>&1 || true
exit 0
