#!/bin/bash
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
BUILD_DIR="$APP_DIR/build"
STAGE_DIR="$BUILD_DIR/Movie Library Test DMG"
DMG_PATH="$BUILD_DIR/Movie Library Test.dmg"
CHECKSUM_PATH="$BUILD_DIR/Movie Library Test.sha256.txt"
MANIFEST_PATH="$BUILD_DIR/Movie Library Test DMG CONTENTS MANIFEST.txt"
SUMMARY_PATH="$BUILD_DIR/Movie Library Test DMG SUMMARY.txt"
INSTALL_NOTES_PATH="$BUILD_DIR/Movie Library Test DMG INSTALL NOTES.txt"
APP_BUNDLE="$APP_DIR/Movie Library.app"
LOG_FILE="$LOG_DIR/movie_library_dmg_build.log"

mkdir -p "$LOG_DIR" "$BUILD_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "=== Movie Library test DMG build ==="
echo "Started: $(date)"
echo "App folder: $APP_DIR"
echo "Build folder: $BUILD_DIR"
echo "Output DMG: $DMG_PATH"
echo ""

if [ "$(uname)" != "Darwin" ]; then
  echo "ERROR: This helper uses macOS hdiutil and can only run on macOS."
  exit 2
fi

if ! command -v hdiutil >/dev/null 2>&1; then
  echo "ERROR: hdiutil was not found. This helper must run on macOS."
  exit 2
fi

if [ ! -d "$APP_BUNDLE" ]; then
  echo "ERROR: Test app bundle missing: $APP_BUNDLE"
  exit 1
fi

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

cat > "$STAGE_DIR/README - Test DMG.txt" <<'README'
Movie Library - Test DMG

This DMG is a packaging and Finder-layout test only.

It is not the final installable Movie Library application yet.
The current test Movie Library.app still expects the Movie Library server files to remain beside it in the app folder during this transition stage.

This DMG intentionally does not include:
- library.db
- config.json
- artwork cache
- logs
- media folders
- Tutor app files
- the shared Caddyfile

Caddy remains separate and shared with the Tutor app.
Remote HTTPS continues to be handled by Caddy.

Use this test DMG only to validate that the packaging helper, app bundle metadata, Applications alias, and Finder behaviour are moving in the right direction.
README

cat > "$STAGE_DIR/INSTALL NOTES.txt" <<'NOTES'
Movie Library - Test DMG install notes

This test image now includes the normal macOS Applications alias so the DMG layout looks closer to a final installer.

Do not treat this as the final installable app yet.
The current test Movie Library.app is still a transition launcher and expects the Movie Library server files to live beside it in the app folder.

Final target design:
- /Applications/Movie Library.app contains the program/control launcher.
- ~/Library/Application Support/Movie Library/ contains changing data such as config, library.db, cache, and logs.
- Prepare Movie Library App Support.command can create that future data-folder skeleton safely before any migration.
- Caddy remains separate for remote HTTPS and continues to protect the Tutor app route.

Safe test sequence:
1. Build this test DMG.
2. Verify this test DMG.
3. Inspect the mounted DMG layout.
4. Do not migrate live data or remove the current server folder until the final app-support migration step exists.
NOTES
cp "$STAGE_DIR/INSTALL NOTES.txt" "$INSTALL_NOTES_PATH"

cp -R "$APP_BUNDLE" "$STAGE_DIR/"
ln -s /Applications "$STAGE_DIR/Applications"

# Include small pointer/manifest files so it is obvious this is not the final DMG.
echo "Generated: $(date)" > "$STAGE_DIR/BUILD INFO.txt"
echo "Source app folder: $APP_DIR" >> "$STAGE_DIR/BUILD INFO.txt"
echo "This is a Finder-layout test DMG, not the final self-contained installer." >> "$STAGE_DIR/BUILD INFO.txt"
echo "Application Support prep helper remains outside this test DMG for now; it is a local transition helper." >> "$STAGE_DIR/BUILD INFO.txt"

{
  echo "Movie Library Test DMG contents manifest"
  echo "Generated: $(date)"
  echo ""
  find "$STAGE_DIR" -maxdepth 3 -print | sed "s#^$STAGE_DIR#.#" | sort
} > "$STAGE_DIR/DMG CONTENTS MANIFEST.txt"
cp "$STAGE_DIR/DMG CONTENTS MANIFEST.txt" "$MANIFEST_PATH"

rm -f "$DMG_PATH" "$CHECKSUM_PATH" "$SUMMARY_PATH"
hdiutil create -volname "Movie Library Test" -srcfolder "$STAGE_DIR" -ov -format UDZO "$DMG_PATH"

if [ ! -f "$DMG_PATH" ]; then
  echo "ERROR: hdiutil finished but DMG was not created."
  exit 1
fi

if command -v shasum >/dev/null 2>&1; then
  (cd "$BUILD_DIR" && shasum -a 256 "$(basename "$DMG_PATH")" > "$(basename "$CHECKSUM_PATH")")
fi

SIZE="$(du -h "$DMG_PATH" | awk '{print $1}')"
echo ""
echo "Created: $DMG_PATH"
echo "Size: $SIZE"
if [ -f "$CHECKSUM_PATH" ]; then
  echo "SHA-256: $CHECKSUM_PATH"
fi
cat > "$SUMMARY_PATH" <<SUMMARY
Movie Library Test DMG build summary
Generated: $(date)
Source app folder: $APP_DIR
Output DMG: $DMG_PATH
Size: $SIZE
Checksum file: $CHECKSUM_PATH
Contents manifest: $MANIFEST_PATH
Install notes: $INSTALL_NOTES_PATH
Finder layout: Movie Library.app + Applications alias + README/notes
Log: $LOG_FILE
SUMMARY

echo "Summary: $SUMMARY_PATH"
echo "Finished: $(date)"
echo "Log: $LOG_FILE"
echo ""

open -R "$DMG_PATH" >/dev/null 2>&1 || true
exit 0
