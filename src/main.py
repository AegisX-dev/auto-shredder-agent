"""
Auto-Shredder Agent — FastAPI Entry Point & API Server.

Wires together all daemon components (classifier, watcher, scheduler, notifier)
and exposes a REST API for the glassmorphic web dashboard.

Run with: uvicorn src.main:app --host 127.0.0.1 --port 5050 --reload
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.classifier import HybridClassifier
from src.database import DatabaseManager
from src.notifier import DesktopNotifier
from src.scheduler import ExpiryScheduler
from src.watcher import DirectoryWatcher

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger: Final = logging.getLogger("auto-shredder")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WEB_DIR: Final[Path] = Path(__file__).parent / "web"
DEFAULT_PORT: Final[int] = 5050

# ---------------------------------------------------------------------------
# Shared daemon state — initialized during lifespan
# ---------------------------------------------------------------------------
_db: DatabaseManager | None = None
_watcher: DirectoryWatcher | None = None
_scheduler: ExpiryScheduler | None = None


def _get_db() -> DatabaseManager:
    """Return the active DatabaseManager or raise if not initialized."""
    if _db is None:
        raise RuntimeError("Database not initialized — server is still starting")
    return _db


def _get_scheduler() -> ExpiryScheduler:
    """Return the active ExpiryScheduler or raise if not initialized."""
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized — server is still starting")
    return _scheduler


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Initialise all daemon components on startup and tear them down on
    shutdown. Uses the modern lifespan context-manager pattern.
    """
    global _db, _watcher, _scheduler  # noqa: PLW0603

    logger.info("🚀 Auto-Shredder Agent starting up …")

    # 1. Database
    _db = DatabaseManager()
    _db.initialize()
    logger.info("✅ Database initialized")

    # 2. Classifier (singleton, lightweight)
    classifier = HybridClassifier()
    logger.info("✅ Classifier loaded")

    # 3. Notifier
    notifier = DesktopNotifier()
    if notifier.is_available():
        logger.info("✅ Desktop notifications available")
    else:
        logger.warning("⚠️  notify-send not found — notifications disabled")

    # 4. Watcher
    _watcher = DirectoryWatcher(
        database=_db,
        classifier=classifier,
        notifier=notifier if _db.get_notifications_enabled() else None,
    )
    _watcher.start()
    logger.info("✅ File watcher started")

    # 5. Scheduler
    _scheduler = ExpiryScheduler(database=_db, notifier=notifier)
    _scheduler.start()
    logger.info("✅ Expiry scheduler started (60 s poll)")

    logger.info("🟢 Auto-Shredder Agent is LIVE on http://127.0.0.1:%d", DEFAULT_PORT)

    yield  # ——— app is running ———

    # Shutdown
    logger.info("🛑 Shutting down …")
    if _watcher is not None:
        _watcher.stop()
        logger.info("  ↳ Watcher stopped")
    if _scheduler is not None:
        _scheduler.stop()
        logger.info("  ↳ Scheduler stopped")
    logger.info("👋 Auto-Shredder Agent stopped")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Auto-Shredder Agent",
    description="Local AI-powered file retention manager",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _serialize_file(tracked_file: object) -> dict[str, object]:
    """Convert a TrackedFile dataclass to a JSON-safe dict."""
    return dataclasses.asdict(tracked_file)


# ---------------------------------------------------------------------------
# API Routes — Files
# ---------------------------------------------------------------------------
@app.get("/api/files")
async def get_active_files() -> JSONResponse:
    """Return all files with status = scheduled or snoozed."""
    db = _get_db()
    files = db.get_active_files()
    return JSONResponse([_serialize_file(f) for f in files])


@app.get("/api/files/history")
async def get_history(limit: int = 50) -> JSONResponse:
    """Return trashed / kept files for the history panel."""
    db = _get_db()
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    files = db.get_history(limit=limit)
    return JSONResponse([_serialize_file(f) for f in files])


@app.get("/api/files/all")
async def get_all_files(limit: int = 100) -> JSONResponse:
    """Return all tracked files regardless of status."""
    db = _get_db()
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    files = db.get_all_files(limit=limit)
    return JSONResponse([_serialize_file(f) for f in files])


@app.get("/api/files/{file_id}")
async def get_file_detail(file_id: int) -> JSONResponse:
    """Return a single tracked file by ID."""
    db = _get_db()
    tracked = db.get_file_by_id(file_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail=f"File ID {file_id} not found")
    return JSONResponse(_serialize_file(tracked))


# ---------------------------------------------------------------------------
# API Routes — File Actions
# ---------------------------------------------------------------------------
@app.post("/api/files/{file_id}/keep")
async def keep_file(file_id: int) -> JSONResponse:
    """Mark a file as Keep Forever — removes its expiry timer."""
    db = _get_db()
    tracked = db.get_file_by_id(file_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail=f"File ID {file_id} not found")

    db.mark_kept(file_id)
    logger.info("🔒 Kept forever: %s", tracked.filename)
    return JSONResponse({"status": "ok", "action": "kept", "file_id": file_id})


@app.post("/api/files/{file_id}/snooze")
async def snooze_file(file_id: int, hours: int = 24) -> JSONResponse:
    """Snooze a file — pushes the expiry timer forward."""
    db = _get_db()
    tracked = db.get_file_by_id(file_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail=f"File ID {file_id} not found")

    if hours < 1 or hours > 720:
        raise HTTPException(status_code=400, detail="hours must be between 1 and 720")

    db.mark_snoozed(file_id, snooze_hours=hours)
    logger.info("⏸️  Snoozed +%dh: %s", hours, tracked.filename)
    return JSONResponse({"status": "ok", "action": "snoozed", "file_id": file_id, "hours": hours})


@app.post("/api/files/{file_id}/trash")
async def trash_file(file_id: int) -> JSONResponse:
    """Immediately trash a file via send2trash."""
    scheduler = _get_scheduler()
    db = _get_db()

    tracked = db.get_file_by_id(file_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail=f"File ID {file_id} not found")

    success = scheduler.trash_now(file_id)
    if not success:
        raise HTTPException(
            status_code=410,
            detail=f"File '{tracked.filename}' could not be trashed — it may already be missing",
        )

    logger.info("🗑️  Trashed immediately: %s", tracked.filename)
    return JSONResponse({"status": "ok", "action": "trashed", "file_id": file_id})


# ---------------------------------------------------------------------------
# API Routes — Stats
# ---------------------------------------------------------------------------
@app.get("/api/stats")
async def get_stats() -> JSONResponse:
    """Return aggregate dashboard statistics."""
    db = _get_db()
    stats = db.get_stats()
    return JSONResponse(stats)


# ---------------------------------------------------------------------------
# API Routes — Settings
# ---------------------------------------------------------------------------
@app.get("/api/settings")
async def get_settings() -> JSONResponse:
    """Return current settings as key-value pairs."""
    db = _get_db()
    return JSONResponse({
        "notifications_enabled": db.get_notifications_enabled(),
        "watched_directories": db.get_watched_directories(),
    })


@app.post("/api/settings")
async def update_settings(request: Request) -> JSONResponse:
    """Update one or more settings. Accepts a JSON body of {key: value} pairs."""
    db = _get_db()
    body = await request.json()

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    allowed_keys: set[str] = {"notifications_enabled", "watched_directories"}
    unknown_keys = set(body.keys()) - allowed_keys
    if unknown_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown setting keys: {', '.join(sorted(unknown_keys))}",
        )

    for key, value in body.items():
        if key == "notifications_enabled":
            if not isinstance(value, bool):
                raise HTTPException(status_code=400, detail="notifications_enabled must be a boolean")
            db.set_setting(key, str(value).lower())
        elif key == "watched_directories":
            if isinstance(value, list):
                db.set_setting(key, ",".join(str(d) for d in value))
            elif isinstance(value, str):
                db.set_setting(key, value)
            else:
                raise HTTPException(status_code=400, detail="watched_directories must be a string or list")

    logger.info("⚙️  Settings updated: %s", list(body.keys()))
    return JSONResponse({"status": "ok", "updated": list(body.keys())})


# ---------------------------------------------------------------------------
# API Routes — Health
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health_check() -> JSONResponse:
    """Lightweight health check for monitoring."""
    watcher_ok = _watcher is not None and _watcher.is_running
    scheduler_ok = _scheduler is not None and _scheduler.is_running
    return JSONResponse({
        "status": "healthy" if (watcher_ok and scheduler_ok) else "degraded",
        "watcher": "running" if watcher_ok else "stopped",
        "scheduler": "running" if scheduler_ok else "stopped",
    })


# ---------------------------------------------------------------------------
# Static files — Web dashboard
# ---------------------------------------------------------------------------
@app.get("/")
async def serve_dashboard() -> FileResponse:
    """Serve the main dashboard HTML page."""
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=503, detail="Dashboard UI not built yet")
    return FileResponse(index_path, media_type="text/html")


# Mount static assets (CSS, JS) — must come AFTER explicit routes
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """Run the server directly via `python -m src.main`."""
    import uvicorn

    host = os.environ.get("SHREDDER_HOST", "127.0.0.1")
    port = int(os.environ.get("SHREDDER_PORT", str(DEFAULT_PORT)))

    uvicorn.run(
        "src.main:app",
        host=host,
        port=port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
