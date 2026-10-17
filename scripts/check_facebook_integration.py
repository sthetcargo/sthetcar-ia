#!/usr/bin/env python3
"""Static safety checks for the Facebook publishing change; no tokens/API calls."""
import ast
from pathlib import Path
from jinja2 import Environment

root = Path(__file__).resolve().parents[1]
main_path = root / "app" / "main.py"
template_path = root / "app" / "templates" / "instagram.html"
source = main_path.read_text(encoding="utf-8")
tree = ast.parse(source, filename=str(main_path))
assert any(
    isinstance(node, ast.Import) and any(alias.name == "time" for alias in node.names)
    for node in tree.body
), "main.py must import time at module scope for publication confirmation expiry"


# Confirm the target routes and helpers exist without importing the app
# (which would require production environment variables and Supabase).
function_names = {
    node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
required_functions = {
    "facebook_login",
    "facebook_callback",
    "facebook_validate",
    "facebook_disconnect",
    "publish_facebook_for_post",
    "publish_selected_platforms",
    "confirm_platform_publication",
    "_prepare_publication_confirmation",
    "_preflight_selected_platforms",
    "_load_pending_publication_confirmation",
    "preflight_instagram_for_post",
    "find_facebook_publication_record",
    "load_facebook_publication_audit",
    "rewrite_pending_caption",
    "_friendly_caption_rewrite_error",
    "_wait_for_container",
    "instagram_create_container",
    "instagram_test_media",
    "dashboard_history_display",
}
missing = required_functions - function_names
assert not missing, f"Missing required functions: {sorted(missing)}"

routes = set()
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            continue
        if decorator.func.attr != "route" or not decorator.args:
            continue
        value = decorator.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            routes.add(value.value)
required_routes = {
    "/auth/facebook",
    "/auth/facebook/callback",
    "/facebook/validate",
    "/facebook/disconnect",
    "/publicar-plataformas",
    "/publicar-plataformas/confirmar",
    "/revisar/<pending_id>/reescrever",
}
missing_routes = required_routes - routes
assert not missing_routes, f"Missing required routes: {sorted(missing_routes)}"

# Draft reuse must compare selected post content, not only list position.
start = source.index("def draft_matches_post(")
end = source.index("\ndef meta_ready(", start)
draft_matcher = source[start:end]
for field in ('"caption"', '"hashtags"', '"vehicle"', '"service"', '"approved_at"'):
    assert field in draft_matcher, f"Draft matcher is missing {field}"
assert "fallback_hashtags(" in draft_matcher, "Draft matcher must mirror the existing fallback hashtag behavior"

# Facebook Login for Business must use the saved configuration ID, not a scope list.
login_start = source.index('def facebook_login():')
login_end = source.index('\n\n@app.route("/auth/facebook/callback")', login_start)
facebook_login_source = source[login_start:login_end]
assert '"config_id": cfg["login_config_id"]' in facebook_login_source, "Facebook OAuth must pass the Login for Business config_id"
assert '"scope":' not in facebook_login_source, "Login for Business config must supply permissions instead of a scope query"
assert 'FACEBOOK_LOGIN_CONFIG_ID' in source, "Facebook configuration ID must be read from environment"

# Publishing must be split across two HTTP steps: selection/preflight cannot publish.
function_nodes = {
    node.name: node for node in ast.walk(tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
prepare_source = ast.get_source_segment(source, function_nodes["publish_selected_platforms"])
assert "_prepare_publication_confirmation" in prepare_source
assert "publish_facebook_for_post(" not in prepare_source
assert "publish_instagram_for_post(" not in prepare_source

confirm_source = ast.get_source_segment(source, function_nodes["confirm_platform_publication"])
for marker in ('request.form.get("confirmation_id"', 'secrets.compare_digest', 'request.form.get("acknowledge")', 'request.form.get("action",'):
    assert marker in confirm_source, f"Confirmation route missing guard: {marker}"
assert "_preflight_selected_platforms(post_index, selected)" in confirm_source
assert 'session.pop("pending_platform_publication", None)' in confirm_source

# Caption rewrite must guard queue/form normalization and never erase the original caption on failure.
rewrite_source = ast.get_source_segment(source, function_nodes["rewrite_pending_caption"])
try_pos = rewrite_source.find("try:")
assert try_pos >= 0, "Caption rewrite route must guard potentially failing operations"
assert rewrite_source.find("queue = load_approval_queue()") > try_pos, "Queue access should be enclosed in the rewrite exception guard"
assert "str(request.form.get(\"current_caption\") or pending.get(\"caption\") or \"\").strip()" in rewrite_source
assert "app.logger.exception(\"Falha ao reescrever legenda;" in rewrite_source
assert "_friendly_caption_rewrite_error(exc)" in rewrite_source
assert "original_pending = copy.deepcopy(pending)" in rewrite_source
assert '"rewrite_error.html"' in rewrite_source
assert "difflib.SequenceMatcher" in rewrite_source, "Rewrite route must reject captions too similar to the current caption"
assert "igual ou muito parecida" in rewrite_source, "Rewrite route must explain when a new caption was not meaningfully different"

# Gemini rewriter may not silently return the source caption when attempts fail.
gemini_path = root / "app" / "ai" / "gemini.py"
gemini_source = gemini_path.read_text(encoding="utf-8")
gemini_tree = ast.parse(gemini_source, filename=str(gemini_path))
gemini_functions = {
    node.name: node for node in ast.walk(gemini_tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
rewrite_caption_source = ast.get_source_segment(gemini_source, gemini_functions["rewrite_caption"])
looks_like_caption_source = ast.get_source_segment(
    gemini_source,
    {node.name: node for node in ast.walk(gemini_tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}["_looks_like_caption"],
)
assert '"hashtags:"' not in looks_like_caption_source.casefold(), "Caption validator must not reject the HASHTAGS section explicitly required by the prompt"
assert "HASHTAGS:" in rewrite_caption_source, "Rewrite prompt should keep the explicit hashtags output section"
assert "return source_caption or _fallback_caption" not in rewrite_caption_source, "Rewriter must not report the original caption as a successful rewrite"
assert "A IA não conseguiu produzir uma legenda suficientemente diferente" in rewrite_caption_source


# Parse the review and safe error templates, too.
review_template_path = root / "app" / "templates" / "review_pending.html"
review_template = review_template_path.read_text(encoding="utf-8")
Environment().parse(review_template)
rewrite_error_path = root / "app" / "templates" / "rewrite_error.html"
rewrite_error_template = rewrite_error_path.read_text(encoding="utf-8")
Environment().parse(rewrite_error_template)
assert "rewrite_error or request.args.get(\"rewrite_error\")" in review_template
assert "A legenda e as fotos do post não foram alteradas" in rewrite_error_template


# Discarding a publication-queue item hides it from active dashboard history,
# while preserving the original history record and its post index.
dashboard_history_source = ast.get_source_segment(source, function_nodes["dashboard_history_display"])
assert 'publication_queue_status' in dashboard_history_source and '"DESCARTADO"' in dashboard_history_source
assert '"DIVIDIDO"' in dashboard_history_source, "Split parents must stay in history storage but be hidden from active history"
assert 'visible_item["history_index"] = history_index' in dashboard_history_source
metrics_source = ast.get_source_segment(source, function_nodes["dashboard_metrics"])
assert '"DESCARTADO"' in metrics_source and '"DIVIDIDO"' in metrics_source, "Discarded and split-parent records must not inflate active post metrics"

# Oversized approved posts must split into independent, reviewable parts without losing media.
split_module_path = root / "app" / "publication_split.py"
split_module_source = split_module_path.read_text(encoding="utf-8")
ast.parse(split_module_source, filename=str(split_module_path))
split_tests_source = (root / "tests" / "test_publication_split.py").read_text(encoding="utf-8")
for marker in (
    "test_twelve_photos_split_into_five_pairs_and_one_pair",
    "test_eleven_photos_are_not_dropped_or_repeated",
    "test_posts_with_ten_or_fewer_photos_remain_one_unchanged_part",
):
    assert marker in split_tests_source, f"Missing publication split regression test: {marker}"
split_route_source = ast.get_source_segment(source, function_nodes["split_publication_in_queue"])
for marker in (
    "split_photo_collections", "parent[\"publication_queue_status\"] = \"DIVIDIDO\"",
    "parent[\"split_children\"]", "save_history(history)", "instagram_already_published",
    "child[\"instagram_publication\"]", "split_pending_facebook",
):
    assert marker in split_route_source, f"Publication split route is missing safety behavior: {marker}"
instagram_items_source = ast.get_source_segment(source, function_nodes["instagram_media_items"])
assert "chosen[:10]" not in instagram_items_source, "Do not silently drop the last two photos"
assert "Dividir em partes" in instagram_items_source, "Oversized posts need a clear split instruction"
facebook_media_source = ast.get_source_segment(source, function_nodes["facebook_media_urls"])
assert "Divida-a em partes" in facebook_media_source, "Facebook 10-photo limit should explain the supported solution"

dashboard_template_path = root / "app" / "templates" / "dashboard.html"
dashboard_template = dashboard_template_path.read_text(encoding="utf-8")
Environment().parse(dashboard_template)
assert "{% for item in history_display %}" in dashboard_template
assert "post=item.history_index" in dashboard_template, "Filtered history must retain original post indexes"
assert "Posts ativos" in dashboard_template and "Descartados" in dashboard_template

# Parse both Jinja templates without executing actions or making API calls.
template = template_path.read_text(encoding="utf-8")
Environment().parse(template)
confirm_template_path = root / "app" / "templates" / "confirm_platform_publication.html"
confirm_template = confirm_template_path.read_text(encoding="utf-8")
Environment().parse(confirm_template)
for marker in ('name="platforms"', "facebook_login", "publish_selected_platforms", "csrf_token"):
    assert marker in template, f"Central template missing expected marker: {marker}"
for marker in ('confirmation_id', 'name="acknowledge"', 'value="publish"', 'value="cancel"', 'instagram_caption_preview', 'facebook_caption_preview'):
    assert marker in confirm_template, f"Confirmation template missing expected marker: {marker}"
assert 'url_for(\'publish_selected_platforms\') }}" onsubmit=' not in template, "Multi-platform publication must use a dedicated confirmation page"
assert 'url_for(\'instagram_publish\') }}" onsubmit=' not in template, "Instagram publication must use a dedicated confirmation page"
assert 'name="confirm" value="yes"' not in template, "Do not trust hidden confirmation flags"
assert 'name="access_token"' not in template + confirm_template, "Templates must never render an access-token form field"

# Instagram media readiness: poll photo/video/carousel processing and reuse a pending container.
function_nodes = {
    node.name: node for node in ast.walk(tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
wait_source = ast.get_source_segment(source, function_nodes["_wait_for_container"])
assert 'status in {"FINISHED", "PUBLISHED"}' in wait_source
assert "raise RuntimeError(" in wait_source and "timeout" in wait_source and "O Instagram ainda está processando a mídia" in wait_source
assert not wait_source.rstrip().endswith("return last"), "Timed-out containers must not be treated as a successful media test"

container_source = ast.get_source_segment(source, function_nodes["instagram_create_container"])
assert '_wait_for_container(connection, child_id, timeout=120)' in container_source, "Carousel children must finish processing, including photos"

test_media_source = ast.get_source_segment(source, function_nodes["instagram_test_media"])
assert 'status = _wait_for_container(connection, container_id, timeout=120)' in test_media_source, "The test-media route must poll image containers too"
assert 'existing_container_id' in test_media_source and 'existing_status not in {"ERROR", "EXPIRED"}' in test_media_source, "Retry must reuse an in-progress container"
assert test_media_source.find("save_publication_draft(draft)") < test_media_source.find("_wait_for_container(connection, container_id"), "Save container ID before polling so a retry can resume"



# License-plate privacy: output copies only are blurred, originals remain intact,
# ambiguous detection fails closed, and unprocessed legacy posts cannot publish.
privacy_path = root / "app" / "ai" / "plate_privacy.py"
privacy_source = privacy_path.read_text(encoding="utf-8")
privacy_tree = ast.parse(privacy_source, filename=str(privacy_path))
privacy_functions = {
    node.name: node for node in ast.walk(privacy_tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
for required in ("create_plate_safe_copies", "_normalized_copy", "_pixel_box", "_blur_region"):
    assert required in privacy_functions, f"Plate privacy helper missing {required}"
assert "ImageOps.exif_transpose" in privacy_source, "Plate coordinates must use normalized image orientation"
assert "ImageFilter.GaussianBlur" in privacy_source and "Image.Resampling.NEAREST" in privacy_source
assert "source.with_name" in privacy_source, "Redaction must write a separate copy instead of overwriting the original"
assert "INSTAGRAM_IMAGE_SIZE = 1080" in privacy_source, "Publication copies must use a predictable supported size"
assert "PUBLICATION_BACKGROUND" in privacy_source and "canvas.paste(image, offset)" in privacy_source
assert 'source.stem}_placa_protegida.jpg' in privacy_source, "Instagram publication copies must be JPEG"
assert 'format="JPEG"' in privacy_source, "Instagram API images must be saved as JPEG"
assert "ImageOps.contain(" in privacy_source, "Source photos must be contained without cropping to a supported square canvas"
assert "test_portrait_png_and_panorama_webp_become_square_jpeg_without_cropping" in (root / "tests" / "test_plate_privacy.py").read_text(encoding="utf-8")
assert "path.unlink()" in privacy_source, "Failed redaction copies must be cleaned up"
assert "needs_review and not plates" in privacy_source, "Uncertain detections without a usable box must fail closed"

detector_path = root / "app" / "ai" / "gemini.py"
detector_source = detector_path.read_text(encoding="utf-8")
detector_tree = ast.parse(detector_source, filename=str(detector_path))
detector_functions = {
    node.name: node for node in ast.walk(detector_tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
assert "detect_license_plates" in detector_functions
plate_detector_source = ast.get_source_segment(detector_source, detector_functions["detect_license_plates"])
assert "needs_review" in plate_detector_source and "x1" in plate_detector_source
assert "Não transcreva" in plate_detector_source, "The detector must localize plates without reading their characters"
plate_parser_source = ast.get_source_segment(detector_source, detector_functions["_parse_plate_detection_json"])
assert "json.loads" in plate_parser_source, "Detector response must be parsed as strict JSON"
assert "isinstance(payload, dict)" in plate_parser_source, "Detector JSON must be an object"
assert "range(1, len(paths) + 1)" in plate_detector_source, "Every input image index must be verified"
assert 'response_mime_type="application/json"' in plate_detector_source
assert "response_schema=PLATE_DETECTION_RESPONSE_SCHEMA" in plate_detector_source

dashboard_route_source = ast.get_source_segment(source, function_nodes["dashboard"])
assert "create_plate_safe_copies" in dashboard_route_source, "New uploads must be processed before creating a post"
assert "before_images_original" in dashboard_route_source and "after_images_original" in dashboard_route_source
assert "vídeos" in dashboard_route_source.casefold(), "Video uploads must be blocked until video-frame redaction exists"
preflight_source = ast.get_source_segment(source, function_nodes["_preflight_selected_platforms"])
assert 'str(post.get("plate_redaction_version", "")) != "2"' in preflight_source, "Only version-2 square JPEG posts may publish"
assert "plate_redaction_version" in ast.get_source_segment(source, function_nodes["publication_confirmation_fingerprint"])

dashboard_template_path = root / "app" / "templates" / "dashboard.html"
dashboard_template = dashboard_template_path.read_text(encoding="utf-8")
review_template_path = root / "app" / "templates" / "review_pending.html"
review_template = review_template_path.read_text(encoding="utf-8")
confirm_template_path = root / "app" / "templates" / "confirm_platform_publication.html"
confirm_template = confirm_template_path.read_text(encoding="utf-8")
for content, label in (
    (dashboard_template, "dashboard"),
    (review_template, "review pending"),
    (confirm_template, "publication confirmation"),
):
    Environment().parse(content)
    assert "plate_redaction_message" in content, f"{label} template must show the plate-redaction status"
assert 'accept="image/jpeg,image/png,image/webp"' in dashboard_template
assert "Vídeos serão liberados" in dashboard_template

# Bound memory for the free Render instance while retaining support for 6+6 photos.
assert "const MAX_DIMENSION = 1280;" in dashboard_template, "Browser upload should limit decoded image memory"
assert "const JPEG_QUALITY = 0.76;" in dashboard_template, "Browser upload should use lower-memory JPEG compression"
assert "PLATE_DETECTION_BATCH_SIZE = 3" in privacy_source, "Plate detection must be split into bounded batches"
assert "_detect_license_plates(batch)" in privacy_source
privacy_tests_source = (root / "tests" / "test_plate_privacy.py").read_text(encoding="utf-8")
assert "test_plate_detection_batches_large_photo_sets" in privacy_tests_source
assert "analyze_before_after(\n                        safe_before_paths, safe_after_paths" in dashboard_route_source, "Gemini analysis must use smaller protected copies, not larger originals"
for stage_marker in (
    "proteger placas", "analisar cópias protegidas", "enviar imagens ao armazenamento",
    "app.logger.exception", "create_request_id",
):
    assert stage_marker in dashboard_route_source, f"Post-generation stage diagnostics missing {stage_marker}"

# The pending-review page must support enlarging processed photos without changing
# hidden post fields or bypassing the approval/publication safeguards.
assert 'id="photo-lightbox"' in review_template, "Review page needs a photo lightbox"
assert "zoomablePhotoSelector" in review_template and "enhanceZoomablePhotos()" in review_template
assert "openPhotoLightbox(img)" in review_template and "closePhotoLightbox()" in review_template
assert "event.key==='Escape'" in review_template, "Lightbox must close with Escape"
assert "event.key==='Enter' || event.key===' '" in review_template, "Zoomable photos must be keyboard accessible"
assert "currentSrc || img.src" in review_template, "Lightbox must show the actual processed preview"
assert "Clique ou toque em qualquer foto para ampliar" in review_template
assert 'id="approval-form"' in review_template and 'name="action" value="approve"' in review_template
assert 'name="action" value="publish"' not in review_template, "Photo zoom must not add publication controls"


# The Instagram media-test endpoint must safely handle state-load failures before
# any external media request, rather than returning Flask's unhelpful HTTP 500.
media_test_source = ast.get_source_segment(source, function_nodes["instagram_test_media"])
first_try = media_test_source.find("try:")
first_history_load = media_test_source.find("history = reconcile_publication_records(enrich_history(load_history()))")
assert first_try >= 0 and first_history_load > first_try, "Media-test state loading must be guarded"
for marker in ("load_instagram_connection()", "load_publication_audit()", "MEDIA-", "app.logger.exception"):
    assert marker in media_test_source, f"Media-test initialization error handling missing {marker}"
assert "Nenhuma publicação foi feita" in media_test_source, "Failure must tell the user no post was published"
assert "Código de diagnóstico:" in media_test_source, "Failure must provide a safe diagnostic code"
assert 'str(post.get("plate_redaction_version", "")) != "2"' in media_test_source, "Old copies must be blocked before submitting media to Instagram"
assert "normalização quadrada para o Instagram" in media_test_source, "Old posts need a clear explanation of why retesting is blocked"


# UI timestamps must be converted only for display to Brasília time. Stored publication
# keys/timestamps remain unchanged so matching and duplicate protection keep working.
time_utils_path = root / "app" / "time_utils.py"
time_utils_source = time_utils_path.read_text(encoding="utf-8")
ast.parse(time_utils_source, filename=str(time_utils_path))
assert "America/Sao_Paulo" in time_utils_source
assert "timezone.utc" in time_utils_source, "Naive historical Render timestamps must be interpreted as UTC"
assert "astimezone(BRASILIA_TIMEZONE)" in time_utils_source
main_import_source = source
assert "from app.time_utils import format_local_datetime" in main_import_source
assert 'app.add_template_filter(format_local_datetime, "local_datetime")' in main_import_source
for content, label in (
    (dashboard_template, "dashboard"),
    (review_template, "review pending"),
    (confirm_template, "publication confirmation"),
):
    Environment().parse(content)
instagram_template_path = root / "app" / "templates" / "instagram.html"
instagram_template = instagram_template_path.read_text(encoding="utf-8")
publication_queue_path = root / "app" / "templates" / "publication_queue.html"
publication_queue_template = publication_queue_path.read_text(encoding="utf-8")
for content, label in (
    (instagram_template, "Instagram"),
    (dashboard_template, "dashboard"),
    (publication_queue_template, "publication queue"),
):
    Environment().parse(content)
    assert "|local_datetime" in content, f"{label} dates must use Brasília time formatting"
assert "draft.prepared_at.replace('T',' ')" not in instagram_template
assert "item.created_at.replace('T',' ')" not in dashboard_template
assert "item.publication.published_at.replace('T',' ')" not in publication_queue_template


# The Gunicorn sync-worker default is 30 seconds. Keep AI image-generation requests
# bounded but allow time for multiple external model calls in one post.
gunicorn_config_path = root / "gunicorn.conf.py"
gunicorn_source = gunicorn_config_path.read_text(encoding="utf-8")
gunicorn_tree = ast.parse(gunicorn_source, filename=str(gunicorn_config_path))
gunicorn_assignments = {
    node.targets[0].id: node.value.value
    for node in gunicorn_tree.body
    if isinstance(node, ast.Assign)
    and len(node.targets) == 1
    and isinstance(node.targets[0], ast.Name)
    and isinstance(node.value, ast.Constant)
}
assert gunicorn_assignments.get("timeout") == 180
assert gunicorn_assignments.get("graceful_timeout") == 30

gemini_path = root / "app" / "ai" / "gemini.py"
gemini_source = gemini_path.read_text(encoding="utf-8")
gemini_tree = ast.parse(gemini_source, filename=str(gemini_path))
client_node = next(node for node in ast.walk(gemini_tree) if isinstance(node, ast.FunctionDef) and node.name == "_client")
client_source = ast.get_source_segment(gemini_source, client_node)
assert "types.HttpOptions(timeout=60_000)" in client_source, "Gemini HTTP call timeout must be explicit"
generate_node = next(node for node in ast.walk(gemini_tree) if isinstance(node, ast.FunctionDef) and node.name == "_generate")
generate_source = ast.get_source_segment(gemini_source, generate_node)
assert "Gemini generation started model=%s" in generate_source
assert "Gemini generation completed model=%s elapsed_seconds=%.1f" in generate_source
assert "Gemini generation failed model=%s elapsed_seconds=%.1f error_type=%s" in generate_source


# Audit regressions: inference functions must live in a pure, offline-testable module.
inference_path = root / "app" / "ai" / "text_inference.py"
inference_source = inference_path.read_text(encoding="utf-8")
inference_tree = ast.parse(inference_source, filename=str(inference_path))
inference_functions = {
    node.name: node for node in ast.walk(inference_tree)
    if isinstance(node, ast.FunctionDef)
}
assert {"infer_vehicle", "infer_service"} <= set(inference_functions)
assert "from app.ai.text_inference import infer_service, infer_vehicle" in source
assert 'r"VE[IÍ]CULO\\s+IDENTIFICADO' in inference_source
assert "não identificado" in inference_source
assert "Lavagem e Higienização" in inference_source
assert "test_uncertain_vehicle_is_not_inferred_from_placeholder" in (root / "tests" / "test_text_inference.py").read_text(encoding="utf-8")

# Portuguese text should be UTF-8, not UTF-8 bytes misread as Latin-1.
mojibake_markers = ("Ã£", "Ã§", "Ã©", "Ã­", "Ã¡", "Ã³", "Ãº", "Ãª", "Ãµ", "Ã", "â€”", "â€“", "â€™")
for relative_path in ("app/main.py", "app/ai/text_inference.py", "migrar_v33_para_nuvem.py", "README.md"):
    content = (root / relative_path).read_text(encoding="utf-8")
    assert not any(marker in content for marker in mojibake_markers), f"Corrupted Portuguese encoding remains in {relative_path}"

# Gemini generation configuration is covered with a fake client; no credential or API call is used.
generation_config_node = next(
    node for node in ast.walk(gemini_tree)
    if isinstance(node, ast.FunctionDef) and node.name == "_generation_config"
)
generation_config_source = ast.get_source_segment(gemini_source, generation_config_node)
assert 'startswith(("gemini-3", "gemini-4"))' in generation_config_source
assert "types.GenerateContentConfig(**options)" in generation_config_source
assert "test_generate_uses_mocked_client_without_network_or_api_key" in (root / "tests" / "test_gemini_config.py").read_text(encoding="utf-8")

# Keep 413 handling safe even if state reads fail while building the error page.
handler_node = next(
    node for node in ast.walk(tree)
    if isinstance(node, ast.FunctionDef) and node.name == "request_too_large"
)
handler_source = ast.get_source_segment(source, handler_node)
assert "try:" in handler_source and "except Exception:" in handler_source
assert "Falha ao carregar estado enquanto tratava upload acima do limite" in handler_source

# Production package/runtime declarations must be explicit and match the Render runtime.
assert (root / ".python-version").read_text(encoding="utf-8").strip() == "3.14.3"
requirements_text = (root / "requirements.txt").read_text(encoding="utf-8")
assert "gunicorn>=" in requirements_text
assert (root / ".gitignore").is_file()
assert "py[cod]" in (root / ".gitignore").read_text(encoding="utf-8")


# Plate detection must request structured JSON, not rely only on prompt instructions.
plate_detector_node = next(
    node for node in ast.walk(gemini_tree)
    if isinstance(node, ast.FunctionDef) and node.name == "detect_license_plates"
)
plate_detector_source = ast.get_source_segment(gemini_source, plate_detector_node)
assert 'response_mime_type="application/json"' in plate_detector_source
assert "response_schema=PLATE_DETECTION_RESPONSE_SCHEMA" in plate_detector_source
assert "max_output_tokens=4096" in plate_detector_source
assert "def _parse_plate_detection_json" in gemini_source
assert "def _plate_response_status" in gemini_source
plate_detection_tests = (root / "tests" / "test_plate_detection_response.py").read_text(encoding="utf-8")
for marker in (
    "test_detector_uses_schema_mode_and_keeps_strict_validation",
    "test_detector_does_not_accept_unparseable_response",
    "test_parser_fails_closed_for_natural_language",
):
    assert marker in plate_detection_tests, f"Missing plate-detection response test: {marker}"


# Variable photo intake is limited by aggregate payload bytes, not a fixed per-side photo count.
assert "MAX_PHOTOS_PER_SIDE" not in source, "Backend must not enforce a fixed per-side photo count"
assert "MAX_PHOTOS" not in dashboard_template, "Browser must not silently truncate selected photos"
assert "Quantidade variável" in dashboard_template
assert "total após compressão deve ficar em até 30 MB" in dashboard_template
assert "target[\"before_images\"] = before" in source and "target[\"after_images\"] = after" in source
assert "target[\"before_images\"] = before[:MAX_PHOTOS_PER_SIDE]" not in source

# Visual analysis must batch many images and preserve global image indexes in prompts.
visual_analysis_node = next(
    node for node in ast.walk(gemini_tree)
    if isinstance(node, ast.FunctionDef) and node.name == "analyze_before_after"
)
visual_analysis_source = ast.get_source_segment(gemini_source, visual_analysis_node)
assert "VISUAL_ANALYSIS_BATCH_SIZE = 4" in gemini_source
assert "range(0, total_groups, VISUAL_ANALYSIS_BATCH_SIZE)" in visual_analysis_source
assert "FOTO ANTES {global_index}" in visual_analysis_source
assert "FOTO DEPOIS {global_index}" in visual_analysis_source
variable_photo_tests = (root / "tests" / "test_variable_photo_counts.py").read_text(encoding="utf-8")
assert "test_visual_analysis_batches_all_photos_without_network_calls" in variable_photo_tests
assert "test_arbitrary_photo_counts_split_with_no_loss_or_silent_truncation" in variable_photo_tests

print("PASS: static integration checks, UTF-8 text, variable photo intake, bounded visual-analysis batches, dynamic publication splitting, Gemini config regression, structured plate detection, safe upload handling, runtime declarations, and Jinja templates.")
