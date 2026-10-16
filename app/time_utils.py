"""Date/time formatting for the Sthetcar IA user interface."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


BRASILIA_TIMEZONE = ZoneInfo("America/Sao_Paulo")


def format_local_datetime(value):
    """Format timestamps in Brasília time without changing the stored value.

    Historical naive ISO timestamps were created on Render, whose system clock
    is UTC. Treat those values as UTC, and accept timezone-aware ISO strings too.
    """
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return raw.replace("T", " ")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BRASILIA_TIMEZONE).strftime("%d/%m/%Y às %H:%M:%S")
