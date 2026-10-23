"""Public-facing labels for related Sthetcar social posts.

The original protected media stays intact. The optional visual label is rendered
into a separate derivative used only as the first carousel item.
"""
from io import BytesIO
import re


def build_series_title(vehicle):
    vehicle = " ".join(str(vehicle or "").split()).strip()
    return f"Transformação completa: {vehicle}" if vehicle else "Transformação completa do veículo"


def _strip_prior_series_copy(value):
    text = str(value or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    while lines and (
        lines[0].strip().startswith("🚗 Transformação completa:")
        or lines[0].strip().startswith("🚗 Transformação completa do veículo")
    ):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    cleaned = []
    for line in lines:
        value = line.strip()
        if re.match(r"^(➡️\s*)?(Na próxima publicação:|Continue na próxima publicação:)", value, re.I):
            continue
        if re.match(r"^✅\s*Esta é a última parte desta transformação", value, re.I):
            continue
        if re.match(r"^Parte \d+ de \d+ desta transformação\.?$", value, re.I):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def build_series_caption(
    caption,
    vehicle,
    service,
    part_number,
    part_count,
    next_service=None,
    hashtags="",
    max_chars=2200,
    series_title=None,
):
    """Add a consistent series heading and useful continuation CTA.

    Hashtags are stored in a separate field by Sthetcar; their length is reserved
    here so the combined caption is less likely to exceed Instagram's limit.
    """
    if not isinstance(part_number, int) or not isinstance(part_count, int):
        raise ValueError("Part numbers must be integers")
    if part_count < 2 or part_number < 1 or part_number > part_count:
        raise ValueError("Series part must be between 1 and part_count")

    vehicle_text = " ".join(str(vehicle or "").split()).strip()
    service_text = " ".join(str(service or "").split()).strip()
    if not service_text:
        service_text = "serviço automotivo"
    title = " ".join(str(series_title or build_series_title(vehicle_text)).split()).strip()
    heading = f"🚗 {title} — Parte {part_number}/{part_count}: {service_text}"

    next_service_text = " ".join(str(next_service or "").split()).strip()
    if part_number < part_count:
        if next_service_text and next_service_text.casefold() != service_text.casefold():
            footer = f"➡️ Na próxima publicação: {next_service_text}."
        elif next_service_text:
            footer = (
                f"➡️ Continue na próxima publicação: Parte {part_number + 1}/{part_count} "
                f"de {service_text}."
            )
        else:
            footer = (
                f"➡️ Esta transformação continua na próxima publicação, "
                f"Parte {part_number + 1}/{part_count}."
            )
    else:
        footer = "✅ Esta é a última parte desta transformação. Obrigado por acompanhar o trabalho da Sthetcar!"

    body = _strip_prior_series_copy(caption)
    reserved = len(str(hashtags or "").strip()) + (2 if str(hashtags or "").strip() else 0)
    caption_budget = max(1, int(max_chars) - reserved)
    fixed_length = len(heading) + len(footer) + 4
    body_budget = max(0, caption_budget - fixed_length)
    if len(body) > body_budget:
        if body_budget <= 1:
            body = "…" if body_budget else ""
        else:
            trimmed = body[: body_budget - 1]
            if " " in trimmed:
                trimmed = trimmed.rsplit(" ", 1)[0]
            body = trimmed.rstrip() + "…"

    sections = [heading]
    if body:
        sections.append(body)
    sections.append(footer)
    result = "\n\n".join(sections)
    if len(result) > caption_budget:
        # Defensive fallback for very small budgets or unusually long vehicle names.
        result = result[:caption_budget].rstrip()
    return result


def _load_font(size):
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_series_badge_image(image_bytes, vehicle, service, part_number, part_count, series_title=None):
    """Create a new labeled image by adding a banner above the full original photo.

    No part of the source photo is cropped or painted over. The caller stores this
    derivative separately and leaves the original protected image untouched.
    """
    from PIL import Image, ImageDraw

    if not isinstance(part_number, int) or not isinstance(part_count, int):
        raise ValueError("Part numbers must be integers")
    if part_count < 2 or part_number < 1 or part_number > part_count:
        raise ValueError("Series part must be between 1 and part_count")

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.load()
            photo = source.convert("RGB")
    except Exception as exc:
        raise ValueError("Não foi possível abrir a foto para colocar o selo da sequência.") from exc

    width, height = photo.size
    if width < 1 or height < 1:
        raise ValueError("A imagem da sequência está vazia.")

    # Keep the added strip small. For the square normalized Instagram photos this
    # makes the overall aspect ratio about 0.9, within Instagram's portrait bounds.
    max_band_for_square_ratio = max(0, int(width / 0.8 - height))
    if max_band_for_square_ratio < 24:
        raise ValueError("A proporção desta foto não permite adicionar o selo com segurança.")
    band_height = min(max(48, int(width * 0.12)), max_band_for_square_ratio)

    title = " ".join(str(series_title or build_series_title(vehicle)).split()).strip().upper()
    service_text = " ".join(str(service or "Serviço automotivo").split()).strip().upper()
    subtitle = f"PARTE {part_number}/{part_count} · {service_text}"

    canvas = Image.new("RGB", (width, height + band_height), (20, 24, 32))
    canvas.paste(photo, (0, band_height))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, width, band_height - 1), fill=(20, 24, 32))
    draw.rectangle((0, band_height - 4, width, band_height - 1), fill=(49, 91, 234))

    padding = max(8, int(width * 0.025))
    available_width = max(1, width - padding * 2)

    def fit_font(text, target_size):
        size = max(9, target_size)
        while size > 8:
            font = _load_font(size)
            box = draw.textbbox((0, 0), text, font=font)
            if box[2] - box[0] <= available_width:
                return font
            size -= 1
        return _load_font(8)

    title_font = fit_font(title, max(10, int(width * 0.034)))
    subtitle_font = fit_font(subtitle, max(9, int(width * 0.029)))
    title_y = max(4, int(band_height * 0.14))
    subtitle_y = max(title_y + 18, int(band_height * 0.54))
    draw.text((padding, title_y), title, font=title_font, fill=(255, 255, 255))
    draw.text((padding, subtitle_y), subtitle, font=subtitle_font, fill=(220, 230, 255))

    output = BytesIO()
    canvas.save(output, format="JPEG", quality=92, optimize=True)
    return output.getvalue()
