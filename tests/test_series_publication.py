import io
import unittest

from PIL import Image

from app.series_publication import (
    build_series_badge_image,
    build_series_caption,
    build_series_title,
)


class SeriesPublicationTests(unittest.TestCase):
    def test_title_is_shared_and_vehicle_specific(self):
        self.assertEqual(build_series_title("  Toyota   Corolla "), "Transformação completa: Toyota Corolla")
        self.assertEqual(build_series_title(""), "Transformação completa do veículo")

    def test_category_caption_names_current_and_next_service(self):
        caption = build_series_caption(
            "A lataria e os vidros estavam cobertos de poeira.",
            "Corolla",
            "Lavagem externa",
            part_number=1,
            part_count=2,
            next_service="Higienização interna",
            hashtags="#Sthetcar #LavagemExterna",
        )
        self.assertTrue(caption.startswith("🚗 Transformação completa: Corolla — Parte 1/2: Lavagem externa"))
        self.assertIn("A lataria e os vidros estavam cobertos", caption)
        self.assertIn("Na próxima publicação: Higienização interna.", caption)
        self.assertLessEqual(len(caption) + 2 + len("#Sthetcar #LavagemExterna"), 2200)

    def test_last_part_has_closing_message(self):
        caption = build_series_caption(
            "Higienizamos bancos, portas e painel.",
            "Corolla",
            "Higienização interna",
            part_number=2,
            part_count=2,
            next_service=None,
        )
        self.assertIn("Parte 2/2", caption)
        self.assertIn("Esta é a última parte desta transformação", caption)
        self.assertNotIn("Na próxima publicação", caption)

    def test_same_service_parts_point_to_next_number(self):
        caption = build_series_caption(
            "Continuação do serviço.",
            "S-10",
            "Lavagem externa",
            part_number=1,
            part_count=3,
            next_service="Lavagem externa",
        )
        self.assertIn("Parte 1/3", caption)
        self.assertIn("Parte 2/3 de Lavagem externa", caption)

    def test_caption_rewrite_does_not_stack_old_series_heading(self):
        caption = build_series_caption(
            "🚗 Transformação completa: Corolla — Parte 1/2: Lavagem externa\n\nTexto antigo.\n\n➡️ Na próxima publicação: Higienização interna.",
            "Corolla",
            "Lavagem externa",
            part_number=1,
            part_count=2,
            next_service="Higienização interna",
        )
        self.assertEqual(caption.count("🚗 Transformação completa: Corolla"), 1)
        self.assertEqual(caption.count("Na próxima publicação"), 1)

    def test_caption_reserves_room_for_hashtags(self):
        body = "descrição " * 500
        hashtags = "#Sthetcar " * 15
        caption = build_series_caption(
            body, "Corolla", "Lavagem externa", 1, 2, "Higienização interna",
            hashtags=hashtags, max_chars=2200
        )
        self.assertLessEqual(len(caption) + 2 + len(hashtags.strip()), 2200)
        self.assertTrue(caption.endswith("Na próxima publicação: Higienização interna."))

    def test_badge_adds_a_separate_header_without_cropping_source_photo(self):
        source = Image.new("RGB", (400, 400), (220, 35, 35))
        bytes_io = io.BytesIO()
        source.save(bytes_io, format="JPEG", quality=100)
        original_bytes = bytes_io.getvalue()

        result = build_series_badge_image(
            original_bytes, "Corolla", "Lavagem externa", 1, 2
        )
        with Image.open(io.BytesIO(original_bytes)) as original, Image.open(io.BytesIO(result)) as labeled:
            self.assertEqual(labeled.width, original.width)
            self.assertGreater(labeled.height, original.height)
            self.assertGreaterEqual(labeled.width / labeled.height, 0.8)
            self.assertLessEqual(labeled.width / labeled.height, 1.91)
            # The source image is pasted whole below the label; its center remains red.
            center = labeled.getpixel((labeled.width // 2, labeled.height - original.height // 2))
            self.assertGreater(center[0], 170)
            self.assertLess(center[1], 90)
            header = labeled.getpixel((5, 5))
            self.assertLess(max(header), 80)

    def test_invalid_series_part_numbers_are_rejected(self):
        with self.assertRaises(ValueError):
            build_series_caption("Texto.", "Corolla", "Lavagem", 0, 2)
        with self.assertRaises(ValueError):
            build_series_badge_image(b"not-an-image", "Corolla", "Lavagem", 1, 2)


if __name__ == "__main__":
    unittest.main()
