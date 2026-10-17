"""Photo-category helpers for creating separate service posts from one appointment."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PhotoCategory:
    key: str
    label: str
    service: str


PHOTO_CATEGORIES = (
    PhotoCategory("externa", "Externa", "Lavagem externa"),
    PhotoCategory("interna", "Interna", "Higienização interna"),
    PhotoCategory("chassi", "Chassi", "Lavagem do chassi"),
    PhotoCategory("motor", "Motor", "Limpeza do motor"),
    PhotoCategory("geral", "Geral / não separar", ""),
)
CATEGORY_BY_KEY = {category.key: category for category in PHOTO_CATEGORIES}


def normalize_photo_categories(values, expected_count, field_label):
    """Validate one category per uploaded photo; legacy forms default to 'geral'."""
    values = list(values or [])
    if not values:
        return ["geral"] * expected_count
    if len(values) != expected_count:
        raise ValueError(
            f"Não foi possível identificar a categoria de cada foto de {field_label}. "
            "Confira a seleção de fotos e tente novamente."
        )

    normalized = []
    for value in values:
        key = str(value or "geral").strip().lower() or "geral"
        if key not in CATEGORY_BY_KEY:
            raise ValueError(
                f"A categoria de uma foto de {field_label} é inválida. "
                "Selecione Externa, Interna, Chassi, Motor ou Geral."
            )
        normalized.append(key)
    return normalized


def group_photo_indexes(before_categories, after_categories):
    """Group matching BEFORE/AFTER photo indexes by selected service area.

    A category is emitted only when used. Every emitted category must have at
    least one BEFORE and one AFTER photo, so a category cannot silently lose its
    comparison pair. Indexes preserve upload order.
    """
    before_categories = list(before_categories or [])
    after_categories = list(after_categories or [])
    before_normalized = normalize_photo_categories(
        before_categories, len(before_categories), "ANTES"
    )
    after_normalized = normalize_photo_categories(
        after_categories, len(after_categories), "DEPOIS"
    )

    used = set(before_normalized) | set(after_normalized)
    groups = []
    for category in PHOTO_CATEGORIES:
        if category.key not in used:
            continue
        before_indexes = [
            index for index, key in enumerate(before_normalized) if key == category.key
        ]
        after_indexes = [
            index for index, key in enumerate(after_normalized) if key == category.key
        ]
        if not before_indexes or not after_indexes:
            raise ValueError(
                f"A categoria '{category.label}' precisa ter pelo menos uma foto de "
                "ANTES e uma de DEPOIS. Ajuste as categorias nas miniaturas; nenhuma foto foi descartada."
            )
        groups.append({
            "key": category.key,
            "label": category.label,
            "service": category.service,
            "before_indexes": before_indexes,
            "after_indexes": after_indexes,
        })

    if not groups:
        raise ValueError("Marque pelo menos uma foto de ANTES e uma de DEPOIS.")
    return groups
