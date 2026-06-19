"""Fail-closed gate for client-controlled view dotted-paths on the live transport.

SECURITY (GHSA — unauthenticated arbitrary module import): the WebSocket / SSE
mount path resolves the view to mount from a **client-supplied** dotted path by
calling ``__import__(module_path, ...)``. Importing a module that is not already
loaded executes its **top-level code** (import side effects) — so a client could
cause the server to import (and run the top-level code of) any importable module
by name, before any ``LiveView``-subclass check or per-view auth. The previous
``LIVEVIEW_ALLOWED_MODULES`` guard was *fail-open* (``if allowed_modules:`` —
skipped when unset, the default) and used loose ``startswith`` matching.

This module centralizes a **fail-closed** gate that every mount sink must call
*before* importing a client-supplied path. The rule:

  A client view path may be resolved iff EITHER
    (a) its module is **already imported** (in ``sys.modules``) — so resolving it
        executes no new code (URL-routed views are loaded by URLconf at startup;
        this keeps zero-config mounting working), OR
    (b) it matches ``LIVEVIEW_ALLOWED_MODULES`` on a **module-segment boundary**
        (explicit opt-in for lazily-imported views).

  Otherwise the resolve is refused **without importing anything** — closing the
  arbitrary-import / arbitrary-code-execution primitive.
"""

from __future__ import annotations

import sys


def _segment_match(module_path: str, entry: str) -> bool:
    """True iff ``module_path`` equals ``entry`` or is a dotted child of it.

    Segment-boundary match (NOT ``startswith``): ``"app"`` matches ``"app"`` and
    ``"app.views"`` but NOT ``"app_evil"`` / ``"apphacks"``.
    """
    return module_path == entry or module_path.startswith(entry + ".")


def is_view_import_allowed(view_path: str) -> bool:
    """Fail-closed check: may this client-supplied view path be resolved?

    Returns ``False`` (reject, do NOT import) unless the module is already
    loaded or the path is explicitly allowlisted. See module docstring.
    """
    if not isinstance(view_path, str) or "." not in view_path:
        return False
    module_path = view_path.rsplit(".", 1)[0]
    if not module_path:
        return False

    # (b) Explicit allowlist — segment-boundary match. Opt-in lazy import.
    from django.conf import settings

    allowed = getattr(settings, "LIVEVIEW_ALLOWED_MODULES", None) or []
    for entry in allowed:
        if isinstance(entry, str) and entry and _segment_match(module_path, entry):
            return True

    # (a) Already-imported module — resolving it runs no NEW top-level code.
    return module_path in sys.modules
