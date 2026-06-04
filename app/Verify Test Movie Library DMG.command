#!/bin/bash
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
BUILD_DIR="$APP_DIR/build"
DMG_PATH="$BUILD_DIR/Movie Library Test.dmg"
CHECKSUM_PATH="$BUILD_DIR/Movie Library Test.sha256.txt"
LOG_FILE="$LOG_DIR/movie_library_dmg_verify.log"
VERIFY_SUMMARY_PATH="$BUILD_DIR/Movie Library Test DMG VERIFY SUMMARY.txt"
MOUNT_DIR=""

mkdir -p "$LOG_DIR" "$BUILD_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

cleanup() {
  if [ -n "${MOUNT_DIR:-}" ] && [ -d "$MOUNT_DIR" ]; then
    hdiutil detach "$MOUNT_DIR" >/dev/null 2>&1 || true
    rmdir "$MOUNT_DIR" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo ""
echo "=== Movie Library test DMG verification ==="
echo "Started: $(date)"
echo "App folder: $APP_DIR"
echo "Build folder: $BUILD_DIR"
echo "DMG path: $DMG_PATH"
echo ""

if [ "$(uname)" != "Darwin" ]; then
  echo "ERROR: This helper uses macOS hdiutil and can only run on macOS."
  exit 2
fi

if ! command -v hdiutil >/dev/null 2>&1; then
  echo "ERROR: hdiutil was not found. This helper must run on macOS."
  exit 2
fi

if [ ! -f "$DMG_PATH" ]; then
  echo "ERROR: Test DMG was not found. Build it first with Build Test Movie Library DMG.command."
  exit 1
fi

if [ -f "$CHECKSUM_PATH" ] && command -v shasum >/dev/null 2>&1; then
  echo "Checking SHA-256 checksum file..."
  (cd "$BUILD_DIR" && shasum -a 256 -c "$(basename "$CHECKSUM_PATH")") || {
    echo "ERROR: SHA-256 checksum did not validate."
    exit 1
  }
else
  echo "Checksum file not found or shasum unavailable; continuing with mount verification."
fi

echo ""
echo "Image info:"
hdiutil imageinfo "$DMG_PATH" | sed -n '1,20p'

MOUNT_DIR="$(mktemp -d "/tmp/movie-library-test-dmg.XXXXXX")"
echo ""
echo "Mounting read-only at: $MOUNT_DIR"
hdiutil attach "$DMG_PATH" -readonly -nobrowse -mountpoint "$MOUNT_DIR"

echo ""
echo "Mounted contents:"
find "$MOUNT_DIR" -maxdepth 2 -print | sed "s#^$MOUNT_DIR#.#"

missing=0
for expected in \
  "Movie Library.app" \
  "Applications" \
  "README - Test DMG.txt" \
  "INSTALL NOTES.txt" \
  "BUILD INFO.txt" \
  "DMG CONTENTS MANIFEST.txt"
do
  if [ -e "$MOUNT_DIR/$expected" ] || [ -L "$MOUNT_DIR/$expected" ]; then
    echo "OK: $expected"
  else
    echo "MISSING: $expected"
    missing=1
  fi
done

if [ -L "$MOUNT_DIR/Applications" ]; then
  echo "OK: Applications is a Finder-style alias/symlink."
else
  echo "WARNING: Applications entry exists but is not a symlink/alias."
fi

if [ "$missing" -ne 0 ]; then
  echo ""
  echo "ERROR: Test DMG is missing expected lightweight packaging files."
  exit 1
fi

echo ""
cat > "$VERIFY_SUMMARY_PATH" <<SUMMARY
Movie Library Test DMG verification summary
Verified: $(date)
DMG: $DMG_PATH
Checksum file: $CHECKSUM_PATH
Mounted read-only and found expected lightweight files, including the Applications alias/Finder layout notes.
Log: $LOG_FILE
SUMMARY

echo "Verification passed."
echo "Summary: $VERIFY_SUMMARY_PATH"
echo "Finished: $(date)"
echo "Log: $LOG_FILE"
echo ""
exit 0
