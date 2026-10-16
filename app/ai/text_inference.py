"""Conservative vehicle and service inference from visual-analysis text.

Keep these helpers independent of Flask, Supabase, and Gemini so they can be
unit-tested without credentials or network calls.
"""
import re


def infer_vehicle(text: str):
    """Infer a vehicle/model from explicit phrases in the AI analysis.

    The AI may write model names in lowercase, inside a markdown section, or
    after words such as "picape". Keep the inference conservative.
    """
    import re

    text = text or ""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ""

    # Prefer the dedicated section produced by the vision prompt.
    match = re.search(
        r"VE[IÍ]CULO\s+IDENTIFICADO\s*:\s*([^\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        value = match.group(1).strip(" .,:;()-")
        invalid_values = {
            "não identificado", "nao identificado", "não informado", "nao informado",
            "não foi possível determinar", "nao foi possivel determinar",
            "posicionada sobre uma", "posicionado sobre uma", "não foi identificada",
            "nao foi identificada", "não é possível identificar", "nao e possivel identificar",
        }
        value_key = re.sub(r"\s+", " ", value.casefold()).strip()
        # Never turn a fragment of the generated prose into the vehicle name.
        if value and value_key not in invalid_values and len(value.split()) <= 5:
            generic_starts = ("posicion", "localiz", "apresent", "observ", "mostr", "está", "esta ")
            if not value_key.startswith(generic_starts):
                return value

    # Common explicit model codes/names that are safe to recognize when the
    # analysis clearly refers to the vehicle.
    known_models = [
        (r"\bs[- ]?10\b", "S-10"),
        (r"\branger\b", "Ranger"),
        (r"\bhilux\b", "Hilux"),
        (r"\bt[- ]?cross\b", "T-Cross"),
        (r"\btracker\b", "Tracker"),
        (r"\bcorolla\b", "Corolla"),
        (r"\bcivic\b", "Civic"),
        (r"\bcompass\b", "Compass"),
        (r"\bhr[- ]?v\b", "HR-V"),
        (r"\bcreta\b", "Creta"),
        (r"\bonix\b", "Onix"),
        (r"\bkwid\b", "Kwid"),
        (r"\bargo\b", "Argo"),
    ]
    for pattern, label in known_models:
        if re.search(pattern, normalized, re.IGNORECASE):
            return label

    # Finally, capture a short phrase immediately following an explicit
    # vehicle type, e.g. "picape S-10" or "veículo Corolla".
    match = re.search(
        r"(?:ve[ií]culo|picape|pickup|carro|autom[oó]vel|caminhonete)\s+(?:da\s+marca\s+)?([A-Za-z0-9][A-Za-z0-9-]*(?:\s+[A-Za-z0-9][A-Za-z0-9-]*){0,2})",
        normalized,
        flags=re.IGNORECASE,
    )
    if match:
        value = match.group(1).strip(" .,:;()")
        # Avoid swallowing generic prose instead of a model.
        stop = {"passou", "apresenta", "foi", "está", "esta", "tem", "com", "por"}
        first = value.split()[0].lower() if value else ""
        if value and first not in stop and len(value) <= 40:
            return value

    return ""

def infer_service(text: str):
    """Infer a conservative service label from the AI analysis when blank."""
    text = (text or "").lower()
    candidates = [
        ("Lavagem e Higienização", ("lavagem", "higienização")),
        ("Lavagem e Higienização", ("lavagem", "limpeza interna")),
        ("Higienização Interna", ("higienização interna",)),
        ("Lavagem Externa", ("lavagem externa",)),
        ("Polimento", ("polimento",)),
        ("Detalhamento Automotivo", ("detalhamento",)),
    ]
    for label, terms in candidates:
        if all(term in text for term in terms):
            return label
    return ""
