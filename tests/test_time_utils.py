import unittest

from app.time_utils import format_local_datetime


class LocalDatetimeFormattingTests(unittest.TestCase):
    def test_naive_render_timestamp_is_interpreted_as_utc(self):
        self.assertEqual(
            format_local_datetime("2026-10-16T02:21:58"),
            "15/10/2026 às 23:21:58",
        )

    def test_utc_z_suffix_converts_to_brasilia_time(self):
        self.assertEqual(
            format_local_datetime("2026-10-16T02:21:58Z"),
            "15/10/2026 às 23:21:58",
        )

    def test_already_brasilia_offset_is_not_shifted_twice(self):
        self.assertEqual(
            format_local_datetime("2026-10-15T23:21:58-03:00"),
            "15/10/2026 às 23:21:58",
        )

    def test_empty_value_is_empty(self):
        self.assertEqual(format_local_datetime(""), "")

    def test_unrecognized_value_is_returned_readably(self):
        self.assertEqual(format_local_datetime("data desconhecida"), "data desconhecida")


if __name__ == "__main__":
    unittest.main()
