"""
Shared HTML building helpers for djust template tags and framework renderers.

Centralising these avoids the "two escape paths" XSS regression class: every
attribute value and text content must pass through ``django.utils.html.escape``
exactly once, in one well-tested place.

See issue #650 (``{% live_input %}`` standalone field tag).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from django.utils.html import escape


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
