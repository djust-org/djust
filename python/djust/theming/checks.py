"""Django system checks for djust-theming configuration validation."""

from typing import Any, Iterator

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning, register, Tags

from ._config import get_theme_config
from .accessibility import AccessibilityValidator
from ._registry_accessor import get_registry
from .a11y_exemptions import A11Y_EXEMPTIONS, CONTRAST_PAIRS


# Foreground/background pairs to check — the canonical matrix defined in
# ``a11y_exemptions`` and re-exported here under the historical name for
# backwards compatibility with existing imports (#2874: previously a private
# 13-pair copy that had drifted from the pytest gate's 6-pair matrix; one
# shared list is the #1646 cure).
# Each entry is (attr_fg, attr_bg, minimum_ratio, label).


def _contrast_check_scope() -> str:
    """Return the configured W001 contrast-check scope.

    #1005 — defaults to ``"active"`` (only the configured preset is
    contrast-checked) since djust ships 65+ built-in presets and an
    "all" sweep on every ``manage.py check`` produces hundreds of
    warnings for presets the project never uses, drowning real signal.

    Theme-pack authors who want to exhaustively validate every preset
    can opt in via::

        DJUST_THEMING = {"contrast_check_scope": "all"}

    Returns ``"all"`` or ``"active"``. Unknown values fall back to
    ``"active"`` (the safer signal-preserving default).
    """
    cfg = getattr(settings, "DJUST_THEMING", {}) or {}
    scope = cfg.get("contrast_check_scope", "active")
    return "all" if scope == "all" else "active"


def _presets_to_check() -> Iterator[tuple[str, Any]]:
    """Yield ``(preset_name, preset)`` pairs for W001 contrast checks.

    Honors the scope returned by ``_contrast_check_scope()``: in
    ``"active"`` mode only the configured preset is yielded (resolved
    via the same ``LIVEVIEW_CONFIG["theme"]["preset"]`` setting that
    ``check_preset_valid`` reads); in ``"all"`` mode every registered
    preset is yielded.

    If the configured active preset is missing from the registry, no
    pairs are yielded — ``check_preset_valid`` will already have fired
    a more direct E002 error pointing at the misconfiguration.
    """
    registry = get_registry()
    if _contrast_check_scope() == "all":
        yield from registry.list_presets().items()
        return

    active_name = get_theme_config().get("preset", "default")
    if not registry.has_preset(active_name):
        return
    yield active_name, registry.list_presets()[active_name]


@register(Tags.compatibility)
def check_preset_contrast(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Validate the active theme preset meets WCAG AA contrast ratios.

    #1005 — scoped by default to the active preset only. Set
    ``DJUST_THEMING = {"contrast_check_scope": "all"}`` to validate
    every registered preset (theme-pack-authoring mode).

    #2874 — pairs documented in
    ``djust.theming.a11y_exemptions.A11Y_EXEMPTIONS`` (the same
    reason-carrying, anti-rot-gated list the all-presets pytest gate
    enforces) are skipped, so the shipped legacy palettes do not turn
    every ``manage.py check`` into noise. The check stays fully armed
    for user-authored presets, whose names have no exemption entries.
    """
    warnings = []
    validator = AccessibilityValidator()

    for preset_name, preset in _presets_to_check():
        for mode_name in ("light", "dark"):
            tokens = getattr(preset, mode_name)
            for fg_attr, bg_attr, minimum, label in CONTRAST_PAIRS:
                if (preset_name, mode_name, fg_attr, bg_attr) in A11Y_EXEMPTIONS:
                    continue  # documented legacy-palette debt (#2060/#2874)
                fg = getattr(tokens, fg_attr)
                bg = getattr(tokens, bg_attr)
                ratio = validator.calculate_contrast_ratio(fg, bg)
                if ratio < minimum:
                    warnings.append(
                        Warning(
                            f'Preset "{preset_name}" {mode_name} mode: {label} '
                            f"contrast ratio {ratio:.2f}:1 < {minimum}:1 (WCAG AA)",
                            hint=f"Adjust {fg_attr} or {bg_attr} to achieve at least "
                            f"{minimum}:1 contrast.",
                            id="djust_theming.W001",
                        )
                    )

    return warnings


@register(Tags.compatibility)
def check_css_prefix(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Validate css_prefix is safe and well-formed."""
    import re

    errors: list[CheckMessage] = []
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
def check_context_processor(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
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
        # A Warning, not an Error (#3028): the processor is optional. The
        # {% theme_head %} / {% theme_switcher %} / {% theme_panel %} tags work
        # without it; it only supplies the ``{{ theme_head }}``-style variable
        # shortcuts. As an Error it blocked migrate / runserver for apps that
        # use the tags. The id keeps its E prefix so existing
        # SILENCED_SYSTEM_CHECKS entries still match.
        errors.append(
            Warning(
                "djust.theming.context_processors.theme_context is not in any "
                "TEMPLATES backend's context_processors list. Theme template "
                "variables (theme_head, theme_switcher, etc.) will not be available; "
                "the {% theme_head %} / {% theme_switcher %} tags still work.",
                hint=(
                    'Add "djust.theming.context_processors.theme_context" to '
                    "TEMPLATES[0]['OPTIONS']['context_processors'] if templates use "
                    "the {{ theme_head }} variables, or silence djust_theming.E001."
                ),
                id="djust_theming.E001",
            )
        )

    return errors


@register(Tags.compatibility)
def check_preset_valid(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
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
def check_design_system_valid(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
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
