"""
Shared HTML building helpers for djust template tags and framework renderers.

Centralising these avoids the "two escape paths" XSS regression class: every
attribute value and text content must pass through ``django.utils.html.escape``
exactly once, in one well-tested place.

See issue #650 (``{% live_input %}`` standalone field tag).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, cast

from django.utils.html import escape
from django.utils.safestring import mark_safe


def build_tag(
    tag: str,
    attrs: Mapping[str, Any],
    content: Optional[str] = None,
    *,
    content_is_safe: bool = False,
) -> str:
    """Build an HTML tag with every attribute value HTML-escaped.

    Args:
        tag: The element name (``"input"``, ``"textarea"``, ``"select"``).
        attrs: Mapping of attribute names to values. Keys with ``None`` or
            ``False`` values are omitted entirely (useful for boolean
            attributes — pass ``True`` to emit them as ``name="name"``).
            Every value is coerced to ``str`` and HTML-escaped.
        content: Inner text / HTML. When ``None`` the tag is self-closing
            (``<input ... />``). When set, the tag is rendered with
            opening and closing tags (``<textarea ...>content</textarea>``).
        content_is_safe: If ``True``, ``content`` is assumed to already be
            safe HTML (e.g. a pre-built ``<option>`` list) and is NOT
            escaped. Callers must ensure this. Default ``False`` escapes
            content for safety.

    Returns:
        The rendered HTML string.

    Examples:
        >>> build_tag("input", {"type": "text", "value": "hello"})
        '<input type="text" value="hello" />'

        >>> build_tag("textarea", {"name": "msg"}, "line 1\\nline 2")
        '<textarea name="msg">line 1\\nline 2</textarea>'

        >>> build_tag("input", {"type": "checkbox", "checked": True})
        '<input type="checkbox" checked="checked" />'
    """
    parts = []
    for key, value in attrs.items():
        if value is None or value is False:
            continue
        if value is True:
            # HTML boolean attributes are canonically rendered as name="name".
            parts.append(f'{key}="{escape(key)}"')
        else:
            parts.append(f'{key}="{escape(str(value))}"')

    attrs_str = " ".join(parts)
    if content is None:
        return f"<{tag} {attrs_str} />" if attrs_str else f"<{tag} />"

    safe_content = content if content_is_safe else escape(content)
    if attrs_str:
        return f"<{tag} {attrs_str}>{safe_content}</{tag}>"
    return f"<{tag}>{safe_content}</{tag}>"


def safe_html(html: str) -> str:
    """``mark_safe`` with a concrete ``str`` return type (#2379).

    Since #2379 the Rust tag bridge ESCAPES a handler's return unless it
    carries ``__html__`` — Django's ``SimpleNode.render`` rule — so a handler
    that means markup must say so. ``mark_safe`` is decorated with
    ``@keep_lazy`` (untyped), and mypy infers its return as ``Any`` when
    Django ships without type stubs, which would leak ``Any`` out of every
    handler's ``-> str`` return (``no-any-return``) and break the ADR-023
    strict islands.

    This pins the return to the built-in ``str``. Behaviourally identical to
    calling ``mark_safe`` directly — ``SafeString`` IS a ``str`` subclass and
    the HTML bytes are unchanged.

    ``components/rust_handlers.py`` has carried a private ``_safe`` with this
    exact body and reason since before #2379; this is the same helper in a
    module the tag packages can all import, so the four handlers #2379 marks
    do not each grow a fourth copy of the workaround (#1646). ``_safe`` stays
    where it is: it is applied at ~190 call sites in that one module, and
    re-pointing them is a rename with no behavioural content.
    """
    return cast(str, mark_safe(html))
