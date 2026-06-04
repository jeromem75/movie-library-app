#!/bin/zsh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.jay.movie-library"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
RUNNER="$APP_DIR/run_movie_library_server.command"
LOG_OUT="$APP_DIR/launchd.out.log"
LOG_ERR="$APP_DIR/launchd.err.log"

mkdir -p "$PLIST_DIR"
chmod +x "$RUNNER"

/usr/bin/python3 - <<PY
import plistlib
from pathlib import Path
label = "$LABEL"
plist_path = Path("$PLIST_PATH")
runner = "$RUNNER"
app_dir = "$APP_DIR"
log_out = "$LOG_OUT"
log_err = "$LOG_ERR"
plist = {
    "Label": label,
    "ProgramArguments": ["/bin/zsh", runner],
    "WorkingDirectory": app_dir,
    "RunAtLoad": True,
    "KeepAlive": True,
    "StandardOutPath": log_out,
    "StandardErrorPath": log_err,
    "EnvironmentVariables": {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    },
}
with plist_path.open("wb") as fh:
    plistlib.dump(plist, fh, sort_keys=False)
PY

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"
launchctl start "$LABEL" >/dev/null 2>&1 || true

echo "Movie Library LaunchAgent installed: $PLIST_PATH"
echo "Label: $LABEL"
echo "Logs:"
echo "  $LOG_OUT"
echo "  $LOG_ERR"
