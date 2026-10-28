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

import sys
import warnings
from types import FrameType
from typing import Optional

__all__ = ["class_statement_stacklevel", "warn_deprecated"]


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


def _is_internal(frame: FrameType) -> bool:
    name = frame.f_globals.get("__name__", "")
    return (name.startswith("djust.") and not name.startswith("djust.tests.")) or name == "abc"


def class_statement_stacklevel() -> int:
    """The ``stacklevel`` that makes a warning raised from ``__init_subclass__``
    point at the application's ``class`` statement.

    Call it from the ``__init_subclass__`` that calls :func:`warn_deprecated`.
    How far up the ``class`` statement sits depends on the application's base
    order: another base's ``__init_subclass__`` (``PresenceMixin``,
    ``TutorialMixin``) can chain to ours, and ``abc.ABCMeta.__new__`` adds a
    Python frame. So the level is counted: skip djust's own frames and the
    ``abc`` machinery, and stop at the first frame that is the application's.
    Test modules (``djust.tests.*``) count as the application.
    """
    # Frame 1 is the ``__init_subclass__`` that called us; warn_deprecated's
    # own ``stacklevel=2`` already means "that function", so start one above.
    frame: Optional[FrameType] = sys._getframe(1).f_back
    level = 3
    while frame is not None and _is_internal(frame):
        frame = frame.f_back
        level += 1
    return level
