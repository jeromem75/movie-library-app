#!/bin/bash
set -u
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/movie_library_permissions.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "=== Movie Library permission repair ==="
echo "Started: $(date)"
echo "App folder: $APP_DIR"
echo ""

fix_exec() {
  local target="$1"
  if [ -e "$target" ]; then
    chmod +x "$target" 2>/dev/null || true
    echo "chmod +x: $target"
  else
    echo "missing: $target"
  fi
}

fix_quarantine() {
  local target="$1"
  if [ -e "$target" ] && command -v xattr >/dev/null 2>&1; then
    xattr -dr com.apple.quarantine "$target" 2>/dev/null || true
    echo "quarantine cleared if present: $target"
  fi
}

fix_exec "$APP_DIR/run_movie_library_server.command"
fix_exec "$APP_DIR/Movie Library.command"
fix_exec "$APP_DIR/Movie Library Control.command"
fix_exec "$APP_DIR/Fix Movie Library Permissions.command"
fix_exec "$APP_DIR/Build Test Movie Library DMG.command"
fix_exec "$APP_DIR/Verify Test Movie Library DMG.command"
fix_exec "$APP_DIR/Prepare Movie Library App Support.command"
fix_exec "$APP_DIR/Verify Movie Library App Support.command"
fix_exec "$APP_DIR/Dry Run Movie Library Migration.command"
fix_exec "$APP_DIR/Backup Movie Library Migration Data.command"
fix_exec "$APP_DIR/Verify Movie Library Migration Backup.command"
fix_exec "$APP_DIR/Stage Movie Library Migration Data.command"
fix_exec "$APP_DIR/Verify Movie Library Migration Stage.command"
fix_exec "$APP_DIR/Check Movie Library Migration Cutover.command"
fix_exec "$APP_DIR/Plan Movie Library Migration Cutover.command"
fix_exec "$APP_DIR/Movie Library.app/Contents/MacOS/MovieLibrary"
fix_exec "$APP_DIR/Movie Library Control.app/Contents/MacOS/MovieLibraryControl"

echo ""
fix_quarantine "$APP_DIR/Movie Library.app"
fix_quarantine "$APP_DIR/Movie Library Control.app"
fix_quarantine "$APP_DIR/Movie Library.command"
fix_quarantine "$APP_DIR/Movie Library Control.command"
fix_quarantine "$APP_DIR/run_movie_library_server.command"
fix_quarantine "$APP_DIR/Fix Movie Library Permissions.command"
fix_quarantine "$APP_DIR/Build Test Movie Library DMG.command"
fix_quarantine "$APP_DIR/Verify Test Movie Library DMG.command"
fix_quarantine "$APP_DIR/Prepare Movie Library App Support.command"
fix_quarantine "$APP_DIR/Verify Movie Library App Support.command"
fix_quarantine "$APP_DIR/Dry Run Movie Library Migration.command"
fix_quarantine "$APP_DIR/Backup Movie Library Migration Data.command"
fix_quarantine "$APP_DIR/Verify Movie Library Migration Backup.command"
fix_quarantine "$APP_DIR/Stage Movie Library Migration Data.command"
fix_quarantine "$APP_DIR/Verify Movie Library Migration Stage.command"
fix_quarantine "$APP_DIR/Check Movie Library Migration Cutover.command"
fix_quarantine "$APP_DIR/Plan Movie Library Migration Cutover.command"

echo ""
echo "Finished: $(date)"
echo "Log: $LOG_FILE"
echo ""
