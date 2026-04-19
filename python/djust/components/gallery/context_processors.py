"""Gallery context processors — provide theme and gallery data on every request."""

from .views import _get_theme_css, _get_theme_options


def gallery_theme(request):
    """Inject theme state into every template context.

    Reads design system, preset, and mode from cookies. Validates against
    available options and falls back to safe defaults. Generates the
    theme CSS server-side so the initial page load is styled correctly.

    Add to settings.TEMPLATES[0]['OPTIONS']['context_processors']:
        'djust_components.gallery.context_processors.gallery_theme'
    """
    preset_options, ds_options = _get_theme_options()

    design_system = request.COOKIES.get("gallery_ds", "material")
    if design_system not in ds_options:
        design_system = "material"

    preset = request.COOKIES.get("gallery_preset", "default")
    if preset not in preset_options:
        preset = "default"

    mode = request.COOKIES.get("gallery_mode", "light")
    if mode not in ("light", "dark"):
        mode = "light"

    theme_css = _get_theme_css(
        preset=preset,
        design_system=design_system,
        mode=mode,
    )

    return {
        "theme_css": theme_css,
        "design_system": design_system,
        "ds_options": ds_options,
        "preset": preset,
        "preset_options": preset_options,
        "mode": mode,
        "preview_mode": "desktop",
    }
