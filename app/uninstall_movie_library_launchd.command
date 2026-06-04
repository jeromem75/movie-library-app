#!/bin/zsh
set -euo pipefail

LABEL="com.jay.movie-library"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl stop "$LABEL" >/dev/null 2>&1 || true
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
rm -f "$PLIST_PATH"

echo "Movie Library LaunchAgent removed: $LABEL"
