import unittest

from app.photo_categories import (
    CATEGORY_BY_KEY,
    group_photo_indexes,
    normalize_photo_categories,
)


class PhotoCategoryTests(unittest.TestCase):
    def test_group_external_and_internal_photos_independently(self):
        before = ["externa", "interna", "interna", "externa", "interna", "interna"]
        after = ["externa", "interna", "interna", "externa", "interna", "interna"]

        groups = group_photo_indexes(before, after)

        self.assertEqual([group["key"] for group in groups], ["externa", "interna"])
        self.assertEqual(groups[0]["before_indexes"], [0, 3])
        self.assertEqual(groups[0]["after_indexes"], [0, 3])
        self.assertEqual(groups[0]["service"], "Lavagem externa")
        self.assertEqual(groups[1]["before_indexes"], [1, 2, 4, 5])
        self.assertEqual(groups[1]["after_indexes"], [1, 2, 4, 5])
        self.assertEqual(groups[1]["service"], "Higienização interna")

    def test_supports_all_four_service_areas(self):
        before = ["externa", "interna", "chassi", "motor"]
        after = ["externa", "interna", "chassi", "motor"]

        groups = group_photo_indexes(before, after)

        self.assertEqual([group["key"] for group in groups], ["externa", "interna", "chassi", "motor"])
        self.assertEqual([group["service"] for group in groups], [
            "Lavagem externa", "Higienização interna", "Lavagem do chassi", "Limpeza do motor"
        ])

    def test_general_selection_preserves_single_post_behavior(self):
        groups = group_photo_indexes(["geral", "geral"], ["geral", "geral"])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["key"], "geral")
        self.assertEqual(groups[0]["before_indexes"], [0, 1])
        self.assertEqual(groups[0]["after_indexes"], [0, 1])

    def test_legacy_forms_without_categories_default_to_general(self):
        before = normalize_photo_categories([], 3, "ANTES")
        after = normalize_photo_categories(None, 2, "DEPOIS")
        self.assertEqual(before, ["geral", "geral", "geral"])
        self.assertEqual(after, ["geral", "geral"])

    def test_rejects_category_count_mismatch(self):
        with self.assertRaisesRegex(ValueError, "categoria de cada foto"):
            normalize_photo_categories(["externa"], 2, "ANTES")

    def test_rejects_unknown_category(self):
        with self.assertRaisesRegex(ValueError, "categoria.*inválida"):
            normalize_photo_categories(["interior"], 1, "ANTES")

    def test_requires_before_and_after_per_category(self):
        with self.assertRaisesRegex(ValueError, "precisa ter pelo menos uma foto de ANTES e uma de DEPOIS"):
            group_photo_indexes(["externa", "interna"], ["externa"])

    def test_every_photo_index_is_preserved_exactly_once(self):
        before = ["externa", "interna", "motor", "chassi", "externa", "interna"]
        after = ["externa", "interna", "motor", "chassi", "externa", "interna"]
        groups = group_photo_indexes(before, after)
        before_indexes = [index for group in groups for index in group["before_indexes"]]
        after_indexes = [index for group in groups for index in group["after_indexes"]]
        self.assertEqual(sorted(before_indexes), list(range(len(before))))
        self.assertEqual(sorted(after_indexes), list(range(len(after))))
        self.assertEqual(len(before_indexes), len(set(before_indexes)))
        self.assertEqual(len(after_indexes), len(set(after_indexes)))

    def test_each_key_has_a_human_friendly_label(self):
        for key in ("externa", "interna", "chassi", "motor", "geral"):
            self.assertTrue(CATEGORY_BY_KEY[key].label)


if __name__ == "__main__":
    unittest.main()
