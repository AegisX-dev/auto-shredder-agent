"""
Auto-Shredder Agent — Desktop Notification Engine

Sends native Linux desktop notifications via `notify-send`.
Handles both individual file alerts and batched group notifications.

Requirements:
    - `notify-send` must be available on the system (ships with libnotify-bin
      on Ubuntu/Debian, installed by default on most desktop distros).

Thread safety:
    All methods are stateless and safe to call from any thread.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Final

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_NAME: Final[str] = "Auto-Shredder"
APP_ICON: Final[str] = "user-trash-symbolic"  # Standard freedesktop icon
URGENCY: Final[str] = "normal"
EXPIRE_MS: Final[int] = 8000  # Notification auto-dismiss after 8 seconds

# Category emoji map for notification display
CATEGORY_EMOJI: Final[dict[str, str]] = {
    "installer": "📦",
    "screenshot": "📸",
    "receipt_invoice": "🧾",
    "temporary": "🗑️",
    "document": "📄",
    "media": "🎬",
    "archive": "📁",
    "unknown": "❓",
}


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------

class DesktopNotifier:
    """
    Sends native desktop notifications via notify-send.

    Usage:
        notifier = DesktopNotifier()
        if notifier.is_available():
            notifier.notify_new_file("report.pdf", "document", "Keep Forever")
            notifier.notify_batch(14, "Downloads")
    """

    def __init__(self) -> None:
        self._available: bool | None = None

    def is_available(self) -> bool:
        """Check if notify-send is installed on the system."""
        if self._available is None:
            self._available = shutil.which("notify-send") is not None
            if not self._available:
                logger.warning(
                    "notify-send not found. Desktop notifications will be silent. "
                    "Install with: sudo apt install libnotify-bin"
                )
        return self._available

    def notify_new_file(
        self,
        filename: str,
        category: str,
        retention_label: str,
    ) -> None:
        """
        Send a notification for a single newly detected file.

        Example notification:
            Title: 🧹 Auto-Shredder
            Body:  📦 report_installer.deb
                   Category: Installer → Trashing in 24 Hours
        """
        emoji = CATEGORY_EMOJI.get(category, "❓")
        body = (
            f"{emoji} {filename}\n"
            f"Category: {category.replace('_', ' ').title()} → {retention_label}"
        )
        self._send(f"🧹 {APP_NAME}", body)

    def notify_batch(self, count: int, directory_name: str) -> None:
        """
        Send a grouped notification for multiple files detected at once.

        Example notification:
            Title: 🧹 Auto-Shredder
            Body:  🔔 14 new files detected in Downloads.
                   Open the dashboard to review.
        """
        body = (
            f"🔔 {count} new files detected in {directory_name}.\n"
            f"Open the dashboard to review."
        )
        self._send(f"🧹 {APP_NAME}", body)

    def notify_trashed(self, filename: str) -> None:
        """Send a notification when a file is moved to trash."""
        body = f"🗑️ {filename} has been moved to System Trash."
        self._send(f"🧹 {APP_NAME}", body)

    def _send(self, title: str, body: str) -> None:
        """Execute the notify-send command."""
        if not self.is_available():
            logger.debug("Notification suppressed (notify-send unavailable): %s", title)
            return

        cmd = [
            "notify-send",
            "--app-name", APP_NAME,
            "--icon", APP_ICON,
            "--urgency", URGENCY,
            "--expire-time", str(EXPIRE_MS),
            title,
            body,
        ]

        try:
            subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=5,
            )
            logger.debug("Notification sent: %s", title)
        except subprocess.TimeoutExpired:
            logger.warning("notify-send timed out")
        except FileNotFoundError:
            logger.error("notify-send binary not found at runtime")
            self._available = False
