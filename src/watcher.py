"""
Auto-Shredder Agent — File System Watcher

Monitors directories for new file creation events using watchdog (inotify).
Implements intelligent batch detection: if ≥3 files arrive within a 3-second
sliding window, they are grouped into a single batch notification instead
of firing N individual alerts.

Architecture:
    DirectoryWatcher
        └── _EventHandler (watchdog FileSystemEventHandler)
                ├── detects file creation
                ├── skips hidden / incomplete / already-tracked files
                ├── classifies via HybridClassifier
                ├── inserts into database
                └── buffers events for batch detection
        └── _BatchProcessor (background thread)
                ├── flushes the event buffer every 3 seconds
                └── decides: individual notifications or single batch alert

Thread safety:
    The event buffer uses threading.Lock for safe access between the
    watchdog observer thread and the batch processor thread.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from src.classifier import HybridClassifier, ClassificationResult
from src.database import DatabaseManager
from src.notifier import DesktopNotifier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_WINDOW_SECONDS: Final[float] = 3.0
BATCH_THRESHOLD: Final[int] = 3  # ≥3 files in window → batch notification
STABILIZATION_DELAY: Final[float] = 0.5  # Wait for file writes to finish


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PendingEvent:
    """A file event waiting in the batch buffer."""
    filepath: str
    filename: str
    classification: ClassificationResult
    file_size: int
    timestamp: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Watchdog Event Handler
# ---------------------------------------------------------------------------

class _EventHandler(FileSystemEventHandler):
    """
    Handles file creation events from watchdog.
    Classifies files and pushes them into a shared buffer for batch processing.
    """

    def __init__(
        self,
        classifier: HybridClassifier,
        database: DatabaseManager,
        buffer: list[PendingEvent],
        buffer_lock: threading.Lock,
    ) -> None:
        super().__init__()
        self._classifier = classifier
        self._db = database
        self._buffer = buffer
        self._lock = buffer_lock

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        """Called when a file is created in a watched directory."""
        if event.is_directory:
            return

        filepath = event.src_path
        filename = os.path.basename(filepath)

        # Skip hidden files and incomplete downloads
        if self._classifier.should_skip(filename):
            logger.debug("Skipping file: %s", filename)
            return

        # Brief delay to let the file finish writing
        time.sleep(STABILIZATION_DELAY)

        # Skip if file disappeared during stabilization
        if not os.path.exists(filepath):
            logger.debug("File vanished before processing: %s", filename)
            return

        # Skip if already tracked
        if self._db.file_exists(filepath):
            logger.debug("File already tracked: %s", filename)
            return

        # Classify
        result = self._classifier.classify(filename)

        # Get file size
        try:
            file_size = os.path.getsize(filepath)
        except OSError:
            file_size = 0

        # Push to batch buffer
        pending = PendingEvent(
            filepath=filepath,
            filename=filename,
            classification=result,
            file_size=file_size,
        )

        with self._lock:
            self._buffer.append(pending)

        logger.info(
            "File detected: %s → %s (conf=%.2f, %s)",
            filename, result.category, result.confidence, result.source,
        )


# ---------------------------------------------------------------------------
# Batch Processor
# ---------------------------------------------------------------------------

class _BatchProcessor:
    """
    Background thread that flushes the event buffer every BATCH_WINDOW_SECONDS.
    Decides whether to send individual or grouped notifications.
    """

    def __init__(
        self,
        database: DatabaseManager,
        notifier: DesktopNotifier,
        buffer: list[PendingEvent],
        buffer_lock: threading.Lock,
    ) -> None:
        self._db = database
        self._notifier = notifier
        self._buffer = buffer
        self._lock = buffer_lock
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the batch processor thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="batch-processor",
            daemon=True,
        )
        self._thread.start()
        logger.info("Batch processor started (window=%.1fs, threshold=%d)",
                     BATCH_WINDOW_SECONDS, BATCH_THRESHOLD)

    def stop(self) -> None:
        """Stop the batch processor and flush remaining events."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        # Final flush
        self._flush()
        logger.info("Batch processor stopped")

    def _run(self) -> None:
        """Main loop: sleep for the batch window, then flush."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=BATCH_WINDOW_SECONDS)
            self._flush()

    def _flush(self) -> None:
        """Process all buffered events."""
        with self._lock:
            if not self._buffer:
                return
            events = list(self._buffer)
            self._buffer.clear()

        if not events:
            return

        # Check notification preference
        notifications_enabled = self._db.get_notifications_enabled()

        # Assign a batch ID if this is a batch
        batch_id: str | None = None
        if len(events) >= BATCH_THRESHOLD:
            batch_id = uuid.uuid4().hex[:12]

        # Insert all events into the database
        for evt in events:
            self._db.insert_tracked_file(
                filepath=evt.filepath,
                filename=evt.filename,
                extension=evt.classification.extension,
                category=evt.classification.category,
                confidence=evt.classification.confidence,
                source=evt.classification.source,
                retention_hours=evt.classification.retention_hours,
                retention_label=evt.classification.retention_label,
                file_size_bytes=evt.file_size,
                batch_id=batch_id,
            )

        # Send notifications
        if notifications_enabled:
            if batch_id:
                # Grouped notification
                directory_name = os.path.basename(
                    os.path.dirname(events[0].filepath)
                )
                self._notifier.notify_batch(len(events), directory_name)
                logger.info(
                    "Batch notification sent: %d files in %s",
                    len(events), directory_name,
                )
            else:
                # Individual notifications
                for evt in events:
                    self._notifier.notify_new_file(
                        evt.filename,
                        evt.classification.category,
                        evt.classification.retention_label,
                    )


# ---------------------------------------------------------------------------
# Public API: DirectoryWatcher
# ---------------------------------------------------------------------------

class DirectoryWatcher:
    """
    Main watcher interface. Monitors directories and processes file events.

    Usage:
        watcher = DirectoryWatcher(database, classifier)
        watcher.start()   # begins monitoring in background threads
        ...
        watcher.stop()    # clean shutdown
    """

    def __init__(
        self,
        database: DatabaseManager,
        classifier: HybridClassifier | None = None,
        notifier: DesktopNotifier | None = None,
    ) -> None:
        self._db = database
        self._classifier = classifier or HybridClassifier()
        self._notifier = notifier or DesktopNotifier()

        # Shared event buffer
        self._buffer: list[PendingEvent] = []
        self._buffer_lock = threading.Lock()

        # Watchdog observer
        self._observer = Observer()
        self._event_handler = _EventHandler(
            classifier=self._classifier,
            database=self._db,
            buffer=self._buffer,
            buffer_lock=self._buffer_lock,
        )

        # Batch processor
        self._batch_processor = _BatchProcessor(
            database=self._db,
            notifier=self._notifier,
            buffer=self._buffer,
            buffer_lock=self._buffer_lock,
        )

        self._running = False

    def start(self) -> None:
        """Start watching all configured directories."""
        if self._running:
            logger.warning("Watcher already running")
            return

        watched_dirs = self._db.get_watched_directories()

        for directory in watched_dirs:
            expanded = os.path.expanduser(directory)
            if not os.path.isdir(expanded):
                logger.warning("Watch directory does not exist, skipping: %s", expanded)
                continue

            self._observer.schedule(
                self._event_handler,
                expanded,
                recursive=False,  # Only top-level files
            )
            logger.info("Watching directory: %s", expanded)

        self._observer.start()
        self._batch_processor.start()
        self._running = True
        logger.info("DirectoryWatcher started (%d directories)", len(watched_dirs))

    def stop(self) -> None:
        """Stop watching and clean up threads."""
        if not self._running:
            return

        logger.info("Stopping DirectoryWatcher...")
        self._observer.stop()
        self._observer.join(timeout=5.0)
        self._batch_processor.stop()
        self._running = False
        logger.info("DirectoryWatcher stopped")

    @property
    def is_running(self) -> bool:
        """Check if the watcher is currently active."""
        return self._running
