"""``{% dj_suspense %}`` block tag — template-level async loading boundaries (v0.5.0).

A Suspense boundary wraps a section of a template that depends on one or more
:class:`~djust.async_result.AsyncResult` assigns (typically produced via
:meth:`~djust.mixins.async_work.AsyncWorkMixin.assign_async`). While any of the
awaited references are still loading, the boundary emits a *fallback* (either
a user-provided template or a default skeleton div). If any reference failed,
the boundary emits an error div. Once all references are ``ok``, the body is
rendered verbatim.

Example usage::

    {% dj_suspense await="metrics" fallback="components/metric_skeleton.html" %}
      <div class="metric">{{ metrics.result.total_users }}</div>
    {% enddj_suspense %}

Multiple references may be awaited with a comma-separated list::

    {% dj_suspense await="metrics,chart_data" %}…{% enddj_suspense %}

Design notes:

* Explicit ``await=`` is required to distinguish awaited references from
  context vars the body merely reads. This keeps the tag predictable and
  debuggable — no reflection magic.
* Missing / unknown references are treated as *loading* (defensive) so a
  typo doesn't silently leak unfinished state.
* Nested ``{% dj_suspense %}`` works because each boundary resolves
  independently against the context.
"""

from __future__ import annotations

import html
import logging
from typing import Any, Iterable

from ..async_result import AsyncResult

logger = logging.getLogger(__name__)


_DEFAULT_FALLBACK_HTML = (
    '<div class="djust-suspense-fallback" role="status" aria-live="polite">'
    '<span class="djust-suspense-spinner" aria-hidden="true"></span>'
    '<span class="djust-suspense-label">Loading…</span>'
    "</div>"
)


def _parse_suspense_args(args: Iterable[str]) -> dict[str, str]:
    """Parse ``key=val`` pairs from the suspense tag argv.

    Only string-literal or bareword values are meaningful for this handler
    (``await``, ``fallback``). Values wrapped in single or double quotes are
    stripped; everything else is kept as-is.
    """
    out: dict[str, str] = {}
    for raw in args:
        token = str(raw)
        if "=" not in token:
            continue
        key, val = token.split("=", 1)
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        out[key] = val
    return out


def _await_names(raw: str) -> list[str]:
    """Split the ``await="a,b,c"`` argument into a clean name list."""
    return [part.strip() for part in raw.split(",") if part.strip()]


def _render_fallback(fallback: str, context: dict[str, Any]) -> str:
    """Render the fallback template or return the default skeleton markup."""
    if not fallback:
        return _DEFAULT_FALLBACK_HTML
    try:
        from django.template.loader import render_to_string
    except ImportError:  # pragma: no cover — Django is always present in runtime
        logger.debug("dj_suspense: Django not available, falling back to default spinner")
        return _DEFAULT_FALLBACK_HTML
    try:
        return render_to_string(fallback, context)
    except Exception as exc:  # noqa: BLE001 — fallback gracefully on template errors
        logger.warning(
            "dj_suspense: failed to render fallback template %s: %s",
            fallback,
            exc,
        )
        return _DEFAULT_FALLBACK_HTML


def _render_error(error: BaseException) -> str:
    """Render the error placeholder for a failed ``AsyncResult``."""
    message = html.escape(str(error) or error.__class__.__name__)
    return f'<div class="djust-suspense-error" role="alert"><span>{message}</span></div>'


class SuspenseTagHandler:
    """Block handler for ``{% dj_suspense await="..." fallback="..." %}``.

    The handler classifies the boundary's state by inspecting each awaited
    reference in the render context:

    * Any awaited ref is ``loading`` (or unknown / missing) → render fallback.
    * Any awaited ref is ``failed``                           → render error div.
    * All awaited refs are ``ok``                             → render body.
    * No ``await=`` arg                                       → passthrough body.
    """

    def render(self, args: list[str], content: str, context: dict[str, Any]) -> str:
        kwargs = _parse_suspense_args(args)
        await_raw = kwargs.get("await", "")
        fallback = kwargs.get("fallback", "")

        if not await_raw:
            return content

        names = _await_names(await_raw)
        if not names:
            return content

        any_failed_error: BaseException | None = None
        any_loading = False

        for ref_name in names:
            value = context.get(ref_name)
            if isinstance(value, AsyncResult):
                if value.failed:
                    any_failed_error = value.error
                    break
                # AsyncResult guarantees exactly one of loading/ok/failed is
                # True (see AsyncResult.__post_init__), so `not value.ok` is
                # redundant once `failed` is ruled out — `value.loading` is
                # sufficient.
                if value.loading:
                    any_loading = True
            else:
                # Unknown / missing / non-AsyncResult refs are treated as
                # loading — defensive default so a typo doesn't silently
                # render stale content. Emit a debug log so the typo case
                # surfaces during development.
                if value is not None:
                    logger.debug(
                        "dj_suspense await=%s expected AsyncResult, got %s",
                        ref_name,
                        type(value).__name__,
                    )
                any_loading = True

        if any_failed_error is not None:
            return _render_error(any_failed_error)
        if any_loading:
            return _render_fallback(fallback, context)
        return content
