import unittest
from unittest.mock import MagicMock, patch

from app.ai.gemini import _generate, _generation_config


class GeminiGenerationConfigTests(unittest.TestCase):
    def test_gemini_36_omits_deprecated_temperature(self):
        config = _generation_config("gemini-3.6-flash", 0.4, 1400)
        self.assertEqual(config.max_output_tokens, 1400)
        self.assertIsNone(config.temperature)

    def test_gemini_35_flash_lite_uses_api_default_sampling(self):
        config = _generation_config("models/gemini-3.5-flash-lite", 0.65, 1400)
        self.assertIsNone(config.temperature)

    def test_gemini_4_omits_temperature(self):
        config = _generation_config("gemini-4-flash", 0.8, 1200)
        self.assertIsNone(config.temperature)

    def test_older_models_keep_existing_temperature(self):
        config = _generation_config("gemini-2.5-flash", 0.4, 1400)
        self.assertEqual(config.temperature, 0.4)
        self.assertEqual(config.max_output_tokens, 1400)

    def test_generate_uses_mocked_client_without_network_or_api_key(self):
        fake_response = object()
        client = MagicMock()
        client.models.generate_content.return_value = fake_response
        with patch("app.ai.gemini._models_to_try", return_value=["gemini-3.6-flash"]):
            response, model = _generate(
                client,
                contents=["synthetic offline test input"],
                temperature=0.4,
                max_output_tokens=77,
            )
        self.assertIs(response, fake_response)
        self.assertEqual(model, "gemini-3.6-flash")
        kwargs = client.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs["contents"], ["synthetic offline test input"])
        self.assertIsNone(kwargs["config"].temperature)
        self.assertEqual(kwargs["config"].max_output_tokens, 77)


if __name__ == "__main__":
    unittest.main()
