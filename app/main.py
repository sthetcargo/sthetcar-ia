from pathlib import Path
from datetime import datetime, timedelta
from app.time_utils import format_local_datetime
from app.publication_split import split_photo_collections
from app.photo_categories import group_photo_indexes, normalize_photo_categories
from app.series_publication import build_series_badge_image, build_series_caption, build_series_title
import copy
import difflib
import json
import hashlib
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
import urllib.error

from flask import Flask, render_template, request, url_for, redirect, session, abort
from werkzeug.utils import secure_filename

from app.ai.vision import analyze_before_after
from app.ai.plate_privacy import create_plate_safe_copies
from app.ai.copywriter import generate_post
from app.ai.gemini import rewrite_caption
from app.ai.text_inference import infer_service, infer_vehicle
from app.storage import (
    upload_file, delete_file, signed_url,
    make_remote_ref, is_remote_ref, remote_path_from_ref,
)
from app.cloud_state import load_cloud_state, save_cloud_state, cloud_state_status

BASE_DIR = Path(__file__).resolve().parent.parent
BEFORE_DIR = BASE_DIR / "uploads" / "before"
AFTER_DIR = BASE_DIR / "uploads" / "after"
GENERATED_DIR = BASE_DIR / "generated"
HISTORY_PATH = GENERATED_DIR / "historico_posts_aprovados.json"
META_TOKEN_PATH = GENERATED_DIR / "instagram_connection.json"
PUBLICATION_DRAFT_PATH = GENERATED_DIR / "instagram_publication_draft.json"
APPROVAL_QUEUE_PATH = GENERATED_DIR / "fila_aprovacao.json"
DISCARDED_PATH = GENERATED_DIR / "posts_descartados.json"
PUBLICATION_DISCARDED_PATH = GENERATED_DIR / "posts_publicacao_descartados.json"
FACEBOOK_CONNECTION_PATH = GENERATED_DIR / "facebook_connection.json"
FACEBOOK_PUBLICATION_AUDIT_FILE = GENERATED_DIR / "publicacoes_facebook.json"

for folder in (BEFORE_DIR, AFTER_DIR, GENERATED_DIR):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.add_template_filter(format_local_datetime, "local_datetime")

PUBLICATION_AUDIT_FILE = BASE_DIR / "generated" / "publicacoes_instagram.json"

def load_publication_audit():
    data = _load_state("publication_audit", PUBLICATION_AUDIT_FILE, [])
    return data if isinstance(data, list) else []

def save_publication_audit(items):
    _save_state("publication_audit", PUBLICATION_AUDIT_FILE, items)


def load_facebook_publication_audit():
    data = _load_state("facebook_publication_audit", FACEBOOK_PUBLICATION_AUDIT_FILE, [])
    return data if isinstance(data, list) else []


def save_facebook_publication_audit(items):
    _save_state("facebook_publication_audit", FACEBOOK_PUBLICATION_AUDIT_FILE, items)


def publication_key(post):
    """Stable local key for one approved post, independent of history index."""
    return str(post.get("approved_at", "")).strip()


def find_publication_record(post, audit=None, username=""):
    """Find a prior Instagram publication using approved_at, not list position."""
    pub = post.get("instagram_publication") or {}
    if str(pub.get("status", "")).upper() == "PUBLICADO":
        return pub

    key = publication_key(post)
    if not key:
        return {}
    audit = audit if audit is not None else load_publication_audit()
    for record in audit:
        if str(record.get("approved_at", "")).strip() != key:
            continue
        record_user = str(record.get("username", "")).strip()
        if username and record_user and record_user != username:
            continue
        if str(record.get("status", "")).upper() == "PUBLICADO":
            return record
    return {}


def reconcile_publication_records(history, audit=None):
    """Make the local history and publication audit agree before rendering any queue."""
    audit = audit if audit is not None else load_publication_audit()
    changed = False
    for item in history:
        record = find_publication_record(item, audit)
        if record and item.get("instagram_publication") != record:
            item["instagram_publication"] = record
            item["status"] = "PUBLICADO NO INSTAGRAM"
            changed = True
    if changed:
        save_history(history)
    return history

app.secret_key = os.getenv("FLASK_SECRET_KEY", "").strip()
AUTH_ENABLED = True  # Autenticacao obrigatoria em qualquer ambiente.
AUTH_USERNAME = os.getenv("STHETCAR_ADMIN_USERNAME", "admin").strip() or "admin"
AUTH_PASSWORD = os.getenv("STHETCAR_ADMIN_PASSWORD", "").strip()

if AUTH_ENABLED and not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY deve ser configurado quando a autenticação estiver habilitada.")
if AUTH_ENABLED and not AUTH_PASSWORD:
    raise RuntimeError("STHETCAR_ADMIN_PASSWORD deve ser configurado quando a autenticação estiver habilitada.")


def _csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _is_authenticated():
    return (not AUTH_ENABLED) or bool(session.get("authenticated"))


@app.context_processor
def inject_security_context():
    return {"csrf_token": _csrf_token, "auth_enabled": AUTH_ENABLED, "authenticated": _is_authenticated()}


@app.before_request
def enforce_auth_and_csrf():
    endpoint = request.endpoint or ""
    public_endpoints = {"login", "health", "static"}
    if endpoint in public_endpoints:
        return None
    if AUTH_ENABLED and not session.get("authenticated"):
        return redirect(url_for("login", next=request.full_path))
    if request.method == "POST" and AUTH_ENABLED:
        supplied = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not supplied or not expected or not secrets.compare_digest(supplied, expected):
            abort(400, description="Token de segurança inválido ou ausente. Atualize a página e tente novamente.")
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if not AUTH_ENABLED:
        return redirect(url_for("dashboard"))
    if session.get("authenticated"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if secrets.compare_digest(username, AUTH_USERNAME) and secrets.compare_digest(password, AUTH_PASSWORD):
            session.clear()
            session["authenticated"] = True
            session["username"] = AUTH_USERNAME
            session["csrf_token"] = secrets.token_urlsafe(32)
            next_url = request.args.get("next") or request.form.get("next") or url_for("dashboard")
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = url_for("dashboard")
            return redirect(next_url)
        error = "Usuário ou senha inválidos."
    return render_template("login.html", error=error, next=request.args.get("next", ""))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login")) if AUTH_ENABLED else redirect(url_for("dashboard"))

COOKIE_SECURE = os.getenv("STHETCAR_COOKIE_SECURE", "1").strip().lower() in {"1", "true", "yes", "on"}
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
)

app.config["MAX_CONTENT_LENGTH"] = 35 * 1024 * 1024
MAX_TOTAL_UPLOAD_BYTES = 30 * 1024 * 1024
ALLOWED = {"jpg", "jpeg", "png", "webp", "mp4", "mov"}
VIDEO_EXTENSIONS = {"mp4", "mov"}
MAX_VIDEO_BYTES = 20 * 1024 * 1024


STATE_FILES = {
    "history": HISTORY_PATH,
    "approval_queue": APPROVAL_QUEUE_PATH,
    "instagram_connection": META_TOKEN_PATH,
    "facebook_connection": FACEBOOK_CONNECTION_PATH,
    "facebook_publication_audit": FACEBOOK_PUBLICATION_AUDIT_FILE,
    "publication_draft": PUBLICATION_DRAFT_PATH,
    "discarded_posts": DISCARDED_PATH,
    "publication_discarded": PUBLICATION_DISCARDED_PATH,
    "publication_audit": PUBLICATION_AUDIT_FILE,
    "last_approved": GENERATED_DIR / "ultimo_post_aprovado.json",
}

# Optional state keys may legitimately be absent in an existing production
# database. They get an in-memory default and are created in Supabase only
# when the application actually needs to save them. They never resurrect an
# old local JSON snapshot automatically.
OPTIONAL_STATE_DEFAULTS = {
    "discarded_posts": [],
    # New Facebook keys are optional until the owner connects the Page.
    "facebook_connection": {},
    "facebook_publication_audit": [],
}


def _read_local_json(path, default):
    if not Path(path).exists():
        return default
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value
    except (json.JSONDecodeError, OSError):
        return default


def _write_local_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_state(key, path, default):
    cloud_value = load_cloud_state(key)
    if cloud_value is not None:
        # Keep a local cache for diagnostics/recovery, but never use it as the
        # source of truth when cloud state exists.
        _write_local_json(path, cloud_value)
        return cloud_value

    # In production/strict-cloud mode, a missing key is not permission to
    # resurrect an old local snapshot. Local -> cloud seeding must be an
    # explicit migration action.
    seed_missing = os.getenv("V33_SEED_MISSING_FROM_LOCAL", "0").strip().lower() in {"1", "true", "yes", "on"}
    if not seed_missing:
        # Some state keys are optional and were introduced after the original
        # production schema. Return their safe default without writing it to
        # Supabase and, importantly, without reviving an old local snapshot.
        if key in OPTIONAL_STATE_DEFAULTS:
            return OPTIONAL_STATE_DEFAULTS[key]
        if os.getenv("V33_REQUIRE_CLOUD_STATE", "1").strip().lower() in {"1", "true", "yes", "on"}:
            raise RuntimeError(
                f"Estado '{key}' não existe no Supabase app_state. "
                "Não será usado um JSON local automaticamente. Execute a migração controlada se necessário."
            )
        return _read_local_json(path, default)

    local_value = _read_local_json(path, default)
    save_cloud_state(key, local_value)
    return local_value


def _save_state(key, path, data):
    # In strict online mode save_cloud_state raises instead of silently
    # diverging the local and cloud copies.
    save_cloud_state(key, data)
    _write_local_json(path, data)


def instagram_config():
    return {
        "app_id": os.getenv("META_APP_ID", "").strip(),
        "app_secret": os.getenv("META_APP_SECRET", "").strip(),
        "redirect_uri": os.getenv("INSTAGRAM_REDIRECT_URI", "http://127.0.0.1:8001/auth/instagram/callback").strip(),
        "api_version": os.getenv("META_GRAPH_VERSION", "v26.0").strip(),
    }


def facebook_config():
    """Separate Facebook Page OAuth config while reusing the existing Meta app."""
    ig_cfg = instagram_config()
    redirect_uri = os.getenv("FACEBOOK_REDIRECT_URI", "").strip()
    if not redirect_uri:
        ig_redirect = ig_cfg.get("redirect_uri", "")
        if "/auth/instagram/callback" in ig_redirect:
            redirect_uri = ig_redirect.replace("/auth/instagram/callback", "/auth/facebook/callback")
        else:
            public_base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
            redirect_uri = f"{public_base}/auth/facebook/callback" if public_base else "http://127.0.0.1:8001/auth/facebook/callback"
    return {
        "app_id": ig_cfg["app_id"],
        "app_secret": ig_cfg["app_secret"],
        "redirect_uri": redirect_uri,
        "api_version": os.getenv("META_GRAPH_VERSION", "v26.0").strip(),
        "page_id": os.getenv("FACEBOOK_PAGE_ID", "").strip(),
        "login_config_id": os.getenv("FACEBOOK_LOGIN_CONFIG_ID", "").strip(),
    }


def facebook_meta_ready():
    cfg = facebook_config()
    return bool(cfg["app_id"] and cfg["app_secret"] and cfg["redirect_uri"] and cfg["login_config_id"])


def load_facebook_connection():
    data = _load_state("facebook_connection", FACEBOOK_CONNECTION_PATH, {})
    return data if isinstance(data, dict) else {}


def save_facebook_connection(data):
    _save_state("facebook_connection", FACEBOOK_CONNECTION_PATH, data)


def load_instagram_connection():
    data = _load_state("instagram_connection", META_TOKEN_PATH, {})
    return data if isinstance(data, dict) else {}


def save_instagram_connection(data):
    _save_state("instagram_connection", META_TOKEN_PATH, data)



def load_publication_draft():
    data = _load_state("publication_draft", PUBLICATION_DRAFT_PATH, {})
    if not isinstance(data, dict):
        return {}
    repaired, changed = repair_media_collection([data])
    result = repaired[0] if repaired else {}
    if changed:
        save_publication_draft(result)
    return result


def save_publication_draft(data):
    _save_state("publication_draft", PUBLICATION_DRAFT_PATH, data)





def load_approval_queue():
    data = _load_state("approval_queue", APPROVAL_QUEUE_PATH, [])
    if not isinstance(data, list):
        return []
    repaired, changed = repair_media_collection(data)
    if changed:
        save_approval_queue(repaired)
    return repaired


def save_approval_queue(queue):
    _save_state("approval_queue", APPROVAL_QUEUE_PATH, queue)



def load_discarded_posts():
    data = _load_state("discarded_posts", DISCARDED_PATH, [])
    return data if isinstance(data, list) else []


def save_discarded_posts(items):
    _save_state("discarded_posts", DISCARDED_PATH, items)



def _find_local_image(value, side):
    if not value:
        return None
    raw = str(value).strip()
    parsed = urllib.parse.urlparse(raw)
    path_text = parsed.path if parsed.scheme else raw
    filename = Path(path_text).name
    if not filename:
        return None

    preferred = "before" if side == "before" else "after"
    roots = _image_search_roots()
    ordered = sorted(roots, key=lambda item: 0 if item[1] == preferred else 1)
    for root, root_side in ordered:
        candidate = root / filename
        if candidate.is_file():
            return candidate
    return None


def _remote_ref_for_local(path, side):
    path = Path(path)
    remote_side = "before" if side == "before" else "after"
    remote_path = f"{remote_side}/{path.name}"
    upload_file(path, remote_path)
    return make_remote_ref(remote_path)


def _create_series_badge_ref(image_ref, vehicle, service, part_number, part_count, series_title=None):
    """Upload a labeled derivative of the first BEFORE image without altering source media."""
    token = secrets.token_hex(12)
    remote_path = f"before/series_badges/{token}.jpg"
    temporary_path = GENERATED_DIR / f"series_badge_{token}.jpg"
    try:
        if is_remote_ref(image_ref):
            source_path = remote_path_from_ref(image_ref)
            source_url = signed_url(source_path, 900)
            if not source_url:
                raise RuntimeError("Não foi possível gerar um endereço temporário para a foto protegida.")
            with urllib.request.urlopen(source_url, timeout=20) as response:
                image_bytes = response.read(15 * 1024 * 1024 + 1)
            if len(image_bytes) > 15 * 1024 * 1024:
                raise RuntimeError("A foto protegida excede o limite seguro para criar o selo.")
        else:
            local_source = _find_local_image(image_ref, "before")
            if local_source is None:
                raise RuntimeError("A foto protegida não foi encontrada para criar o selo da sequência.")
            image_bytes = local_source.read_bytes()

        badge_bytes = build_series_badge_image(
            image_bytes, vehicle, service, part_number, part_count,
            series_title=series_title,
        )
        temporary_path.write_bytes(badge_bytes)
        upload_file(temporary_path, remote_path)
        return make_remote_ref(remote_path)
    except Exception as exc:
        app.logger.exception(
            "Falha ao criar selo visual da sequência: part=%s/%s type=%s",
            part_number, part_count, type(exc).__name__,
        )
        raise RuntimeError(
            "Não foi possível preparar o selo visual da sequência. "
            "Nenhuma publicação foi feita; tente novamente ou revise os logs."
        ) from exc
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def ensure_remote_image_ref(value, side):
    if not value:
        return ""
    raw = str(value).strip()
    if is_remote_ref(raw):
        return raw

    local = _find_local_image(raw, side)
    if local is not None:
        try:
            return _remote_ref_for_local(local, side)
        except Exception:
            return raw
    return raw


def media_url(value):
    if not value:
        return ""
    raw = str(value).strip()
    if is_remote_ref(raw):
        path = remote_path_from_ref(raw)
        try:
            return signed_url(path, 3600) or raw
        except Exception:
            return raw
    if raw.startswith("/uploads/"):
        side = "before" if raw.startswith("/uploads/before/") else "after"
        local = _find_local_image(raw, side)
        if local is not None:
            try:
                ref = _remote_ref_for_local(local, side)
                path = remote_path_from_ref(ref)
                return signed_url(path, 3600) or raw
            except Exception:
                return raw
    return raw



app.add_template_filter(media_url, "media_url")

def is_video_media(value):
    raw = str(value or "").split("?", 1)[0].lower()
    return any(raw.endswith("." + ext) for ext in VIDEO_EXTENSIONS)

app.add_template_filter(is_video_media, "is_video_media")


def repair_media_collection(items):
    changed = False
    repaired = []
    for item in items:
        if not isinstance(item, dict):
            repaired.append(item)
            continue
        current = dict(item)
        for side in ("before", "after"):
            plural = f"{side}_images"
            singular = f"{side}_image"
            values = current.get(plural) or []
            if not values and current.get(singular):
                values = [current.get(singular)]
            new_values = [ensure_remote_image_ref(v, side) for v in values if v]
            if new_values != values:
                current[plural] = new_values
                changed = True
            if new_values:
                if current.get(singular) != new_values[0]:
                    current[singular] = new_values[0]
                    changed = True
        repaired.append(current)
    return repaired, changed


def delete_pending_uploads(pending):
    """Delete files belonging only to an unapproved pending item from local and cloud storage."""
    for side in ("before", "after"):
        for value in (pending.get(f"{side}_images") or []):
            if not value:
                continue
            raw = str(value).strip()
            try:
                if is_remote_ref(raw):
                    delete_file(remote_path_from_ref(raw))
                    continue
                if raw.startswith("/uploads/"):
                    local = _find_local_image(raw, side)
                    if local and local.exists():
                        local.unlink()
            except Exception:
                pass



def new_pending_id():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + secrets.token_hex(3)


def build_publication_draft(post, post_index, connection):
    caption = (post.get("caption") or "").strip()
    hashtags = (post.get("hashtags") or "").strip()
    if not hashtags:
        hashtags = fallback_hashtags(post.get("vehicle", ""), post.get("service", ""))
    full_caption = f"{caption}\n\n{hashtags}".strip() if hashtags else caption
    return {
        "prepared_at": datetime.now().isoformat(timespec="seconds"),
        "status": "PREPARADO — não publicado",
        "post_index": post_index,
        "approved_at": post.get("approved_at", ""),
        "publication_key": publication_key(post),
        "post_vehicle": post.get("vehicle", ""),
        "post_service": post.get("service", ""),
        "instagram": {
            "user_id": connection.get("user_id", ""),
            "username": connection.get("username", ""),
        },
        "vehicle": post.get("vehicle", ""),
        "service": post.get("service", ""),
        "caption": caption,
        "hashtags": hashtags,
        "full_caption": full_caption,
        "before_images": post.get("before_images", []),
        "after_images": post.get("after_images", []),
        "note": "Rascunho local. Nenhuma chamada de publicação final foi executada.",
    }


def draft_matches_post(draft, post, post_index, connection):
    """Only reuse a draft when identity AND the effective approved content match."""
    if not draft or not post:
        return False
    expected_caption = (post.get("caption") or "").strip()
    expected_hashtags = (post.get("hashtags") or "").strip()
    if not expected_hashtags:
        expected_hashtags = fallback_hashtags(post.get("vehicle", ""), post.get("service", ""))
    return (
        str(draft.get("post_index", "")) == str(post_index)
        and draft.get("approved_at", "") == post.get("approved_at", "")
        and draft.get("instagram", {}).get("user_id", "") == connection.get("user_id", "")
        and (draft.get("caption") or "").strip() == expected_caption
        and (draft.get("hashtags") or "").strip() == expected_hashtags
        and draft.get("vehicle", "") == post.get("vehicle", "")
        and draft.get("service", "") == post.get("service", "")
    )


def meta_ready():
    cfg = instagram_config()
    return bool(cfg["app_id"] and cfg["app_secret"] and cfg["redirect_uri"])


def exchange_instagram_code(code):
    cfg = instagram_config()
    payload = urllib.parse.urlencode({
        "client_id": cfg["app_id"],
        "client_secret": cfg["app_secret"],
        "grant_type": "authorization_code",
        "redirect_uri": cfg["redirect_uri"],
        "code": code,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.instagram.com/oauth/access_token",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def extend_instagram_token(short_token):
    cfg = instagram_config()
    query = urllib.parse.urlencode({
        "grant_type": "ig_exchange_token",
        "client_secret": cfg["app_secret"],
        "access_token": short_token,
    })
    url = f"https://graph.instagram.com/access_token?{query}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def get_instagram_profile(access_token):
    cfg = instagram_config()
    query = urllib.parse.urlencode({
        "fields": "id,username,name,account_type",
        "access_token": access_token,
    })
    url = f"https://graph.instagram.com/{cfg['api_version']}/me?{query}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))




def _http_json_request(url, data=None, method="GET", timeout=60, api_label="Instagram API"):
    payload = None
    headers = {"Accept": "application/json", "User-Agent": "Sthetcar-IA/29.0"}
    if data is not None:
        payload = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=payload, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
        except json.JSONDecodeError:
            detail = {"raw": body}
        raise RuntimeError(f"{api_label} {exc.code}: {json.dumps(detail, ensure_ascii=False)}") from exc





def facebook_graph_request(path, access_token, params=None, data=None, method="GET", timeout=60):
    """Call the Facebook Graph API without returning credentials to the UI."""
    cfg = facebook_config()
    endpoint = f"https://graph.facebook.com/{cfg['api_version']}/{str(path).lstrip('/')}"
    try:
        if method.upper() == "GET":
            query = dict(params or {})
            query["access_token"] = access_token
            url = f"{endpoint}?{urllib.parse.urlencode(query)}"
            return _http_json_request(url, method="GET", timeout=timeout, api_label="Facebook Graph API")
        payload = dict(data or {})
        payload["access_token"] = access_token
        return _http_json_request(endpoint, data=payload, method=method.upper(), timeout=timeout, api_label="Facebook Graph API")
    except Exception as exc:
        raise RuntimeError(_safe_error_message(exc)) from exc


def facebook_media_urls(post):
    """Resolve approved photos to signed URLs; the Facebook flow deliberately excludes video."""
    facebook_post = post
    if post.get("facebook_before_images_override"):
        facebook_post = dict(post)
        facebook_post["before_images"] = list(post.get("facebook_before_images_override") or [])
        facebook_post["before_image"] = (
            facebook_post["before_images"][0] if facebook_post["before_images"] else ""
        )
    before = [x for x in (facebook_post.get("before_images") or [facebook_post.get("before_image")]) if x]
    after = [x for x in (facebook_post.get("after_images") or [facebook_post.get("after_image")]) if x]
    if len(before) + len(after) > 10:
        raise RuntimeError(
            f"Esta publicação tem {len(before) + len(after)} fotos. "
            "Divida-a em partes na Fila de Publicações antes de publicar no Facebook."
        )
    items = instagram_media_items(facebook_post)
    if any(str(item.get("media_type", "")).upper() != "IMAGE" for item in items):
        raise RuntimeError("A publicação no Facebook aceita somente fotos neste fluxo. O fluxo do Instagram permanece separado.")
    urls = [str(item.get("url", "")).strip() for item in items if str(item.get("url", "")).strip()]
    if not urls:
        raise RuntimeError("Nenhuma foto válida foi encontrada para publicação no Facebook.")
    return urls


def build_full_caption(post):
    caption = (post.get("facebook_caption_override") or post.get("caption") or "").strip()
    hashtags = (post.get("hashtags") or "").strip()
    if not hashtags:
        hashtags = fallback_hashtags(post.get("vehicle", ""), post.get("service", ""))
    return f"{caption}\n\n{hashtags}".strip() if hashtags else caption


def find_facebook_publication_record(post, audit=None, page_id=""):
    publication = post.get("facebook_publication") or {}
    if str(publication.get("status", "")).upper() == "PUBLICADO":
        if not page_id or not publication.get("page_id") or str(publication.get("page_id")) == str(page_id):
            return publication
    key = publication_key(post)
    if not key:
        return {}
    audit = audit if audit is not None else load_facebook_publication_audit()
    for record in audit:
        if str(record.get("approved_at", "")).strip() != key:
            continue
        if str(record.get("status", "")).upper() != "PUBLICADO":
            continue
        record_page = str(record.get("page_id", "")).strip()
        if page_id and record_page and record_page != str(page_id):
            continue
        return record
    return {}


def _safe_error_message(message):
    """Redact credentials if an upstream error ever echoes a token or secret."""
    value = str(message or "")
    value = re.sub(r"(?i)(access_token|fb_exchange_token|client_secret|token)\s*[=:]\s*[^&\s,}]+", r"\1=[REDACTED]", value)
    value = re.sub(r"EAA[A-Za-z0-9]{20,}", "[REDACTED_ACCESS_TOKEN]", value)
    return value[:500]


def log_platform_attempt(platform, post, status, message=""):
    """Keep failed/blocked attempts separate for each platform."""
    item = {
        "attempted_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "platform": platform,
        "approved_at": post.get("approved_at", ""),
        "publication_key": publication_key(post),
        "vehicle": post.get("vehicle", ""),
        "service": post.get("service", ""),
        "message": _safe_error_message(message),
    }
    if platform == "facebook":
        audit = load_facebook_publication_audit()
        audit.insert(0, item)
        save_facebook_publication_audit(audit)
    else:
        audit = load_publication_audit()
        audit.insert(0, item)
        save_publication_audit(audit)
    return item


def publish_facebook_for_post(post_index):
    history = enrich_history(load_history())
    connection = load_facebook_connection()
    audit = load_facebook_publication_audit()
    if not connection.get("access_token") or not connection.get("page_id"):
        raise RuntimeError("Conecte a Página do Facebook antes de publicar.")
    if not (0 <= post_index < len(history)):
        raise RuntimeError("Post aprovado não encontrado.")
    post = history[post_index]
    previous = find_facebook_publication_record(post, audit, connection.get("page_id", ""))
    if previous:
        raise RuntimeError("Este post já foi publicado no Facebook. A duplicação foi bloqueada.")

    urls = facebook_media_urls(post)
    caption = build_full_caption(post)
    page_id = connection["page_id"]
    media_ids = []
    if len(urls) == 1:
        result = facebook_graph_request(
            f"{page_id}/photos",
            connection["access_token"],
            data={"url": urls[0], "caption": caption, "published": "true", "is_gen_ai": "false"},
            method="POST",
            timeout=90,
        )
        media_id = result.get("id")
        if not media_id:
            raise RuntimeError("O Facebook não retornou o ID da foto publicada.")
        media_ids.append(str(media_id))
        post_id = result.get("post_id") or media_id
    else:
        for url in urls:
            result = facebook_graph_request(
                f"{page_id}/photos",
                connection["access_token"],
                data={"url": url, "published": "false", "is_gen_ai": "false"},
                method="POST",
                timeout=90,
            )
            media_id = result.get("id")
            if not media_id:
                raise RuntimeError("O Facebook não retornou o ID de uma foto para montar a publicação.")
            media_ids.append(str(media_id))
        attached = {
            f"attached_media[{i}]": json.dumps({"media_fbid": media_id})
            for i, media_id in enumerate(media_ids)
        }
        result = facebook_graph_request(
            f"{page_id}/feed",
            connection["access_token"],
            data={"message": caption, **attached},
            method="POST",
            timeout=90,
        )
        post_id = result.get("id")
        if not post_id:
            raise RuntimeError("O Facebook não retornou o ID da publicação criada.")

    now = datetime.now().isoformat(timespec="seconds")
    record = {
        "published_at": now,
        "status": "PUBLICADO",
        "platform": "facebook",
        "post_id": str(post_id),
        "media_ids": media_ids,
        "page_id": str(page_id),
        "page_name": connection.get("page_name", ""),
        "post_index": post_index,
        "approved_at": post.get("approved_at", ""),
        "publication_key": publication_key(post),
        "vehicle": post.get("vehicle", ""),
        "service": post.get("service", ""),
    }
    post["facebook_publication"] = record
    save_history(history)
    audit = load_facebook_publication_audit()
    if not any(
        str(item.get("approved_at", "")).strip() == publication_key(post)
        and str(item.get("page_id", "")) == str(page_id)
        and str(item.get("status", "")).upper() == "PUBLICADO"
        for item in audit
    ):
        audit.insert(0, record)
    save_facebook_publication_audit(audit)
    return record


def preflight_instagram_for_post(post_index):
    history = reconcile_publication_records(enrich_history(load_history()))
    connection = load_instagram_connection()
    audit = load_publication_audit()
    draft = load_publication_draft()
    if not connection.get("access_token") or not connection.get("username"):
        raise RuntimeError("Instagram não está conectado.")
    if not (0 <= post_index < len(history)):
        raise RuntimeError("Post aprovado não encontrado.")
    post = history[post_index]
    if find_publication_record(post, audit, connection.get("username", "")):
        raise RuntimeError("Este post já foi publicado no Instagram.")
    if not draft_matches_post(draft, post, post_index, connection):
        raise RuntimeError("O rascunho do Instagram não corresponde ao post selecionado. Prepare e teste a mídia deste post.")
    container_id = (draft.get("container_id") or "").strip()
    if not container_id:
        raise RuntimeError("Prepare e teste a mídia no Instagram antes de publicar.")
    status = instagram_container_status(connection, container_id)
    status_code = (status.get("status_code") or "").upper()
    if status_code not in {"FINISHED", "PUBLISHED"}:
        raise RuntimeError(f"O Instagram ainda está processando a mídia: {status_code or 'status desconhecido'}.")
    if status_code == "PUBLISHED" and not draft.get("published_media_id"):
        raise RuntimeError("O Instagram já marca a mídia como publicada, mas não há ID local registrado. Para evitar duplicação, não publicaremos outra plataforma por este fluxo até revisar o registro.")
    return True


def publish_instagram_for_post(post_index):
    history = reconcile_publication_records(enrich_history(load_history()))
    connection = load_instagram_connection()
    audit = load_publication_audit()
    draft = load_publication_draft()
    if not connection.get("access_token") or not connection.get("username"):
        raise RuntimeError("Instagram não está conectado.")
    if not (0 <= post_index < len(history)):
        raise RuntimeError("Post aprovado não encontrado.")
    post = history[post_index]
    prior = find_publication_record(post, audit, connection.get("username", ""))
    if prior:
        raise RuntimeError("Este post já consta como publicado no Instagram. A duplicação foi bloqueada.")
    if not draft_matches_post(draft, post, post_index, connection):
        raise RuntimeError("O rascunho não corresponde ao post aprovado atual. Prepare a mídia deste post novamente.")
    container_id = (draft.get("container_id") or "").strip()
    if not container_id:
        raise RuntimeError("Prepare e teste a mídia no Instagram antes de publicar.")

    status = instagram_container_status(connection, container_id)
    status_code = (status.get("status_code") or "").upper()
    if status_code not in {"FINISHED", "PUBLISHED"}:
        raise RuntimeError(f"O Instagram ainda está processando a mídia: {status_code or 'status desconhecido'}.")
    if status_code == "PUBLISHED":
        if not draft.get("published_media_id"):
            raise RuntimeError("O Instagram já marca a mídia como publicada, mas não há ID local registrado. Para evitar duplicação, não enviaremos novamente.")
        published = {"id": draft.get("published_media_id")}
    else:
        published = instagram_publish_container(connection, container_id)
    media_id = published.get("id")
    now = datetime.now().isoformat(timespec="seconds")
    draft.update({
        "published_at": now,
        "status": "PUBLICADO NO INSTAGRAM",
        "published_media_id": media_id,
        "publish_response": published,
    })
    save_publication_draft(draft)
    record = {
        "published_at": now,
        "status": "PUBLICADO",
        "platform": "instagram",
        "media_id": media_id or "",
        "container_id": container_id,
        "username": connection.get("username", ""),
        "post_index": post_index,
        "approved_at": post.get("approved_at", ""),
        "publication_key": publication_key(post),
        "vehicle": post.get("vehicle", ""),
        "service": post.get("service", ""),
    }
    post["instagram_publication"] = record
    audit = load_publication_audit()
    duplicate = any(
        str(item.get("approved_at", "")).strip() == publication_key(post)
        and str(item.get("username", "")).strip() == str(connection.get("username", "")).strip()
        and str(item.get("status", "")).upper() == "PUBLICADO"
        for item in audit
    )
    if not duplicate:
        audit.insert(0, record)
    save_publication_audit(audit)
    save_history(history)
    return record




def local_path_from_upload_url(value):
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    path = parsed.path if parsed.scheme else value
    if path.startswith("/uploads/"):
        return path
    return ""


def instagram_media_items(post):
    # Preserve the approved order: BEFORE 1, AFTER 1, BEFORE 2, AFTER 2...
    after = [x for x in (post.get("after_images") or [post.get("after_image")]) if x]
    before = [x for x in (post.get("before_images") or [post.get("before_image")]) if x]
    chosen = []
    for index in range(max(len(before), len(after))):
        if index < len(before):
            chosen.append((before[index], "before"))
        if index < len(after):
            chosen.append((after[index], "after"))

    if len(chosen) > 10:
        raise RuntimeError(
            f"Esta publicação tem {len(chosen)} fotos. O Instagram aceita até 10 por carrossel. "
            "Use 'Dividir em partes' na Fila de Publicações; nenhuma foto será cortada silenciosamente."
        )

    result = []
    for value, side in chosen:
        ref = ensure_remote_image_ref(value, side)
        if is_remote_ref(ref):
            url = signed_url(remote_path_from_ref(ref), 3600)
            if url:
                result.append({"url": url, "media_type": media_kind(ref), "ref": ref})
                continue
        if str(value).startswith("http"):
            result.append({"url": str(value), "media_type": media_kind(value), "ref": str(value)})

    if not result:
        raise RuntimeError("Nenhuma mídia válida foi encontrada para publicação.")
    return result


def instagram_media_urls(post):
    return [item["url"] for item in instagram_media_items(post)]


def _wait_for_container(connection, container_id, timeout=120):
    started = datetime.now().timestamp()
    last = {}
    while datetime.now().timestamp() - started < timeout:
        last = instagram_container_status(connection, container_id)
        status = (last.get("status_code") or "").upper()
        if status in {"FINISHED", "PUBLISHED"}:
            return last
        if status in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Instagram não conseguiu processar a mídia: {status} {last.get('status', '')}".strip())
        import time
        time.sleep(4)
    status = (last.get("status_code") or "status desconhecido").upper()
    raise RuntimeError(
        f"O Instagram ainda está processando a mídia ({status}) após {timeout} segundos. "
        "O rascunho foi preservado; tente preparar e testar novamente em alguns instantes."
    )


def instagram_create_container(connection, media_items, caption):
    cfg = instagram_config()
    base = f"https://graph.instagram.com/{cfg['api_version']}"
    user_id = connection.get("user_id", "")
    token = connection.get("access_token", "")
    if not user_id or not token:
        raise RuntimeError("Conexão do Instagram incompleta.")

    if len(media_items) == 1:
        item = media_items[0]
        data = {
            "caption": caption,
            "access_token": token,
        }
        if item["media_type"] == "VIDEO":
            data.update({"media_type": "VIDEO", "video_url": item["url"]})
        else:
            data.update({"image_url": item["url"]})
        return _http_json_request(f"{base}/{user_id}/media", data=data, method="POST", timeout=90)

    children = []
    for item in media_items:
        data = {
            "is_carousel_item": "true",
            "access_token": token,
        }
        if item["media_type"] == "VIDEO":
            data.update({"media_type": "VIDEO", "video_url": item["url"]})
        else:
            data.update({"image_url": item["url"]})
        child = _http_json_request(
            f"{base}/{user_id}/media", data=data, method="POST", timeout=90
        )
        child_id = child.get("id")
        if not child_id:
            raise RuntimeError(f"Instagram não retornou o ID do item do carrossel: {child}")
        # Carousel children must finish processing before the parent is created.
        # This applies to images as well as videos.
        _wait_for_container(connection, child_id, timeout=120)
        children.append(child_id)

    carousel = _http_json_request(
        f"{base}/{user_id}/media",
        data={
            "media_type": "CAROUSEL",
            "children": ",".join(children),
            "caption": caption,
            "access_token": token,
        },
        method="POST",
        timeout=90,
    )
    carousel["children"] = children
    return carousel

def instagram_container_status(connection, container_id):
    cfg = instagram_config()
    token = connection.get("access_token", "")
    query = urllib.parse.urlencode({
        "fields": "status_code,status",
        "access_token": token,
    })
    return _http_json_request(
        f"https://graph.instagram.com/{cfg['api_version']}/{container_id}?{query}",
        method="GET",
        timeout=30,
    )


def instagram_publish_container(connection, container_id):
    cfg = instagram_config()
    user_id = connection.get("user_id", "")
    token = connection.get("access_token", "")
    if not user_id or not token:
        raise RuntimeError("Conexão do Instagram incompleta.")
    return _http_json_request(
        f"https://graph.instagram.com/{cfg['api_version']}/{user_id}/media_publish",
        data={
            "creation_id": container_id,
            "access_token": token,
        },
        method="POST",
        timeout=90,
    )


def allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED


def is_video_file(path_or_name):
    name = str(path_or_name or "").split("?", 1)[0].lower()
    return any(name.endswith("." + ext) for ext in VIDEO_EXTENSIONS)


def media_kind(value):
    return "VIDEO" if is_video_file(value) else "IMAGE"


def _image_search_roots():
    """Return current and legacy project upload folders used during V30â†’V31 migration."""
    roots = [(BEFORE_DIR, "before"), (AFTER_DIR, "after")]
    # The user normally keeps versions side by side, e.g. E:\Sthetcar IA V30 and V31.
    # Search the previous version without ever modifying it.
    for version_name in ("Sthetcar IA V30", "Sthetcar IA V29", "Sthetcar IA V28"):
        legacy = BASE_DIR.parent / version_name / "uploads"
        roots.extend([(legacy / "before", "before"), (legacy / "after", "after")])
    return roots


def resolve_image_reference(value, side):
    """Convert legacy local image references into stable Supabase references when possible."""
    return ensure_remote_image_ref(value, side)


def repair_history_images(history):
    changed = False
    repaired_history = []
    for item in history:
        if not isinstance(item, dict):
            repaired_history.append(item)
            continue
        current = dict(item)
        for side in ("before", "after"):
            plural = f"{side}_images"
            singular = f"{side}_image"
            values = current.get(plural) or ([current.get(singular)] if current.get(singular) else [])
            repaired = [resolve_image_reference(v, side) for v in values if v]
            if repaired != values:
                current[plural] = repaired
                changed = True
            if repaired and current.get(singular) != repaired[0]:
                current[singular] = repaired[0]
                changed = True
        repaired_history.append(current)
    if changed:
        save_history(repaired_history)
    return repaired_history


def load_history():
    data = _load_state("history", HISTORY_PATH, [])
    if not isinstance(data, list):
        data = []
    return repair_history_images(data)


def save_history(history):
    _save_state("history", HISTORY_PATH, history)



def fallback_hashtags(vehicle: str = "", service: str = ""):
    """Return safe local hashtags when the AI omitted them or an older post has none."""
    vehicle_text = (vehicle or "").strip()
    service_text = (service or "").strip()

    tags = [
        "#Sthetcar",
        "#EsteticaAutomotiva",
        "#DetalhamentoAutomotivo",
        "#CarDetailing",
        "#AntesEDepois",
    ]

    service_map = {
        "lavagem externa": ["#LavagemExterna", "#LavagemAutomotiva"],
        "higienização interna": ["#HigienizacaoInterna", "#LimpezaAutomotiva"],
        "lavagem e higienização": ["#LavagemAutomotiva", "#HigienizacaoAutomotiva"],
        "polimento": ["#PolimentoAutomotivo", "#BrilhoAutomotivo"],
    }
    service_key = service_text.casefold()
    tags.extend(service_map.get(service_key, []))

    # Add a model-specific tag only when the vehicle field explicitly contains it.
    vehicle_clean = re.sub(r"[^\w]+", " ", vehicle_text, flags=re.UNICODE).strip(" _")
    if vehicle_clean:
        vehicle_tag = re.sub(r"\s+", "", vehicle_clean)
        if vehicle_tag and not vehicle_tag.startswith("#"):
            tags.append("#" + vehicle_tag)
        lower = vehicle_clean.casefold()
        known_vehicle_tags = {
            "amarok": "#VolkswagenAmarok",
            "s 10": "#ChevroletS10",
            "s-10": "#ChevroletS10",
            "ranger": "#FordRanger",
            "hilux": "#ToyotaHilux",
            "t cross": "#VolkswagenTCross",
            "tracker": "#ChevroletTracker",
        }
        for key, tag in known_vehicle_tags.items():
            if key in lower:
                tags.append(tag)
                break

    # Keep the set compact and deterministic.
    unique = []
    seen = set()
    for tag in tags:
        if tag.casefold() not in seen:
            seen.add(tag.casefold())
            unique.append(tag)
    return " ".join(unique[:10])


def split_post(post_text: str, vehicle: str = "", service: str = ""):
    """Separate the AI response into editable caption and hashtags, with a safe fallback."""
    text = (post_text or "").strip()
    upper = text.upper()

    # Accept the canonical marker and a few common formatting variations returned by AI.
    match = re.search(r"(?:^|\n)\s*HASHTAGS?\s*:\s*", text, flags=re.IGNORECASE)
    if match:
        caption = text[:match.start()].strip()
        hashtags = text[match.end():].strip()
    else:
        caption = text
        hashtags = ""

    if caption.upper().startswith("LEGENDA:"):
        caption = caption[len("LEGENDA:"):].strip()

    if not hashtags:
        hashtags = fallback_hashtags(vehicle, service)

    return caption, hashtags


def enrich_history(history):
    """Backfill vehicle/service for older approved posts that were saved blank."""
    changed = False
    for item in history:
        if not item.get("vehicle"):
            vehicle = infer_vehicle(item.get("analysis", ""))
            if vehicle:
                item["vehicle"] = vehicle
                changed = True
        if not item.get("service"):
            source = "\n".join([item.get("analysis", ""), item.get("caption", "")])
            service = infer_service(source)
            if service:
                item["service"] = service
                changed = True

        # Older approved posts may have a perfectly valid caption but no hashtags.
        # Backfill them locally so the review and publication screens are complete.
        if not (item.get("hashtags") or "").strip():
            item["hashtags"] = fallback_hashtags(item.get("vehicle", ""), item.get("service", ""))
            changed = True
    if changed:
        save_history(history)
    return history



def dashboard_history_display(history):
    """Hide posts discarded from the publication queue without deleting their audit records."""
    visible = []
    for history_index, item in enumerate(history):
        if str(item.get("publication_queue_status", "")).strip().upper() in {"DESCARTADO", "DIVIDIDO"}:
            continue
        visible_item = dict(item)
        visible_item["history_index"] = history_index
        visible.append(visible_item)
    return visible


def dashboard_metrics(history):
    """Build metrics using the reconciled publication audit as source of truth."""
    history = reconcile_publication_records(history)
    history = [
        item for item in history
        if str(item.get("publication_queue_status", "")).strip().upper() not in {"DESCARTADO", "DIVIDIDO"}
    ]
    total_photos = 0
    published = 0
    pending_publication = 0
    vehicles = set()
    services = set()
    latest_published = None

    for item in history:
        before = item.get("before_images") or ([item.get("before_image")] if item.get("before_image") else [])
        after = item.get("after_images") or ([item.get("after_image")] if item.get("after_image") else [])
        total_photos += len([x for x in before + after if x])

        vehicle = (item.get("vehicle") or "").strip()
        service = (item.get("service") or "").strip()
        if vehicle:
            vehicles.add(vehicle)
        if service:
            services.add(service)

        pub = item.get("instagram_publication") or {}
        if str(pub.get("status", "")).upper() == "PUBLICADO":
            published += 1
            when = pub.get("published_at") or ""
            if when and (latest_published is None or when > latest_published):
                latest_published = when
        else:
            pending_publication += 1

    return {
        "approved": len(history),
        "published": published,
        "pending_publication": pending_publication,
        "photos": total_photos,
        "vehicles": len(vehicles),
        "services": len(services),
        "latest_published": latest_published,
    }

@app.errorhandler(413)
def request_too_large(error):
    # A handler for oversized uploads must not fail by querying Supabase again
    # while trying to render its own error page.
    history = []
    approval_queue = []
    history_display = []
    metrics = {
        "approved": 0, "published": 0, "pending_publication": 0,
        "photos": 0, "vehicles": 0, "services": 0, "latest_published": None,
    }
    try:
        history = load_history()
        approval_queue = load_approval_queue()
        history_display = dashboard_history_display(history)
        metrics = dashboard_metrics(history)
    except Exception:
        app.logger.exception("Falha ao carregar estado enquanto tratava upload acima do limite")
    return render_template(
        "dashboard.html",
        result=None,
        error="O envio ficou acima do limite de 35 MB do servidor. As fotos originais continuam preservadas; reduza a quantidade ou o tamanho dos arquivos e tente novamente.",
        approved=None,
        history=history,
        history_display=history_display,
        approval_queue=approval_queue,
        metrics=metrics,
    ), 413


@app.route("/", methods=["GET", "POST"])
def dashboard():
    create_request_id = f"CREATE-{secrets.token_hex(4).upper()}" if request.method == "POST" else ""
    if create_request_id:
        app.logger.info(
            "Solicitação de criação recebida [%s], content_length=%s",
            create_request_id,
            request.content_length,
        )
    result = None
    error = request.args.get("queue_error")
    approved = None
    try:
        history = reconcile_publication_records(enrich_history(load_history()))
        approval_queue = load_approval_queue()
        metrics = dashboard_metrics(history)
    except Exception as exc:
        diagnostic_id = create_request_id or f"LOAD-{secrets.token_hex(4).upper()}"
        app.logger.exception(
            "Falha ao carregar estado da página principal [%s] (%s)",
            diagnostic_id,
            type(exc).__name__,
        )
        error = (
            "Não foi possível carregar os dados necessários para continuar. "
            "Nenhum post foi criado. Atualize a página e tente novamente. "
            f"Código de diagnóstico: {diagnostic_id}."
        )
        return render_template(
            "dashboard.html",
            result=None,
            error=error,
            approved=None,
            history=[],
            history_display=[],
            approval_queue=[],
            metrics={
                "approved": 0, "published": 0, "pending_publication": 0,
                "photos": 0, "vehicles": 0, "services": 0, "latest_published": None,
            },
        ), 503

    if request.method == "POST":
        action = request.form.get("action", "generate")

        if action == "approve":
            caption = request.form.get("caption", "").strip()
            hashtags = request.form.get("hashtags", "").strip()
            vehicle = request.form.get("vehicle", "").strip()
            service = request.form.get("service", "").strip()
            pending_id = request.form.get("pending_id", "").strip()
            approved_pending = next(
                (item for item in approval_queue if pending_id and item.get("pending_id") == pending_id),
                {},
            )

            if not vehicle:
                error = "Informe o veículo antes de aprovar o conteúdo."
            elif not service:
                error = "Informe o serviço realizado antes de aprovar o conteúdo."
            elif not caption:
                error = "A legenda não pode ficar vazia."
            else:
                approved_at = datetime.now().isoformat(timespec="seconds")
                approval = {
                    "approved_at": approved_at,
                    "caption": caption,
                    "hashtags": hashtags,
                    "vehicle": vehicle,
                    "service": service,
                    "status": "APROVADO — aguardando conexão com o Instagram",
                }

                # Keep the latest approval for compatibility with the MVP.
                approval_path = GENERATED_DIR / "ultimo_post_aprovado.json"
                _save_state("last_approved", approval_path, approval)

                # Persist every approved post in the history.
                ordered_before = [x.strip() for x in request.form.getlist("before_images") if x.strip()]
                ordered_after = [x.strip() for x in request.form.getlist("after_images") if x.strip()]
                history_item = {
                    "approved_at": approved_at,
                    "caption": caption,
                    "hashtags": hashtags,
                    "status": "APROVADO — aguardando conexão com o Instagram",
                    # The DOM order is the approved order. V29 lets the user drag/reorder
                    # photos before approval and this list preserves that exact sequence.
                    "before_images": ordered_before,
                    "after_images": ordered_after,
                    "before_image": ordered_before[0] if ordered_before else "",
                    "after_image": ordered_after[0] if ordered_after else "",
                    "vehicle": request.form.get("vehicle", "").strip(),
                    "service": request.form.get("service", "").strip(),
                    "analysis": request.form.get("analysis", "").strip(),
                    # Keep the source originals for archival, but only the processed
                    # before_images/after_images are eligible for publication.
                    "before_images_original": list(approved_pending.get("before_images_original") or []),
                    "after_images_original": list(approved_pending.get("after_images_original") or []),
                    "plate_redaction_version": approved_pending.get("plate_redaction_version", 0),
                    "plate_redaction_count": approved_pending.get("plate_redaction_count", 0),
                    "plate_redaction_image_count": approved_pending.get("plate_redaction_image_count", 0),
                    "plate_redaction_review_required": bool(approved_pending.get("plate_redaction_review_required", False)),
                    "plate_redaction_message": approved_pending.get("plate_redaction_message", ""),
                    "service_category": approved_pending.get("service_category", "geral"),
                    "service_category_label": approved_pending.get("service_category_label", ""),
                    "service_group_id": approved_pending.get("service_group_id", ""),
                    "service_group_index": approved_pending.get("service_group_index", 1),
                    "service_group_count": approved_pending.get("service_group_count", 1),
                    "publication_series_title": approved_pending.get("publication_series_title", ""),
                    "publication_series_label": approved_pending.get("publication_series_label", ""),
                    "publication_part_number": approved_pending.get("publication_part_number"),
                    "publication_part_count": approved_pending.get("publication_part_count"),
                    "series_badge_enabled": bool(approved_pending.get("series_badge_enabled", False)),
                    "series_badge_source_ref": approved_pending.get("series_badge_source_ref", ""),
                    "series_badge_image_ref": approved_pending.get("series_badge_image_ref", ""),
                    "series_label_note": approved_pending.get("series_label_note", ""),
                }
                history.insert(0, history_item)
                save_history(history)
                if pending_id:
                    approval_queue = [item for item in approval_queue if item.get("pending_id") != pending_id]
                    save_approval_queue(approval_queue)
                approved = approval
                metrics = dashboard_metrics(history)
        else:
            before_files = [f for f in request.files.getlist("before") if f and f.filename]
            after_files = [f for f in request.files.getlist("after") if f and f.filename]
            vehicle = request.form.get("vehicle", "").strip()
            service = request.form.get("service", "").strip()
            notes = request.form.get("notes", "").strip()
            raw_before_categories = request.form.getlist("before_category")
            raw_after_categories = request.form.getlist("after_category")
            photo_groups = []
            category_error = ""
            if before_files and after_files:
                try:
                    before_categories = normalize_photo_categories(
                        raw_before_categories, len(before_files), "ANTES"
                    )
                    after_categories = normalize_photo_categories(
                        raw_after_categories, len(after_files), "DEPOIS"
                    )
                    photo_groups = group_photo_indexes(before_categories, after_categories)
                except ValueError as exc:
                    category_error = str(exc)

            if not before_files or not after_files:
                error = "Envie pelo menos 1 mídia em ANTES e 1 mídia em DEPOIS."
            elif category_error:
                error = category_error
            elif any(not allowed(f.filename) for f in before_files + after_files):
                error = "Use JPG, JPEG, PNG ou WEBP."
            elif any(is_video_file(f.filename) for f in before_files + after_files):
                error = "A proteção automática de placas nesta versão funciona apenas com fotos. Selecione JPG, JPEG, PNG ou WEBP; o tratamento de vídeos será adicionado posteriormente."
            elif any(is_video_file(f.filename) and (getattr(f, "content_length", None) or 0) > MAX_VIDEO_BYTES for f in before_files + after_files):
                error = "Cada vídeo deve ter no máximo 20 MB."
            elif request.content_length and request.content_length > app.config["MAX_CONTENT_LENGTH"]:
                error = "O envio ultrapassou o limite de 30 MB. As fotos são comprimidas automaticamente antes do envio; se isso acontecer, reduza a quantidade de fotos."
            else:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                before_paths, after_paths = [], []
                safe_paths = []
                before_urls, after_urls = [], []
                before_original_urls, after_original_urls = [], []
                stage = "salvar fotos enviadas"
                try:
                    app.logger.info(
                        "Criação de post [%s]: iniciando salvamento de %d foto(s)",
                        create_request_id,
                        len(before_files) + len(after_files),
                    )
                    for idx, file in enumerate(before_files, 1):
                        name = f"{stamp}_antes_{idx}_{secure_filename(file.filename)}"
                        path = BEFORE_DIR / name
                        file.save(path)
                        before_paths.append(path)
                    for idx, file in enumerate(after_files, 1):
                        name = f"{stamp}_depois_{idx}_{secure_filename(file.filename)}"
                        path = AFTER_DIR / name
                        file.save(path)
                        after_paths.append(path)

                    saved_paths = before_paths + after_paths
                    total_saved = sum(path.stat().st_size for path in saved_paths if path.exists())
                    oversized_video = next((path for path in saved_paths if is_video_file(path.name) and path.stat().st_size > MAX_VIDEO_BYTES), None)
                    if oversized_video:
                        raise RuntimeError(f"O vídeo {oversized_video.name} ultrapassa o limite de 20 MB.")
                    if total_saved > MAX_TOTAL_UPLOAD_BYTES:
                        raise RuntimeError(f"As mídias ficaram em {total_saved / (1024 * 1024):.1f} MB. O limite total é de 30 MB.")

                    total_saved = sum(path.stat().st_size for path in before_paths + after_paths if path.exists())
                    app.logger.info(
                        "Criação de post [%s]: uploads salvos, fotos=%d, bytes=%d",
                        create_request_id,
                        len(before_paths) + len(after_paths),
                        total_saved,
                    )

                    # Keep each Gemini vision request bounded to small image batches.
                    # Never overwrite the uploaded copies; protection writes separate squares.
                    stage = "proteger placas"
                    app.logger.info(
                        "Criação de post [%s]: iniciando proteção de placas",
                        create_request_id,
                    )
                    safe_paths, plate_redaction = create_plate_safe_copies(before_paths + after_paths)
                    safe_before_paths = safe_paths[:len(before_paths)]
                    safe_after_paths = safe_paths[len(before_paths):]
                    app.logger.info(
                        "Criação de post [%s]: proteção concluída, fotos=%d",
                        create_request_id,
                        len(safe_paths),
                    )

                    # Analyze every service area independently so captions do not mix
                    # exterior, interior, chassis, and engine work. Plate-protected copies
                    # are already produced for the complete appointment.
                    stage = "analisar cópias protegidas por categoria"
                    app.logger.info(
                        "Criação de post [%s]: %d área(s) de serviço, %d foto(s) protegidas",
                        create_request_id,
                        len(photo_groups),
                        len(safe_paths),
                    )
                    group_results = []
                    for group_index, group in enumerate(photo_groups, 1):
                        group_before_paths = [
                            safe_before_paths[index] for index in group["before_indexes"]
                        ]
                        group_after_paths = [
                            safe_after_paths[index] for index in group["after_indexes"]
                        ]
                        group_service = group["service"] if group["key"] != "geral" else service
                        group_notes_parts = [notes] if notes else []
                        if group["key"] != "geral":
                            group_notes_parts.append(
                                f"Este post deve se concentrar somente na área {group['label']}. "
                                "Não atribua a esta área serviços que não apareçam nas fotos."
                            )
                            if service:
                                group_notes_parts.append(
                                    f"Contexto geral informado para o atendimento: {service}. "
                                    "Use somente os detalhes pertinentes à categoria deste post."
                                )
                        group_notes = "\n\n".join(group_notes_parts)
                        app.logger.info(
                            "Criação de post [%s]: analisando categoria %s (%d antes, %d depois)",
                            create_request_id,
                            group["key"],
                            len(group_before_paths),
                            len(group_after_paths),
                        )
                        analysis = analyze_before_after(
                            group_before_paths,
                            group_after_paths,
                            vehicle,
                            group_service,
                            group_notes,
                        )
                        resolved_vehicle = vehicle or infer_vehicle(analysis)
                        resolved_service = group_service or infer_service(analysis)
                        stage = f"gerar legenda da categoria {group['key']}"
                        post = generate_post(
                            analysis, resolved_vehicle, resolved_service, group_notes
                        )
                        caption, hashtags = split_post(
                            post, resolved_vehicle, resolved_service
                        )
                        group_results.append({
                            "group": group,
                            "analysis": analysis,
                            "post": post,
                            "caption": caption,
                            "hashtags": hashtags,
                            "vehicle": resolved_vehicle,
                            "service": resolved_service,
                        })

                    # When one appointment creates several service-specific posts, use the
                    # same public series title and explicitly point to the next service.
                    series_count = len(group_results)
                    series_title = ""
                    if series_count > 1:
                        series_vehicle = vehicle or group_results[0]["vehicle"]
                        series_title = build_series_title(series_vehicle)
                        for series_index, group_result in enumerate(group_results, 1):
                            group_result["vehicle"] = series_vehicle
                            next_service = (
                                group_results[series_index]["service"]
                                if series_index < series_count else None
                            )
                            group_result["caption"] = build_series_caption(
                                group_result["caption"],
                                series_vehicle,
                                group_result["service"],
                                series_index,
                                series_count,
                                next_service=next_service,
                                hashtags=group_result["hashtags"],
                            )
                            group_result["series_title"] = series_title
                            group_result["series_label"] = (
                                f"Parte {series_index}/{series_count} · {group_result['service']}"
                            )
                            group_result["series_part_number"] = series_index
                            group_result["series_part_count"] = series_count

                    # Upload each original and protected copy exactly once. Each categorized
                    # pending post refers only to the subset assigned to that category.
                    stage = "enviar imagens ao armazenamento"
                    before_original_urls = [_remote_ref_for_local(path, "before") for path in before_paths]
                    after_original_urls = [_remote_ref_for_local(path, "after") for path in after_paths]
                    before_urls = [_remote_ref_for_local(path, "before") for path in safe_before_paths]
                    after_urls = [_remote_ref_for_local(path, "after") for path in safe_after_paths]
                    app.logger.info(
                        "Criação de post [%s]: envio ao armazenamento concluído",
                        create_request_id,
                    )

                    per_image_plate_counts = plate_redaction.get(
                        "plate_redaction_per_image_counts", []
                    )
                    per_image_review_required = plate_redaction.get(
                        "plate_redaction_per_image_review_required", []
                    )
                    pending_records = []
                    for group_index, group_result in enumerate(group_results, 1):
                        group = group_result["group"]
                        before_indexes = group["before_indexes"]
                        after_indexes = group["after_indexes"]
                        group_before_urls = [before_urls[index] for index in before_indexes]
                        group_after_urls = [after_urls[index] for index in after_indexes]
                        series_badge_enabled = len(group_results) > 1
                        series_badge_source_ref = ""
                        series_badge_image_ref = ""
                        if series_badge_enabled:
                            series_badge_source_ref = group_before_urls[0]
                            series_badge_image_ref = _create_series_badge_ref(
                                series_badge_source_ref,
                                group_result["vehicle"],
                                group_result["service"],
                                group_index,
                                len(group_results),
                            )
                            group_before_urls[0] = series_badge_image_ref
                        group_before_original_urls = [
                            before_original_urls[index] for index in before_indexes
                        ]
                        group_after_original_urls = [
                            after_original_urls[index] for index in after_indexes
                        ]
                        combined_indexes = before_indexes + [
                            len(before_paths) + index for index in after_indexes
                        ]
                        group_plate_count = sum(
                            per_image_plate_counts[index]
                            for index in combined_indexes
                            if index < len(per_image_plate_counts)
                        )
                        group_images_with_plates = sum(
                            1 for index in combined_indexes
                            if index < len(per_image_plate_counts)
                            and per_image_plate_counts[index] > 0
                        )
                        group_review_required = any(
                            per_image_review_required[index]
                            for index in combined_indexes
                            if index < len(per_image_review_required)
                        )
                        if group_plate_count:
                            group_plate_message = (
                                f"Foram detectadas e borradas {group_plate_count} placa(s) em "
                                f"{group_images_with_plates} foto(s) desta categoria. "
                                "Revise todas as prévias; a detecção automática pode falhar."
                            )
                        else:
                            group_plate_message = (
                                "Nenhuma placa foi detectada automaticamente nesta categoria. "
                                "Revise todas as prévias, pois a detecção automática pode falhar."
                            )
                        if group_review_required:
                            group_plate_message += (
                                " A IA sinalizou uma ou mais imagens para conferência humana."
                            )
                        result = {
                            "analysis": group_result["analysis"],
                            "post": group_result["post"],
                            "caption": group_result["caption"],
                            "hashtags": group_result["hashtags"],
                            "before_images": group_before_urls,
                            "after_images": group_after_urls,
                            "before_images_original": group_before_original_urls,
                            "after_images_original": group_after_original_urls,
                            "plate_redaction_version": plate_redaction.get("plate_redaction_version", 2),
                            "plate_redaction_count": group_plate_count,
                            "plate_redaction_image_count": len(combined_indexes),
                            "plate_redaction_review_required": group_review_required,
                            "plate_redaction_message": group_plate_message,
                            "before_image": group_before_urls[0],
                            "after_image": group_after_urls[0],
                            "vehicle": group_result["vehicle"],
                            "service": group_result["service"],
                            "service_category": group["key"],
                            "service_category_label": group["label"],
                            "service_group_id": create_request_id,
                            "service_group_index": group_index,
                            "service_group_count": len(group_results),
                            "publication_series_title": group_result.get("series_title", ""),
                            "publication_series_label": group_result.get("series_label", ""),
                            "publication_part_number": group_result.get("series_part_number"),
                            "publication_part_count": group_result.get("series_part_count"),
                            "series_badge_enabled": series_badge_enabled,
                            "series_badge_source_ref": series_badge_source_ref,
                            "series_badge_image_ref": series_badge_image_ref,
                            "series_label_note": "",
                        }
                        pending = {
                            **result,
                            "pending_id": new_pending_id(),
                            "created_at": datetime.now().isoformat(timespec="seconds"),
                            "status": "AGUARDANDO APROVAÇÃO",
                        }
                        pending_records.append(pending)

                    if len(pending_records) > 1:
                        # One appointment can produce several independent posts, but
                        # no category is approved/published automatically.
                        approval_queue = pending_records + approval_queue
                        save_approval_queue(approval_queue)
                        app.logger.info(
                            "Criação de post [%s]: %d publicações separadas aguardam revisão",
                            create_request_id,
                            len(pending_records),
                        )
                        return redirect(url_for(
                            "review_pending",
                            pending_id=pending_records[0]["pending_id"],
                        ))

                    pending = pending_records[0]
                    result = {
                        key: value for key, value in pending.items()
                        if key not in {"pending_id", "created_at", "status"}
                    }
                    approval_queue.insert(0, pending)
                    save_approval_queue(approval_queue)
                    result["pending_id"] = pending["pending_id"]
                    app.logger.info(
                        "Criação de post [%s]: concluída com sucesso, 1 publicação aguardando revisão",
                        create_request_id,
                    )
                except Exception as exc:
                    app.logger.exception(
                        "Falha ao criar post [%s], etapa=%s, tipo=%s",
                        create_request_id,
                        stage,
                        type(exc).__name__,
                    )
                    for path in before_paths + after_paths + safe_paths:
                        try:
                            if path.exists():
                                path.unlink()
                        except Exception:
                            pass
                    error = str(exc)

    return render_template(
        "dashboard.html",
        result=result,
        error=error,
        approved=approved,
        history=history,
        history_display=dashboard_history_display(history),
        approval_queue=approval_queue,
        metrics=metrics,
    )


@app.route("/revisar/<pending_id>")
def review_pending(pending_id):
    queue = load_approval_queue()
    pending = next((item for item in queue if item.get("pending_id") == pending_id), None)
    if not pending:
        return redirect(url_for("dashboard", queue_error="Conteúdo pendente não encontrado."))
    return render_template("review_pending.html", result=pending, pending=pending, history=enrich_history(load_history()), approval_queue=queue)


def _friendly_caption_rewrite_error(exc):
    """Return a user-safe explanation while keeping diagnostic details in server logs."""
    message = str(exc or "").casefold()
    if "igual ou muito parecida" in message or "suficientemente diferente" in message or "produzir uma legenda" in message:
        return "A IA não conseguiu criar uma versão suficientemente diferente. A legenda original foi preservada. Escolha outro foco (por exemplo, educativo, benefícios ou comercial) e tente novamente."
    if "gemini_api_key" in message or "api key" in message and "not configured" in message:
        return "A chave da IA não está configurada no servidor. A legenda atual e as fotos foram preservadas."
    if any(code in message for code in ("503", "unavailable", "429", "resource_exhausted", "rate limit", "temporariamente indisponível")):
        return "A IA está temporariamente indisponível ou atingiu o limite de uso. A legenda atual e as fotos foram preservadas; aguarde alguns minutos e tente novamente."
    if "análise visual" in message and ("vazia" in message or "não existe" in message):
        return "Este conteúdo está sem análise visual para orientar a reescrita. A legenda atual e as fotos foram preservadas."
    return "Não foi possível reescrever a legenda. A legenda atual e as fotos foram preservadas. Verifique os logs do aplicativo antes de tentar novamente."


@app.route("/revisar/<pending_id>/reescrever", methods=["POST"])
def rewrite_pending_caption(pending_id):
    """Rewrite only caption/hashtags without allowing route setup failures to become HTTP 500 pages."""
    queue = []
    pending = None
    original_pending = None
    try:
        # All data access and form normalization belongs inside this try block.
        # This prevents null/missing values from escaping to Flask's generic 500 page.
        queue = load_approval_queue()
        pending = next((item for item in queue if item.get("pending_id") == pending_id), None)
        if not pending:
            return redirect(url_for("dashboard", queue_error="Conteúdo pendente não encontrado."))

        original_pending = copy.deepcopy(pending)
        vehicle = str(request.form.get("vehicle") or pending.get("vehicle") or "").strip()
        service = str(request.form.get("service") or pending.get("service") or "").strip()
        analysis = str(pending.get("analysis") or "").strip()
        current_caption = str(request.form.get("current_caption") or pending.get("caption") or "").strip()
        rewrite_count = int(pending.get("rewrite_count", 0) or 0) + 1
        rewrite_angle = str(request.form.get("rewrite_angle") or "").strip()

        allowed_angles = {
            "problema_cliente",
            "processo_tecnico",
            "transformacao_antes_depois",
            "beneficios",
            "educativa",
            "comercial",
        }
        if rewrite_angle and rewrite_angle not in allowed_angles:
            raise RuntimeError("O foco de reescrita selecionado não é válido.")
        if not analysis:
            raise RuntimeError("A análise visual deste conteúdo está vazia.")

        generated = str(rewrite_caption(
            analysis,
            vehicle,
            service,
            current_caption,
            rewrite_count=rewrite_count,
            rewrite_angle=rewrite_angle,
        ) or "").strip()
        caption, hashtags = split_post(generated, vehicle, service)
        if not caption.strip():
            raise RuntimeError("A IA não retornou uma nova legenda. Tente novamente em alguns instantes.")

        # Defensive guard at the route boundary: never report success if the
        # generated caption is effectively the same as the one the user saw.
        def _normalize_caption_for_compare(value):
            return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

        old_normalized = _normalize_caption_for_compare(current_caption)
        new_normalized = _normalize_caption_for_compare(caption)
        if old_normalized and new_normalized:
            similarity = difflib.SequenceMatcher(None, old_normalized, new_normalized).ratio()
            old_opening = re.split(r"(?<=[.!?])\s+", old_normalized, maxsplit=1)[0]
            new_opening = re.split(r"(?<=[.!?])\s+", new_normalized, maxsplit=1)[0]
            if similarity >= 0.88 or (old_opening and old_opening == new_opening):
                raise RuntimeError(
                    "A IA devolveu uma legenda igual ou muito parecida com a atual. "
                    "O texto original foi preservado; escolha outro foco e tente novamente."
                )

        pending["vehicle"] = vehicle
        pending["service"] = service
        pending["post"] = generated
        pending["caption"] = caption
        pending["hashtags"] = hashtags
        pending["rewrite_count"] = rewrite_count
        if rewrite_angle:
            pending["rewrite_angle"] = rewrite_angle
        save_approval_queue(queue)
        return redirect(url_for("review_pending", pending_id=pending_id, rewritten=1))
    except Exception as exc:
        app.logger.exception("Falha ao reescrever legenda; pending_id=%s", pending_id)
        error_message = _friendly_caption_rewrite_error(exc)

        # If persistence failed after generation, restore the original in-memory
        # item so the UI never shows a rewrite that was not saved successfully.
        if pending is not None and original_pending is not None:
            pending.clear()
            pending.update(original_pending)
            return render_template(
                "review_pending.html",
                result=pending,
                pending=pending,
                history=[],
                approval_queue=queue if isinstance(queue, list) else [pending],
                rewrite_error=error_message,
            ), 200

        # If even loading the queue failed, avoid a raw Flask traceback page.
        return render_template(
            "rewrite_error.html",
            pending_id=pending_id,
            error=error_message,
        ), 503

@app.route("/descartar/<pending_id>", methods=["POST"])
def discard_pending(pending_id):
    queue = load_approval_queue()
    pending = next((item for item in queue if item.get("pending_id") == pending_id), None)
    if not pending:
        return redirect(url_for("dashboard", queue_error="Conteúdo pendente não encontrado."))

    queue = [item for item in queue if item.get("pending_id") != pending_id]
    save_approval_queue(queue)

    discarded = load_discarded_posts()
    discarded.insert(0, {
        "discarded_at": datetime.now().isoformat(timespec="seconds"),
        "pending_id": pending_id,
        "created_at": pending.get("created_at", ""),
        "vehicle": pending.get("vehicle", ""),
        "service": pending.get("service", ""),
        "caption": pending.get("caption", ""),
    })
    save_discarded_posts(discarded[:200])
    delete_pending_uploads(pending)
    return redirect(url_for("dashboard", notice="Conteúdo descartado. As fotos pendentes foram removidas do fluxo."))


@app.route("/auth/instagram")
def instagram_login():
    if not meta_ready():
        return redirect(url_for("instagram_central", setup="missing"))
    state = secrets.token_urlsafe(24)
    session["instagram_oauth_state"] = state
    cfg = instagram_config()
    params = {
        "client_id": cfg["app_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": "instagram_business_basic,instagram_business_content_publish",
        "state": state,
    }
    return redirect("https://www.instagram.com/oauth/authorize?" + urllib.parse.urlencode(params))


@app.route("/auth/instagram/callback")
def instagram_callback():
    error = request.args.get("error")
    if error:
        return redirect(url_for("instagram_central", oauth_error=error))
    expected = session.pop("instagram_oauth_state", None)
    received = request.args.get("state")
    if not expected or not received or not secrets.compare_digest(expected, received):
        return redirect(url_for("instagram_central", oauth_error="state_invalido"))
    code = request.args.get("code", "").strip()
    if not code:
        return redirect(url_for("instagram_central", oauth_error="codigo_ausente"))
    try:
        short = exchange_instagram_code(code)
        short_token = short.get("access_token")
        if not short_token:
            raise RuntimeError("A Meta não retornou o token de acesso.")
        long_lived = extend_instagram_token(short_token)
        access_token = long_lived.get("access_token") or short_token
        profile = get_instagram_profile(access_token)
        connection = {
            "connected_at": datetime.now().isoformat(timespec="seconds"),
            "access_token": access_token,
            "user_id": profile.get("id") or short.get("user_id", ""),
            "username": profile.get("username", ""),
            "name": profile.get("name", ""),
            "account_type": profile.get("account_type", ""),
            "token_type": "long_lived" if long_lived.get("access_token") else "short_lived",
            "expires_in": long_lived.get("expires_in"),
        }
        save_instagram_connection(connection)
        return redirect(url_for("instagram_central", connected="1"))
    except Exception as exc:
        return redirect(url_for("instagram_central", oauth_error=str(exc)))


@app.route("/auth/instagram/disconnect", methods=["POST"])
def instagram_disconnect():
    save_instagram_connection({})
    save_publication_draft({})
    return redirect(url_for("instagram_central", disconnected="1"))




@app.route("/auth/facebook")
def facebook_login():
    cfg = facebook_config()
    if not facebook_meta_ready():
        return redirect(url_for("instagram_central", facebook_error="Configure META_APP_ID, META_APP_SECRET, FACEBOOK_LOGIN_CONFIG_ID e FACEBOOK_REDIRECT_URI no ambiente do servidor."))
    state = secrets.token_urlsafe(24)
    session["facebook_oauth_state"] = state
    params = {
        "client_id": cfg["app_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        # Login do Facebook para Empresas uses a saved Meta configuration;
        # the config_id supplies the permission set instead of the scope query.
        "config_id": cfg["login_config_id"],
        "override_default_response_type": "true",
        "state": state,
    }
    oauth_url = f"https://www.facebook.com/{cfg['api_version']}/dialog/oauth?{urllib.parse.urlencode(params)}"
    return redirect(oauth_url)


@app.route("/auth/facebook/callback")
def facebook_callback():
    oauth_error = request.args.get("error_description") or request.args.get("error")
    if oauth_error:
        return redirect(url_for("instagram_central", facebook_error=f"Autorização do Facebook cancelada ou recusada: {_safe_error_message(oauth_error)}"))
    expected = session.pop("facebook_oauth_state", None)
    received = request.args.get("state")
    if not expected or not received or not secrets.compare_digest(expected, received):
        return redirect(url_for("instagram_central", facebook_error="O estado da autorização do Facebook não confere. Tente conectar novamente."))
    code = request.args.get("code", "").strip()
    if not code:
        return redirect(url_for("instagram_central", facebook_error="A Meta não retornou o código de autorização do Facebook."))
    cfg = facebook_config()
    try:
        if not facebook_meta_ready():
            raise RuntimeError("Configuração Meta incompleta no ambiente.")
        token_params = urllib.parse.urlencode({
            "client_id": cfg["app_id"],
            "client_secret": cfg["app_secret"],
            "redirect_uri": cfg["redirect_uri"],
            "code": code,
        })
        token_url = f"https://graph.facebook.com/{cfg['api_version']}/oauth/access_token?{token_params}"
        short_result = _http_json_request(token_url, timeout=30, api_label="Facebook OAuth")
        user_token = short_result.get("access_token", "")
        if not user_token:
            raise RuntimeError("A Meta não retornou token de usuário para o Facebook.")
        # This Login for Business configuration is explicitly set to issue a
        # system-user access token. Do not exchange it as if it were a standard
        # short-lived user token; Meta may reject fb_exchange_token for this flow.
        # Use the token returned by the authorization-code exchange to enumerate
        # the Page grant, then store the Page access token returned by /me/accounts.
        long_result = {"expires_in": short_result.get("expires_in")}
        pages_result = facebook_graph_request(
            "me/accounts", user_token,
            params={"fields": "id,name,access_token,tasks", "limit": 100},
            timeout=45,
        )
        pages = pages_result.get("data") or []
        if not pages:
            raise RuntimeError("A Meta não retornou Páginas para essa conta. Verifique o acesso à Página e tente conectar novamente.")
        configured_page_id = cfg.get("page_id", "")
        if configured_page_id:
            page = next((item for item in pages if str(item.get("id", "")) == configured_page_id), None)
            if page is None:
                raise RuntimeError("A Página definida em FACEBOOK_PAGE_ID não foi retornada pela Meta. Confirme o acesso à Página.")
        elif len(pages) == 1:
            page = pages[0]
        else:
            raise RuntimeError("Mais de uma Página foi encontrada. Defina FACEBOOK_PAGE_ID no ambiente para selecionar a Página correta.")
        page_token = page.get("access_token", "")
        if not page_token:
            raise RuntimeError("A Meta não retornou token de acesso para a Página selecionada.")
        tasks = page.get("tasks") or []
        if tasks and not ({"CREATE_CONTENT", "MANAGE"} & set(tasks)):
            raise RuntimeError("A conta não tem a tarefa CREATE_CONTENT ou MANAGE nessa Página.")
        connection = {
            "connected_at": datetime.now().isoformat(timespec="seconds"),
            "validated_at": datetime.now().isoformat(timespec="seconds"),
            "validation_status": "OK",
            "page_id": str(page.get("id", "")),
            "page_name": page.get("name", ""),
            "access_token": page_token,
            "tasks": tasks,
            "token_type": "page",
            "user_token_expires_in": long_result.get("expires_in"),
        }
        save_facebook_connection(connection)
        return redirect(url_for("instagram_central", facebook_connected="1"))
    except Exception as exc:
        return redirect(url_for("instagram_central", facebook_error=f"Falha ao conectar Facebook: {_safe_error_message(exc)}"))


@app.route("/facebook/validate", methods=["POST"])
def facebook_validate():
    connection = load_facebook_connection()
    if not connection.get("access_token") or not connection.get("page_id"):
        return redirect(url_for("instagram_central", facebook_error="Facebook ainda não está conectado."))
    try:
        profile = facebook_graph_request(
            connection["page_id"], connection["access_token"],
            params={"fields": "id,name"}, timeout=30
        )
        connection.update({
            "page_id": str(profile.get("id") or connection.get("page_id", "")),
            "page_name": profile.get("name") or connection.get("page_name", ""),
            "validated_at": datetime.now().isoformat(timespec="seconds"),
            "validation_status": "OK",
            "validation_error": "",
        })
        save_facebook_connection(connection)
        return redirect(url_for("instagram_central", facebook_validated="1"))
    except Exception as exc:
        safe = _safe_error_message(exc)
        connection.update({"validation_status": "ERRO", "validation_error": safe})
        save_facebook_connection(connection)
        return redirect(url_for("instagram_central", facebook_error=f"Falha ao validar Facebook: {safe}"))


@app.route("/facebook/disconnect", methods=["POST"])
def facebook_disconnect():
    save_facebook_connection({})
    return redirect(url_for("instagram_central", facebook_disconnected="1"))


@app.route("/instagram/validate", methods=["POST"])
def instagram_validate():
    connection = load_instagram_connection()
    if not connection.get("access_token"):
        return redirect(url_for("instagram_central", oauth_error="Instagram ainda não está conectado."))
    try:
        profile = get_instagram_profile(connection["access_token"])
        connection.update({
            "validated_at": datetime.now().isoformat(timespec="seconds"),
            "user_id": profile.get("id") or connection.get("user_id", ""),
            "username": profile.get("username") or connection.get("username", ""),
            "name": profile.get("name") or connection.get("name", ""),
            "account_type": profile.get("account_type") or connection.get("account_type", ""),
            "validation_status": "OK",
        })
        save_instagram_connection(connection)
        return redirect(url_for("instagram_central", validated="1"))
    except Exception as exc:
        connection["validation_status"] = "ERRO"
        connection["validation_error"] = str(exc)
        save_instagram_connection(connection)
        return redirect(url_for("instagram_central", oauth_error=f"Falha na validação: {exc}"))


@app.route("/instagram/prepare", methods=["POST"])
def instagram_prepare():
    history = reconcile_publication_records(enrich_history(load_history()))
    connection = load_instagram_connection()
    audit = load_publication_audit()
    raw_index = request.form.get("post_index", "0")
    try:
        post_index = int(raw_index)
    except (TypeError, ValueError):
        post_index = 0

    if not connection.get("access_token") or not connection.get("username"):
        return redirect(url_for("instagram_central", post=post_index, oauth_error="Conecte e valide o Instagram antes de preparar a publicação."))
    if not (0 <= post_index < len(history)):
        return redirect(url_for("instagram_central", oauth_error="Post aprovado não encontrado."))

    post = history[post_index]
    prior = find_publication_record(post, audit, connection.get("username", ""))
    if prior:
        return redirect(url_for("instagram_central", post=post_index, published="1", oauth_error="Este post já foi publicado no Instagram. A publicação duplicada foi bloqueada."))
    draft = build_publication_draft(post, post_index, connection)
    save_publication_draft(draft)
    return redirect(url_for("instagram_central", post=post_index, prepared="1"))


@app.route("/instagram/test-media", methods=["POST"])
def instagram_test_media():
    # These reads used to run outside the route's error handler. A transient
    # Supabase/app_state failure could therefore produce Flask's plain HTTP 500
    # instead of returning a safe diagnostic to the user.
    try:
        history = reconcile_publication_records(enrich_history(load_history()))
        connection = load_instagram_connection()
        audit = load_publication_audit()
    except Exception as exc:
        diagnostic_id = "MEDIA-" + secrets.token_hex(4).upper()
        app.logger.exception(
            "Falha ao carregar dados para preparar mídia do Instagram [%s] (%s)",
            diagnostic_id,
            type(exc).__name__,
        )
        return redirect(url_for(
            "instagram_central",
            oauth_error=(
                "Não foi possível carregar os dados para preparar a mídia. "
                "Nenhuma publicação foi feita. Atualize a página e tente novamente. "
                f"Código de diagnóstico: {diagnostic_id}."
            ),
        ))

    try:
        post_index = int(request.form.get("post_index", "0"))
    except (TypeError, ValueError):
        post_index = 0

    if not connection.get("access_token") or not connection.get("username"):
        return redirect(url_for("instagram_central", post=post_index, oauth_error="Conecte o Instagram antes de testar a mídia."))
    if not (0 <= post_index < len(history)):
        return redirect(url_for("instagram_central", oauth_error="Post aprovado não encontrado."))

    try:
        post = history[post_index]
        prior = find_publication_record(post, audit, connection.get("username", ""))
        if prior:
            return redirect(url_for("instagram_central", post=post_index, published="1", oauth_error="Este post já foi publicado no Instagram. O teste de mídia foi bloqueado para evitar duplicação."))
        if str(post.get("plate_redaction_version", "")) != "2":
            return redirect(url_for(
                "instagram_central",
                post=post_index,
                oauth_error=(
                    "Este post usa cópias de mídia antigas, anteriores à normalização quadrada para o Instagram. "
                    "Para evitar repetir o erro de proporção, crie um novo post com as fotos. "
                    "Nenhuma publicação foi feita."
                ),
            ))
        draft = load_publication_draft()
        draft_matches = draft_matches_post(draft, post, post_index, connection)
        if not draft_matches:
            draft = build_publication_draft(post, post_index, connection)

        media_items = instagram_media_items(post)
        existing_container_id = str(draft.get("container_id") or "").strip()
        existing_status = str((draft.get("container_status") or {}).get("status_code") or "").upper()
        if draft_matches and existing_container_id and existing_status not in {"ERROR", "EXPIRED"}:
            # Resume polling the same container after IN_PROGRESS or a timeout.
            container_id = existing_container_id
        else:
            container = instagram_create_container(connection, media_items, draft["full_caption"])
            container_id = str(container.get("id") or "").strip()
            if not container_id:
                raise RuntimeError("Instagram não retornou o ID do container.")
            draft.update({
                "container_id": container_id,
                "container_status": {},
                "public_media_urls": [item["url"] for item in media_items],
                "media_types": [item["media_type"] for item in media_items],
                "status": "MÍDIA EM PROCESSAMENTO NO INSTAGRAM — ainda não publicada",
                "note": "Container criado; aguardando o Instagram concluir o processamento.",
            })
            # Save the container before polling so retry can reuse it.
            save_publication_draft(draft)

        status = _wait_for_container(connection, container_id, timeout=120)
        draft.update({
            "media_tested_at": datetime.now().isoformat(timespec="seconds"),
            "container_id": container_id,
            "container_status": status,
            "public_media_urls": [item["url"] for item in media_items],
            "media_types": [item["media_type"] for item in media_items],
            "status": "MÍDIA PREPARADA NO INSTAGRAM — ainda não publicada",
            "note": "Container criado no Instagram. A publicação final ainda exige confirmação explícita.",
        })
        save_publication_draft(draft)
        return redirect(url_for("instagram_central", post=post_index, media_tested="1"))
    except Exception as exc:
        return redirect(url_for("instagram_central", post=post_index, oauth_error=f"Falha ao preparar mídia no Instagram: {exc}"))


PENDING_PUBLICATION_CONFIRMATION_TTL_SECONDS = 10 * 60


def publication_confirmation_fingerprint(post):
    """Fingerprint approved content so confirmation cannot drift to another post."""
    fields = (
        "approved_at", "vehicle", "service", "caption", "hashtags",
        "before_image", "after_image", "before_images", "after_images",
    )
    fields = fields + ("plate_redaction_version", "plate_redaction_count")
    payload = {field: post.get(field) for field in fields}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _preflight_selected_platforms(post_index, selected):
    """Validate every selected target without creating any public post."""
    selected = set(selected)
    history = reconcile_publication_records(enrich_history(load_history()))
    if not (0 <= post_index < len(history)):
        raise RuntimeError("Post aprovado não encontrado.")
    post = history[post_index]
    queue_status = str(post.get("publication_queue_status", "")).strip().upper()
    if queue_status == "DIVIDIDO":
        raise RuntimeError("Este post original foi dividido em partes. Publique as partes individuais na Fila de Publicações.")
    before_images = post.get("before_images") or ([post.get("before_image")] if post.get("before_image") else [])
    after_images = post.get("after_images") or ([post.get("after_image")] if post.get("after_image") else [])
    if len(before_images) + len(after_images) > 10:
        raise RuntimeError(
            f"Este post tem {len(before_images) + len(after_images)} fotos. "
            "Divida-o em partes na Fila de Publicações antes de preparar a mídia."
        )
    if not publication_key(post):
        raise RuntimeError("O post aprovado não tem uma identificação estável para confirmação.")
    if str(post.get("plate_redaction_version", "")) != "2":
        raise RuntimeError(
            "Este post foi criado antes da normalização quadrada das fotos para o Instagram. "
            "Para publicar com segurança, crie novamente o conteúdo com as fotos e revise as cópias protegidas antes de confirmar."
        )
    if "instagram" in selected:
        preflight_instagram_for_post(post_index)
    if "facebook" in selected:
        fb_connection = load_facebook_connection()
        fb_audit = load_facebook_publication_audit()
        if not fb_connection.get("access_token") or not fb_connection.get("page_id"):
            raise RuntimeError("Conecte a Página do Facebook antes de publicar.")
        if find_facebook_publication_record(post, fb_audit, fb_connection.get("page_id", "")):
            raise RuntimeError("Este post já foi publicado no Facebook. A duplicação foi bloqueada.")
        facebook_media_urls(post)
    return post


def _prepare_publication_confirmation(post_index, selected):
    """First step: preflight and display a separate confirmation page; never publish here."""
    session.pop("pending_platform_publication", None)
    selected = sorted({value for value in selected if value in {"instagram", "facebook"}})
    if not selected:
        return redirect(url_for(
            "instagram_central",
            post=post_index,
            oauth_error="Selecione pelo menos uma plataforma para publicar.",
        ))

    post = {}
    try:
        post = _preflight_selected_platforms(post_index, selected)
        session["pending_platform_publication"] = {
            "id": secrets.token_urlsafe(32),
            "created_at": time.time(),
            "post_index": post_index,
            "approved_at": publication_key(post),
            "fingerprint": publication_confirmation_fingerprint(post),
            "platforms": selected,
        }
        return redirect(url_for("confirm_platform_publication"))
    except Exception as exc:
        message = _safe_error_message(exc)[:350]
        for platform in selected:
            try:
                log_platform_attempt(
                    platform,
                    post or {"approved_at": "", "vehicle": "", "service": ""},
                    "BLOQUEADO",
                    message,
                )
            except Exception:
                pass
        session["last_platform_results"] = [
            {"platform": platform, "status": "BLOQUEADO", "message": message}
            for platform in selected
        ]
        return redirect(url_for("instagram_central", post=post_index, platforms_result="1"))


def _load_pending_publication_confirmation():
    """Load a recent, one-post confirmation intent and reject stale or altered content."""
    pending = session.get("pending_platform_publication")
    if not isinstance(pending, dict):
        return None, None, "Não existe uma confirmação de publicação pendente. Selecione as plataformas novamente."

    try:
        created_at = float(pending.get("created_at", 0))
        post_index = int(pending.get("post_index", -1))
        selected = pending.get("platforms", [])
    except (TypeError, ValueError):
        session.pop("pending_platform_publication", None)
        return None, None, "A confirmação ficou inválida. Inicie novamente."

    if created_at <= 0 or time.time() - created_at > PENDING_PUBLICATION_CONFIRMATION_TTL_SECONDS:
        session.pop("pending_platform_publication", None)
        return None, None, "A confirmação expirou após 10 minutos. Inicie novamente."
    if not isinstance(selected, list) or not selected or any(
        platform not in {"instagram", "facebook"} for platform in selected
    ) or len(set(selected)) != len(selected):
        session.pop("pending_platform_publication", None)
        return None, None, "A seleção de plataformas da confirmação é inválida. Inicie novamente."

    history = reconcile_publication_records(enrich_history(load_history()))
    if not (0 <= post_index < len(history)):
        session.pop("pending_platform_publication", None)
        return None, None, "O post aprovado não foi encontrado. Inicie novamente."
    post = history[post_index]
    if (
        publication_key(post) != str(pending.get("approved_at", ""))
        or publication_confirmation_fingerprint(post) != str(pending.get("fingerprint", ""))
    ):
        session.pop("pending_platform_publication", None)
        return None, None, "O conteúdo do post mudou desde a confirmação inicial. Revise o post e inicie novamente."
    return pending, post, None


@app.route("/instagram/publish", methods=["POST"])
def instagram_publish():
    # Legacy Instagram-only button now uses the same server-rendered confirmation step.
    try:
        post_index = int(request.form.get("post_index", "0"))
    except (TypeError, ValueError):
        post_index = 0
    return _prepare_publication_confirmation(post_index, {"instagram"})


@app.route("/publicar-plataformas", methods=["POST"])
def publish_selected_platforms():
    try:
        post_index = int(request.form.get("post_index", "0"))
    except (TypeError, ValueError):
        post_index = 0
    selected = set(request.form.getlist("platforms"))
    # This request only prepares a confirmation intent. It cannot publish anything.
    return _prepare_publication_confirmation(post_index, selected)


def _render_publication_confirmation(pending, post, confirmation_error=None):
    """Render the exact target captions for the pending platform selection."""
    selected = pending["platforms"]
    draft = load_publication_draft() if "instagram" in selected else {}
    instagram_caption = str(draft.get("full_caption") or "") if "instagram" in selected else ""
    facebook_caption = build_full_caption(post) if "facebook" in selected else ""
    return render_template(
        "confirm_platform_publication.html",
        pending=pending,
        post=post,
        post_index=int(pending["post_index"]),
        selected_platforms=selected,
        confirmation_id=pending["id"],
        instagram_caption_preview=instagram_caption,
        facebook_caption_preview=facebook_caption,
        connection=load_instagram_connection(),
        facebook_connection=load_facebook_connection(),
        confirmation_minutes=PENDING_PUBLICATION_CONFIRMATION_TTL_SECONDS // 60,
        confirmation_error=confirmation_error,
    )


@app.route("/publicar-plataformas/confirmar", methods=["GET", "POST"])
def confirm_platform_publication():
    if request.method == "GET":
        pending, post, error = _load_pending_publication_confirmation()
        if error:
            return redirect(url_for("instagram_central", oauth_error=error))
        return _render_publication_confirmation(pending, post)

    pending = session.get("pending_platform_publication")
    supplied_id = request.form.get("confirmation_id", "")
    if (
        not isinstance(pending, dict)
        or not pending.get("id")
        or not supplied_id
        or not secrets.compare_digest(str(pending.get("id", "")), supplied_id)
    ):
        session.pop("pending_platform_publication", None)
        return redirect(url_for(
            "instagram_central",
            oauth_error="Confirmação ausente ou inválida. Nenhuma plataforma foi publicada.",
        ))

    action = request.form.get("action", "")
    if action == "cancel":
        session.pop("pending_platform_publication", None)
        return redirect(url_for("instagram_central", post=pending.get("post_index", 0), publication_cancelled="1"))
    if action != "publish":
        session.pop("pending_platform_publication", None)
        return redirect(url_for(
            "instagram_central",
            post=pending.get("post_index", 0),
            oauth_error="Ação de confirmação inválida. Nenhuma plataforma foi publicada.",
        ))

    # Enforce explicit acknowledgement on the server; browser-required is only a convenience.
    if request.form.get("acknowledge") != "yes":
        pending, post, error = _load_pending_publication_confirmation()
        if error:
            return redirect(url_for("instagram_central", oauth_error=error))
        return _render_publication_confirmation(
            pending,
            post,
            confirmation_error="Marque a caixa de autorização para confirmar. Nenhuma publicação foi feita.",
        )

    pending, post, error = _load_pending_publication_confirmation()
    if error:
        return redirect(url_for("instagram_central", oauth_error=error))
    post_index = int(pending["post_index"])
    selected = list(pending["platforms"])

    # Recheck authorization, media and duplicate guards at the moment of confirmation.
    try:
        post = _preflight_selected_platforms(post_index, selected)
    except Exception as exc:
        session.pop("pending_platform_publication", None)
        message = _safe_error_message(exc)[:350]
        for platform in selected:
            try:
                log_platform_attempt(platform, post or {"approved_at": "", "vehicle": "", "service": ""}, "BLOQUEADO", message)
            except Exception:
                pass
        session["last_platform_results"] = [
            {"platform": platform, "status": "BLOQUEADO", "message": message}
            for platform in selected
        ]
        return redirect(url_for("instagram_central", post=post_index, platforms_result="1"))

    # Consume the pending intent before making any external publishing request.
    session.pop("pending_platform_publication", None)
    results = []
    for platform in ("instagram", "facebook"):
        if platform not in selected:
            continue
        try:
            if platform == "instagram":
                record = publish_instagram_for_post(post_index)
                label, target_id = "Instagram", record.get("media_id", "")
            else:
                record = publish_facebook_for_post(post_index)
                label, target_id = "Facebook", record.get("post_id", "")
            results.append({
                "platform": platform,
                "status": "PUBLICADO",
                "message": f"{label} publicado. ID: {target_id}",
            })
        except Exception as exc:
            message = _safe_error_message(exc)[:350]
            try:
                latest_history = enrich_history(load_history())
                latest_post = latest_history[post_index] if 0 <= post_index < len(latest_history) else post
                log_platform_attempt(platform, latest_post, "ERRO", message)
            except Exception:
                pass
            results.append({"platform": platform, "status": "ERRO", "message": message})
    session["last_platform_results"] = results
    return redirect(url_for("instagram_central", post=post_index, platforms_result="1"))


@app.route("/fila-publicacoes", methods=["GET"])
def publication_queue():
    """Manual publication queue with editable photo order and discard protection."""
    history = reconcile_publication_records(enrich_history(load_history()))
    connection = load_instagram_connection()
    audit = load_publication_audit()
    facebook_connection = load_facebook_connection()
    facebook_audit = load_facebook_publication_audit()
    pending = []
    published = []
    discarded = []
    divided = []

    for index, item in enumerate(history):
        pub = find_publication_record(item, audit, connection.get("username", ""))
        facebook_pub = find_facebook_publication_record(
            item, facebook_audit, facebook_connection.get("page_id", "")
        )
        before_images = item.get("before_images") or ([item.get("before_image")] if item.get("before_image") else [])
        after_images = item.get("after_images") or ([item.get("after_image")] if item.get("after_image") else [])
        queue_status = str(item.get("publication_queue_status", "")).upper()
        record = {
            "index": index,
            "approved_at": item.get("approved_at", ""),
            "vehicle": item.get("vehicle", ""),
            "service": item.get("service", ""),
            "caption": item.get("caption", ""),
            "before_images": before_images,
            "after_images": after_images,
            "photo_count": len(before_images) + len(after_images),
            "publication": pub,
            "facebook_publication": facebook_pub,
            "facebook_published": str(facebook_pub.get("status", "")).upper() == "PUBLICADO",
            "queue_status": queue_status,
            "split_count": len(item.get("split_children") or []),
            "split_pending_facebook": bool(item.get("split_pending_facebook", False)),
            "publication_series_title": item.get("publication_series_title", ""),
            "publication_series_label": item.get("publication_series_label", ""),
            "publication_part_number": item.get("publication_part_number"),
            "publication_part_count": item.get("publication_part_count"),
            "series_badge_enabled": bool(item.get("series_badge_enabled", False)),
            "series_label_note": item.get("series_label_note", ""),
        }
        if queue_status == "DESCARTADO":
            discarded.append(record)
        elif queue_status == "DIVIDIDO":
            divided.append(record)
        elif (
            str(pub.get("status", "")).upper() == "PUBLICADO"
            and record["split_pending_facebook"]
            and not record["facebook_published"]
        ):
            # A split child whose first ten photos already appeared on Instagram
            # remains actionable until its Facebook publication also succeeds.
            pending.append(record)
        elif str(pub.get("status", "")).upper() == "PUBLICADO":
            published.append(record)
        else:
            pending.append(record)

    return render_template(
        "publication_queue.html",
        pending=pending,
        published=published,
        discarded=discarded,
        divided=divided,
        connection=connection,
        split_done=request.args.get("split_done") == "1",
        queue_error=request.args.get("queue_error", ""),
        total=sum(1 for item in history if str(item.get("publication_queue_status", "")).upper() != "DIVIDIDO"),
    )


@app.route("/fila-publicacoes/editar", methods=["POST"])
def edit_publication_queue():
    """Persist photo order/removals or discard an approved post from the queue."""
    approved_at = request.form.get("approved_at", "").strip()
    action = request.form.get("queue_action", "save_order").strip()
    history = load_history()
    target = next((item for item in history if str(item.get("approved_at", "")).strip() == approved_at), None)
    if target is None:
        return redirect(url_for("publication_queue"))

    if action == "discard":
        target["publication_queue_status"] = "DESCARTADO"
        target["discarded_from_queue_at"] = datetime.now().isoformat(timespec="seconds")
        target["status"] = "DESCARTADO DA FILA DE PUBLICAÃ‡ÃƒO"
        save_history(history)
        discarded_items = _load_state("publication_discarded", PUBLICATION_DISCARDED_PATH, [])
        if not isinstance(discarded_items, list):
            discarded_items = []
        discarded_items.append({
            "approved_at": approved_at,
            "vehicle": target.get("vehicle", ""),
            "service": target.get("service", ""),
            "discarded_at": target["discarded_from_queue_at"],
            "reason": request.form.get("reason", "Descartado manualmente da fila de publicação.").strip(),
        })
        _save_state("publication_discarded", PUBLICATION_DISCARDED_PATH, discarded_items)
        return redirect(url_for("publication_queue"))

    before = [x.strip() for x in request.form.getlist("before_images") if x.strip()]
    after = [x.strip() for x in request.form.getlist("after_images") if x.strip()]
    if not before or not after:
        return redirect(url_for("publication_queue"))

    target["before_images"] = before
    target["after_images"] = after
    target.pop("before_image", None)
    target.pop("after_image", None)
    target["photo_order_updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_history(history)
    return redirect(url_for("publication_queue"))


@app.route("/fila-publicacoes/dividir", methods=["POST"])
def split_publication_in_queue():
    """Create independently reviewable parts without deleting the source post."""
    approved_at = request.form.get("approved_at", "").strip()
    history = load_history()
    parent_index = next(
        (
            index for index, item in enumerate(history)
            if str(item.get("approved_at", "")).strip() == approved_at
        ),
        -1,
    )
    if parent_index < 0:
        return redirect(url_for("publication_queue", queue_error="Não encontrei o post selecionado."))

    parent = history[parent_index]
    queue_status = str(parent.get("publication_queue_status", "")).strip().upper()
    if queue_status in {"DESCARTADO", "DIVIDIDO"}:
        return redirect(url_for("publication_queue", queue_error="Este post já foi descartado ou dividido."))

    before = parent.get("before_images") or ([parent.get("before_image")] if parent.get("before_image") else [])
    after = parent.get("after_images") or ([parent.get("after_image")] if parent.get("after_image") else [])
    total_photos = len(before) + len(after)
    if total_photos <= 10:
        return redirect(url_for("publication_queue", queue_error="Este post tem até 10 fotos e não precisa ser dividido."))

    instagram_connection = load_instagram_connection()
    instagram_record = find_publication_record(
        parent, load_publication_audit(), instagram_connection.get("username", "")
    )
    facebook_connection = load_facebook_connection()
    facebook_record = find_facebook_publication_record(
        parent, load_facebook_publication_audit(), facebook_connection.get("page_id", "")
    )
    if str(facebook_record.get("status", "")).upper() == "PUBLICADO":
        return redirect(url_for(
            "publication_queue",
            queue_error=(
                "Este post já foi publicado no Facebook. A divisão foi bloqueada para evitar duplicações. "
                "Nenhum post foi alterado."
            ),
        ))

    try:
        parts = split_photo_collections(before, after, max_items=10)
    except (TypeError, ValueError) as exc:
        app.logger.warning("Falha ao dividir publicação aprovada: %s", type(exc).__name__)
        return redirect(url_for(
            "publication_queue",
            queue_error="Não foi possível dividir as fotos com segurança. O post original foi preservado.",
        ))

    if len(parts) < 2 or any(
        len(part["before_images"]) + len(part["after_images"]) > 10 for part in parts
    ):
        return redirect(url_for(
            "publication_queue",
            queue_error="A divisão não gerou partes válidas. O post original foi preservado.",
        ))

    source_before_original = parent.get("before_images_original") or []
    source_after_original = parent.get("after_images_original") or []
    before_original_offset = 0
    after_original_offset = 0
    existing_keys = {
        str(item.get("approved_at", "")).strip()
        for item in history
        if str(item.get("approved_at", "")).strip()
    }
    children = []
    now = datetime.now()
    instagram_already_published = str(instagram_record.get("status", "")).upper() == "PUBLICADO"
    for part_number, part in enumerate(parts, 1):
        part_before = list(part["before_images"])
        part_after = list(part["after_images"])
        part_before_original = source_before_original[
            before_original_offset:before_original_offset + len(part_before)
        ]
        part_after_original = source_after_original[
            after_original_offset:after_original_offset + len(part_after)
        ]
        before_original_offset += len(part_before)
        after_original_offset += len(part_after)

        candidate = now + timedelta(microseconds=part_number)
        part_approved_at = candidate.isoformat(timespec="microseconds")
        while part_approved_at in existing_keys:
            candidate += timedelta(microseconds=1)
            part_approved_at = candidate.isoformat(timespec="microseconds")
        existing_keys.add(part_approved_at)

        child = copy.deepcopy(parent)
        child["approved_at"] = part_approved_at
        child["source_approved_at"] = approved_at
        child["publication_group_id"] = approved_at
        child["publication_part_number"] = part_number
        child["publication_part_count"] = len(parts)
        child["publication_queue_status"] = "AGUARDANDO"
        child["status"] = f"APROVADO — PARTE {part_number}/{len(parts)} — aguardando publicação"
        child["before_images"] = part_before
        child["after_images"] = part_after
        child["before_image"] = part_before[0] if part_before else ""
        child["after_image"] = part_after[0] if part_after else ""
        child["before_images_original"] = list(part_before_original)
        child["after_images_original"] = list(part_after_original)

        service_text = str(parent.get("service", "") or "serviço automotivo").strip()
        vehicle_text = str(parent.get("vehicle", "") or "").strip()
        series_title = build_series_title(vehicle_text)
        if int(parent.get("service_group_count", 1) or 1) > 1:
            series_title = f"{series_title} · {service_text}"
        part_caption = build_series_caption(
            parent.get("caption", ""),
            vehicle_text,
            service_text,
            part_number,
            len(parts),
            next_service=service_text if part_number < len(parts) else None,
            hashtags=parent.get("hashtags", ""),
            series_title=series_title,
        )
        child["publication_series_title"] = series_title
        child["publication_series_label"] = (
            f"Parte {part_number}/{len(parts)} · {service_text}"
        )
        child["publication_part_number"] = part_number
        child["publication_part_count"] = len(parts)
        already_published_first_part = part_number == 1 and instagram_already_published
        child.pop("facebook_caption_override", None)
        child.pop("facebook_before_images_override", None)
        if already_published_first_part:
            # Never pretend the historical Instagram caption/photo was updated:
            # this part already exists online and must not be published again.
            child["caption"] = str(parent.get("caption", "") or "").strip()
            child["series_badge_enabled"] = False
            child["series_label_note"] = (
                "A parte 1 já estava publicada no Instagram antes dos rótulos da série. "
                "Ela não será reenviada nem alterada no Instagram; a versão do Facebook "
                "receberá o selo e a legenda da sequência."
            )
            facebook_badge = _create_series_badge_ref(
                part_before[0], vehicle_text, service_text, part_number, len(parts),
                series_title=series_title,
            )
            child["facebook_before_images_override"] = list(part_before)
            child["facebook_before_images_override"][0] = facebook_badge
            child["facebook_caption_override"] = part_caption
            child["series_badge_enabled"] = True
        else:
            child["caption"] = part_caption
            child["before_images"][0] = _create_series_badge_ref(
                part_before[0], vehicle_text, service_text, part_number, len(parts),
                series_title=series_title,
            )
            child["before_image"] = child["before_images"][0]
            child["series_badge_enabled"] = True
            child["series_label_note"] = ""
        child["plate_redaction_message"] = (
            f"Parte {part_number} de {len(parts)}. Esta publicação usa {len(part_before) + len(part_after)} "
            "cópia(s) de mídia protegidas. Revise todas as prévias; a detecção automática pode falhar."
        )
        child["plate_redaction_image_count"] = len(part_before) + len(part_after)
        child["source_plate_redaction_count"] = parent.get("plate_redaction_count")
        # The original aggregate detection count cannot be attributed to individual
        # photos from the saved summary, so do not present it as a per-part count.
        child["plate_redaction_count"] = None
        child["split_pending_facebook"] = False
        child.pop("split_children", None)
        child.pop("discarded_from_queue_at", None)
        child.pop("instagram_publication", None)
        child.pop("facebook_publication", None)

        # The original ten carousel items already published to Instagram must not
        # be published a second time. Transfer their existing record to part one.
        if part_number == 1 and instagram_already_published:
            child["instagram_publication"] = copy.deepcopy(instagram_record)
            child["status"] = "PUBLICADO NO INSTAGRAM — aguardando Facebook"
            child["split_pending_facebook"] = True

        children.append(child)

    parent["publication_queue_status"] = "DIVIDIDO"
    parent["split_at"] = datetime.now().isoformat(timespec="seconds")
    parent["split_children"] = [child["approved_at"] for child in children]
    parent["split_part_count"] = len(children)
    parent["status"] = "DIVIDIDO EM PARTES — post original preservado para histórico"
    history[parent_index + 1:parent_index + 1] = children
    save_history(history)

    app.logger.info(
        "Post aprovado dividido em partes: photos=%d parts=%d instagram_previously_published=%s",
        total_photos,
        len(children),
        instagram_already_published,
    )
    return redirect(url_for("publication_queue", split_done="1"))


@app.route("/instagram")
def instagram_central():
    history = reconcile_publication_records(enrich_history(load_history()))
    audit = load_publication_audit()

    # Accept both parameter names used by previous V19 builds and always
    # normalize the value to an integer before comparing it with list bounds.
    raw_index = request.args.get("post")
    if raw_index is None:
        raw_index = request.args.get("post_index", "0")
    try:
        post_index = int(raw_index)
    except (TypeError, ValueError):
        post_index = 0

    post = None
    if 0 <= post_index < len(history):
        post = history[post_index]

    connection = load_instagram_connection()
    draft = load_publication_draft()
    publication = find_publication_record(post, audit, connection.get("username", "")) if post else {}
    facebook_connection = load_facebook_connection()
    facebook_audit = load_facebook_publication_audit()
    facebook_publication = find_facebook_publication_record(post, facebook_audit, facebook_connection.get("page_id", "")) if post else {}
    draft_matches_selected = bool(post and draft_matches_post(draft, post, post_index, connection))
    platform_results = session.pop("last_platform_results", []) if request.args.get("platforms_result") == "1" else []
    return render_template(
        "instagram.html",
        history=history,
        post=post,
        post_index=post_index,
        publication=publication,
        already_published=bool(publication),
        connection=connection,
        draft=draft,
        draft_matches_selected=draft_matches_selected,
        facebook_connection=facebook_connection,
        facebook_ready=facebook_meta_ready(),
        facebook_publication=facebook_publication,
        facebook_already_published=bool(facebook_publication),
        platform_results=platform_results,
        meta_ready=meta_ready(),
        oauth_error=request.args.get("oauth_error"),
        setup_missing=request.args.get("setup") == "missing",
        connected=request.args.get("connected") == "1",
        validated=request.args.get("validated") == "1",
        prepared=request.args.get("prepared") == "1",
        media_tested=request.args.get("media_tested") == "1",
        published=request.args.get("published") == "1",
        disconnected=request.args.get("disconnected") == "1",
        facebook_connected=request.args.get("facebook_connected") == "1",
        facebook_validated=request.args.get("facebook_validated") == "1",
        facebook_disconnected=request.args.get("facebook_disconnected") == "1",
        facebook_error=request.args.get("facebook_error"),
    )


@app.route("/health", methods=["GET"])
def health():
    """Small deployment diagnostic without exposing secrets or application state."""
    cloud = cloud_state_status()
    ok = bool(cloud["table_available"]) if cloud["enabled"] else True
    return {
        "status": "ok" if ok else "degraded",
        "cloud_state": {
            "enabled": cloud["enabled"],
            "required": cloud["required"],
            "table_available": cloud["table_available"],
        },
    }, (200 if ok else 503)


@app.route("/uploads/<folder>/<filename>")
def uploaded_file(folder, filename):
    from flask import send_from_directory

    if folder == "before":
        directory = BEFORE_DIR
    elif folder == "after":
        directory = AFTER_DIR
    else:
        return "Not found", 404

    return send_from_directory(directory, filename)


@app.route("/privacy")
def privacy_policy():
    return render_template("privacy.html")
if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
    host = os.getenv("FLASK_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("FLASK_PORT", "8001"))
    app.run(host=host, port=port, debug=debug)



