"""Write session-saved public state back onto a rebuilt view.

The HTTP event fallback, the WebSocket snapshot restore and the sticky-child
restore all rebuild a view instance from the context a previous request saved.
That context can hold a key named like something the view *class* defines —
``ctx["images_go_out"] = self.images_go_out()`` next to the method of the same
name. A plain ``setattr`` then replaces the method (or shadows the property) on
the instance with the saved value, and the next call fails with
``TypeError: 'bool' object is not callable``. The saved value is plain data,
so writing it over class behaviour is never what the view meant: the key is
skipped. A method is reported (once per view class and key); a property is
skipped quietly, since it computes the value itself (``@computed`` values are
in every context) and a read-only one already refused the write.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, Mapping, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_WARNED: Set[Tuple[type, str]] = set()

_MISSING = object()


def class_member_kind(cls: type, name: str) -> Optional[str]:
    """``"method"`` / ``"property"`` when ``cls`` defines ``name`` as one, else ``None``.

    Looked up statically (no descriptor runs). Any callable class attribute
    counts as a method: functions, static/class methods, nested classes.
    """
    attr = inspect.getattr_static(cls, name, _MISSING)
    if attr is _MISSING:
        return None
    if isinstance(attr, property) or getattr(attr, "_djust_framework_derived", False):
        # A framework-derived attribute computes its value itself (ThemeMixin's
        # rendered HTML, #3626); a value saved by an older release must not
        # shadow it.
        return "property"
    if isinstance(attr, (staticmethod, classmethod)) or callable(attr):
        return "method"
    return None


def restore_public_state(view: Any, saved_state: Mapping[str, Any]) -> Dict[str, Any]:
    """Apply ``saved_state`` to ``view``; return what was applied.

    Skips private keys and callable values (as every caller did before), and a
    key that names a method (logged) or property of the view's class.
    """
    from .security import safe_setattr

    cls = type(view)
    applied: Dict[str, Any] = {}
    for key, value in saved_state.items():
        if not isinstance(key, str) or key.startswith("_") or callable(value):
            continue
        kind = class_member_kind(cls, key)
        if kind == "property":
            continue
        if kind is not None:
            if (cls, key) not in _WARNED:
                _WARNED.add((cls, key))
                logger.warning(
                    "Not restoring saved state %r onto %s: the class defines a "
                    "method of that name, which the saved value would replace. "
                    "Rename the context key.",
                    key,
                    cls.__qualname__,
                )
            continue
        if safe_setattr(view, key, value, allow_private=False):
            applied[key] = value
    return applied
