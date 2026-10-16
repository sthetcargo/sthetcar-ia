"""Pure helpers for splitting an approved before/after post into platform-safe parts."""
from math import ceil


def split_photo_collections(before_images, after_images, max_items=10):
    """Split paired before/after images into chunks within a carousel item limit.

    Each part takes the same index range from BEFORE and DEPOIS, so a comparison
    pair stays together. For 6+6 images and a limit of 10 this yields 5+5 and
    1+1. The input collections are copied and never mutated.
    """
    if not isinstance(max_items, int) or max_items < 2:
        raise ValueError("max_items must be an integer greater than or equal to 2")

    before = [str(value).strip() for value in (before_images or []) if str(value or "").strip()]
    after = [str(value).strip() for value in (after_images or []) if str(value or "").strip()]
    if not before or not after:
        raise ValueError("Both BEFORE and DEPOIS must contain at least one image")
    if len(before) + len(after) <= max_items:
        return [{"before_images": before, "after_images": after}]

    photos_per_side = max_items // 2
    part_count = ceil(max(len(before), len(after)) / photos_per_side)
    parts = []
    for part_index in range(part_count):
        start = part_index * photos_per_side
        part_before = before[start:start + photos_per_side]
        part_after = after[start:start + photos_per_side]
        if len(part_before) + len(part_after) > max_items:
            raise ValueError("A split part exceeds the configured publication limit")
        if not part_before and not part_after:
            continue
        parts.append({
            "before_images": part_before,
            "after_images": part_after,
        })

    # A defensive invariant: no photo can be omitted or repeated by the split.
    flattened_before = [value for part in parts for value in part["before_images"]]
    flattened_after = [value for part in parts for value in part["after_images"]]
    if flattened_before != before or flattened_after != after:
        raise ValueError("Splitting changed the original photo sequence")
    if any(len(part["before_images"]) + len(part["after_images"]) > max_items for part in parts):
        raise ValueError("A split part exceeds the configured publication limit")
    return parts
