import os
import json
from pathlib import Path

_profile = os.environ.get("SETU_PROFILE", "default")
_base = Path.home() / ".setu" / _profile

CREDENTIALS_PATH = _base / "credentials.json"
CONFIG_PATH = _base / "config.json"
PID_FILE = _base / "daemon.pid"
LOG_FILE = _base / "daemon.log"


def get_coordinator_url() -> str:
    """Return the coordinator base URL, checking config file then env var."""
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            url = data.get("coordinator_url", "").strip()
            if url:
                return url.rstrip("/")
        except (json.JSONDecodeError, OSError):
            pass
    return os.getenv("COORDINATOR_URL", "http://localhost:8000").rstrip("/")
