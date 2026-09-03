"""Django's ``{% debug %}`` for the Rust engine (#2556).

``defaulttags.DebugNode.render`` gates on ``settings.DEBUG`` — it renders
``""`` in production, not "regardless" — and then emits
``escape(pformat(d))`` for each context dict (the user's dict first, the
``{'False': False, 'None': None, 'True': True}`` builtins last), ``"\\n\\n"``,
and ``escape(pformat(sys.modules))``. This handler matches that gate exactly,
no looser and no stricter.

What reaches it is SAFER than what reaches Django's node, and the parity test
pins it (``test_remaining_builtin_tags_2556.py``, the #1867 falsification):
the dict a handler receives has already been through the serialization floor
(``docs/SECURE_DEFAULTS.md`` Pattern 1 — ``password`` and friends never reach
it) and every model in the sidecar arrives as a ``_SidecarModelProxy``, whose
``repr`` is the model's own ``<Class: str>`` and nothing more.

The Rust ``Context`` has no builtins layer: a Django ``Context.flatten()``
carries ``True`` / ``False`` / ``None`` as plain keys (the plain backend) and
a LiveView render carries none. Both are normalized to Django's two-dict
shape so the bytes agree.
"""

from __future__ import annotations

import sys
from pprint import pformat
from typing import Any, ClassVar, Dict, List, Optional, Set

from django.conf import settings
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

from . import TagHandler, register

#: ``BaseContext._reset_dicts``' builtins dict, the LAST dict ``DebugNode``
#: iterates.
_BUILTINS: Dict[str, Any] = {"False": False, "None": None, "True": True}


@register("debug")
class DebugTagHandler(TagHandler):
    """``{% debug %}`` — ``defaulttags.DebugNode`` over the render context."""

    RESOLVE_ARG_POSITIONS: ClassVar[Optional[Set[int]]] = frozenset()  # type: ignore[assignment]

    def render(self, args: List[str], context: Dict[str, Any]) -> SafeString:
        if not settings.DEBUG:
            return mark_safe("")
        user_dict = dict(context)
        for key, builtin in _BUILTINS.items():
            if key in user_dict and user_dict[key] is builtin:
                del user_dict[key]
        output = [
            escape(pformat(user_dict)),
            escape(pformat(_BUILTINS)),
            "\n\n",
            escape(pformat(sys.modules)),
        ]
        return mark_safe("".join(output))
