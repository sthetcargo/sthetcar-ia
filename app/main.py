from pathlib import Path
from datetime import datetime
import json
import os
import re
import secrets
import urllib.parse
import urllib.request
import urllib.error

from flask import Flask, render_template, request, url_for, redirect, session, abort
from werkzeug.utils import secure_filename

from app.ai.vision import analyze_before_after
from app.ai.copywriter import generate_post
from app.ai.gemini import rewrite_caption
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

for folder in (BEFORE_DIR, AFTER_DIR, GENERATED_DIR):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

PUBLICATION_AUDIT_FILE = BASE_DIR / "generated" / "publicacoes_instagram.json"

def load_publication_audit():
    data = _load_state("publication_audit", PUBLICATION_AUDIT_FILE, [])
    return data if isinstance(data, list) else []

def save_publication_audit(items):
    _save_state("publication_audit", PUBLICATION_AUDIT_FILE, items)


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
MAX_PHOTOS_PER_SIDE = 6
MAX_TOTAL_UPLOAD_BYTES = 30 * 1024 * 1024
ALLOWED = {"jpg", "jpeg", "png", "webp"}


STATE_FILES = {
    "history": HISTORY_PATH,
    "approval_queue": APPROVAL_QUEUE_PATH,
    "instagram_connection": META_TOKEN_PATH,
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
        "status": "PREPARADO â€” nÃ£o publicado",
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
        "note": "Rascunho local. Nenhuma chamada de publicaÃ§Ã£o final foi executada.",
    }


def draft_matches_post(draft, post, post_index, connection):
    return (
        bool(draft)
        and str(draft.get("post_index", "")) == str(post_index)
        and draft.get("approved_at", "") == post.get("approved_at", "")
        and draft.get("instagram", {}).get("user_id", "") == connection.get("user_id", "")
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




def _http_json_request(url, data=None, method="GET", timeout=60):
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
        raise RuntimeError(f"Instagram API {exc.code}: {json.dumps(detail, ensure_ascii=False)}") from exc



def local_path_from_upload_url(value):
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    path = parsed.path if parsed.scheme else value
    if path.startswith("/uploads/"):
        return path
    return ""


def instagram_media_urls(post):
    # Preserve the approved order: BEFORE 1, AFTER 1, BEFORE 2, AFTER 2...
    after = [x for x in (post.get("after_images") or [post.get("after_image")]) if x]
    before = [x for x in (post.get("before_images") or [post.get("before_image")]) if x]
    chosen = []
    for index in range(max(len(before), len(after))):
        if index < len(before):
            chosen.append((before[index], "before"))
        if index < len(after):
            chosen.append((after[index], "after"))

    result = []
    for value, side in chosen[:10]:
        ref = ensure_remote_image_ref(value, side)
        if is_remote_ref(ref):
            url = signed_url(remote_path_from_ref(ref), 900)
            if url:
                result.append(url)
                continue
        if str(value).startswith("http"):
            result.append(str(value))

    if not result:
        raise RuntimeError("Nenhuma foto vÃ¡lida foi encontrada para publicaÃ§Ã£o.")
    return result


def instagram_create_container(connection, image_urls, caption):
    cfg = instagram_config()
    base = f"https://graph.instagram.com/{cfg['api_version']}"
    user_id = connection.get("user_id", "")
    token = connection.get("access_token", "")
    if not user_id or not token:
        raise RuntimeError("ConexÃ£o do Instagram incompleta.")

    if len(image_urls) == 1:
        data = {
            "image_url": image_urls[0],
            "caption": caption,
            "access_token": token,
        }
        return _http_json_request(f"{base}/{user_id}/media", data=data, method="POST", timeout=90)

    children = []
    for image_url in image_urls:
        child = _http_json_request(
            f"{base}/{user_id}/media",
            data={
                "image_url": image_url,
                "is_carousel_item": "true",
                "access_token": token,
            },
            method="POST",
            timeout=90,
        )
        child_id = child.get("id")
        if not child_id:
            raise RuntimeError(f"Instagram nÃ£o retornou o ID do item do carrossel: {child}")
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
        raise RuntimeError("ConexÃ£o do Instagram incompleta.")
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
        "higienizaÃ§Ã£o interna": ["#HigienizacaoInterna", "#LimpezaAutomotiva"],
        "lavagem e higienizaÃ§Ã£o": ["#LavagemAutomotiva", "#HigienizacaoAutomotiva"],
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
        r"VE[IÃ]CULO\s+IDENTIFICADO\s*:\s*([^\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        value = match.group(1).strip(" .,:;()-")
        invalid_values = {
            "nÃ£o identificado", "nao identificado", "nÃ£o informado", "nao informado",
            "nÃ£o foi possÃ­vel determinar", "nao foi possivel determinar",
            "posicionada sobre uma", "posicionado sobre uma", "nÃ£o foi identificada",
            "nao foi identificada", "nÃ£o Ã© possÃ­vel identificar", "nao e possivel identificar",
        }
        value_key = re.sub(r"\s+", " ", value.casefold()).strip()
        # Never turn a fragment of the generated prose into the vehicle name.
        if value and value_key not in invalid_values and len(value.split()) <= 5:
            generic_starts = ("posicion", "localiz", "apresent", "observ", "mostr", "estÃ¡", "esta ")
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
    # vehicle type, e.g. "picape S-10" or "veÃ­culo Corolla".
    match = re.search(
        r"(?:ve[iÃ­]culo|picape|pickup|carro|autom[oÃ³]vel|caminhonete)\s+(?:da\s+marca\s+)?([A-Za-z0-9][A-Za-z0-9-]*(?:\s+[A-Za-z0-9][A-Za-z0-9-]*){0,2})",
        normalized,
        flags=re.IGNORECASE,
    )
    if match:
        value = match.group(1).strip(" .,:;()")
        # Avoid swallowing generic prose instead of a model.
        stop = {"passou", "apresenta", "foi", "estÃ¡", "esta", "tem", "com", "por"}
        first = value.split()[0].lower() if value else ""
        if value and first not in stop and len(value) <= 40:
            return value

    return ""

def infer_service(text: str):
    """Infer a conservative service label from the AI analysis when blank."""
    text = (text or "").lower()
    candidates = [
        ("Lavagem e HigienizaÃ§Ã£o", ("lavagem", "higienizaÃ§Ã£o")),
        ("Lavagem e HigienizaÃ§Ã£o", ("lavagem", "limpeza interna")),
        ("HigienizaÃ§Ã£o Interna", ("higienizaÃ§Ã£o interna",)),
        ("Lavagem Externa", ("lavagem externa",)),
        ("Polimento", ("polimento",)),
        ("Detalhamento Automotivo", ("detalhamento",)),
    ]
    for label, terms in candidates:
        if all(term in text for term in terms):
            return label
    return ""


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



def dashboard_metrics(history):
    """Build metrics using the reconciled publication audit as source of truth."""
    history = reconcile_publication_records(history)
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
    return render_template(
        "dashboard.html",
        result=None,
        error="O servidor recusou o envio porque o pacote ficou grande demais. Selecione menos fotos ou tente novamente; o V11 jÃ¡ comprime as imagens no navegador antes de enviar.",
        approved=None,
        history=load_history(),
        approval_queue=load_approval_queue(),
    ), 413


@app.route("/", methods=["GET", "POST"])
def dashboard():
    result = None
    error = request.args.get("queue_error")
    approved = None
    history = reconcile_publication_records(enrich_history(load_history()))
    approval_queue = load_approval_queue()
    metrics = dashboard_metrics(history)

    if request.method == "POST":
        action = request.form.get("action", "generate")

        if action == "approve":
            caption = request.form.get("caption", "").strip()
            hashtags = request.form.get("hashtags", "").strip()
            vehicle = request.form.get("vehicle", "").strip()
            service = request.form.get("service", "").strip()
            pending_id = request.form.get("pending_id", "").strip()

            if not vehicle:
                error = "Informe o veÃ­culo antes de aprovar o conteÃºdo."
            elif not service:
                error = "Informe o serviÃ§o realizado antes de aprovar o conteÃºdo."
            elif not caption:
                error = "A legenda nÃ£o pode ficar vazia."
            else:
                approved_at = datetime.now().isoformat(timespec="seconds")
                approval = {
                    "approved_at": approved_at,
                    "caption": caption,
                    "hashtags": hashtags,
                    "vehicle": vehicle,
                    "service": service,
                    "status": "APROVADO â€” aguardando conexÃ£o com o Instagram",
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
                    "status": "APROVADO â€” aguardando conexÃ£o com o Instagram",
                    # The DOM order is the approved order. V29 lets the user drag/reorder
                    # photos before approval and this list preserves that exact sequence.
                    "before_images": ordered_before,
                    "after_images": ordered_after,
                    "before_image": ordered_before[0] if ordered_before else "",
                    "after_image": ordered_after[0] if ordered_after else "",
                    "vehicle": request.form.get("vehicle", "").strip(),
                    "service": request.form.get("service", "").strip(),
                    "analysis": request.form.get("analysis", "").strip(),
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

            if not before_files or not after_files:
                error = "Envie pelo menos 1 foto em ANTES e 1 foto em DEPOIS."
            elif len(before_files) > MAX_PHOTOS_PER_SIDE or len(after_files) > MAX_PHOTOS_PER_SIDE:
                error = f"VocÃª pode enviar atÃ© {MAX_PHOTOS_PER_SIDE} fotos em cada lado."
            elif any(not allowed(f.filename) for f in before_files + after_files):
                error = "Use JPG, JPEG, PNG ou WEBP."
            elif request.content_length and request.content_length > app.config["MAX_CONTENT_LENGTH"]:
                error = "O envio ultrapassou o limite de 30 MB. As fotos sÃ£o comprimidas automaticamente antes do envio; se isso acontecer, reduza a quantidade de fotos."
            else:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                before_paths, after_paths = [], []
                before_urls, after_urls = [], []
                try:
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

                    analysis = analyze_before_after(
                        before_paths, after_paths, vehicle, service, notes
                    )
                    before_urls = [_remote_ref_for_local(path, "before") for path in before_paths]
                    after_urls = [_remote_ref_for_local(path, "after") for path in after_paths]
                    # If the user left these fields blank, use only conservative
                    # labels that are explicitly supported by the visual analysis.
                    resolved_vehicle = vehicle or infer_vehicle(analysis)
                    resolved_service = service or infer_service(analysis)
                    post = generate_post(analysis, resolved_vehicle, resolved_service, notes)
                    caption, hashtags = split_post(post, resolved_vehicle, resolved_service)

                    result = {
                        "analysis": analysis,
                        "post": post,
                        "caption": caption,
                        "hashtags": hashtags,
                        "before_images": before_urls,
                        "after_images": after_urls,
                        "before_image": before_urls[0],
                        "after_image": after_urls[0],
                        "vehicle": resolved_vehicle,
                        "service": resolved_service,
                    }
                    pending = {
                        **result,
                        "pending_id": new_pending_id(),
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "status": "AGUARDANDO APROVAÃ‡ÃƒO",
                    }
                    approval_queue.insert(0, pending)
                    save_approval_queue(approval_queue)
                    result["pending_id"] = pending["pending_id"]
                except Exception as exc:
                    error = str(exc)

    return render_template(
        "dashboard.html",
        result=result,
        error=error,
        approved=approved,
        history=history,
        approval_queue=approval_queue,
        metrics=metrics,
    )


@app.route("/revisar/<pending_id>")
def review_pending(pending_id):
    queue = load_approval_queue()
    pending = next((item for item in queue if item.get("pending_id") == pending_id), None)
    if not pending:
        return redirect(url_for("dashboard", queue_error="ConteÃºdo pendente nÃ£o encontrado."))
    return render_template("review_pending.html", result=pending, pending=pending, history=enrich_history(load_history()), approval_queue=queue)


@app.route("/revisar/<pending_id>/reescrever", methods=["POST"])
def rewrite_pending_caption(pending_id):
    """Rewrite only the caption/hashtags using the existing approved visual analysis."""
    queue = load_approval_queue()
    pending = next((item for item in queue if item.get("pending_id") == pending_id), None)
    if not pending:
        return redirect(url_for("dashboard", queue_error="ConteÃºdo pendente nÃ£o encontrado."))

    vehicle = request.form.get("vehicle", pending.get("vehicle", "")).strip()
    service = request.form.get("service", pending.get("service", "")).strip()
    analysis = pending.get("analysis", "").strip()
    # O texto digitado no editor tem prioridade sobre o valor salvo na fila.
    current_caption = request.form.get("current_caption", "").strip() or pending.get("caption", "").strip()

    try:
        rewrite_count = int(pending.get("rewrite_count", 0) or 0) + 1
        rewrite_angle = request.form.get("rewrite_angle", "").strip()
        generated = rewrite_caption(
            analysis,
            vehicle,
            service,
            current_caption,
            rewrite_count=rewrite_count,
            rewrite_angle=rewrite_angle,
        )
        caption, hashtags = split_post(generated, vehicle, service)
        if not caption.strip():
            raise RuntimeError("A IA nÃ£o retornou uma nova legenda. Tente novamente em alguns instantes.")
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
        return redirect(url_for("review_pending", pending_id=pending_id, rewrite_error=str(exc)))


@app.route("/descartar/<pending_id>", methods=["POST"])
def discard_pending(pending_id):
    queue = load_approval_queue()
    pending = next((item for item in queue if item.get("pending_id") == pending_id), None)
    if not pending:
        return redirect(url_for("dashboard", queue_error="ConteÃºdo pendente nÃ£o encontrado."))

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
    return redirect(url_for("dashboard", notice="ConteÃºdo descartado. As fotos pendentes foram removidas do fluxo."))


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
            raise RuntimeError("A Meta nÃ£o retornou o token de acesso.")
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


@app.route("/instagram/validate", methods=["POST"])
def instagram_validate():
    connection = load_instagram_connection()
    if not connection.get("access_token"):
        return redirect(url_for("instagram_central", oauth_error="Instagram ainda nÃ£o estÃ¡ conectado."))
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
        return redirect(url_for("instagram_central", oauth_error=f"Falha na validaÃ§Ã£o: {exc}"))


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
        return redirect(url_for("instagram_central", post=post_index, oauth_error="Conecte e valide o Instagram antes de preparar a publicaÃ§Ã£o."))
    if not (0 <= post_index < len(history)):
        return redirect(url_for("instagram_central", oauth_error="Post aprovado nÃ£o encontrado."))

    post = history[post_index]
    prior = find_publication_record(post, audit, connection.get("username", ""))
    if prior:
        return redirect(url_for("instagram_central", post=post_index, published="1", oauth_error="Este post jÃ¡ foi publicado no Instagram. A publicaÃ§Ã£o duplicada foi bloqueada."))
    draft = build_publication_draft(post, post_index, connection)
    save_publication_draft(draft)
    return redirect(url_for("instagram_central", post=post_index, prepared="1"))


@app.route("/instagram/test-media", methods=["POST"])
def instagram_test_media():
    history = reconcile_publication_records(enrich_history(load_history()))
    connection = load_instagram_connection()
    audit = load_publication_audit()
    try:
        post_index = int(request.form.get("post_index", "0"))
    except (TypeError, ValueError):
        post_index = 0

    if not connection.get("access_token") or not connection.get("username"):
        return redirect(url_for("instagram_central", post=post_index, oauth_error="Conecte o Instagram antes de testar a mÃ­dia."))
    if not (0 <= post_index < len(history)):
        return redirect(url_for("instagram_central", oauth_error="Post aprovado nÃ£o encontrado."))

    try:
        post = history[post_index]
        prior = find_publication_record(post, audit, connection.get("username", ""))
        if prior:
            return redirect(url_for("instagram_central", post=post_index, published="1", oauth_error="Este post jÃ¡ foi publicado no Instagram. O teste de mÃ­dia foi bloqueado para evitar duplicaÃ§Ã£o."))
        draft = load_publication_draft()
        if not draft_matches_post(draft, post, post_index, connection):
            draft = build_publication_draft(post, post_index, connection)

        image_urls = instagram_media_urls(post)
        container = instagram_create_container(connection, image_urls, draft["full_caption"])
        container_id = container.get("id")
        if not container_id:
            raise RuntimeError(f"Instagram nÃ£o retornou o ID do container: {container}")
        status = instagram_container_status(connection, container_id)
        draft.update({
            "media_tested_at": datetime.now().isoformat(timespec="seconds"),
            "container_id": container_id,
            "container_status": status,
            "public_media_urls": image_urls,
            "status": "MÃDIA PREPARADA NO INSTAGRAM â€” ainda nÃ£o publicada",
            "note": "Container criado no Instagram. A publicaÃ§Ã£o final ainda exige confirmaÃ§Ã£o explÃ­cita.",
        })
        save_publication_draft(draft)
        return redirect(url_for("instagram_central", post=post_index, media_tested="1"))
    except Exception as exc:
        return redirect(url_for("instagram_central", post=post_index, oauth_error=f"Falha ao preparar mÃ­dia no Instagram: {exc}"))


@app.route("/instagram/publish", methods=["POST"])
def instagram_publish():
    history = reconcile_publication_records(enrich_history(load_history()))
    connection = load_instagram_connection()
    audit = load_publication_audit()
    draft = load_publication_draft()
    try:
        post_index = int(request.form.get("post_index", draft.get("post_index", "0")))
    except (TypeError, ValueError):
        post_index = 0

    if request.form.get("confirm") != "yes":
        return redirect(url_for("instagram_central", post=post_index, oauth_error="PublicaÃ§Ã£o cancelada: confirmaÃ§Ã£o nÃ£o recebida."))
    if not connection.get("access_token") or not connection.get("username"):
        return redirect(url_for("instagram_central", post=post_index, oauth_error="Instagram nÃ£o estÃ¡ conectado."))
    if not (0 <= post_index < len(history)):
        return redirect(url_for("instagram_central", oauth_error="Post aprovado nÃ£o encontrado."))

    post = history[post_index]
    prior = find_publication_record(post, audit, connection.get("username", ""))
    if prior:
        return redirect(url_for("instagram_central", post=post_index, published="1", oauth_error="PublicaÃ§Ã£o duplicada bloqueada: este post jÃ¡ consta como publicado."))
    if not draft_matches_post(draft, post, post_index, connection):
        return redirect(url_for("instagram_central", post=post_index, oauth_error="O rascunho nÃ£o corresponde ao post aprovado atual. Prepare a mÃ­dia novamente."))

    container_id = (draft.get("container_id") or "").strip()
    if not container_id:
        return redirect(url_for("instagram_central", post=post_index, oauth_error="Prepare e teste a mÃ­dia no Instagram antes de publicar."))

    try:
        status = instagram_container_status(connection, container_id)
        status_code = (status.get("status_code") or "").upper()
        if status_code and status_code not in {"FINISHED", "PUBLISHED"}:
            return redirect(url_for("instagram_central", post=post_index, oauth_error=f"O Instagram ainda nÃ£o liberou o container para publicaÃ§Ã£o: {status_code}."))
        if status_code == "PUBLISHED" and draft.get("published_media_id"):
            return redirect(url_for("instagram_central", post=post_index, published="1"))

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

        # Keep the approved history as the source of truth for the workflow.
        publication_record = {
            "published_at": now,
            "status": "PUBLICADO",
            "media_id": media_id or "",
            "container_id": container_id,
            "username": connection.get("username", ""),
            "post_index": post_index,
            "approved_at": post.get("approved_at", ""),
            "publication_key": publication_key(post),
            "vehicle": post.get("vehicle", ""),
            "service": post.get("service", ""),
        }
        post["instagram_publication"] = publication_record
        audit = load_publication_audit()
        duplicate_audit = any(
            str(item.get("approved_at", "")).strip() == publication_key(post)
            and str(item.get("username", "")).strip() == str(connection.get("username", "")).strip()
            and str(item.get("status", "")).upper() == "PUBLICADO"
            for item in audit
        )
        if not duplicate_audit:
            audit.insert(0, publication_record)
        save_publication_audit(audit)
        save_history(history)
        return redirect(url_for("instagram_central", post=post_index, published="1"))
    except Exception as exc:
        return redirect(url_for("instagram_central", post=post_index, oauth_error=f"Falha na publicaÃ§Ã£o: {exc}"))


@app.route("/fila-publicacoes", methods=["GET"])
def publication_queue():
    """Manual publication queue with editable photo order and discard protection."""
    history = reconcile_publication_records(enrich_history(load_history()))
    connection = load_instagram_connection()
    audit = load_publication_audit()
    pending = []
    published = []
    discarded = []

    for index, item in enumerate(history):
        pub = find_publication_record(item, audit, connection.get("username", ""))
        before_images = item.get("before_images") or ([item.get("before_image")] if item.get("before_image") else [])
        after_images = item.get("after_images") or ([item.get("after_image")] if item.get("after_image") else [])
        record = {
            "index": index,
            "approved_at": item.get("approved_at", ""),
            "vehicle": item.get("vehicle", ""),
            "service": item.get("service", ""),
            "caption": item.get("caption", ""),
            "before_images": before_images,
            "after_images": after_images,
            "publication": pub,
            "queue_status": str(item.get("publication_queue_status", "")).upper(),
        }
        if record["queue_status"] == "DESCARTADO":
            discarded.append(record)
        elif str(pub.get("status", "")).upper() == "PUBLICADO":
            published.append(record)
        else:
            pending.append(record)

    return render_template(
        "publication_queue.html",
        pending=pending,
        published=published,
        discarded=discarded,
        connection=connection,
        total=len(history),
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

    target["before_images"] = before[:MAX_PHOTOS_PER_SIDE]
    target["after_images"] = after[:MAX_PHOTOS_PER_SIDE]
    target.pop("before_image", None)
    target.pop("after_image", None)
    target["photo_order_updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_history(history)
    return redirect(url_for("publication_queue"))


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
    return render_template(
        "instagram.html",
        history=history,
        post=post,
        post_index=post_index,
        publication=publication,
        already_published=bool(publication),
        connection=connection,
        draft=draft,
        meta_ready=meta_ready(),
        oauth_error=request.args.get("oauth_error"),
        setup_missing=request.args.get("setup") == "missing",
        connected=request.args.get("connected") == "1",
        validated=request.args.get("validated") == "1",
        prepared=request.args.get("prepared") == "1",
        media_tested=request.args.get("media_tested") == "1",
        published=request.args.get("published") == "1",
        disconnected=request.args.get("disconnected") == "1",
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


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
    host = os.getenv("FLASK_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("FLASK_PORT", "8001"))
    app.run(host=host, port=port, debug=debug)



