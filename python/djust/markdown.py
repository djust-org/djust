"""
Safe Markdown rendering for djust LiveView templates.

This module exposes a single high-level helper, :func:`render_markdown`, that
wraps the Rust-side ``djust._rust.render_markdown`` function and returns a
``SafeString`` — the HTML is already escaped where it matters (raw tags are
neutralised, ``javascript:`` URLs are stripped), so marking it safe for
Django's auto-escape pipeline is the correct thing to do.

Typical use is through the ``{% djust_markdown %}`` template tag (see
:mod:`djust.template_tags.markdown`), but the Python function is useful for
tests, admin views, or pre-rendering outside a template.

Example
-------
>>> from djust.markdown import render_markdown
>>> render_markdown("# Hello\\n")
'<h1>Hello</h1>\\n'
"""

from __future__ import annotations

from django.utils.safestring import SafeString, mark_safe

__all__ = ["render_markdown"]


def render_markdown(
    src: str,
    *,
    provisional: bool = True,
    tables: bool = True,
    strikethrough: bool = True,
    task_lists: bool = False,
) -> SafeString:
    """
    Render Markdown source to a sanitised ``SafeString``.

    The Rust implementation guarantees:

    - Raw HTML tags in ``src`` are HTML-escaped (never emitted live).
    - ``javascript:`` / ``vbscript:`` / ``data:`` URLs are neutralised to ``#``.
    - Inputs larger than 10 MiB are returned wrapped in a
      ``<pre class="djust-md-toobig">`` block, never parsed.

    Because of those guarantees the return value is marked safe for Django's
    template auto-escape.

    Parameters
    ----------
    src:
        Markdown source. Must be ``str``.
    provisional:
        If ``True`` (the default) the trailing partially-typed line is rendered
        as escaped plain text so streaming LLM output does not flicker mid-syntax.
    tables, strikethrough, task_lists:
        Toggle optional GFM features.

    Returns
    -------
    SafeString
        HTML safe to interpolate unescaped.
    """
    # Import locally so that the module is importable (with a clear error) even
    # when the Rust extension has not yet been built.
    from djust._rust import render_markdown as _rust_render_markdown

    html = _rust_render_markdown(
        src,
        provisional=provisional,
        tables=tables,
        strikethrough=strikethrough,
        task_lists=task_lists,
    )
    # html is already sanitised by the Rust side.
    return mark_safe(html)
