"""
Auto-Shredder Agent — Hybrid File Classifier

Architecture:
    Layer 1 (Rules): Deterministic extension and filename pattern matching.
                     Handles ~80% of common files with 100% accuracy.
    Layer 2 (ML):    Pure-Python TF-IDF weighted Naive Bayes classifier.
                     Handles ambiguous filenames that rules can't resolve.
                     Zero external dependencies — all math uses Python stdlib.

Categories:
    installer, screenshot, receipt_invoice, temporary,
    document, media, archive, unknown

Performance targets:
    - Classification latency: < 50ms (typically < 1ms)
    - Memory overhead: < 1MB for model weights
    - Zero network calls, zero GPU, zero sklearn/numpy
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_DATA_PATH: Final[str] = os.path.join(os.path.dirname(__file__), "model_data.json")

# Files actively being written — skip these entirely
INCOMPLETE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".crdownload",  # Chrome
    ".part",        # Firefox / wget
    ".partial",     # IE / Edge legacy
    ".download",    # Safari
    ".tmp",         # Generic partial (handled separately when not in-progress)
    ".opdownload",  # Opera
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Immutable result of a file classification."""
    category: str
    confidence: float
    source: str  # "rule" or "ml"
    retention_hours: int  # -1 means Keep Forever
    retention_label: str
    filename: str
    extension: str


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Retention policy for a category."""
    hours: int
    label: str


# ---------------------------------------------------------------------------
# Layer 1: Deterministic Rule Engine
# ---------------------------------------------------------------------------

class RuleEngine:
    """
    Fast, deterministic classification based on file extension and
    filename patterns. Returns a category string or None if no rule matched.
    """

    # Extension → category mapping (lowercase, with leading dot)
    EXTENSION_MAP: Final[dict[str, str]] = {
        # Installers
        ".deb": "installer", ".rpm": "installer", ".snap": "installer",
        ".flatpakref": "installer", ".appimage": "installer",
        ".msi": "installer", ".exe": "installer", ".dmg": "installer",
        ".pkg": "installer", ".run": "installer", ".bin": "installer",
        ".sh": "installer",

        # Disk images (also installer-adjacent)
        ".iso": "installer", ".img": "installer",

        # Temporary / ephemeral
        ".tmp": "temporary", ".log": "temporary", ".bak": "temporary",
        ".swp": "temporary", ".swo": "temporary", ".old": "temporary",
        ".cache": "temporary", ".pid": "temporary",

        # Documents
        ".pdf": "document", ".docx": "document", ".doc": "document",
        ".odt": "document", ".rtf": "document", ".txt": "document",
        ".md": "document", ".tex": "document", ".epub": "document",
        ".pptx": "document", ".ppt": "document", ".odp": "document",
        ".xlsx": "document", ".xls": "document", ".ods": "document",
        ".csv": "document",

        # Media — Video
        ".mp4": "media", ".mkv": "media", ".avi": "media",
        ".mov": "media", ".wmv": "media", ".flv": "media",
        ".webm": "media", ".m4v": "media", ".3gp": "media",

        # Media — Audio
        ".mp3": "media", ".flac": "media", ".wav": "media",
        ".aac": "media", ".ogg": "media", ".wma": "media",
        ".m4a": "media", ".opus": "media",

        # Media — Image
        ".jpg": "media", ".jpeg": "media", ".png": "media",
        ".gif": "media", ".bmp": "media", ".svg": "media",
        ".webp": "media", ".tiff": "media", ".ico": "media",
        ".heic": "media", ".heif": "media", ".avif": "media",
        ".raw": "media", ".cr2": "media", ".nef": "media",

        # Archives
        ".zip": "archive", ".tar": "archive", ".gz": "archive",
        ".bz2": "archive", ".xz": "archive", ".7z": "archive",
        ".rar": "archive", ".zst": "archive",
        ".tar.gz": "archive", ".tar.bz2": "archive",
        ".tar.xz": "archive", ".tar.zst": "archive",
        ".tgz": "archive",

        # Torrents (treat as archive)
        ".torrent": "archive",
    }

    # Filename patterns (compiled once, checked in order)
    FILENAME_PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
        # Screenshots: Ubuntu / GNOME / KDE / Flameshot / Shutter / generic
        (re.compile(r"^screenshot", re.IGNORECASE), "screenshot"),
        (re.compile(r"^screen[\s_-]?shot", re.IGNORECASE), "screenshot"),
        (re.compile(r"^capture[\s_-]?\d", re.IGNORECASE), "screenshot"),
        (re.compile(r"^snip[\s_-]?\d", re.IGNORECASE), "screenshot"),
        (re.compile(r"^\d{4}-\d{2}-\d{2}[\s_-]+\d{2}[.:]\d{2}", re.IGNORECASE), "screenshot"),

        # Receipts / invoices
        (re.compile(r"receipt", re.IGNORECASE), "receipt_invoice"),
        (re.compile(r"invoice", re.IGNORECASE), "receipt_invoice"),
        (re.compile(r"payment[\s_-]?confirm", re.IGNORECASE), "receipt_invoice"),
        (re.compile(r"order[\s_-]?confirm", re.IGNORECASE), "receipt_invoice"),
        (re.compile(r"payslip", re.IGNORECASE), "receipt_invoice"),
        (re.compile(r"tax[\s_-]?return", re.IGNORECASE), "receipt_invoice"),
        (re.compile(r"salary[\s_-]?slip", re.IGNORECASE), "receipt_invoice"),
        (re.compile(r"expense[\s_-]?report", re.IGNORECASE), "receipt_invoice"),
        (re.compile(r"billing[\s_-]?statement", re.IGNORECASE), "receipt_invoice"),
    ]

    @staticmethod
    def classify(filename: str) -> str | None:
        """
        Attempt to classify a file by filename patterns first, then extension.

        Priority order:
            1. Filename patterns (screenshots, receipts, invoices) — these
               carry stronger semantic signal than a generic extension.
            2. Compound extensions (.tar.gz, .tar.bz2, etc.)
            3. Simple extensions (.pdf, .png, .deb, etc.)

        Args:
            filename: The basename of the file (e.g., "report_final.pdf").

        Returns:
            Category string if a rule matched, None otherwise.
        """
        name_lower = filename.lower()
        stem = Path(filename).stem  # filename without extension

        # --- Filename pattern matching (highest priority) ---
        # Semantic patterns like "invoice", "screenshot" override generic
        # extension classification (e.g., .pdf → document).
        for pattern, category in RuleEngine.FILENAME_PATTERNS:
            if pattern.search(stem):
                return category

        # --- Compound extension matching ---
        for compound_ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"):
            if name_lower.endswith(compound_ext):
                return RuleEngine.EXTENSION_MAP[compound_ext]

        # --- Simple extension matching (fallback) ---
        _, ext = os.path.splitext(name_lower)
        if ext in RuleEngine.EXTENSION_MAP:
            return RuleEngine.EXTENSION_MAP[ext]

        return None


# ---------------------------------------------------------------------------
# Layer 2: Pure-Python TF-IDF + Naive Bayes Classifier
# ---------------------------------------------------------------------------

class MicroMLClassifier:
    """
    Lightweight Naive Bayes text classifier operating on filename tokens.

    Math:
        P(class | tokens) ∝ P(class) × ∏ P(token | class)

        In log-space:
        log P(class | tokens) = log P(class) + Σ log P(token | class)

        TF-IDF weighting is applied to token contributions:
        weighted_log_likelihood = idf(token) × log P(token | class)

    Unseen tokens use Laplace smoothing via `smoothing_log_likelihood`.
    """

    def __init__(self, model_path: str = MODEL_DATA_PATH) -> None:
        self._vocabulary: dict[str, int] = {}
        self._idf_weights: dict[str, float] = {}
        self._class_log_priors: dict[str, float] = {}
        self._feature_log_likelihoods: dict[str, dict[str, float]] = {}
        self._smoothing_ll: float = -4.60  # log(1/100) — heavy penalty for unseen tokens
        self._retention_policies: dict[str, RetentionPolicy] = {}
        self._loaded: bool = False
        self._model_path = model_path

    def load(self) -> None:
        """Load pre-trained model weights from JSON. Idempotent."""
        if self._loaded:
            return

        logger.info("Loading ML model weights from %s", self._model_path)
        start = time.monotonic()

        with open(self._model_path, "r", encoding="utf-8") as f:
            data: dict = json.load(f)

        self._vocabulary = data["vocabulary"]
        self._idf_weights = data["idf_weights"]
        self._class_log_priors = data["class_log_priors"]
        self._feature_log_likelihoods = data["feature_log_likelihoods"]
        self._smoothing_ll = data.get("smoothing_log_likelihood", -4.60)

        # Parse retention policies
        for category, policy_data in data["retention_policies"].items():
            self._retention_policies[category] = RetentionPolicy(
                hours=policy_data["hours"],
                label=policy_data["label"],
            )

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "ML model loaded: %d vocab terms, %d classes in %.1fms",
            len(self._vocabulary),
            len(self._class_log_priors),
            elapsed_ms,
        )
        self._loaded = True

    def _tokenize(self, filename: str) -> list[str]:
        """
        Extract classification tokens from a filename.

        Strategy:
            1. Strip extension
            2. Lowercase
            3. Split on non-alphanumeric characters (underscores, dashes, dots, spaces)
            4. Filter out pure numeric tokens and single-char tokens
            5. Return only tokens present in our vocabulary
        """
        stem = Path(filename).stem.lower()

        # Split on any non-alphanumeric boundary
        raw_tokens = re.split(r"[^a-z0-9]+", stem)

        # Also try to split camelCase: "myInvoiceFile" → ["my", "invoice", "file"]
        expanded: list[str] = []
        for token in raw_tokens:
            # Split camelCase
            camel_parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", token).lower().split()
            expanded.extend(camel_parts)

        # Filter: keep only known vocabulary tokens, skip numerics and short noise
        return [
            t for t in expanded
            if len(t) > 1 and not t.isdigit() and t in self._vocabulary
        ]

    def classify(self, filename: str) -> tuple[str, float]:
        """
        Classify a filename using TF-IDF weighted Naive Bayes.

        Args:
            filename: The basename of the file.

        Returns:
            Tuple of (predicted_category, confidence_score).
            Confidence is a probability in [0.0, 1.0] derived from
            the softmax of log-posterior scores.
        """
        if not self._loaded:
            self.load()

        tokens = self._tokenize(filename)

        if not tokens:
            return "unknown", 0.0

        # Compute log-posterior for each class
        scores: dict[str, float] = {}
        for cls, log_prior in self._class_log_priors.items():
            score = log_prior
            cls_likelihoods = self._feature_log_likelihoods.get(cls, {})

            for token in tokens:
                log_ll = cls_likelihoods.get(token, self._smoothing_ll)
                idf = self._idf_weights.get(token, 1.0)
                score += idf * log_ll

            scores[cls] = score

        # Find the winning class
        best_class = max(scores, key=lambda c: scores[c])
        best_score = scores[best_class]

        # Convert log-posteriors to probabilities via log-sum-exp (softmax)
        # Shift scores for numerical stability
        max_score = best_score
        exp_sum = sum(math.exp(s - max_score) for s in scores.values())
        confidence = 1.0 / exp_sum  # exp(best - best) / exp_sum = 1 / exp_sum

        return best_class, round(confidence, 4)

    def get_retention_policy(self, category: str) -> RetentionPolicy:
        """Get the retention policy for a category."""
        return self._retention_policies.get(
            category,
            RetentionPolicy(hours=168, label="7 Days"),  # Safe default
        )


# ---------------------------------------------------------------------------
# Public API: HybridClassifier
# ---------------------------------------------------------------------------

class HybridClassifier:
    """
    Main classification interface combining the rule engine and ML classifier.

    Usage:
        classifier = HybridClassifier()
        result = classifier.classify("ubuntu-24.04-desktop-amd64.iso")
        print(result.category)        # "installer"
        print(result.confidence)      # 1.0
        print(result.source)          # "rule"
        print(result.retention_label) # "24 Hours"
    """

    def __init__(self, model_path: str = MODEL_DATA_PATH) -> None:
        self._ml = MicroMLClassifier(model_path=model_path)
        self._ml.load()
        logger.info("HybridClassifier initialized (rules + ML engine ready)")

    @staticmethod
    def is_incomplete_download(filename: str) -> bool:
        """Check if a file is still being downloaded / written."""
        name_lower = filename.lower()
        return any(name_lower.endswith(ext) for ext in INCOMPLETE_EXTENSIONS)

    @staticmethod
    def is_hidden(filename: str) -> bool:
        """Check if a file is a hidden dotfile."""
        return filename.startswith(".")

    def should_skip(self, filename: str) -> bool:
        """Check if a file should be entirely ignored by the watcher."""
        return self.is_hidden(filename) or self.is_incomplete_download(filename)

    def classify(self, filename: str) -> ClassificationResult:
        """
        Classify a file using the hybrid engine.

        Priority:
            1. Rule engine (extension + filename patterns) → confidence 1.0
            2. ML engine (TF-IDF Naive Bayes) → confidence varies

        Args:
            filename: The basename of the file (not the full path).

        Returns:
            ClassificationResult with category, confidence, source, and
            retention policy.
        """
        start = time.monotonic()
        basename = os.path.basename(filename)
        _, ext = os.path.splitext(basename.lower())

        # Layer 1: Rules
        rule_result = RuleEngine.classify(basename)
        if rule_result is not None:
            policy = self._ml.get_retention_policy(rule_result)
            elapsed_us = (time.monotonic() - start) * 1_000_000
            logger.debug(
                "Rule classification: %s → %s (%.0fμs)",
                basename, rule_result, elapsed_us,
            )
            return ClassificationResult(
                category=rule_result,
                confidence=1.0,
                source="rule",
                retention_hours=policy.hours,
                retention_label=policy.label,
                filename=basename,
                extension=ext,
            )

        # Layer 2: ML
        ml_category, ml_confidence = self._ml.classify(basename)
        policy = self._ml.get_retention_policy(ml_category)
        elapsed_us = (time.monotonic() - start) * 1_000_000
        logger.debug(
            "ML classification: %s → %s (conf=%.2f, %.0fμs)",
            basename, ml_category, ml_confidence, elapsed_us,
        )

        return ClassificationResult(
            category=ml_category,
            confidence=ml_confidence,
            source="ml",
            retention_hours=policy.hours,
            retention_label=policy.label,
            filename=basename,
            extension=ext,
        )


# ---------------------------------------------------------------------------
# Module-level convenience (lazy singleton)
# ---------------------------------------------------------------------------

_classifier_instance: HybridClassifier | None = None


def get_classifier() -> HybridClassifier:
    """Get or create the global HybridClassifier singleton."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = HybridClassifier()
    return _classifier_instance


def classify_file(filename: str) -> ClassificationResult:
    """Convenience function: classify a file using the global classifier."""
    return get_classifier().classify(filename)
