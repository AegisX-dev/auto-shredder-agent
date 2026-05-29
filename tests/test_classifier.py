"""
Unit tests for the Auto-Shredder Hybrid Classifier.

Tests cover:
    1. Rule engine — extension-based classification
    2. Rule engine — filename pattern matching (screenshots, receipts)
    3. ML engine — ambiguous filename classification
    4. Skip logic — hidden files, incomplete downloads
    5. Retention policies — correct hours/labels per category
    6. Performance — classification latency under 50ms
    7. Edge cases — empty names, unknown extensions, compound extensions
"""

from __future__ import annotations

import os
import sys
import time
import unittest

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.classifier import (
    ClassificationResult,
    HybridClassifier,
    RuleEngine,
)


class TestRuleEngine(unittest.TestCase):
    """Test Layer 1: Deterministic rule-based classification."""

    # --- Extension-based rules ---

    def test_installer_deb(self) -> None:
        self.assertEqual(RuleEngine.classify("slack-desktop-4.38.deb"), "installer")

    def test_installer_iso(self) -> None:
        self.assertEqual(RuleEngine.classify("ubuntu-24.04-desktop-amd64.iso"), "installer")

    def test_installer_appimage(self) -> None:
        self.assertEqual(RuleEngine.classify("Obsidian-1.5.12.AppImage"), "installer")

    def test_installer_exe(self) -> None:
        self.assertEqual(RuleEngine.classify("setup_v2.exe"), "installer")

    def test_installer_rpm(self) -> None:
        self.assertEqual(RuleEngine.classify("vscode-1.90.rpm"), "installer")

    def test_temporary_tmp(self) -> None:
        self.assertEqual(RuleEngine.classify("session_data.tmp"), "temporary")

    def test_temporary_log(self) -> None:
        self.assertEqual(RuleEngine.classify("app_crash.log"), "temporary")

    def test_temporary_bak(self) -> None:
        self.assertEqual(RuleEngine.classify("config.bak"), "temporary")

    def test_document_pdf(self) -> None:
        self.assertEqual(RuleEngine.classify("annual_report_2025.pdf"), "document")

    def test_document_docx(self) -> None:
        self.assertEqual(RuleEngine.classify("resume_john_doe.docx"), "document")

    def test_document_csv(self) -> None:
        self.assertEqual(RuleEngine.classify("sales_data.csv"), "document")

    def test_document_pptx(self) -> None:
        self.assertEqual(RuleEngine.classify("quarterly_review.pptx"), "document")

    def test_document_xlsx(self) -> None:
        self.assertEqual(RuleEngine.classify("budget_2026.xlsx"), "document")

    def test_media_mp4(self) -> None:
        self.assertEqual(RuleEngine.classify("vacation_video.mp4"), "media")

    def test_media_mp3(self) -> None:
        self.assertEqual(RuleEngine.classify("lofi_beats.mp3"), "media")

    def test_media_png(self) -> None:
        self.assertEqual(RuleEngine.classify("banner_design.png"), "media")

    def test_media_webp(self) -> None:
        self.assertEqual(RuleEngine.classify("hero-image.webp"), "media")

    def test_media_flac(self) -> None:
        self.assertEqual(RuleEngine.classify("album_track.flac"), "media")

    def test_archive_zip(self) -> None:
        self.assertEqual(RuleEngine.classify("project_files.zip"), "archive")

    def test_archive_tar_gz(self) -> None:
        self.assertEqual(RuleEngine.classify("node-v20.11.0-linux-x64.tar.gz"), "archive")

    def test_archive_7z(self) -> None:
        self.assertEqual(RuleEngine.classify("photos_backup.7z"), "archive")

    def test_archive_rar(self) -> None:
        self.assertEqual(RuleEngine.classify("game_mods.rar"), "archive")

    def test_archive_tar_xz(self) -> None:
        self.assertEqual(RuleEngine.classify("kernel-source.tar.xz"), "archive")

    # --- Filename pattern rules ---

    def test_screenshot_prefix(self) -> None:
        self.assertEqual(RuleEngine.classify("Screenshot_2026-05-29.png"), "screenshot")

    def test_screenshot_timestamp_prefix(self) -> None:
        self.assertEqual(RuleEngine.classify("2026-05-29 14:32:01.png"), "screenshot")

    def test_screenshot_capture_prefix(self) -> None:
        self.assertEqual(RuleEngine.classify("Capture_001.jpg"), "screenshot")

    def test_screenshot_snip(self) -> None:
        self.assertEqual(RuleEngine.classify("Snip_2026-01-15.png"), "screenshot")

    def test_receipt_keyword(self) -> None:
        self.assertEqual(RuleEngine.classify("amazon_receipt_march.pdf"), "receipt_invoice")

    def test_invoice_keyword(self) -> None:
        self.assertEqual(RuleEngine.classify("Invoice_12345.pdf"), "receipt_invoice")

    def test_payment_confirmation(self) -> None:
        self.assertEqual(RuleEngine.classify("payment_confirmation_stripe.pdf"), "receipt_invoice")

    def test_order_confirmation(self) -> None:
        self.assertEqual(RuleEngine.classify("order_confirmation_A29381.pdf"), "receipt_invoice")

    def test_payslip(self) -> None:
        self.assertEqual(RuleEngine.classify("payslip_may_2026.pdf"), "receipt_invoice")

    def test_tax_return(self) -> None:
        self.assertEqual(RuleEngine.classify("tax_return_2025.pdf"), "receipt_invoice")

    # --- No match → returns None (ML layer needed) ---

    def test_ambiguous_returns_none(self) -> None:
        self.assertIsNone(RuleEngine.classify("meeting_notes_final"))

    def test_unknown_extension_returns_none(self) -> None:
        self.assertIsNone(RuleEngine.classify("random_data.xyz"))


class TestHybridClassifier(unittest.TestCase):
    """Test the full hybrid classification pipeline (rules + ML)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.classifier = HybridClassifier()

    # --- Rule-based results via HybridClassifier ---

    def test_rule_source_deb(self) -> None:
        result = self.classifier.classify("google-chrome-stable.deb")
        self.assertEqual(result.category, "installer")
        self.assertEqual(result.source, "rule")
        self.assertEqual(result.confidence, 1.0)

    def test_rule_source_pdf(self) -> None:
        result = self.classifier.classify("thesis_final_draft.pdf")
        self.assertEqual(result.category, "document")
        self.assertEqual(result.source, "rule")

    def test_rule_source_mp4(self) -> None:
        result = self.classifier.classify("birthday_party.mp4")
        self.assertEqual(result.category, "media")
        self.assertEqual(result.source, "rule")

    def test_rule_source_screenshot(self) -> None:
        result = self.classifier.classify("Screenshot from 2026-05-29.png")
        self.assertEqual(result.category, "screenshot")

    # --- ML-based results (ambiguous filenames, no extension match) ---

    def test_ml_receipt_no_ext(self) -> None:
        result = self.classifier.classify("payment_transaction_purchase.dat")
        self.assertEqual(result.source, "ml")
        self.assertEqual(result.category, "receipt_invoice")

    def test_ml_installer_keywords(self) -> None:
        result = self.classifier.classify("setup_installer_package.dat")
        self.assertEqual(result.source, "ml")
        self.assertEqual(result.category, "installer")

    def test_ml_temp_keywords(self) -> None:
        result = self.classifier.classify("debug_crash_dump.dat")
        self.assertEqual(result.source, "ml")
        self.assertEqual(result.category, "temporary")

    def test_ml_document_keywords(self) -> None:
        result = self.classifier.classify("thesis_report_draft.dat")
        self.assertEqual(result.source, "ml")
        self.assertEqual(result.category, "document")

    def test_ml_media_keywords(self) -> None:
        result = self.classifier.classify("music_podcast_recording.dat")
        self.assertEqual(result.source, "ml")
        self.assertEqual(result.category, "media")

    def test_ml_archive_keywords(self) -> None:
        result = self.classifier.classify("backup_archive_export.dat")
        self.assertEqual(result.source, "ml")
        self.assertEqual(result.category, "archive")

    def test_ml_unknown_garbage(self) -> None:
        result = self.classifier.classify("xyzzy_foobar_42.dat")
        self.assertEqual(result.source, "ml")
        self.assertEqual(result.category, "unknown")

    # --- Confidence checks ---

    def test_rule_confidence_is_1(self) -> None:
        result = self.classifier.classify("report.pdf")
        self.assertEqual(result.confidence, 1.0)

    def test_ml_confidence_range(self) -> None:
        result = self.classifier.classify("invoice_payment_receipt.dat")
        self.assertGreater(result.confidence, 0.0)
        self.assertLessEqual(result.confidence, 1.0)

    # --- Retention policies ---

    def test_retention_installer(self) -> None:
        result = self.classifier.classify("steam_installer.deb")
        self.assertEqual(result.retention_hours, 24)
        self.assertEqual(result.retention_label, "24 Hours")

    def test_retention_screenshot(self) -> None:
        result = self.classifier.classify("Screenshot_001.png")
        self.assertEqual(result.retention_hours, 48)
        self.assertEqual(result.retention_label, "48 Hours")

    def test_retention_document_keep_forever(self) -> None:
        result = self.classifier.classify("contract_agreement.pdf")
        self.assertEqual(result.retention_hours, -1)
        self.assertEqual(result.retention_label, "Keep Forever")

    def test_retention_receipt_keep_forever(self) -> None:
        result = self.classifier.classify("amazon_receipt_2026.pdf")
        self.assertEqual(result.retention_hours, -1)
        self.assertEqual(result.retention_label, "Keep Forever")

    def test_retention_temporary(self) -> None:
        result = self.classifier.classify("session.tmp")
        self.assertEqual(result.retention_hours, 6)
        self.assertEqual(result.retention_label, "6 Hours")

    def test_retention_media(self) -> None:
        result = self.classifier.classify("concert.mp4")
        self.assertEqual(result.retention_hours, 168)
        self.assertEqual(result.retention_label, "7 Days")

    def test_retention_archive(self) -> None:
        result = self.classifier.classify("project.zip")
        self.assertEqual(result.retention_hours, 48)
        self.assertEqual(result.retention_label, "48 Hours")

    # --- Skip / ignore logic ---

    def test_skip_crdownload(self) -> None:
        self.assertTrue(self.classifier.should_skip("video.mp4.crdownload"))

    def test_skip_part(self) -> None:
        self.assertTrue(self.classifier.should_skip("archive.zip.part"))

    def test_skip_hidden(self) -> None:
        self.assertTrue(self.classifier.should_skip(".hidden_config"))

    def test_skip_opdownload(self) -> None:
        self.assertTrue(self.classifier.should_skip("file.pdf.opdownload"))

    def test_no_skip_normal_file(self) -> None:
        self.assertFalse(self.classifier.should_skip("report_2026.pdf"))

    # --- ClassificationResult structure ---

    def test_result_has_all_fields(self) -> None:
        result = self.classifier.classify("data_export.zip")
        self.assertIsInstance(result, ClassificationResult)
        self.assertTrue(result.filename)
        self.assertTrue(result.extension)
        self.assertTrue(result.category)
        self.assertTrue(result.source)
        self.assertIsInstance(result.confidence, float)
        self.assertIsInstance(result.retention_hours, int)
        self.assertTrue(result.retention_label)

    # --- Edge cases ---

    def test_empty_extension(self) -> None:
        result = self.classifier.classify("Makefile")
        self.assertIsNotNone(result)

    def test_full_path_extracts_basename(self) -> None:
        result = self.classifier.classify("/home/user/Downloads/report.pdf")
        self.assertEqual(result.category, "document")
        self.assertEqual(result.filename, "report.pdf")


class TestPerformance(unittest.TestCase):
    """Verify classification latency is under 50ms."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.classifier = HybridClassifier()

    def test_rule_classification_under_1ms(self) -> None:
        """Rule-based classification should be near-instant."""
        filenames = [
            "package.deb", "report.pdf", "video.mp4",
            "Screenshot_001.png", "archive.tar.gz",
        ]
        for fname in filenames:
            start = time.monotonic()
            self.classifier.classify(fname)
            elapsed_ms = (time.monotonic() - start) * 1000
            self.assertLess(
                elapsed_ms, 1.0,
                f"Rule classification of '{fname}' took {elapsed_ms:.2f}ms (limit: 1ms)",
            )

    def test_ml_classification_under_50ms(self) -> None:
        """ML-based classification must stay under 50ms."""
        filenames = [
            "invoice_payment_order.dat",
            "debug_crash_dump.dat",
            "music_podcast_recording.dat",
            "thesis_report_draft.dat",
            "backup_archive_export.dat",
            "xyzzy_foobar_42.dat",
        ]
        for fname in filenames:
            start = time.monotonic()
            self.classifier.classify(fname)
            elapsed_ms = (time.monotonic() - start) * 1000
            self.assertLess(
                elapsed_ms, 50.0,
                f"ML classification of '{fname}' took {elapsed_ms:.2f}ms (limit: 50ms)",
            )

    def test_bulk_100_classifications_under_100ms(self) -> None:
        """100 mixed classifications should complete in under 100ms total."""
        filenames = [
            "report.pdf", "song.mp3", "Screenshot_001.png",
            "data.zip", "invoice_001.dat", "crash_dump.log",
            "movie_clip.mp4", "thesis_v2.dat", "backup.tar.gz",
            "random_file.xyz",
        ] * 10  # 100 files

        start = time.monotonic()
        for fname in filenames:
            self.classifier.classify(fname)
        elapsed_ms = (time.monotonic() - start) * 1000

        self.assertLess(
            elapsed_ms, 100.0,
            f"Bulk 100 classifications took {elapsed_ms:.2f}ms (limit: 100ms)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
