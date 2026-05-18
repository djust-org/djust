"""Internal deprecation helper for the djust framework.

This module is framework-internal (underscore-prefixed) — it is NOT part of
the public API surface and is itself covered by the "underscore-prefixed
names are internal" clause of the API-stability policy
(``docs/API_STABILITY.md``).

``warn_deprecated`` is the single, standardized way djust emits a runtime
``DeprecationWarning``. Routing every deprecation through it guarantees the
policy's hard rules mechanically:

* every deprecation names the version it was deprecated in (``since``);
* every deprecation names a concrete earliest-removal version (``removed_in``);
* every deprecation may name a migration path (``instead``);
* ``stacklevel`` is set so the warning points at the *caller's* frame, not
  djust's own internals.
"""

from __future__ import annotations

import warnings

__all__ = ["warn_deprecated"]


def warn_deprecated(
    what: str,
    *,
    since: str,
    removed_in: str,
    instead: str | None = None,
    stacklevel: int = 2,
) -> None:
    """Emit a standardized djust ``DeprecationWarning``.

    Args:
        what: Human-readable name of the deprecated thing — e.g. ``"@event"``
            or ``"LiveViewForm"``.
        since: The djust version the thing was deprecated in — e.g. ``"0.3"``
            or ``"1.0"``. Keyword-only.
        removed_in: The earliest djust version the thing may be removed in —
            e.g. ``"1.1.0"`` or ``"2.0.0"``. Keyword-only.
        instead: Optional replacement / migration path — e.g.
            ``"@event_handler"``. When omitted, the message carries no
            migration clause. Keyword-only.
        stacklevel: Forwarded to :func:`warnings.warn`. The default of ``2``
            makes the warning point at the function that *calls*
            ``warn_deprecated``. Increase it by one for each additional
            wrapper frame between the deprecated entry point and this call.
            Keyword-only.

    The emitted message has the form::

        {what} is deprecated since djust {since} and will be removed
        no earlier than {removed_in}. Use {instead} instead.

    (The trailing ``Use {instead} instead.`` clause is omitted when
    ``instead`` is ``None``.)
    """
    message = (
        f"{what} is deprecated since djust {since} and will be removed "
        f"no earlier than {removed_in}."
    )
    if instead is not None:
        message += f" Use {instead} instead."

    warnings.warn(message, DeprecationWarning, stacklevel=stacklevel)
