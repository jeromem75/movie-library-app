#!/bin/zsh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found. Install Python 3 first."
  exit 1
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

"$APP_DIR/.venv/bin/python3" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/python3" -m pip install -r requirements.txt

exec "$APP_DIR/.venv/bin/python3" "$APP_DIR/web_app.py"
