import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.ai.gemini import (
    PLATE_DETECTION_RESPONSE_SCHEMA,
    _generation_config,
    _parse_plate_detection_json,
    detect_license_plates,
)


class PlateDetectionStructuredOutputTests(unittest.TestCase):
    def test_schema_config_requests_json_mode(self):
        config = _generation_config(
            "gemini-3.6-flash",
            temperature=0,
            max_output_tokens=4096,
            response_mime_type="application/json",
            response_schema=PLATE_DETECTION_RESPONSE_SCHEMA,
        )
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertIsNotNone(config.response_schema)
        self.assertEqual(config.max_output_tokens, 4096)
        self.assertIsNone(config.temperature)

    def test_schema_requires_image_index_boxes_and_review_flag(self):
        image_schema = PLATE_DETECTION_RESPONSE_SCHEMA["properties"]["images"]["items"]
        self.assertEqual(image_schema["required"], ["index", "plates", "needs_review"])
        plate_schema = image_schema["properties"]["plates"]["items"]
        self.assertEqual(plate_schema["required"], ["x1", "y1", "x2", "y2"])

    def test_parser_accepts_valid_json(self):
        payload = {"images": [{"index": 1, "plates": [], "needs_review": False}]}
        self.assertEqual(_parse_plate_detection_json(json.dumps(payload)), payload)

    def test_parser_accepts_legacy_markdown_fence(self):
        payload = {"images": [{"index": 1, "plates": [], "needs_review": True}]}
        self.assertEqual(
            _parse_plate_detection_json("Here is JSON:\n```json\n" + json.dumps(payload) + "\n```"),
            payload,
        )

    def test_parser_fails_closed_for_natural_language(self):
        with self.assertRaisesRegex(RuntimeError, "formato inválido"):
            _parse_plate_detection_json("Não consegui encontrar o formato solicitado.")

    def test_parser_rejects_empty_or_non_object_responses(self):
        with self.assertRaisesRegex(RuntimeError, "não retornou uma resposta"):
            _parse_plate_detection_json("")
        with self.assertRaisesRegex(RuntimeError, "objeto JSON inválido"):
            _parse_plate_detection_json('[{"index":1}]')

    def test_detector_uses_schema_mode_and_keeps_strict_validation(self):
        raw = {"images": [{"index": 1, "plates": [], "needs_review": False}]}
        response = SimpleNamespace(text=json.dumps(raw), candidates=[], prompt_feedback=None)
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "fake.jpg"
            image_path.touch()
            with patch("app.ai.gemini._client", return_value=client), \
                 patch("app.ai.gemini._image_part", return_value="synthetic-image-part"), \
                 patch("app.ai.gemini._generate", return_value=(response, "gemini-3.6-flash")) as generate:
                result = detect_license_plates([image_path])

        self.assertEqual(result, raw["images"])
        kwargs = generate.call_args.kwargs
        self.assertEqual(kwargs["response_mime_type"], "application/json")
        self.assertEqual(kwargs["response_schema"], PLATE_DETECTION_RESPONSE_SCHEMA)
        self.assertEqual(kwargs["max_output_tokens"], 4096)

    def test_detector_does_not_accept_unparseable_response(self):
        response = SimpleNamespace(
            text="A resposta não está em JSON.",
            candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
            prompt_feedback=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "fake.jpg"
            image_path.touch()
            with patch("app.ai.gemini._client", return_value=MagicMock()), \
                 patch("app.ai.gemini._image_part", return_value="synthetic-image-part"), \
                 patch("app.ai.gemini._generate", return_value=(response, "gemini-3.6-flash")):
                with self.assertRaisesRegex(RuntimeError, "formato inválido"):
                    detect_license_plates([image_path])


if __name__ == "__main__":
    unittest.main()
