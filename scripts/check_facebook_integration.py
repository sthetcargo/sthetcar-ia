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
    "preflight_instagram_for_post",
    "find_facebook_publication_record",
    "load_facebook_publication_audit",
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

# Parse the complete Jinja template without executing actions or making API calls.
template = template_path.read_text(encoding="utf-8")
Environment().parse(template)
for marker in ("name=\"platforms\"", "facebook_login", "publish_selected_platforms", "csrf_token"):
    assert marker in template, f"Template missing expected marker: {marker}"
assert "name=\"access_token\"" not in template, "Template must never render an access-token form field"

print("PASS: Python AST, Facebook route inventory, content-bound draft guard, and Jinja template.")
