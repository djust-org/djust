"""Django system checks for djust-theming configuration validation."""

from django.conf import settings
from django.core.checks import Error, Warning, register, Tags

from .manager import get_theme_config
from .accessibility import AccessibilityValidator
from .registry import get_registry


# Foreground/background pairs to check (attr_fg, attr_bg, label)
CONTRAST_PAIRS = [
    ("foreground", "background", "text on background"),
    ("card_foreground", "card", "text on card"),
    ("primary_foreground", "primary", "text on primary"),
    ("secondary_foreground", "secondary", "text on secondary"),
    ("muted_foreground", "muted", "text on muted"),
    ("accent_foreground", "accent", "text on accent"),
    ("destructive_foreground", "destructive", "text on destructive"),
    ("success_foreground", "success", "text on success"),
    ("warning_foreground", "warning", "text on warning"),
    ("info_foreground", "info", "text on info"),
    ("popover_foreground", "popover", "text on popover"),
    ("code_foreground", "code", "text on code"),
    ("brand_foreground", "brand", "text on brand"),
]


@register(Tags.compatibility)
def check_preset_contrast(app_configs, **kwargs):
    """Validate all registered theme presets meet WCAG AA contrast ratios."""
    warnings = []
    validator = AccessibilityValidator()

    for preset_name, preset in get_registry().list_presets().items():
        for mode_name in ("light", "dark"):
            tokens = getattr(preset, mode_name)
            for fg_attr, bg_attr, label in CONTRAST_PAIRS:
                fg = getattr(tokens, fg_attr)
                bg = getattr(tokens, bg_attr)
                ratio = validator.calculate_contrast_ratio(fg, bg)
                if ratio < validator.AA_NORMAL:
                    warnings.append(
                        Warning(
                            f'Preset "{preset_name}" {mode_name} mode: {label} '
                            f"contrast ratio {ratio:.2f}:1 < {validator.AA_NORMAL}:1 (WCAG AA)",
                            hint=f"Adjust {fg_attr} or {bg_attr} to achieve at least "
                            f"{validator.AA_NORMAL}:1 contrast.",
                            id="djust_theming.W001",
                        )
                    )

    return warnings


@register(Tags.compatibility)
def check_css_prefix(app_configs, **kwargs):
    """Validate css_prefix is safe and well-formed."""
    import re

    errors = []
    config = get_theme_config()
    prefix = config.get("css_prefix", "")

    if not prefix:
        return errors

    # Security: reject characters that could break CSS selectors or inject HTML
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9-]*$", prefix):
        errors.append(
            Error(
                f'css_prefix "{prefix}" contains invalid characters. '
                f"Only letters, digits, and hyphens are allowed, and it must start with a letter.",
                hint='Use a prefix like "djt-" or "myapp-" (letters, digits, hyphens only).',
                id="djust_theming.E004",
            )
        )
        return errors

    if not prefix.endswith("-"):
        errors.append(
            Warning(
                f'css_prefix "{prefix}" does not end with "-". '
                f'Component classes will render as ".{prefix}btn" instead of ".{prefix}-btn". '
                f'Consider using "{prefix}-" for cleaner class names.',
                hint='Add a trailing "-" to css_prefix, e.g. "dj-" instead of "dj".',
                id="djust_theming.W002",
            )
        )

    return errors


@register(Tags.compatibility)
def check_context_processor(app_configs, **kwargs):
    """Validate that theme_context is in TEMPLATES context_processors."""
    errors = []
    context_processor_path = "djust.theming.context_processors.theme_context"

    templates = getattr(settings, "TEMPLATES", [])
    found = False
    for template_config in templates:
        processors = template_config.get("OPTIONS", {}).get("context_processors", [])
        if context_processor_path in processors:
            found = True
            break

    if not found:
        errors.append(
            Error(
                "djust.theming.context_processors.theme_context is not in any "
                "TEMPLATES backend's context_processors list. Theme template "
                "variables (theme_head, theme_switcher, etc.) will not be available.",
                hint=(
                    'Add "djust.theming.context_processors.theme_context" to '
                    "TEMPLATES[0]['OPTIONS']['context_processors'] in your settings."
                ),
                id="djust_theming.E001",
            )
        )

    return errors


@register(Tags.compatibility)
def check_preset_valid(app_configs, **kwargs):
    """Validate that the configured preset name exists in THEME_PRESETS."""
    errors = []
    config = get_theme_config()
    preset = config.get("preset", "default")

    registry = get_registry()
    if not registry.has_preset(preset):
        valid_names = ", ".join(sorted(registry.list_presets().keys()))
        errors.append(
            Error(
                f'LIVEVIEW_CONFIG["theme"]["preset"] is set to "{preset}", '
                f"which is not a registered theme preset.",
                hint=f"Valid preset names are: {valid_names}",
                id="djust_theming.E002",
            )
        )

    return errors


@register(Tags.compatibility)
def check_design_system_valid(app_configs, **kwargs):
    """Validate that the configured design system (theme) name exists in DESIGN_SYSTEMS."""
    errors = []
    config = get_theme_config()
    theme = config.get("theme", "material")

    registry = get_registry()
    if not registry.has_theme(theme):
        valid_names = ", ".join(sorted(registry.list_themes().keys()))
        errors.append(
            Error(
                f'LIVEVIEW_CONFIG["theme"]["theme"] is set to "{theme}", '
                f"which is not a registered design system.",
                hint=f"Valid design system names are: {valid_names}",
                id="djust_theming.E003",
            )
        )

    return errors
