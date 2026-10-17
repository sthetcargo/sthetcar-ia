import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.ai.gemini import VISUAL_ANALYSIS_BATCH_SIZE, analyze_before_after
from app.publication_split import split_photo_collections


class VariablePhotoCountTests(unittest.TestCase):
    def test_visual_analysis_batches_all_photos_without_network_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            before = []
            after = []
            for index in range(1, 15):
                before_path = Path(folder) / f"before_{index}.jpg"
                after_path = Path(folder) / f"after_{index}.jpg"
                before_path.touch()
                after_path.touch()
                before.append(before_path)
                after.append(after_path)

            response_count = 0

            def fake_generate(client, contents, **kwargs):
                nonlocal response_count
                response_count += 1
                return SimpleNamespace(text=f"Resumo visual do lote {response_count}"), "gemini-test"

            with patch("app.ai.gemini._client", return_value=object()), \
                 patch("app.ai.gemini._media_part", side_effect=lambda path: f"IMAGE:{path.name}"), \
                 patch("app.ai.gemini._generate", side_effect=fake_generate) as generate:
                result = analyze_before_after(before, after, "Veículo de teste", "Serviço de teste")

        self.assertEqual(generate.call_count, 4)
        self.assertEqual(VISUAL_ANALYSIS_BATCH_SIZE, 4)
        self.assertIn("LOTE 4/4", result)
        for index in range(1, 15):
            self.assertIn(f"FOTO ANTES {index}", " ".join(
                str(item) for call in generate.call_args_list for item in call.args[1]
            ))
            self.assertIn(f"FOTO DEPOIS {index}", " ".join(
                str(item) for call in generate.call_args_list for item in call.args[1]
            ))
        batch_image_counts = []
        for call in generate.call_args_list:
            contents = call.args[1]
            batch_image_counts.append(sum(1 for item in contents if isinstance(item, str) and item.startswith("IMAGE:")))
        self.assertEqual(batch_image_counts, [8, 8, 8, 4])

    def test_missing_batch_analysis_fails_without_creating_partial_caption(self):
        with tempfile.TemporaryDirectory() as folder:
            before = Path(folder) / "before.jpg"
            after = Path(folder) / "after.jpg"
            before.touch()
            after.touch()
            with patch("app.ai.gemini._client", return_value=object()), \
                 patch("app.ai.gemini._media_part", return_value="synthetic-image"), \
                 patch("app.ai.gemini._generate", return_value=(SimpleNamespace(text=""), "gemini-test")):
                with self.assertRaisesRegex(RuntimeError, "não retornou análise para o lote"):
                    analyze_before_after([before], [after])

    def test_arbitrary_photo_counts_split_with_no_loss_or_silent_truncation(self):
        before = [f"before-{i}" for i in range(1, 38)]
        after = [f"after-{i}" for i in range(1, 34)]
        parts = split_photo_collections(before, after, max_items=10)
        self.assertGreater(len(parts), 3)
        self.assertEqual([x for part in parts for x in part["before_images"]], before)
        self.assertEqual([x for part in parts for x in part["after_images"]], after)
        self.assertTrue(all(
            len(part["before_images"]) + len(part["after_images"]) <= 10
            for part in parts
        ))

    def test_forty_two_photos_make_an_automatic_number_of_parts(self):
        before = [f"before-{i}" for i in range(1, 22)]
        after = [f"after-{i}" for i in range(1, 22)]
        parts = split_photo_collections(before, after, max_items=10)
        self.assertEqual([len(p["before_images"]) + len(p["after_images"]) for p in parts], [10, 10, 10, 10, 2])


if __name__ == "__main__":
    unittest.main()
