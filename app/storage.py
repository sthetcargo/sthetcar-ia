from pathlib import Path
import mimetypes

from app.supabase_client import supabase

BUCKET = "sthetcar-fotos"
REMOTE_PREFIX = "supabase://"


def make_remote_ref(remote_path):
    remote_path = str(remote_path or "").lstrip("/")
    return f"{REMOTE_PREFIX}{remote_path}" if remote_path else ""


def is_remote_ref(value):
    return str(value or "").startswith(REMOTE_PREFIX)


def remote_path_from_ref(value):
    raw = str(value or "")
    if not is_remote_ref(raw):
        return ""
    return raw[len(REMOTE_PREFIX):].lstrip("/")


def upload_file(local_path, remote_path):
    local_path = Path(local_path)
    content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"

    with open(local_path, "rb") as f:
        return supabase.storage.from_(BUCKET).upload(
            str(remote_path).lstrip("/"),
            f,
            {
                "upsert": "true",
                "content-type": content_type,
                "cache-control": "3600",
            },
        )


def delete_file(remote_path):
    return supabase.storage.from_(BUCKET).remove([str(remote_path).lstrip("/")])


def create_signed_url(remote_path, expires_in=3600):
    return supabase.storage.from_(BUCKET).create_signed_url(
        str(remote_path).lstrip("/"),
        expires_in,
    )


def signed_url(remote_path, expires_in=3600):
    result = create_signed_url(remote_path, expires_in)
    return result.get("signedURL") or result.get("signed_url") or ""
