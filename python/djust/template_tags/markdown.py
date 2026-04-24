"""
Django ``{% djust_markdown %}`` template tag handler for djust.

Registered on module import via the :func:`djust.template_tags.register`
decorator (same pattern as ``{% url %}`` and ``{% static %}``). The handler
dispatches to the Rust-side :func:`djust._rust.render_markdown` function which
is safe-by-default — raw HTML in the source is escaped, ``javascript:`` URLs
are neutralised, and inputs are size-capped before parsing.

Usage in templates
------------------

.. code-block:: django

    {% djust_markdown body %}
    {% djust_markdown post.content tables=False %}
    {% djust_markdown llm_output provisional=True %}

Accepted keyword arguments (all booleans):

- ``provisional`` (default ``True``) — split trailing in-progress line as
  escaped text, preventing mid-token flicker during streaming renders.
- ``tables`` (default ``True``) — enable GFM tables.
- ``strikethrough`` (default ``True``) — enable ``~~strikethrough~~``.
- ``task_lists`` (default ``False``) — enable ``- [ ]`` / ``- [x]`` checkboxes.

Unknown kwargs are logged at WARNING level and ignored.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from . import TagHandler, register

logger = logging.getLogger(__name__)

# Keep these in sync with the Rust-side RenderOpts defaults.
_BOOL_KWARGS = frozenset({"provisional", "tables", "strikethrough", "task_lists"})
_DEFAULTS: Dict[str, bool] = {
    "provisional": True,
    "tables": True,
    "strikethrough": True,
    "task_lists": False,
}


def _coerce_bool(val: Any) -> bool:
    """Coerce a resolved template argument into a ``bool``.

    Accepts native ``bool``, common falsy strings (``"false"``, ``"0"``,
    ``"no"``, ``"off"``, empty string), or falls back to truthiness.
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() not in {"false", "0", "no", "off", ""}
    return bool(val)


@register("djust_markdown")
class MarkdownTagHandler(TagHandler):
    """Handler for ``{% djust_markdown <expr> [kwargs] %}``.

    Resolves the first argument to a Markdown source string and passes it
    through :func:`djust._rust.render_markdown` with the requested GFM options.
    The Rust side guarantees the output is safe HTML; we do not apply
    additional escaping.

    If the Rust extension is unavailable (e.g. in a degraded CI environment)
    the handler falls back to Django ``escape()`` on the raw source so the
    template still renders — just without formatting.
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        if not args:
            logger.warning(
                "{%% djust_markdown %%} called without a source argument — returning empty string"
            )
            return ""

        try:
            from djust._rust import render_markdown as _rust_render_markdown
        except ImportError:
            logger.error(
                "djust._rust.render_markdown not available — Rust extension "
                "not built. Falling back to escape()."
            )
            from django.utils.html import escape

            src_fallback = self._resolve_arg(args[0], context)
            return escape("" if src_fallback is None else str(src_fallback))

        src_raw = self._resolve_arg(args[0], context)
        src = "" if src_raw is None else str(src_raw)

        kwargs: Dict[str, bool] = dict(_DEFAULTS)
        for arg in args[1:]:
            resolved = self._resolve_arg(arg, context)
            if isinstance(resolved, tuple):
                key, value = resolved
                if key in _BOOL_KWARGS:
                    kwargs[key] = _coerce_bool(value)
                else:
                    logger.warning(
                        "{%% djust_markdown %%}: ignoring unknown kwarg %r",
                        key,
                    )
            else:
                logger.warning(
                    "{%% djust_markdown %%}: ignoring positional arg %r "
                    "after source (expected kwargs only)",
                    arg,
                )

        # Rust output is already HTML-escaped where appropriate; it is safe
        # to insert into the rendered page without further escaping. The Rust
        # template engine's CustomTag path trusts the returned string.
        return _rust_render_markdown(src, **kwargs)
