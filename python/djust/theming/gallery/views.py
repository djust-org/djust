"""
Theme gallery views -- gallery, live editor, diff comparison, and component storybook.

Gated by ``DEBUG=True`` or ``is_staff`` for production safety.
"""

import json
import re

from django.conf import settings
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    HttpResponseNotFound,
    JsonResponse,
)
from django.template.loader import render_to_string
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .context import build_gallery_context, serialize_all_design_systems, serialize_all_presets
from djust.theming.theme_packs import DESIGN_SYSTEMS
from .storybook import build_storybook_detail_context, build_storybook_index_context
from djust.theming.contracts import COMPONENT_CONTRACTS
from djust.theming.presets import list_presets
from djust.theming.css_generator import ThemeCSSGenerator as ColorCSSGenerator

# Allowed CSS token names: lowercase letters, digits, hyphens, underscores only.
_VALID_TOKEN_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


@xframe_options_sameorigin
def gallery_view(request):
    """Render the theme component gallery page.

    Access control:
    - Always accessible when ``DEBUG=True``
    - When ``DEBUG=False``, requires ``request.user.is_staff``

    Supports ``?preset=<name>`` query parameter to switch color presets.
    The @xframe_options_sameorigin decorator allows this page to be embedded in
    an iframe from the same origin, which is required by the diff comparison view.
    """
    denied = _check_access(request)
    if denied:
        return denied

    # Read preset from query param
    preset_name = request.GET.get("preset", "default")

    # Validate and normalise preset name
    from djust.theming.registry import get_registry

    if not get_registry().has_preset(preset_name):
        preset_name = "default"

    # Generate unlayered preset override CSS for the gallery.
    # Using generate_variables_only() emits bare :root {} / .dark {} blocks without
    # @layer wrappers. Unlayered CSS always wins over @layer tokens from theme_head,
    # so the preset selector actually changes what the user sees.
    color_gen = ColorCSSGenerator(preset_name=preset_name)
    gallery_preset_css = color_gen.generate_variables_only()

    ctx = build_gallery_context(preset_name=preset_name)
    ctx["request"] = request
    ctx["gallery_preset_css"] = gallery_preset_css

    # Extra context variables consumed directly by template tags in gallery.html
    ctx.update(_template_sample_data())

    html = render_to_string(
        "djust_theming/gallery/gallery.html",
        ctx,
        request=request,
    )
    return HttpResponse(html)


def editor_view(request):
    """Render the live theme editor page.

    Two-panel layout: left panel with token controls, right panel with
    live component preview. All updates happen client-side via CSS custom
    property manipulation -- no server round-trip for preview.

    Supports ``?preset=<name>`` to set the initial preset.
    """
    denied = _check_access(request)
    if denied:
        return denied

    preset_name = request.GET.get("preset", "default")

    ctx = build_gallery_context(preset_name=preset_name)
    ctx["request"] = request
    ctx.update(_template_sample_data())

    # Serialize all presets and design systems for JS initialization
    ctx["preset_data_json"] = json.dumps(serialize_all_presets())
    ctx["design_systems_json"] = json.dumps(serialize_all_design_systems())
    ctx["design_systems"] = DESIGN_SYSTEMS

    html = render_to_string(
        "djust_theming/gallery/editor.html",
        ctx,
        request=request,
    )
    return HttpResponse(html)


def editor_export_view(request):
    """Export edited theme tokens as tokens.css + theme.toml.

    POST only. Expects JSON body with structure::

        {
            "name": "my-theme",
            "radius": 0.5,
            "tokens": {
                "light": {"background": {"h": 0, "s": 0, "l": 100}, ...},
                "dark": {"background": {"h": 240, "s": 10, "l": 4}, ...}
            }
        }

    Returns JSON::

        {"tokens_css": "...", "theme_toml": "..."}
    """
    denied = _check_access(request)
    if denied:
        return denied

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    theme_name = payload.get("name", "custom")
    radius = payload.get("radius", 0.5)
    tokens_data = payload.get("tokens", {})
    custom_properties = payload.get("custom_properties", {})

    # --- Input validation ---
    # Validate theme name: alphanumeric + hyphens only
    if not isinstance(theme_name, str) or not re.match(r"^[a-zA-Z][a-zA-Z0-9 _-]*$", theme_name):
        return JsonResponse({"error": "Invalid theme name"}, status=400)
    if len(theme_name) > 64:
        return JsonResponse({"error": "Theme name too long"}, status=400)

    # Validate radius: must be a number in [0, 4]
    try:
        radius = float(radius)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid radius value"}, status=400)
    if not (0 <= radius <= 4):
        return JsonResponse({"error": "Radius must be between 0 and 4"}, status=400)

    # Validate tokens structure
    if not isinstance(tokens_data, dict):
        return JsonResponse({"error": "Invalid tokens structure"}, status=400)
    for mode in ("light", "dark"):
        mode_tokens = tokens_data.get(mode, {})
        if not isinstance(mode_tokens, dict):
            return JsonResponse({"error": f"Invalid {mode} tokens"}, status=400)
        for token_name, hsl in mode_tokens.items():
            if not _VALID_TOKEN_NAME.match(token_name):
                return JsonResponse({"error": f"Invalid token name: {token_name}"}, status=400)
            if not isinstance(hsl, dict):
                return JsonResponse({"error": f"Invalid HSL for {token_name}"}, status=400)
            for key in ("h", "s", "l"):
                val = hsl.get(key, 0)
                if not isinstance(val, (int, float)):
                    return JsonResponse(
                        {"error": f"Invalid {key} value for {token_name}"}, status=400
                    )

    # Validate custom_properties: must be a flat dict of string->string
    if not isinstance(custom_properties, dict):
        custom_properties = {}
    _VALID_PROP_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9-]*$")
    validated_props = {}
    for prop_name, prop_value in custom_properties.items():
        if (
            isinstance(prop_name, str)
            and isinstance(prop_value, str)
            and _VALID_PROP_NAME.match(prop_name)
            and len(prop_value) < 256
        ):
            validated_props[prop_name] = prop_value

    # Generate tokens.css
    tokens_css = _generate_tokens_css(tokens_data, radius, validated_props)

    # Generate theme.toml
    theme_toml = _generate_theme_toml(theme_name, radius)

    return JsonResponse(
        {
            "tokens_css": tokens_css,
            "theme_toml": theme_toml,
        }
    )


def diff_view(request):
    """Render the side-by-side theme comparison page.

    Uses two iframes loading the gallery with different presets.
    Supports ``?left=<preset>&right=<preset>`` query parameters.
    """
    denied = _check_access(request)
    if denied:
        return denied

    left_preset = request.GET.get("left", "default")
    right_preset = request.GET.get("right", "nord")

    ctx = {
        "presets": list_presets(),
        "left_preset": left_preset,
        "right_preset": right_preset,
        "request": request,
    }

    html = render_to_string(
        "djust_theming/gallery/diff.html",
        ctx,
        request=request,
    )
    return HttpResponse(html)


# ---------------------------------------------------------------------------
# Storybook views
# ---------------------------------------------------------------------------


def storybook_index_view(request):
    """Render the component storybook index -- lists all components.

    Access control: same as gallery (DEBUG=True or is_staff).
    """
    denied = _check_access(request)
    if denied:
        return denied

    ctx = build_storybook_index_context()
    ctx["request"] = request
    ctx["all_components"] = ctx.get("components", [])
    ctx["current_component"] = None

    html = render_to_string(
        "djust_theming/gallery/storybook_index.html",
        ctx,
        request=request,
    )
    return HttpResponse(html)


def storybook_detail_view(request, component_name):
    """Render the storybook detail page for a single component.

    Handles both template-based (24 contracted) and Python (169 total) components.
    Returns 404 if the component name is not recognized.
    """
    denied = _check_access(request)
    if denied:
        return denied

    from .component_registry import _COMPONENT_TO_CATEGORY

    if component_name not in COMPONENT_CONTRACTS and component_name not in _COMPONENT_TO_CATEGORY:
        return HttpResponseNotFound(f"Unknown component: {component_name}")

    try:
        ctx = build_storybook_detail_context(component_name)
    except KeyError:
        return HttpResponseNotFound(f"Unknown component: {component_name}")

    ctx["request"] = request
    # Pass full component list for sidebar navigation
    index_ctx = build_storybook_index_context()
    ctx["all_components"] = index_ctx.get("components", [])
    ctx["current_component"] = component_name

    html = render_to_string(
        "djust_theming/gallery/storybook_detail.html",
        ctx,
        request=request,
    )
    return HttpResponse(html)


def storybook_category_view(request, category):
    """Render a storybook category page showing all components in the category."""
    denied = _check_access(request)
    if denied:
        return denied

    from .component_registry import COMPONENT_CATEGORIES, get_all_components_with_metadata

    if category not in COMPONENT_CATEGORIES:
        return HttpResponseNotFound(f"Unknown category: {category}")

    all_components = get_all_components_with_metadata()
    category_components = [c for c in all_components if c["category"] == category]

    # Enrich template components with contract data
    enriched = []
    for comp in category_components:
        name = comp["name"]
        if name in COMPONENT_CONTRACTS:
            contract = COMPONENT_CONTRACTS[name]
            comp = dict(comp)
            comp["required_count"] = len(contract.required_context)
            comp["optional_count"] = len(contract.optional_context)
            comp["slot_count"] = len(contract.available_slots)
            comp["a11y_count"] = len(contract.accessibility)
        enriched.append(comp)

    index_ctx = build_storybook_index_context()
    ctx = {
        "request": request,
        "category": category,
        "category_components": enriched,
        "all_components": index_ctx.get("components", []),
        "current_component": None,
    }

    html = render_to_string(
        "djust_theming/gallery/storybook_category.html",
        ctx,
        request=request,
    )
    return HttpResponse(html)


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def _generate_tokens_css(
    tokens_data: dict, radius: float, custom_properties: dict | None = None
) -> str:
    """Generate CSS custom property declarations from token data.

    All token names and values are validated before reaching this function,
    but we still cast values to int/float as a defense-in-depth measure.
    """
    lines = ["/* djust-theming -- Custom theme tokens */", ""]

    # Light mode
    lines.append(":root {")
    for name, hsl in sorted(tokens_data.get("light", {}).items()):
        css_name = name.replace("_", "-")
        h, s, l = int(hsl.get("h", 0)), int(hsl.get("s", 0)), int(hsl.get("l", 0))
        lines.append(f"  --{css_name}: {h} {s}% {l}%;")
    lines.append(f"  --radius: {float(radius)}rem;")

    # Non-color custom properties (typography, spacing, animation, shadows)
    if custom_properties:
        lines.append("")
        lines.append("  /* Typography, spacing, animation, shadows */")
        for prop_name, prop_value in sorted(custom_properties.items()):
            lines.append(f"  --{prop_name}: {prop_value};")

    lines.append("}")
    lines.append("")

    # Dark mode
    lines.append('.dark,\n[data-theme="dark"] {')
    for name, hsl in sorted(tokens_data.get("dark", {}).items()):
        css_name = name.replace("_", "-")
        h, s, l = int(hsl.get("h", 0)), int(hsl.get("s", 0)), int(hsl.get("l", 0))
        lines.append(f"  --{css_name}: {h} {s}% {l}%;")
    lines.append("}")

    return "\n".join(lines)


def _generate_theme_toml(name: str, radius: float) -> str:
    """Generate a minimal theme.toml manifest."""
    lines = [
        "[theme]",
        f'name = "{name}"',
        f'display_name = "{name.replace("-", " ").title()}"',
        f"radius = {radius}",
        'design_system = "default"',
        "",
        "[tokens]",
        "# Override specific tokens here",
        '# primary = "220 80% 50%"',
    ]
    return "\n".join(lines)


def _check_access(request):
    """Return an HttpResponseForbidden if access should be denied, else None."""
    gallery_public = getattr(settings, "DJUST_THEMING_GALLERY_PUBLIC", settings.DEBUG)
    if not gallery_public:
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_staff", False):
            return HttpResponseForbidden(
                "Gallery is only available in DEBUG mode or for staff users."
            )
    return None


def _template_sample_data() -> dict:
    """Return sample data dicts used by template tags in the gallery template."""
    return {
        "tab_data": [
            {"label": "Tab 1", "content": "Content for tab 1."},
            {"label": "Tab 2", "content": "Content for tab 2."},
            {"label": "Tab 3", "content": "Content for tab 3."},
        ],
        "table_headers": ["Name", "Email", "Role"],
        "table_rows": [
            ["Alice", "alice@example.com", "Admin"],
            ["Bob", "bob@example.com", "Editor"],
            ["Charlie", "charlie@example.com", "Viewer"],
        ],
        "select_options": [
            {"value": "opt1", "label": "Option 1"},
            {"value": "opt2", "label": "Option 2"},
            {"value": "opt3", "label": "Option 3"},
        ],
        "radio_options": [
            {"value": "sm", "label": "Small"},
            {"value": "md", "label": "Medium"},
            {"value": "lg", "label": "Large"},
        ],
        "breadcrumb_items": [
            {"label": "Home", "url": "/"},
            {"label": "Products", "url": "/products/"},
            {"label": "Current Page", "url": ""},
        ],
        "nav_group_items": [
            {"label": "Users", "url": "/admin/users/"},
            {"label": "Settings", "url": "/admin/settings/"},
        ],
        "nav_items": [
            {"label": "Home", "url": "/"},
            {"label": "Docs", "url": "/docs/"},
            {"label": "Gallery", "url": "/gallery/"},
        ],
        "sidebar_sections": [
            {
                "title": "Main",
                "items": [
                    {"label": "Dashboard", "url": "/dash/"},
                    {"label": "Analytics", "url": "/analytics/"},
                ],
            },
            {
                "title": "Settings",
                "items": [
                    {"label": "Profile", "url": "/profile/"},
                    {"label": "Billing", "url": "/billing/"},
                ],
            },
        ],
    }
