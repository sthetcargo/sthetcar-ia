import unittest

from app.publication_split import split_photo_collections


class PublicationSplitTests(unittest.TestCase):
    def test_twelve_photos_split_into_five_pairs_and_one_pair(self):
        before = [f"before-{index}" for index in range(1, 7)]
        after = [f"after-{index}" for index in range(1, 7)]

        parts = split_photo_collections(before, after, max_items=10)

        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0]["before_images"], before[:5])
        self.assertEqual(parts[0]["after_images"], after[:5])
        self.assertEqual(parts[1]["before_images"], before[5:])
        self.assertEqual(parts[1]["after_images"], after[5:])
        self.assertEqual(
            [len(part["before_images"]) + len(part["after_images"]) for part in parts],
            [10, 2],
        )
        self.assertEqual([value for p in parts for value in p["before_images"]], before)
        self.assertEqual([value for p in parts for value in p["after_images"]], after)

    def test_eleven_photos_are_not_dropped_or_repeated(self):
        before = [f"before-{index}" for index in range(1, 7)]
        after = [f"after-{index}" for index in range(1, 6)]

        parts = split_photo_collections(before, after, max_items=10)

        self.assertEqual([len(p["before_images"]) + len(p["after_images"]) for p in parts], [10, 1])
        self.assertEqual([value for p in parts for value in p["before_images"]], before)
        self.assertEqual([value for p in parts for value in p["after_images"]], after)
        self.assertTrue(all(len(p["before_images"]) + len(p["after_images"]) <= 10 for p in parts))

    def test_posts_with_ten_or_fewer_photos_remain_one_unchanged_part(self):
        before = ["b1", "b2", "b3"]
        after = ["a1", "a2", "a3", "a4", "a5", "a6", "a7"]

        parts = split_photo_collections(before, after, max_items=10)

        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0], {"before_images": before, "after_images": after})

    def test_empty_entries_are_removed_without_changing_valid_order(self):
        parts = split_photo_collections(["b1", "", None, "b2"], ["a1", "a2"], max_items=10)
        self.assertEqual(parts, [{"before_images": ["b1", "b2"], "after_images": ["a1", "a2"]}])

    def test_both_sides_must_have_at_least_one_photo(self):
        with self.assertRaisesRegex(ValueError, "Both BEFORE and DEPOIS"):
            split_photo_collections(["b1"], [], max_items=10)
        with self.assertRaisesRegex(ValueError, "Both BEFORE and DEPOIS"):
            split_photo_collections([], ["a1"], max_items=10)

    def test_max_items_must_be_at_least_two(self):
        with self.assertRaisesRegex(ValueError, "greater than or equal to 2"):
            split_photo_collections(["b1"], ["a1"], max_items=1)


if __name__ == "__main__":
    unittest.main()
