import tempfile
import unittest
from pathlib import Path

from app.analysis_progress import AnalysisProgressStore


class AnalysisProgressStoreTests(unittest.TestCase):
    def test_update_and_read_progress_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnalysisProgressStore(Path(directory))
            payload = store.update(
                "0123456789ABCDEF01234567",
                status="processing",
                stage="protecting",
                message="Protegendo as placas.",
                percent=36,
                completed_items=3,
                total_items=8,
                now_epoch=1000,
            )
            self.assertEqual(payload["percent"], 36)
            snapshot = store.read("0123456789ABCDEF01234567", now_epoch=1010)
            self.assertEqual(snapshot["stage"], "protecting")
            self.assertEqual(snapshot["age_seconds"], 10)
            self.assertFalse(snapshot["stale"])

    def test_long_stage_is_reported_as_stale_without_marking_job_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnalysisProgressStore(Path(directory), stale_after_seconds=45)
            store.update(
                "0123456789ABCDEF01234567",
                status="processing",
                stage="gemini_analysis",
                message="A IA está analisando as fotos.",
                percent=65,
                now_epoch=1000,
            )
            snapshot = store.read("0123456789ABCDEF01234567", now_epoch=1046)
            self.assertTrue(snapshot["stale"])
            self.assertEqual(snapshot["status"], "processing")

    def test_completion_is_100_percent_and_not_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnalysisProgressStore(Path(directory))
            store.update(
                "0123456789ABCDEF01234567",
                status="completed",
                stage="done",
                message="Revisão criada.",
                percent=100,
                now_epoch=1000,
            )
            snapshot = store.read("0123456789ABCDEF01234567", now_epoch=9000)
            self.assertEqual(snapshot["status"], "completed")
            self.assertEqual(snapshot["percent"], 100)
            self.assertFalse(snapshot["stale"])

    def test_error_status_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnalysisProgressStore(Path(directory))
            store.update(
                "0123456789ABCDEF01234567",
                status="error",
                stage="failed",
                message="Falha ao proteger as placas.",
                percent=42,
                now_epoch=1000,
            )
            snapshot = store.read("0123456789ABCDEF01234567", now_epoch=1001)
            self.assertEqual(snapshot["status"], "error")
            self.assertEqual(snapshot["message"], "Falha ao proteger as placas.")

    def test_started_at_is_preserved_between_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnalysisProgressStore(Path(directory))
            first = store.update(
                "0123456789ABCDEF01234567",
                stage="upload",
                percent=10,
                now_epoch=1000,
            )
            second = store.update(
                "0123456789ABCDEF01234567",
                stage="analysis",
                percent=65,
                now_epoch=1060,
            )
            self.assertEqual(first["started_at"], second["started_at"])

    def test_invalid_identifiers_cannot_escape_progress_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnalysisProgressStore(Path(directory))
            self.assertIsNone(store.read("../../etc/passwd"))
            with self.assertRaises(ValueError):
                store.update("../../etc/passwd", stage="bad")

    def test_missing_or_corrupt_snapshot_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnalysisProgressStore(Path(directory))
            self.assertIsNone(store.read("0123456789ABCDEF01234567"))
            (Path(directory) / "0123456789ABCDEF01234567.json").write_text(
                "{bad json", encoding="utf-8"
            )
            self.assertIsNone(store.read("0123456789ABCDEF01234567"))


if __name__ == "__main__":
    unittest.main()
