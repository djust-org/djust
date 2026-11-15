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
from typing import Any, Dict, List, Sequence

from .._html import safe_html
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


def djust_markdown_html(
    source: Any, options: Dict[str, Any], positional: Sequence[Any] = ()
) -> str:
    """The output of ``{% djust_markdown %}``, on either engine (#3994).

    Both the Rust-engine :class:`MarkdownTagHandler` below and the Django-engine tag of the same
    name (``djust.templatetags.live_tags``, ``{% load live_tags %}``) call this with the operands
    their engine resolved, so the two render the same bytes for the same input (the
    dual-registration invariant ``_client_config_html`` states). ``source`` ``None`` is the empty
    document; ``options`` are the keyword operands (an unknown one is logged and ignored, as is a
    positional operand after the source).
    """
    from djust._rust import render_markdown as _rust_render_markdown

    src = "" if source is None else str(source)
    kwargs: Dict[str, bool] = dict(_DEFAULTS)
    for key, value in options.items():
        if key in _BOOL_KWARGS:
            kwargs[key] = _coerce_bool(value)
        else:
            logger.warning("{%% djust_markdown %%}: ignoring unknown kwarg %r", key)
    for arg in positional:
        logger.warning(
            "{%% djust_markdown %%}: ignoring positional arg %r after source (expected kwargs only)",
            arg,
        )

    # Rust output is already HTML-escaped where appropriate; it is safe
    # to insert into the rendered page without further escaping.
    #
    # `mark_safe` since #2379, and the sentence this REPLACES is why:
    # "the Rust template engine's CustomTag path trusts the returned
    # string". It did — unconditionally, for every handler — which is the
    # defect #2379 closes. The path now escapes a return without
    # `__html__` (Django's `SimpleNode.render` rule), so the trust has to
    # be stated rather than assumed, and `render_markdown` is the arm
    # entitled to state it: it sanitises its own output (`markdown.rs`,
    # the `nh3` pass) rather than passing user Markdown through raw.
    return safe_html(_rust_render_markdown(src, **kwargs))


def warn_no_source() -> str:
    """``{% djust_markdown %}`` with no source operand, on either engine: logged, empty."""
    logger.warning(
        "{%% djust_markdown %%} called without a source argument — returning empty string"
    )
    return ""


@register("djust_markdown")
class MarkdownTagHandler(TagHandler):
    """Handler for ``{% djust_markdown <expr> [kwargs] %}``.

    Resolves the first argument to a Markdown source string and passes it
    through :func:`djust._rust.render_markdown` with the requested GFM options
    (:func:`djust_markdown_html`, shared with the Django-engine tag). The Rust
    side guarantees the output is safe HTML; we do not apply additional
    escaping.
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        if not args:
            return warn_no_source()

        src_raw = self._resolve_arg(args[0], context)
        if isinstance(src_raw, tuple):
            # The source is a positional argument, never a kwarg. If
            # ``_resolve_arg`` still returned a (key, value) tuple, the resolved
            # value was shaped like ``key=value`` (no whitespace) and got
            # mistaken for a kwarg; use the raw resolved arg string so the
            # source is never a Python-tuple-repr (#2037 defence-in-depth).
            src_raw = args[0]

        options: Dict[str, Any] = {}
        positional: List[Any] = []
        for arg in args[1:]:
            resolved = self._resolve_arg(arg, context)
            if isinstance(resolved, tuple):
                key, value = resolved
                options[key] = value
            else:
                positional.append(arg)
        return djust_markdown_html(src_raw, options, positional)
