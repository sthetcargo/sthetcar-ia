import os
from datetime import datetime, timezone

from app.supabase_client import supabase

STATE_TABLE = "app_state"
_USE_CLOUD = os.getenv("V33_USE_CLOUD_STATE", "1").strip().lower() not in {"0", "false", "no", "off"}
_REQUIRE_CLOUD = os.getenv("V33_REQUIRE_CLOUD_STATE", "1").strip().lower() in {"1", "true", "yes", "on"}
_SEED_MISSING_FROM_LOCAL = os.getenv("V33_SEED_MISSING_FROM_LOCAL", "0").strip().lower() in {"1", "true", "yes", "on"}
_TABLE_AVAILABLE = None
_LAST_ERROR = ""


def reset_cloud_state_probe():
    global _TABLE_AVAILABLE, _LAST_ERROR
    _TABLE_AVAILABLE = None
    _LAST_ERROR = ""


def cloud_state_status():
    available = _table_available() if _USE_CLOUD else False
    return {
        "enabled": _USE_CLOUD,
        "required": _REQUIRE_CLOUD,
        "table_available": available,
        "last_error": _LAST_ERROR,
    }


def _table_available():
    global _TABLE_AVAILABLE, _LAST_ERROR
    if not _USE_CLOUD:
        return False
    # Do not permanently cache a failed connectivity probe. A transient
    # Supabase/network failure must be rechecked on the next operation.
    if _TABLE_AVAILABLE is True:
        return True
    try:
        supabase.table(STATE_TABLE).select("key").limit(1).execute()
        _TABLE_AVAILABLE = True
        _LAST_ERROR = ""
        return True
    except Exception as exc:
        _TABLE_AVAILABLE = False
        _LAST_ERROR = str(exc)
        return False


def load_cloud_state(key):
    global _TABLE_AVAILABLE, _LAST_ERROR
    if not _table_available():
        if _REQUIRE_CLOUD:
            raise RuntimeError(f"Supabase app_state indisponível: {_LAST_ERROR or 'tabela não acessível'}")
        return None
    try:
        response = (
            supabase.table(STATE_TABLE)
            .select("data")
            .eq("key", key)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if rows:
            return rows[0].get("data")
        return None
    except Exception as exc:
        _TABLE_AVAILABLE = False
        _LAST_ERROR = str(exc)
        if _REQUIRE_CLOUD:
            raise RuntimeError(f"Falha ao ler app_state no Supabase: {exc}") from exc
        return None


def save_cloud_state(key, data):
    global _TABLE_AVAILABLE, _LAST_ERROR
    if not _table_available():
        if _REQUIRE_CLOUD:
            raise RuntimeError(f"Supabase app_state indisponível: {_LAST_ERROR or 'tabela não acessível'}")
        return False
    try:
        supabase.table(STATE_TABLE).upsert(
            {
                "key": key,
                "data": data,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="key",
        ).execute()
        _set_probe_ok()
        return True
    except Exception as exc:
        _TABLE_AVAILABLE = False
        _LAST_ERROR = str(exc)
        if _REQUIRE_CLOUD:
            raise RuntimeError(f"Falha ao salvar app_state no Supabase: {exc}") from exc
        return False


def _set_probe_ok():
    global _TABLE_AVAILABLE, _LAST_ERROR
    _TABLE_AVAILABLE = True
    _LAST_ERROR = ""
