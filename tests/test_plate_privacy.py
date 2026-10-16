import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageChops

from app.ai.plate_privacy import PlatePrivacyError, create_plate_safe_copies


class PlatePrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original = self.root / "car.jpg"
        image = Image.new("RGB", (240, 120), (90, 100, 110))
        draw = ImageDraw.Draw(image)
        draw.rectangle((70, 42, 170, 72), fill=(245, 245, 245))
        for x in range(80, 165, 12):
            draw.rectangle((x, 48, x + 4, 66), fill=(5, 5, 5))
        image.save(self.original, quality=96)
        self.original_bytes = self.original.read_bytes()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detected_plate_creates_blurred_copy_and_preserves_original(self):
        result = [{
            "index": 1,
            "plates": [{"x1": 290, "y1": 420, "x2": 710, "y2": 640}],
            "needs_review": False,
        }]
        with patch("app.ai.plate_privacy._detect_license_plates", return_value=result):
            safe_paths, summary = create_plate_safe_copies([self.original])

        safe = safe_paths[0]
        self.assertNotEqual(safe, self.original)
        self.assertTrue(safe.is_file())
        self.assertEqual(safe.suffix.lower(), ".jpg")
        self.assertEqual(self.original.read_bytes(), self.original_bytes)
        self.assertEqual(summary["plate_redaction_version"], 1)
        self.assertEqual(summary["plate_redaction_count"], 1)
        self.assertIn("borradas", summary["plate_redaction_message"])

        with Image.open(safe) as safe_image:
            self.assertEqual(safe_image.format, "JPEG")
            self.assertEqual(safe_image.size, (1080, 1080))

            # The original 2:1 photo is letterboxed rather than cropped; the
            # detector coordinates are on the resulting square canvas.
            expected = Image.new("RGB", (1080, 1080), (32, 34, 38))
            with Image.open(self.original) as original_image:
                scaled = original_image.convert("RGB").resize((1080, 540), Image.Resampling.LANCZOS)
                expected.paste(scaled, (0, 270))
            check_region = (300, 440, 780, 650)
            safe_crop = safe_image.crop(check_region)
            expected_crop = expected.crop(check_region)
            self.assertIsNotNone(ImageChops.difference(safe_crop, expected_crop).getbbox())

    def test_portrait_png_and_panorama_webp_become_square_jpeg_without_cropping(self):
        portrait = self.root / "portrait.png"
        panorama = self.root / "panorama.webp"
        Image.new("RGB", (120, 240), (190, 30, 30)).save(portrait, format="PNG")
        Image.new("RGB", (480, 120), (30, 190, 30)).save(panorama, format="WEBP")
        portrait_original = portrait.read_bytes()
        panorama_original = panorama.read_bytes()
        result = [
            {"index": 1, "plates": [], "needs_review": False},
            {"index": 2, "plates": [], "needs_review": False},
        ]
        with patch("app.ai.plate_privacy._detect_license_plates", return_value=result):
            safe_paths, summary = create_plate_safe_copies([portrait, panorama])

        self.assertEqual([p.suffix.lower() for p in safe_paths], [".jpg", ".jpg"])
        for safe in safe_paths:
            with Image.open(safe) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (1080, 1080))

        with Image.open(safe_paths[0]) as portrait_result:
            self.assertEqual(portrait_result.getpixel((0, 0)), (32, 34, 38))
            self.assertGreater(portrait_result.getpixel((540, 540))[0], 150)
        with Image.open(safe_paths[1]) as panorama_result:
            self.assertEqual(panorama_result.getpixel((0, 0)), (32, 34, 38))
            self.assertGreater(panorama_result.getpixel((540, 540))[1], 150)

        self.assertEqual(portrait.read_bytes(), portrait_original)
        self.assertEqual(panorama.read_bytes(), panorama_original)
        self.assertEqual(summary["plate_redaction_image_count"], 2)

    def test_no_detected_plate_still_creates_separate_copy(self):
        result = [{"index": 1, "plates": [], "needs_review": False}]
        with patch("app.ai.plate_privacy._detect_license_plates", return_value=result):
            safe_paths, summary = create_plate_safe_copies([self.original])

        self.assertTrue(safe_paths[0].is_file())
        self.assertNotEqual(safe_paths[0], self.original)
        self.assertEqual(self.original.read_bytes(), self.original_bytes)
        self.assertEqual(summary["plate_redaction_count"], 0)
        self.assertIn("Revise todas as prévias", summary["plate_redaction_message"])

    def test_uncertain_result_without_box_fails_closed(self):
        result = [{"index": 1, "plates": [], "needs_review": True}]
        with patch("app.ai.plate_privacy._detect_license_plates", return_value=result):
            with self.assertRaises(PlatePrivacyError):
                create_plate_safe_copies([self.original])

        self.assertTrue(self.original.is_file())
        self.assertFalse((self.root / "car_placa_protegida.jpg").exists())

    def test_unexpected_detector_error_returns_reference_and_logs_diagnostic(self):
        with patch(
            "app.ai.plate_privacy._detect_license_plates",
            side_effect=RuntimeError("simulated detector failure"),
        ):
            with self.assertLogs("app.ai.plate_privacy", level="ERROR") as captured:
                with self.assertRaises(PlatePrivacyError) as raised:
                    create_plate_safe_copies([self.original])

        message = str(raised.exception)
        self.assertIn("Código de diagnóstico: PLATE-", message)
        diagnostic = message.split("Código de diagnóstico: ", 1)[1].split(".", 1)[0]
        self.assertTrue(any(diagnostic in line for line in captured.output))
        self.assertNotIn("simulated detector failure", message)
        self.assertEqual(self.original.read_bytes(), self.original_bytes)
        self.assertFalse((self.root / "car_placa_protegida.jpg").exists())

    def test_invalid_box_fails_closed_and_keeps_original(self):
        result = [{
            "index": 1,
            "plates": [{"x1": 800, "y1": 200, "x2": 300, "y2": 400}],
            "needs_review": False,
        }]
        with patch("app.ai.plate_privacy._detect_license_plates", return_value=result):
            with self.assertRaises(PlatePrivacyError):
                create_plate_safe_copies([self.original])

        self.assertEqual(self.original.read_bytes(), self.original_bytes)
        self.assertFalse((self.root / "car_placa_protegida.jpg").exists())


if __name__ == "__main__":
    unittest.main()
