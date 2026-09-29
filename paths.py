"""Filesystem locations for application code and mutable user data."""

import os
from pathlib import Path

from platformdirs import user_data_path

PROJECT_ROOT = Path(__file__).resolve().parent


def get_data_dir() -> Path:
    """Return an overrideable, per-user application data directory."""
    override = os.environ.get("WA_BOT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(user_data_path("WA_bot", appauthor=False))


DATA_DIR = get_data_dir()
DATABASE_FILE = DATA_DIR / "wa_bot.sqlite3"
PROFILES_DIR = DATA_DIR / "profiles"
MEDIA_DIR = DATA_DIR / "media"
LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "wa_bot.log"
LOCK_FILE = DATA_DIR / "wa_bot.lock"
RUNTIME_FILE = DATA_DIR / "runtime.json"


def ensure_data_dirs() -> None:
    """Create all mutable application directories."""
    for path in (DATA_DIR, PROFILES_DIR, MEDIA_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
