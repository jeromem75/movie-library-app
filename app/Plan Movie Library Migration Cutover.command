#!/bin/bash
set -u
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
APP_SUPPORT_DIR="$HOME/Library/Application Support/Movie Library"
STAGE_DIR="$APP_SUPPORT_DIR/migration-staging/current"
SUMMARY_DIR="$APP_SUPPORT_DIR/migration-staging"
LOG_FILE="$LOG_DIR/movie_library_migration_cutover_plan.log"
SUMMARY_FILE="$SUMMARY_DIR/Movie Library Migration CUTOVER PLAN SUMMARY.txt"
READINESS_SUMMARY="$SUMMARY_DIR/Movie Library Migration CUTOVER READINESS SUMMARY.txt"
mkdir -p "$LOG_DIR" "$SUMMARY_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

status="OK"
warn() { echo "WARNING: $1"; status="WARN"; }

file_state() {
  local label="$1"
  local path="$2"
  if [ -f "$path" ]; then
    local size=""
    size=$(wc -c < "$path" 2>/dev/null | tr -d ' ') || size="unknown"
    echo "$label: present ($size bytes) - $path"
  else
    echo "$label: missing - $path"
    status="WARN"
  fi
}

{
  echo "Movie Library Migration Cutover Plan"
  echo "Generated: $(date)"
  echo "App folder: $APP_DIR"
  echo "Application Support: $APP_SUPPORT_DIR"
  echo "Stage folder: $STAGE_DIR"
  echo ""
  echo "Purpose"
  echo "- This report plans the later live-data switch only."
  echo "- It does not switch Movie Library to Application Support."
  echo "- It does not copy staged files into the live config/database targets."
  echo "- It does not delete, move, edit, or replace the current database/config."
  echo ""
  echo "Current live files"
  file_state "Current config.json" "$APP_DIR/config.json"
  file_state "Current library.db" "$APP_DIR/library.db"
  echo ""
  echo "Non-live staged files"
  file_state "Staged config.json" "$STAGE_DIR/config.json"
  file_state "Staged library.db" "$STAGE_DIR/library.db"
  file_state "Staged SHA256SUMS.txt" "$STAGE_DIR/SHA256SUMS.txt"
  echo ""
  echo "Readiness input"
  file_state "Cutover readiness summary" "$READINESS_SUMMARY"
  echo ""
  echo "Future live targets for a later explicit switch helper"
  echo "Future config target: $APP_SUPPORT_DIR/config.json"
  echo "Future database target: $APP_SUPPORT_DIR/library.db"
  echo "Future cache target: $APP_SUPPORT_DIR/cache/"
  echo "Future logs target: $APP_SUPPORT_DIR/logs/"
  echo ""
  echo "Future switch sequence - not performed by this helper"
  echo "1. Stop the Movie Library Flask server cleanly."
  echo "2. Confirm the cutover readiness report is fresh and clean."
  echo "3. Copy staged config.json to $APP_SUPPORT_DIR/config.json."
  echo "4. Copy staged library.db to $APP_SUPPORT_DIR/library.db."
  echo "5. Start Movie Library with launchers/server code that read config/database from Application Support."
  echo "6. Verify admin login, viewer login, library counts, Movies, TV Shows, scans, and artwork cache."
  echo "7. Verify remote HTTPS through Caddy without editing the shared Caddyfile."
  echo "8. Keep the old app-folder config/database untouched until the Application Support run is confirmed."
  echo ""
  echo "Guardrails"
  echo "- Do not bundle library.db inside the final Movie Library.app."
  echo "- Do not store changing config/cache/logs inside /Applications/Movie Library.app."
  echo "- Do not touch media folders."
  echo "- Do not touch Tutor app files."
  echo "- Do not overwrite or simplify /Volumes/D/Webserver/Caddyfile."
  echo ""
  echo "Result: $status"
} > "$SUMMARY_FILE"

cat "$SUMMARY_FILE"
echo ""
echo "Summary written to: $SUMMARY_FILE"
echo "Log written to: $LOG_FILE"

if [ "$status" = "OK" ]; then
  exit 0
fi
exit 2
