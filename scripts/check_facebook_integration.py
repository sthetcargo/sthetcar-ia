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
assert 'visible_item["history_index"] = history_index' in dashboard_history_source
metrics_source = ast.get_source_segment(source, function_nodes["dashboard_metrics"])
assert '"DESCARTADO"' in metrics_source, "Discarded queue items must not count as active approved posts"

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
assert "json.loads" in plate_detector_source and "range(1, len(paths) + 1)" in plate_detector_source

dashboard_route_source = ast.get_source_segment(source, function_nodes["dashboard"])
assert "create_plate_safe_copies" in dashboard_route_source, "New uploads must be processed before creating a post"
assert "before_images_original" in dashboard_route_source and "after_images_original" in dashboard_route_source
assert "vídeos" in dashboard_route_source.casefold(), "Video uploads must be blocked until video-frame redaction exists"
preflight_source = ast.get_source_segment(source, function_nodes["_preflight_selected_platforms"])
assert 'str(post.get("plate_redaction_version", "")) != "1"' in preflight_source, "Legacy posts without redaction must not publish"
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

print("PASS: Python AST, publication guards, Instagram polling/retry, caption rewrites, discarded history, plate privacy, review photo zoom, and Jinja templates.")
