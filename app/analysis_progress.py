"""File-backed progress snapshots for long-running photo-analysis requests.

Progress contains only operational status and never stores image data, captions,
access tokens, or other publication content. Atomic replacement lets concurrent
Gunicorn threads read a consistent snapshot while the synchronous analysis route
continues working.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time

_REQUEST_ID = re.compile(r"^[A-F0-9]{16,64}$")
_ALLOWED_STATUS = {"processing", "completed", "error"}


class AnalysisProgressStore:
    def __init__(self, directory, stale_after_seconds=45):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.stale_after_seconds = max(10, int(stale_after_seconds))
        self._lock = threading.RLock()

    @staticmethod
    def valid_request_id(request_id):
        value = str(request_id or "").strip().upper()
        return value if _REQUEST_ID.fullmatch(value) else ""

    def _path(self, request_id):
        safe_id = self.valid_request_id(request_id)
        if not safe_id:
            raise ValueError("Identificador de progresso inválido.")
        return self.directory / f"{safe_id}.json"

    def update(
        self,
        request_id,
        *,
        status="processing",
        stage="",
        message="",
        percent=None,
        completed_items=None,
        total_items=None,
        now_epoch=None,
    ):
        if status not in _ALLOWED_STATUS:
            raise ValueError("Status de progresso inválido.")
        path = self._path(request_id)
        now = float(time.time() if now_epoch is None else now_epoch)
        previous = self.read(request_id) or {}
        payload = {
            "request_id": self.valid_request_id(request_id),
            "status": status,
            "stage": str(stage or "")[:80],
            "message": str(message or "")[:500],
            "percent": max(0, min(100, int(percent))) if percent is not None else None,
            "completed_items": max(0, int(completed_items)) if completed_items is not None else None,
            "total_items": max(0, int(total_items)) if total_items is not None else None,
            "started_at": previous.get("started_at") or datetime.fromtimestamp(
                now, timezone.utc
            ).isoformat(timespec="seconds"),
            "updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
            "updated_epoch": now,
        }
        temporary = path.with_suffix(f".{threading.get_ident()}.tmp")
        with self._lock:
            try:
                with temporary.open("w", encoding="utf-8") as stream:
                    json.dump(payload, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return payload

    def read(self, request_id, now_epoch=None):
        try:
            path = self._path(request_id)
        except ValueError:
            return None
        with self._lock:
            try:
                with path.open("r", encoding="utf-8") as stream:
                    payload = json.load(stream)
            except (OSError, json.JSONDecodeError):
                return None
        if not isinstance(payload, dict):
            return None
        now = float(time.time() if now_epoch is None else now_epoch)
        try:
            age = max(0, int(now - float(payload.get("updated_epoch", now))))
        except (TypeError, ValueError):
            age = 0
        payload["age_seconds"] = age
        payload["stale"] = payload.get("status") == "processing" and age >= self.stale_after_seconds
        return payload
