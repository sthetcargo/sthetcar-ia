"""Privacy helpers that create publication-safe image copies.

The original uploads are never overwritten. Detection failures are fail-closed, and
the caller must keep a human-review step because vision models can miss plates.
"""
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class PlatePrivacyError(RuntimeError):
    """Raised when a safe publication copy cannot be verified or created."""


def _detect_license_plates(paths):
    # Lazy import keeps the pixelation helpers testable without API credentials or
    # importing the Gemini SDK. No external call is made at module import time.
    from app.ai.gemini import detect_license_plates

    return detect_license_plates(paths)


def _save_normalized_image(image, destination):
    extension = destination.suffix.lower()
    image = image.convert("RGB")
    if extension in {".jpg", ".jpeg"}:
        image.save(destination, format="JPEG", quality=92, optimize=True)
    elif extension == ".png":
        image.save(destination, format="PNG", optimize=True)
    elif extension == ".webp":
        image.save(destination, format="WEBP", quality=92, method=6)
    else:
        raise PlatePrivacyError("Formato de imagem não suportado pela proteção de placas.")


def _normalized_copy(source):
    source = Path(source)
    if source.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        raise PlatePrivacyError(
            "A proteção automática de placas funciona apenas com JPG, JPEG, PNG ou WEBP."
        )
    destination = source.with_name(f"{source.stem}_placa_protegida{source.suffix.lower()}")
    try:
        with Image.open(source) as opened:
            normalized = ImageOps.exif_transpose(opened)
            _save_normalized_image(normalized, destination)
    except PlatePrivacyError:
        raise
    except Exception as exc:
        raise PlatePrivacyError(
            "Não foi possível preparar uma cópia segura de uma das fotos. Os originais foram preservados."
        ) from exc
    return destination


def _pixel_box(box, width, height):
    if not isinstance(box, dict):
        raise PlatePrivacyError("O detector retornou uma região de placa inválida.")
    try:
        x1, y1, x2, y2 = (float(box[key]) for key in ("x1", "y1", "x2", "y2"))
    except (KeyError, TypeError, ValueError) as exc:
        raise PlatePrivacyError("O detector retornou coordenadas de placa incompletas.") from exc
    if any(value < 0 or value > 1000 for value in (x1, y1, x2, y2)):
        raise PlatePrivacyError("O detector retornou coordenadas fora dos limites da imagem.")
    if x2 <= x1 or y2 <= y1:
        raise PlatePrivacyError("O detector retornou uma região de placa sem área.")

    left, top = round(x1 * width / 1000), round(y1 * height / 1000)
    right, bottom = round(x2 * width / 1000), round(y2 * height / 1000)
    pad_x = max(8, round(max(1, right - left) * 0.20))
    pad_y = max(6, round(max(1, bottom - top) * 0.35))
    left = max(0, left - pad_x)
    top = max(0, top - pad_y)
    right = min(width, right + pad_x)
    bottom = min(height, bottom + pad_y)
    if right <= left or bottom <= top:
        raise PlatePrivacyError("A região da placa ficou inválida após validar as coordenadas.")
    return left, top, right, bottom


def _blur_region(image, box):
    left, top, right, bottom = box
    region = image.crop((left, top, right, bottom))
    # Reduce to very few pixels and then apply a strong blur. Padding is added by
    # _pixel_box so borders and plate frames are covered too.
    small_width = max(2, min(12, region.width // 10))
    small_height = max(2, min(6, region.height // 8))
    pixelated = region.resize((small_width, small_height), Image.Resampling.BOX)
    pixelated = pixelated.resize(region.size, Image.Resampling.NEAREST)
    pixelated = pixelated.filter(
        ImageFilter.GaussianBlur(radius=max(8, min(region.size) * 0.18))
    )
    image.paste(pixelated, (left, top))


def create_plate_safe_copies(source_paths):
    """Return separate normalized copies with plate regions obscured and a summary."""
    source_paths = [Path(path) for path in source_paths]
    if not source_paths:
        raise PlatePrivacyError("Nenhuma foto foi recebida para proteger as placas.")

    safe_paths = []
    try:
        # Normalize EXIF orientation before detection so coordinates map to the
        # exact copy that will be blurred. Source/original files remain untouched.
        for source in source_paths:
            safe_paths.append(_normalized_copy(source))

        detections = _detect_license_plates(safe_paths)
        if not isinstance(detections, list) or len(detections) != len(safe_paths):
            raise PlatePrivacyError("Não foi possível verificar todas as fotos para placas.")

        total_plates = 0
        images_with_plates = 0
        manual_review_required = False
        for path, detection in zip(safe_paths, detections):
            if not isinstance(detection, dict) or not isinstance(detection.get("plates"), list):
                raise PlatePrivacyError("O detector não retornou um resultado válido para cada foto.")
            plates = detection["plates"]
            needs_review = detection.get("needs_review")
            if not isinstance(needs_review, bool):
                raise PlatePrivacyError("O detector não confirmou se a foto exige revisão.")
            if needs_review and not plates:
                raise PlatePrivacyError(
                    "Não foi possível verificar uma foto com segurança. Nenhum post foi criado. "
                    "Tente uma foto mais nítida ou com a placa mais visível."
                )

            if plates:
                with Image.open(path) as opened:
                    image = opened.convert("RGB")
                for plate in plates:
                    _blur_region(image, _pixel_box(plate, image.width, image.height))
                _save_normalized_image(image, path)
                total_plates += len(plates)
                images_with_plates += 1
            manual_review_required = manual_review_required or needs_review

        if total_plates:
            message = (
                f"Foram detectadas e borradas {total_plates} placa(s) em "
                f"{images_with_plates} foto(s). Revise todas as prévias: a detecção automática pode falhar."
            )
        else:
            message = (
                "Nenhuma placa foi detectada automaticamente. Revise todas as prévias, "
                "pois a detecção automática pode falhar."
            )
        if manual_review_required:
            message += " A IA sinalizou uma ou mais imagens para conferência humana."

        return safe_paths, {
            "plate_redaction_version": 1,
            "plate_redaction_count": total_plates,
            "plate_redaction_image_count": len(safe_paths),
            "plate_redaction_review_required": manual_review_required,
            "plate_redaction_message": message,
        }
    except Exception as exc:
        for path in safe_paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        if isinstance(exc, PlatePrivacyError):
            raise
        raise PlatePrivacyError(
            "Não foi possível verificar as placas com segurança. As fotos originais foram preservadas "
            "e nenhum post foi criado. Tente novamente em instantes."
        ) from exc
