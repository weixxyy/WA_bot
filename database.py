"""SQLite persistence for application configuration and run history."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from paths import DATABASE_FILE

DEFAULT_SETTINGS = {
    "message_text": "",
    "message_mode": "caption",
    "schedule_enabled": False,
    "schedule_days": list(range(7)),
    "schedule_times": ["09:00", "18:00"],
    "delay_min": 4.0,
    "delay_max": 8.0,
    "max_attempts": 500,
    "last_scheduler_slot": None,
    "legacy_migration_done": False,
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path=DATABASE_FILE):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    phone TEXT,
                    profile_path TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'needs_login',
                    status_detail TEXT,
                    status_checked_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS groups_list (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invite_url TEXT NOT NULL UNIQUE,
                    name TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_status TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_name TEXT NOT NULL,
                    stored_path TEXT NOT NULL UNIQUE,
                    position INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    planned_attempts INTEGER NOT NULL DEFAULT 0,
                    completed_attempts INTEGER NOT NULL DEFAULT 0,
                    sent_count INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    skipped_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                    group_id INTEGER REFERENCES groups_list(id) ON DELETE SET NULL,
                    account_name TEXT NOT NULL,
                    group_label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_groups_enabled ON groups_list(enabled);
                CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_attempts_run ON attempts(run_id, id);
                """
            )
            for key, value in DEFAULT_SETTINGS.items():
                connection.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, json.dumps(value, ensure_ascii=False)),
                )
            connection.execute(
                """
                UPDATE runs SET status = 'interrupted', finished_at = ?
                WHERE status = 'running'
                """,
                (utc_now(),),
            )
            connection.execute(
                """
                UPDATE attempts
                SET status = 'interrupted',
                    error = 'Приложение было остановлено',
                    finished_at = ?
                WHERE status = 'running'
                """,
                (utc_now(),),
            )
            connection.execute(
                """
                UPDATE accounts
                SET status = 'error',
                    status_detail = 'Предыдущая операция была прервана',
                    status_checked_at = ?
                WHERE status IN ('authorizing', 'checking')
                """,
                (utc_now(),),
            )

    def get_setting(self, key, default=None):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def get_settings(self) -> dict:
        with self.connect() as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        settings = dict(DEFAULT_SETTINGS)
        settings.update({row["key"]: json.loads(row["value"]) for row in rows})
        return settings

    def set_settings(self, values: dict) -> None:
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                [
                    (key, json.dumps(value, ensure_ascii=False))
                    for key, value in values.items()
                ],
            )

    def add_account(self, name: str, phone: str | None, profile_path: Path) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO accounts(name, phone, profile_path, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, phone or None, str(profile_path), utc_now()),
            )
            return cursor.lastrowid

    def list_accounts(self, enabled_only=False) -> list[dict]:
        query = "SELECT * FROM accounts"
        parameters = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name COLLATE NOCASE, id"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    def get_account(self, account_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_account(self, account_id: int, **values) -> None:
        allowed = {
            "name",
            "phone",
            "enabled",
            "status",
            "status_detail",
            "status_checked_at",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        clause = ", ".join(f"{key} = ?" for key in updates)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE accounts SET {clause} WHERE id = ?",  # noqa: S608
                (*updates.values(), account_id),
            )

    def delete_account(self, account_id: int) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def import_groups(self, links: list[str]) -> tuple[int, int]:
        added = 0
        with self.connect() as connection:
            for link in links:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO groups_list(invite_url, created_at)
                    VALUES (?, ?)
                    """,
                    (link, utc_now()),
                )
                added += cursor.rowcount
        return added, len(links) - added

    def list_groups(
        self, *, search="", page=1, page_size=100, enabled_only=False
    ) -> tuple[list[dict], int]:
        conditions = []
        parameters = []
        if search:
            conditions.append("(name LIKE ? OR invite_url LIKE ?)")
            like = f"%{search}%"
            parameters.extend([like, like])
        if enabled_only:
            conditions.append("enabled = 1")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        offset = max(page - 1, 0) * page_size
        with self.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM groups_list{where}", parameters  # noqa: S608
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT * FROM groups_list{where}
                ORDER BY COALESCE(name, invite_url) COLLATE NOCASE, id
                LIMIT ? OFFSET ?
                """,  # noqa: S608
                (*parameters, page_size, offset),
            ).fetchall()
        return [dict(row) for row in rows], total

    def count_enabled_groups(self) -> int:
        with self.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM groups_list WHERE enabled = 1"
            ).fetchone()[0]

    def update_group(self, group_id: int, **values) -> None:
        allowed = {"name", "enabled", "last_status"}
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        clause = ", ".join(f"{key} = ?" for key in updates)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE groups_list SET {clause} WHERE id = ?",  # noqa: S608
                (*updates.values(), group_id),
            )

    def bulk_groups(self, ids: list[int], action: str) -> int:
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as connection:
            if action == "delete":
                cursor = connection.execute(
                    f"DELETE FROM groups_list WHERE id IN ({placeholders})", ids
                )
            elif action in {"enable", "disable"}:
                cursor = connection.execute(
                    f"UPDATE groups_list SET enabled = ? WHERE id IN ({placeholders})",
                    (1 if action == "enable" else 0, *ids),
                )
            else:
                raise ValueError("Неизвестное массовое действие")
        return cursor.rowcount

    def list_images(self) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM images ORDER BY position, id"
                )
            ]

    def add_image(self, original_name: str, stored_path: Path) -> int:
        with self.connect() as connection:
            position = connection.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM images"
            ).fetchone()[0]
            cursor = connection.execute(
                """
                INSERT INTO images(original_name, stored_path, position, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (original_name, str(stored_path), position, utc_now()),
            )
            return cursor.lastrowid

    def get_image(self, image_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM images WHERE id = ?", (image_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_image(self, image_id: int) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM images WHERE id = ?", (image_id,))

    def create_run(self, trigger: str, status: str, planned_attempts=0, reason=None) -> int:
        finished = utc_now() if status == "skipped" else None
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs(
                    trigger, status, started_at, finished_at,
                    planned_attempts, skipped_reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (trigger, status, utc_now(), finished, planned_attempts, reason),
            )
            return cursor.lastrowid

    def finish_run(self, run_id: int, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
                (status, utc_now(), run_id),
            )

    def add_attempt(
        self, run_id: int, account: dict, group: dict, status="running"
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO attempts(
                    run_id, account_id, group_id, account_name,
                    group_label, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    account["id"],
                    group["id"],
                    account["name"],
                    group.get("name") or group["invite_url"],
                    status,
                    utc_now(),
                ),
            )
            return cursor.lastrowid

    def finish_attempt(self, attempt_id: int, status: str, error=None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE attempts SET status = ?, error = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, error, utc_now(), attempt_id),
            )
            sent = 1 if status == "sent" else 0
            failed = 1 if status in {"error", "uncertain"} else 0
            connection.execute(
                """
                UPDATE runs SET
                    completed_attempts = completed_attempts + 1,
                    sent_count = sent_count + ?,
                    error_count = error_count + ?
                WHERE id = (SELECT run_id FROM attempts WHERE id = ?)
                """,
                (sent, failed, attempt_id),
            )

    def recent_runs(self, limit=30) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
                )
            ]

    def get_run(self, run_id: int) -> tuple[dict | None, list[dict]]:
        with self.connect() as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            attempts = connection.execute(
                "SELECT * FROM attempts WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return (dict(run) if run else None, [dict(row) for row in attempts])

    def prune_history(self, days=30) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM runs WHERE started_at < ? AND status != 'running'",
                (cutoff,),
            )
