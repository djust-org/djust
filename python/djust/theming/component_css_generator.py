"""
Generate component CSS with configurable class name prefix.

When ``css_prefix`` is empty the output is identical to the static
``components.css`` file.  When a prefix is provided (e.g. ``"dj-"``),
every component class selector is rewritten: ``.btn`` becomes ``.dj-btn``,
``.card-header`` becomes ``.dj-card-header``, etc.
"""

import re
from functools import lru_cache
from pathlib import Path

# All component class names that appear in components.css, longest-first
# so that e.g. "alert-destructive" is matched before "alert".
_COMPONENT_CLASSES = sorted(
    [
        # Alert
        "alert-content",
        "alert-title",
        "alert-message",
        "alert-dismiss",
        "alert-default",
        "alert-success",
        "alert-warning",
        "alert-destructive",
        "alert",
        # Badge
        "badge-default",
        "badge-secondary",
        "badge-success",
        "badge-warning",
        "badge-destructive",
        "badge-outline",
        "badge",
        # Button
        "btn-primary",
        "btn-secondary",
        "btn-destructive",
        "btn-ghost",
        "btn-link",
        "btn-sm",
        "btn-md",
        "btn-lg",
        "btn",
        # Card
        "card-header",
        "card-title",
        "card-body",
        "card-footer",
        "card",
        # Input
        "input-group",
        "input-label",
        "input",
        # Theme Switcher
        "theme-switcher",
        "theme-mode-controls",
        "theme-mode-btn",
        "theme-preset-select",
        "theme-preset-controls",
        # Form layouts
        "theme-form-stacked",
        "theme-form-horizontal",
        "theme-form-inline",
        "theme-form-field",
        "theme-form-errors",
        "theme-label",
        "theme-help-text",
        "theme-field-error",
        "theme-error-list",
    ],
    key=len,
    reverse=True,
)


def _read_static_css() -> str:
    """Read the canonical static components.css file.

    If the file is wrapped in ``@layer components { ... }`` (for direct
    ``<link>`` tag use), the wrapper is stripped so that callers can
    re-wrap or prefix the raw content.
    """
    css_path = (
        Path(__file__).resolve().parent / "static" / "djust_theming" / "css" / "components.css"
    )
    css = css_path.read_text()

    # Strip outer @layer wrapper if present
    stripped = css.strip()
    if stripped.startswith("@layer components {") and stripped.endswith("}"):
        # Remove the opening "@layer components {" and closing "}"
        inner = stripped[len("@layer components {") : -1]
        return inner.strip()

    return css


def _apply_prefix(css: str, prefix: str) -> str:
    """Apply *prefix* to every component class selector in *css*.

    Works by replacing ``.classname`` with ``.{prefix}classname`` for
    every class in ``_COMPONENT_CLASSES``.  The replacement is done
    longest-first to avoid partial matches.
    """
    if not prefix:
        return css

    # Build a single regex that matches any component class preceded by a dot.
    # Use word boundary after the class name to avoid partial matches, but
    # we also need to handle pseudo-selectors (:hover, ::placeholder) and
    # compound selectors (.active).
    #
    # Strategy: for each class, replace  \.<class>  with  \.<prefix><class>
    # but only when preceded by a dot (selector context).
    for cls in _COMPONENT_CLASSES:
        # Escape for regex (class names use hyphens which are literal in regex)
        escaped = re.escape(cls)
        # Match .<class> when followed by a non-alphanumeric-hyphen char
        # (i.e. end of class name in selector context: whitespace, {, :, ., etc.)
        pattern = r"\." + escaped + r"(?=[^a-zA-Z0-9_-]|$)"
        replacement = "." + prefix + cls
        css = re.sub(pattern, replacement, css)

    return css


@lru_cache(maxsize=16)
def generate_component_css(prefix: str = "") -> str:
    """Return the component CSS with all class selectors prefixed.

    When ``use_css_layers`` is enabled (default), the output is wrapped
    in ``@layer components { ... }``.

    Args:
        prefix: Namespace prefix to prepend to every component class.
                An empty string returns the original CSS unchanged.

    Returns:
        Complete component CSS string.
    """
    from .manager import get_theme_config

    css = _read_static_css()
    css = _apply_prefix(css, prefix)

    config = get_theme_config()
    if config.get("use_css_layers", True):
        css = f"@layer components {{\n{css}\n}}"

    return css
