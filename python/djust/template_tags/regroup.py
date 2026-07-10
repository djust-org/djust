"""Built-in handler for Django's ``{% regroup %}`` template tag.

``{% regroup <expr> by <attr> as <var> %}`` regroups a flat list into a
list of groups, matching Django's ``defaulttags.RegroupNode`` semantics:

* **Input order is preserved** — grouping is *consecutive* (like
  ``itertools.groupby``), never pre-sorted. Two runs of the same grouper
  separated by a different grouper become *separate* groups, so callers
  must sort upstream if they want one group per key.
* ``<var>`` is bound to ``[{"grouper": key, "list": [item, ...]}, ...]``.
  Templates access ``{{ group.grouper }}`` and ``{% for x in group.list %}``.
* ``<attr>`` supports dotted paths (``author.team``) resolved per item
  against dict keys, sequence indices, and object attributes.

Registered as an *assign* tag handler (``register_assign``): it mutates
the template context rather than emitting HTML.

Operand resolution (#2041)
--------------------------
``RegroupTagHandler`` declares ``RESOLVE_ARG_POSITIONS = {0}``, so the
Rust engine resolves **only** ``args[0]`` (the ``<expr>`` source) against
the context — JSON-encoding the structured (list/object) value, so
``<expr>`` arrives as a JSON string which the handler decodes back into
the source records. The ``by`` / ``<attr>`` / ``as`` / ``<var>`` operands
(positions 1-4) arrive **unresolved**, as literal tokens, matching Django
(``RegroupNode`` never resolves the attribute against the outer context).
This makes the attribute-name shadowing bug impossible: before #2041 the
engine resolved *every* arg, so a top-level context variable named like
the ``<attr>`` token (``country``, ``category``, ``type``, ... — djust
auto-exposes public view attributes) shadowed the per-item lookup,
silently corrupting the grouping.

Known limitations vs. Django:

* The ``<expr>`` source must resolve to a JSON-encodable sequence
  (django-normalised context values always are). Filter expressions on
  the source (``cities|dictsort:"country"``) are not supported.
"""

from __future__ import annotations

import json
from itertools import groupby
from typing import Any, ClassVar, Dict, List, Optional, Set

from . import AssignTagHandler, register_assign


@register_assign("regroup")
class RegroupTagHandler(AssignTagHandler):
    """Handler implementing ``{% regroup expr by attr as var %}``.

    Only ``args[0]`` (the source expression) is resolved by the Rust
    engine; the ``by`` / ``<attr>`` / ``as`` / ``<var>`` operands arrive
    as literal tokens (see ``RESOLVE_ARG_POSITIONS`` and the module
    docstring).
    """

    #: Resolve only the source expression (position 0) against the
    #: context; ``by`` / ``<attr>`` / ``as`` / ``<var>`` stay literal so a
    #: context key can't shadow the attribute name (#2041).
    RESOLVE_ARG_POSITIONS: ClassVar[Optional[Set[int]]] = {0}

    def render(self, args: List[str], context: Dict[str, Any]) -> Dict[str, Any]:
        # Expected args: [<expr-json>, "by", <attr>, "as", <var>]. Only
        # <expr> is resolved by the engine; the rest are literal tokens
        # (RESOLVE_ARG_POSITIONS = {0}), so <attr> is the real attribute
        # name, never a shadowed context value (#2041).
        if len(args) < 5 or args[1] != "by" or args[3] != "as":
            # Malformed tag — Django raises TemplateSyntaxError at compile
            # time; the Rust parser has no such hook, so degrade to a
            # no-op merge rather than crashing the whole render.
            return {}

        expr, attr, var_name = args[0], args[2], args[4]

        items = self._decode_source(expr, context)

        groups = [
            {"grouper": key, "list": list(vals)}
            for key, vals in groupby(items, key=lambda item: self._lookup(item, attr))
        ]
        return {var_name: groups}

    @classmethod
    def _decode_source(cls, expr: str, context: Dict[str, Any]) -> List[Any]:
        """Resolve the source expression into a concrete list.

        Two shapes are accepted:

        * **Rust engine path** — a resolved list arg arrives JSON-encoded
          (``"[{...}, ...]"``); decode it back into records.
        * **Direct/fallback path** — an unresolved bare name (missing
          variable, or a direct handler call) is looked up as a
          variable / dotted path in ``context``.

        Non-sequence results are treated as empty (regroup expects a
        sequence of records).
        """
        try:
            decoded = json.loads(expr)
        except (ValueError, TypeError):
            decoded = cls._lookup(context, expr)
        if isinstance(decoded, (list, tuple)):
            return list(decoded)
        return []

    @staticmethod
    def _lookup(item: Any, path: str) -> Any:
        """Resolve a dotted ``path`` against ``item`` (dict / seq / attr).

        Missing keys/attributes resolve to ``None`` (Django renders that
        as an empty grouper), never raising.
        """
        current = item
        for part in path.split("."):
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, (list, tuple)):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                current = getattr(current, part, None)
        return current
