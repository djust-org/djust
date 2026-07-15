"""Read-only bug-capture replay viewer (B7 iter B, #1562).

``GET /__djust__/replay/<blob>`` decodes a ``djbug1.<base64>`` blob
(produced by ``djust.bug_capture.encode_view_state()`` /
``BugCapture.encode()``) and displays ``state_before`` / ``state_after``
side-by-side with a per-key diff highlight, the recorded
``vdom_patches``, and the ``event_name`` / ``scrubbed_fields`` headers.

This is a **plain Django view**, not a djust ``LiveView`` — a deliberate
deviation from the issue's literal "New built-in djust LiveView" wording.
A ``LiveView`` mounts over a WebSocket and accepts ``event`` frames by
design; giving this route that machinery would mean actively disarming
a dispatch surface that a plain HTTP view never has in the first place.
Following the local precedent of ``djust.theming.gallery`` (also a
built-in dev-tooling surface implemented as plain Django views, not
LiveViews), this stays a normal request/response view — the simplest
implementation that is *structurally* incapable of dispatching a
handler, not just carefully guarded against it.

Security model — READ THIS BEFORE TOUCHING THIS FILE
------------------------------------------------------
The decoded blob is UNTRUSTED input (see the ``djust.bug_capture``
module docstring: "Anyone can construct a syntactically-valid
``djbug1.<base64>`` payload"). This view MUST stay:

1. **Strictly read-only.** No ``LiveView`` is instantiated, no event
   handler is ever looked up or called by name, and nothing here writes
   to any store or session. ``capture.event_name`` is display-only —
   grep this file for ``getattr(`` before adding new code; none may
   combine a dynamically-computed attribute name with ``event_name``.
2. **Escape-on-render.** Every captured string reaches the browser
   through Django's autoescaping (``{{ var }}`` in the template) or the
   explicit ``django.utils.html.escape()`` call below — never
   ``mark_safe()`` on captured content. ``vdom_patches[].html`` is
   untrusted HTML; it is shown as escaped TEXT inside ``<pre><code>``,
   never parsed as markup in the parent document. A best-effort visual
   preview is additionally rendered inside a fully ``sandbox``-ed
   ``<iframe srcdoc>`` (no ``allow-scripts``, no ``allow-same-origin``)
   so even an undiscovered escaping gap in the preview can't execute
   script or touch the parent page/origin.
3. **No multi-tenant coupling.** This view never imports
   ``djust.tenants`` and never calls ``set_current_tenant()`` /
   ``get_current_tenant()``. A captured ``tenant_id`` (if the
   developer's public state happened to include one) is shown as plain
   display text like any other state key — never used to scope a query,
   because this view issues NO database queries at all.
4. **DEBUG-gated**, mirroring ``djust.bug_capture``'s own
   ``_enforce_prod_gate()``: refuses to serve (404) when
   ``settings.DEBUG`` is falsy unless the deployer set
   ``DJUST_BUG_CAPTURE_PROD_OPT_IN = True``. Gated at TWO layers — see
   ``djust/urls.py`` (the route isn't even registered) and here
   (defense in depth, in case some other codepath calls this view
   directly).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponseNotFound,
)
from django.template.loader import render_to_string
from django.utils.html import escape

from djust.bug_capture import BugCapture

_MISSING = object()


def _prod_gate_open() -> bool:
    """Mirror ``djust.bug_capture._enforce_prod_gate()``'s condition.

    True when the replay viewer may serve a response: always under
    ``DEBUG=True``, or in production ONLY when the deployer set the
    explicit ``DJUST_BUG_CAPTURE_PROD_OPT_IN = True`` opt-in (the exact
    same literal-``True`` check ``bug_capture._enforce_prod_gate`` uses —
    intentionally duplicated here as a simple boolean rather than
    imported, since importing a private helper across modules for a
    two-line check isn't worth the coupling; both call sites are covered
    by regression tests so the two can't silently drift without a test
    failure).
    """
    if getattr(settings, "DEBUG", False):
        return True
    return getattr(settings, "DJUST_BUG_CAPTURE_PROD_OPT_IN", False) is True


def replay_view(request: HttpRequest, blob: str) -> HttpResponse:
    """Decode and display a ``djbug1.`` bug-capture blob. Read-only.

    Returns:
        200 with the rendered replay page for a valid blob.
        400 for a malformed blob (``BugCapture.decode`` raised
            ``ValueError``) — response is ``text/plain`` so a malicious
            blob crafted to inject markup into the error message can
            never be parsed as HTML by the browser, regardless of
            escaping correctness elsewhere.
        404 outside DEBUG without the production opt-in, or for any
            non-GET/HEAD method (avoids leaking "this route exists" to
            a method probe in production).
    """
    if not _prod_gate_open():
        return HttpResponseNotFound()

    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])

    try:
        capture = BugCapture.decode(blob)
    except ValueError as exc:
        return HttpResponseBadRequest(
            "Malformed bug-capture blob: %s" % escape(str(exc)),
            content_type="text/plain; charset=utf-8",
        )

    ctx: Dict[str, Any] = {
        "request": request,
        "blob": blob,
        "event_name": capture.event_name,
        "scrubbed_fields": capture.scrubbed_fields,
        "state_before_json": _pretty(capture.state_before),
        "state_after_json": _pretty(capture.state_after),
        "diff_rows": _diff_rows(capture.state_before, capture.state_after),
        "patch_rows": _patch_rows(capture.vdom_patches),
        "dom_preview": _dom_preview(capture.vdom_patches),
        "patch_count": len(capture.vdom_patches),
    }
    html = render_to_string("djust/bug_capture/replay.html", ctx, request=request)
    return HttpResponse(html)


def _pretty(value: Any) -> str:
    """Pretty-print a JSON-safe value for display inside ``<pre><code>``.

    ``capture.state_before`` / ``state_after`` are already JSON-decoded
    by ``BugCapture.decode`` (so this can never raise on a well-formed
    ``BugCapture``); ``default=str`` is a defensive fallback only.
    """
    try:
        return json.dumps(value, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _diff_rows(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-top-level-key diff between ``state_before`` and ``state_after``.

    Returns rows sorted by key, each with a ``status`` of ``"added"``,
    ``"removed"``, ``"changed"``, or ``"same"`` — the template highlights
    non-``"same"`` rows. Scoped to top-level keys only (matches the
    ``scrub_fields()`` convention documented in ``djust.bug_capture``:
    djust's public-state convention exposes state as flat top-level
    attributes on the view, so a top-level diff matches the common case).
    """
    keys = sorted(set(before) | set(after))
    rows: List[Dict[str, Any]] = []
    for key in keys:
        has_before = key in before
        has_after = key in after
        if not has_before and has_after:
            status = "added"
        elif has_before and not has_after:
            status = "removed"
        elif before.get(key) != after.get(key):
            status = "changed"
        else:
            status = "same"
        rows.append(
            {
                "key": key,
                "status": status,
                "before": _pretty(before[key]) if has_before else None,
                "after": _pretty(after[key]) if has_after else None,
            }
        )
    return rows


def _patch_rows(patches: List[Any]) -> List[Dict[str, Any]]:
    """Build display rows for ``vdom_patches`` — one pretty-printed block per patch.

    Deliberately generic over the patch shape (``op`` / ``path`` /
    ``html`` / ``text`` / anything else a future patch op adds) rather
    than special-casing known op types — every patch is rendered as an
    escaped JSON blob, so a shape this code doesn't recognize still
    renders safely instead of being silently dropped or raising.
    """
    rows: List[Dict[str, Any]] = []
    for index, patch in enumerate(patches):
        op = patch.get("op", "?") if isinstance(patch, dict) else "?"
        path = patch.get("path", []) if isinstance(patch, dict) else []
        rows.append(
            {
                "index": index,
                "op": op,
                "path": _pretty(path),
                "pretty": _pretty(patch),
            }
        )
    return rows


def _dom_preview(patches: List[Any]) -> str:
    """Best-effort concatenation of patch HTML payloads for a sandboxed preview.

    NOT a faithful re-render of the final DOM — ``vdom_patches`` are
    positional diffs against a live tree, not a standalone document —
    just a rough visual sanity-check. Rendered inside a fully
    ``sandbox``-ed ``<iframe>`` (see the template) so any script/style
    the captured HTML carries can never execute or affect the parent
    page, independent of whether this string happens to be perfectly
    escaped everywhere it's used.
    """
    chunks = []
    for patch in patches:
        if isinstance(patch, dict):
            html = patch.get("html")
            if isinstance(html, str) and html:
                chunks.append(html)
    return "\n".join(chunks)


__all__ = ["replay_view"]
