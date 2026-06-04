import json
from pathlib import Path
cfg_path = Path(__file__).resolve().parent / "config.json"
cfg = {"folder":"", "web":{"username":"admin","password":"change-me-now","port":8765}}
if cfg_path.exists():
    try:
        cfg.update(json.loads(cfg_path.read_text(encoding="utf-8")))
    except Exception:
        pass
username = input("Username [admin]: ").strip() or cfg["web"].get("username", "admin")
password = input("New password: ").strip()
port = input(f"Port [{cfg['web'].get('port', 8765)}]: ").strip()
cfg["web"]["username"] = username
if password:
    cfg["web"]["password"] = password
if port.isdigit():
    cfg["web"]["port"] = int(port)
cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
print("Saved.")
