"""
Auto-Shredder Agent — Expiry Scheduler

Background thread that runs every POLL_INTERVAL_SECONDS to:
    1. Query the database for files whose countdown has expired.
    2. Verify the file still exists on disk (handle external deletions).
    3. Move expired files to System Trash via send2trash.
    4. Update the database status and record the trash timestamp.

Safety guarantees:
    - Files are NEVER permanently deleted (no `os.remove`, no `shutil.rmtree`).
    - All deletions go through `send2trash` → System Trash (~/.local/share/Trash).
    - If send2trash fails, the file is left in place and an error is logged.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Final

from send2trash import send2trash

from src.database import DatabaseManager, TrackedFile
from src.notifier import DesktopNotifier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS: Final[int] = 60  # Check for expired files every 60s


# ---------------------------------------------------------------------------
# Expiry Scheduler
# ---------------------------------------------------------------------------

class ExpiryScheduler:
    """
    Background scheduler that checks for expired files and trashes them.

    Usage:
        scheduler = ExpiryScheduler(database)
        scheduler.start()
        ...
        scheduler.stop()
    """

    def __init__(
        self,
        database: DatabaseManager,
        notifier: DesktopNotifier | None = None,
        poll_interval: int = POLL_INTERVAL_SECONDS,
    ) -> None:
        self._db = database
        self._notifier = notifier or DesktopNotifier()
        self._poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False

    def start(self) -> None:
        """Start the scheduler background thread."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="expiry-scheduler",
            daemon=True,
        )
        self._thread.start()
        self._running = True
        logger.info(
            "ExpiryScheduler started (polling every %ds)", self._poll_interval
        )

    def stop(self) -> None:
        """Stop the scheduler and wait for the thread to exit."""
        if not self._running:
            return

        logger.info("Stopping ExpiryScheduler...")
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._poll_interval + 5)
        self._running = False
        logger.info("ExpiryScheduler stopped")

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is currently active."""
        return self._running

    # -------------------------------------------------------------------
    # Main loop
    # -------------------------------------------------------------------

    def _run(self) -> None:
        """Main scheduler loop."""
        logger.info("Scheduler thread started")

        while not self._stop_event.is_set():
            try:
                self._process_expired_files()
            except Exception:
                logger.exception("Error during expiry check cycle")

            # Wait for the next poll interval (or until stop is called)
            self._stop_event.wait(timeout=self._poll_interval)

    def _process_expired_files(self) -> None:
        """Query for expired files and trash them."""
        expired_files = self._db.get_expired_files()

        if not expired_files:
            return

        logger.info("Found %d expired file(s) to process", len(expired_files))
        trashed_count = 0

        for tracked in expired_files:
            success = self._trash_file(tracked)
            if success:
                trashed_count += 1

        if trashed_count > 0:
            logger.info(
                "Expiry cycle complete: %d/%d files trashed",
                trashed_count, len(expired_files),
            )

    # -------------------------------------------------------------------
    # File trashing
    # -------------------------------------------------------------------

    def _trash_file(self, tracked: TrackedFile) -> bool:
        """
        Move a single file to System Trash.

        Returns:
            True if the file was successfully trashed, False otherwise.
        """
        filepath = tracked.filepath

        # Check if file still exists
        if not os.path.exists(filepath):
            logger.warning(
                "File no longer exists (externally deleted): %s", filepath
            )
            self._db.mark_missing(tracked.id)
            return False

        # Attempt to move to trash
        try:
            send2trash(filepath)
            self._db.mark_trashed(tracked.id)
            logger.info("✓ Trashed: %s (id=%d)", tracked.filename, tracked.id)

            # Send notification if enabled
            if self._db.get_notifications_enabled():
                self._notifier.notify_trashed(tracked.filename)

            return True

        except Exception:
            logger.exception(
                "Failed to trash file: %s (id=%d). File left in place.",
                filepath, tracked.id,
            )
            return False

    # -------------------------------------------------------------------
    # Manual trigger (called from API)
    # -------------------------------------------------------------------

    def trash_now(self, file_id: int) -> bool:
        """
        Immediately trash a specific file by ID (triggered from dashboard).

        Returns:
            True if successful, False otherwise.
        """
        tracked = self._db.get_file_by_id(file_id)
        if tracked is None:
            logger.warning("trash_now: file id=%d not found", file_id)
            return False

        return self._trash_file(tracked)
