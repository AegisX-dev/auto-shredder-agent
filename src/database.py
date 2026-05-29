"""
Auto-Shredder Agent — SQLite Database Layer

Provides all persistence for tracked files, settings, and history.
Uses Python's built-in sqlite3 module (zero extra dependencies).

Schema:
    tracked_files — every file detected by the watcher
    settings      — key-value config (notifications, watched dirs, etc.)

Thread safety:
    Each public method acquires its own connection via _get_connection().
    SQLite WAL mode is enabled for concurrent reads during web dashboard polling.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH: Final[str] = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "shredder.db"
)

# File lifecycle statuses
STATUS_SCHEDULED: Final[str] = "scheduled"   # Counting down to trash
STATUS_KEPT: Final[str] = "kept"             # User clicked "Keep Forever"
STATUS_TRASHED: Final[str] = "trashed"       # Moved to system trash
STATUS_SNOOZED: Final[str] = "snoozed"       # Timer extended by user
STATUS_MISSING: Final[str] = "missing"       # File was externally deleted

# Default settings
DEFAULT_SETTINGS: Final[dict[str, str]] = {
    "notifications_enabled": "true",
    "watched_directories": os.path.expanduser("~/Downloads"),
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TrackedFile:
    """Represents a tracked file record from the database."""
    id: int
    filepath: str
    filename: str
    extension: str
    category: str
    confidence: float
    source: str
    retention_hours: int
    retention_label: str
    status: str
    detected_at: str
    expires_at: str | None
    trashed_at: str | None
    file_size_bytes: int
    batch_id: str | None


# ---------------------------------------------------------------------------
# Database Manager
# ---------------------------------------------------------------------------

class DatabaseManager:
    """
    SQLite database manager for Auto-Shredder Agent.

    Usage:
        db = DatabaseManager()
        db.initialize()
        file_id = db.insert_tracked_file(...)
        expired = db.get_expired_files()
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        # Ensure the data directory exists
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        """Create a new connection with optimal settings."""
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # -------------------------------------------------------------------
    # Schema initialization
    # -------------------------------------------------------------------

    def initialize(self) -> None:
        """Create tables and seed default settings. Idempotent."""
        conn = self._get_connection()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tracked_files (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    filepath        TEXT    NOT NULL UNIQUE,
                    filename        TEXT    NOT NULL,
                    extension       TEXT    NOT NULL DEFAULT '',
                    category        TEXT    NOT NULL,
                    confidence      REAL    NOT NULL DEFAULT 1.0,
                    source          TEXT    NOT NULL DEFAULT 'rule',
                    retention_hours INTEGER NOT NULL DEFAULT 168,
                    retention_label TEXT    NOT NULL DEFAULT '7 Days',
                    status          TEXT    NOT NULL DEFAULT 'scheduled',
                    detected_at     TEXT    NOT NULL,
                    expires_at      TEXT,
                    trashed_at      TEXT,
                    file_size_bytes INTEGER NOT NULL DEFAULT 0,
                    batch_id        TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_files_status
                    ON tracked_files(status);
                CREATE INDEX IF NOT EXISTS idx_files_expires
                    ON tracked_files(expires_at);
                CREATE INDEX IF NOT EXISTS idx_files_batch
                    ON tracked_files(batch_id);

                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            conn.commit()

            # Seed defaults (only if not already set)
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )
            conn.commit()

            logger.info("Database initialized at %s", self._db_path)
        finally:
            conn.close()

    # -------------------------------------------------------------------
    # Tracked files — insert
    # -------------------------------------------------------------------

    def insert_tracked_file(
        self,
        filepath: str,
        filename: str,
        extension: str,
        category: str,
        confidence: float,
        source: str,
        retention_hours: int,
        retention_label: str,
        file_size_bytes: int,
        batch_id: str | None = None,
    ) -> int:
        """
        Insert a newly detected file into the database.

        If retention_hours is -1 (Keep Forever), expires_at is set to NULL
        and status is set to 'kept'.

        Returns:
            The row ID of the inserted record.
        """
        now = datetime.now(timezone.utc).isoformat()

        if retention_hours < 0:
            expires_at = None
            status = STATUS_KEPT
        else:
            from datetime import timedelta
            expires_dt = datetime.now(timezone.utc) + timedelta(hours=retention_hours)
            expires_at = expires_dt.isoformat()
            status = STATUS_SCHEDULED

        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                INSERT INTO tracked_files
                    (filepath, filename, extension, category, confidence, source,
                     retention_hours, retention_label, status, detected_at,
                     expires_at, file_size_bytes, batch_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    filepath, filename, extension, category, confidence, source,
                    retention_hours, retention_label, status, now,
                    expires_at, file_size_bytes, batch_id,
                ),
            )
            conn.commit()
            file_id = cursor.lastrowid
            logger.info(
                "Tracked file inserted: id=%d, %s → %s (%s)",
                file_id, filename, category, status,
            )
            return file_id
        except sqlite3.IntegrityError:
            # File already tracked (duplicate filepath)
            logger.warning("File already tracked, skipping: %s", filepath)
            row = conn.execute(
                "SELECT id FROM tracked_files WHERE filepath = ?", (filepath,)
            ).fetchone()
            return row["id"] if row else -1
        finally:
            conn.close()

    # -------------------------------------------------------------------
    # Tracked files — queries
    # -------------------------------------------------------------------

    def get_expired_files(self) -> list[TrackedFile]:
        """Get all files whose countdown has expired and are still scheduled."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """
                SELECT * FROM tracked_files
                WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (STATUS_SCHEDULED, now),
            ).fetchall()
            return [self._row_to_tracked_file(r) for r in rows]
        finally:
            conn.close()

    def get_active_files(self) -> list[TrackedFile]:
        """Get all files that are scheduled or snoozed (visible on dashboard)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """
                SELECT * FROM tracked_files
                WHERE status IN (?, ?)
                ORDER BY detected_at DESC
                """,
                (STATUS_SCHEDULED, STATUS_SNOOZED),
            ).fetchall()
            return [self._row_to_tracked_file(r) for r in rows]
        finally:
            conn.close()

    def get_all_files(self, limit: int = 100) -> list[TrackedFile]:
        """Get all tracked files, most recent first."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM tracked_files ORDER BY detected_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_tracked_file(r) for r in rows]
        finally:
            conn.close()

    def get_history(self, limit: int = 50) -> list[TrackedFile]:
        """Get trashed and kept files for the history panel."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """
                SELECT * FROM tracked_files
                WHERE status IN (?, ?)
                ORDER BY
                    CASE WHEN trashed_at IS NOT NULL THEN trashed_at ELSE detected_at END DESC
                LIMIT ?
                """,
                (STATUS_TRASHED, STATUS_KEPT, limit),
            ).fetchall()
            return [self._row_to_tracked_file(r) for r in rows]
        finally:
            conn.close()

    def get_file_by_id(self, file_id: int) -> TrackedFile | None:
        """Get a single tracked file by its ID."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM tracked_files WHERE id = ?", (file_id,)
            ).fetchone()
            return self._row_to_tracked_file(row) if row else None
        finally:
            conn.close()

    def file_exists(self, filepath: str) -> bool:
        """Check if a file path is already being tracked."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT 1 FROM tracked_files WHERE filepath = ?", (filepath,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    # -------------------------------------------------------------------
    # Tracked files — status updates
    # -------------------------------------------------------------------

    def mark_trashed(self, file_id: int) -> None:
        """Mark a file as trashed (moved to system trash)."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE tracked_files SET status = ?, trashed_at = ? WHERE id = ?",
                (STATUS_TRASHED, now, file_id),
            )
            conn.commit()
            logger.info("File id=%d marked as trashed", file_id)
        finally:
            conn.close()

    def mark_kept(self, file_id: int) -> None:
        """Mark a file as 'Keep Forever' — remove its expiry."""
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE tracked_files SET status = ?, expires_at = NULL WHERE id = ?",
                (STATUS_KEPT, file_id),
            )
            conn.commit()
            logger.info("File id=%d marked as kept forever", file_id)
        finally:
            conn.close()

    def mark_snoozed(self, file_id: int, snooze_hours: int = 24) -> None:
        """Snooze a file — extend its expiry by the given hours."""
        from datetime import timedelta
        new_expiry = datetime.now(timezone.utc) + timedelta(hours=snooze_hours)
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE tracked_files SET status = ?, expires_at = ? WHERE id = ?",
                (STATUS_SNOOZED, new_expiry.isoformat(), file_id),
            )
            conn.commit()
            logger.info("File id=%d snoozed for %dh", file_id, snooze_hours)
        finally:
            conn.close()

    def mark_missing(self, file_id: int) -> None:
        """Mark a file as missing (externally deleted by user)."""
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE tracked_files SET status = ? WHERE id = ?",
                (STATUS_MISSING, file_id),
            )
            conn.commit()
            logger.debug("File id=%d marked as missing", file_id)
        finally:
            conn.close()

    def trash_now(self, file_id: int) -> str | None:
        """
        Get the filepath for immediate trashing and mark as trashed.

        Returns:
            The filepath string, or None if the file doesn't exist.
        """
        tracked = self.get_file_by_id(file_id)
        if tracked is None:
            return None
        self.mark_trashed(file_id)
        return tracked.filepath

    # -------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------

    def get_stats(self) -> dict[str, int]:
        """Get aggregate stats for the dashboard header."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status IN ('scheduled', 'snoozed')) AS active_count,
                    COUNT(*) FILTER (WHERE status = 'trashed') AS trashed_count,
                    COALESCE(SUM(file_size_bytes) FILTER (WHERE status = 'trashed'), 0) AS bytes_reclaimed
                FROM tracked_files
                """
            ).fetchone()
            return {
                "active_count": row["active_count"],
                "trashed_count": row["trashed_count"],
                "bytes_reclaimed": row["bytes_reclaimed"],
            }
        except sqlite3.OperationalError:
            # FILTER syntax requires SQLite 3.30+; fallback for older versions
            active = conn.execute(
                "SELECT COUNT(*) as c FROM tracked_files WHERE status IN ('scheduled','snoozed')"
            ).fetchone()["c"]
            trashed = conn.execute(
                "SELECT COUNT(*) as c FROM tracked_files WHERE status = 'trashed'"
            ).fetchone()["c"]
            reclaimed = conn.execute(
                "SELECT COALESCE(SUM(file_size_bytes), 0) as s FROM tracked_files WHERE status = 'trashed'"
            ).fetchone()["s"]
            return {
                "active_count": active,
                "trashed_count": trashed,
                "bytes_reclaimed": reclaimed,
            }
        finally:
            conn.close()

    # -------------------------------------------------------------------
    # Settings
    # -------------------------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        """Get a setting value by key."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()

    def set_setting(self, key: str, value: str) -> None:
        """Upsert a setting."""
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()

    def get_notifications_enabled(self) -> bool:
        """Check if desktop notifications are enabled."""
        val = self.get_setting("notifications_enabled")
        return val is None or val.lower() == "true"

    def get_watched_directories(self) -> list[str]:
        """Get the list of directories being monitored."""
        val = self.get_setting("watched_directories")
        if not val:
            return [os.path.expanduser("~/Downloads")]
        return [d.strip() for d in val.split(",") if d.strip()]

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _row_to_tracked_file(row: sqlite3.Row) -> TrackedFile:
        """Convert a database row to a TrackedFile dataclass."""
        return TrackedFile(
            id=row["id"],
            filepath=row["filepath"],
            filename=row["filename"],
            extension=row["extension"],
            category=row["category"],
            confidence=row["confidence"],
            source=row["source"],
            retention_hours=row["retention_hours"],
            retention_label=row["retention_label"],
            status=row["status"],
            detected_at=row["detected_at"],
            expires_at=row["expires_at"],
            trashed_at=row["trashed_at"],
            file_size_bytes=row["file_size_bytes"],
            batch_id=row["batch_id"],
        )
