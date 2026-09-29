"""One-time import of data created by the pre-web version of the bot."""

import shutil
import sqlite3
from contextlib import suppress
from pathlib import Path

from database import Database
from domain import extract_invite_links
from paths import PROFILES_DIR, PROJECT_ROOT


def migrate_legacy_data(database: Database) -> None:
    """Copy legacy profiles and links without changing their originals."""
    if database.get_setting("legacy_migration_done", False):
        return

    legacy_profiles = PROJECT_ROOT / "profiles"
    profile_dirs = []
    if legacy_profiles.exists():
        profile_dirs = [
            path
            for path in legacy_profiles.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ]

    legacy_logs_exist = (PROJECT_ROOT / "logs").exists()
    existing_installation = bool(profile_dirs or legacy_logs_exist)
    known_paths = {item["profile_path"] for item in database.list_accounts()}

    for source in profile_dirs:
        destination = _unique_destination(PROFILES_DIR, source.name)
        if not destination.exists():
            shutil.copytree(source, destination, symlinks=True)
        if str(destination) not in known_paths:
            with suppress(sqlite3.IntegrityError):
                database.add_account(source.name, None, destination)

    if existing_installation:
        try:
            from urls_list import INVITE_URLS
        except (ImportError, AttributeError):
            INVITE_URLS = []
        links = extract_invite_links("\n".join(INVITE_URLS))
        database.import_groups(links)

    database.set_setting("legacy_migration_done", True)


def _unique_destination(parent: Path, name: str) -> Path:
    """Reuse an existing migration target, otherwise return a collision-free path."""
    candidate = parent / name
    if not candidate.exists():
        return candidate
    return candidate
