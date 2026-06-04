#!/bin/bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"
exec /usr/bin/env python3 "$APP_DIR/mac_control_app.py"
