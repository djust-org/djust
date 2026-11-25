"""One ``DeprecationWarning`` per process for a component argument alias (#4207).

The aliases still work; they are deprecated to ``removed_in="3.0.0"``. The
warning is once per (tag, alias) so a page rendering the tag in a loop does not
flood the log.
"""

from __future__ import annotations

_WARNED: set[tuple[str, str]] = set()


def warn_alias(tag: str, alias: str, instead: str, *, stacklevel: int = 4) -> None:
    """Warn once that ``tag``'s ``alias`` argument is deprecated for ``instead``."""
    key = (tag, alias)
    if key in _WARNED:
        return
    _WARNED.add(key)
    from djust._deprecation import warn_deprecated

    warn_deprecated(
        "%s's %s argument" % (tag, alias),
        since="2.0",
        removed_in="3.0.0",
        instead=instead,
        stacklevel=stacklevel,
    )
