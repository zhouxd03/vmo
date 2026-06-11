"""Application path resolution (portable mode).

All user data lives next to the executable (when frozen) or under the project
root (in dev). Static frontend assets are bundled into the PyInstaller temp dir,
placed next to the Nuitka executable, or read from frontend/dist in dev.
"""

import sys
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) or globals().get("__compiled__"))


if _is_frozen():
    APP_DIR = Path(sys.executable).parent
    BUNDLED_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
    STATIC_DIR = BUNDLED_DIR / "static"
else:
    APP_DIR = Path(__file__).resolve().parents[2]  # batch-studio/
    STATIC_DIR = APP_DIR / "frontend" / "dist"
    BUNDLED_DIR = APP_DIR / "backend"

# Persistent user data (portable, next to exe / project root)
DATA_DIR = APP_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
OUTPUT_DIR = APP_DIR / "output"

CREDENTIALS_FILE = DATA_DIR / "credentials.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
TEMPLATES_FILE = DATA_DIR / "templates.json"
AUTH_SESSION_FILE = DATA_DIR / "auth_session.json"


def ensure_dirs() -> None:
    for d in (DATA_DIR, PROJECTS_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)
