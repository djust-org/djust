"""Template variant resolver for native renderers (LVN-II PR-3).

Given a base template name and a renderer's ``output_format``, resolves
the platform-specific variant if one exists, falling back to the base
(HTML) template otherwise.

Example::

    resolve_variant("medicare/home.html", "swiftui")
    # → "medicare/home.swiftui.html" if it exists in any loader,
    #   else "medicare/home.html".

The resolver is renderer-side helper machinery; it does NOT walk the
template AST. The native template-walker that consumes the resolved
variant lands in LVN-II PR-4 (reference variant + stub-client test).

Convention: variants insert the ``output_format`` between the stem and
the final ``.html`` extension. ``home.html`` → ``home.swiftui.html`` /
``home.compose.html``. Authors keep the familiar Django template
syntax — only the tag vocabulary differs.
"""

from __future__ import annotations

from typing import Optional

__all__ = ["resolve_variant", "variant_name"]


def variant_name(base: str, output_format: str) -> str:
    """Insert ``output_format`` before the final ``.html`` extension.

    ``("medicare/home.html", "swiftui") → "medicare/home.swiftui.html"``

    For names without a ``.html`` suffix, appends ``.{output_format}.html``
    — keeps the convention even for unusual template paths.
    """
    if base.endswith(".html"):
        stem = base[: -len(".html")]
    else:
        stem = base
    return f"{stem}.{output_format}.html"


def resolve_variant(base: str, output_format: Optional[str]) -> str:
    """Resolve the actual template name to load given a renderer's output_format.

    If ``output_format`` is ``None`` or ``"html"``, returns ``base``
    unchanged. Otherwise computes the variant name via :func:`variant_name`
    and checks if Django can find it; falls back to ``base`` if not.

    The Django check uses ``django.template.loader.get_template`` which
    raises ``TemplateDoesNotExist`` on miss — we catch and fall through.

    This is the bridge LVN-II PR-4's reference variant uses: existing
    HTML templates stay live; adding a ``.swiftui.html`` sibling lights
    up the native path for that screen, no other config required.
    """
    if not output_format or output_format == "html":
        return base
    variant = variant_name(base, output_format)
    try:
        from django.template.loader import get_template
        from django.template.exceptions import TemplateDoesNotExist
    except ImportError:
        return base
    try:
        get_template(variant)
    except TemplateDoesNotExist:
        return base
    return variant
