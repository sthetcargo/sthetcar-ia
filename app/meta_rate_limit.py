"""Safe detection and backoff helpers for Meta Graph API rate limits.

This module is intentionally pure: it does not call Meta, access credentials,
or change publication records.
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import time
from zoneinfo import ZoneInfo

DEFAULT_COOLDOWN_SECONDS = 60 * 60
MIN_COOLDOWN_SECONDS = 60
MAX_COOLDOWN_SECONDS = 24 * 60 * 60
SUPPORTED_RATE_LIMIT_CODES = {4, 17, 32, 341, 613, 80001, 80002, 80003, 80004, 80005, 80006, 80008}


def _error_object(payload):
    if not isinstance(payload, dict):
        return {}
    error = payload.get("error", payload)
    return error if isinstance(error, dict) else {}


def is_meta_rate_limit_error(payload):
    """Recognize explicit Meta rate-limit responses, not generic 403 errors."""
    error = _error_object(payload)
    message = str(error.get("message") or "").strip().lower()
    try:
        code = int(error.get("code"))
    except (TypeError, ValueError):
        code = None

    if "application request limit reached" in message:
        return True
    if code in SUPPORTED_RATE_LIMIT_CODES and any(
        phrase in message for phrase in ("rate limit", "too many", "limit reached", "limit has been")
    ):
        return True
    return False


def _get_header(headers, name):
    if headers is None:
        return ""
    try:
        value = headers.get(name)
        if value is not None:
            return str(value).strip()
    except Exception:
        pass
    wanted = name.casefold()
    try:
        for key, value in headers.items():
            if str(key).casefold() == wanted:
                return str(value).strip()
    except Exception:
        pass
    return ""


def _retry_after_seconds(headers, now_epoch):
    raw = _get_header(headers, "Retry-After")
    if not raw:
        return None
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        pass
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int(parsed.timestamp() - now_epoch))
    except (TypeError, ValueError, OverflowError):
        return None


def _business_usage_wait_seconds(headers):
    raw = _get_header(headers, "X-Business-Use-Case-Usage")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    estimates = []

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).casefold() == "estimated_time_to_regain_access":
                    try:
                        estimates.append(max(0, int(float(item)) * 60))
                    except (TypeError, ValueError):
                        pass
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return max(estimates) if estimates else None


def make_rate_limit_state(payload, headers=None, now_epoch=None, fallback_seconds=DEFAULT_COOLDOWN_SECONDS):
    """Build non-secret cooldown metadata from a Meta rate-limit error."""
    if not is_meta_rate_limit_error(payload):
        raise ValueError("Payload não representa uma limitação de volume da Meta.")

    now = float(time.time() if now_epoch is None else now_epoch)
    wait = _retry_after_seconds(headers, now)
    source = "retry-after"
    if wait is None:
        wait = _business_usage_wait_seconds(headers)
        source = "business-use-case-usage"
    if wait is None:
        wait = fallback_seconds
        source = "conservative-fallback"

    wait = min(MAX_COOLDOWN_SECONDS, max(MIN_COOLDOWN_SECONDS, int(wait)))
    error = _error_object(payload)
    try:
        code = int(error.get("code"))
    except (TypeError, ValueError):
        code = None
    subcode = error.get("error_subcode")
    fbtrace_id = str(error.get("fbtrace_id") or "").strip()[:100]

    return {
        "blocked_until": int(now + wait),
        "observed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "cooldown_seconds": wait,
        "cooldown_source": source,
        "error_code": code,
        "error_subcode": str(subcode) if subcode is not None else "",
        "fbtrace_id": fbtrace_id,
        "message": "Application request limit reached",
    }


def seconds_remaining(state, now_epoch=None):
    if not isinstance(state, dict):
        return 0
    try:
        blocked_until = int(state.get("blocked_until") or 0)
    except (TypeError, ValueError):
        return 0
    now = int(time.time() if now_epoch is None else now_epoch)
    return max(0, blocked_until - now)


def format_rate_limit_message(state, now_epoch=None):
    now = int(time.time() if now_epoch is None else now_epoch)
    remaining = seconds_remaining(state, now)
    if remaining <= 0:
        return ""
    blocked_until = int(state.get("blocked_until", now + remaining))
    local_until = datetime.fromtimestamp(
        blocked_until, ZoneInfo("America/Sao_Paulo")
    )
    minutes = max(1, (remaining + 59) // 60)
    return (
        "A Meta limitou temporariamente as chamadas do Sthetcar IA. "
        f"Por segurança, o aplicativo suspendeu novas chamadas do Instagram por cerca de {minutes} minuto(s), "
        f"até aproximadamente {local_until:%H:%M} (horário de Brasília). "
        "Esse horário é estimado: a Meta pode liberar antes ou manter o limite por mais tempo. "
        "Não repita a tentativa durante a espera. Nenhuma nova publicação foi feita por esta tentativa."
    )
