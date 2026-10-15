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

print("PASS: Python AST, Facebook routes, publication confirmation guards, Instagram media polling/retry, caption rewrite safeguards, and relevant Jinja templates.")
