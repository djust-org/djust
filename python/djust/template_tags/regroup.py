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
the template context rather than emitting HTML. The Rust engine resolves
assign-tag args before calling the handler, JSON-encoding structured
(list/object) values — so ``<expr>`` arrives as a JSON string, which the
handler decodes back into the source records.

Known limitations vs. Django:

* The ``<expr>`` source must resolve to a JSON-encodable sequence
  (django-normalised context values always are). Filter expressions on
  the source (``cities|dictsort:"country"``) are not supported.
* **WARNING — attribute-name shadowing.** The Rust engine resolves
  *every* assign-tag arg against the context before the handler runs, so
  a top-level context variable whose name exactly matches the ``<attr>``
  token shadows the per-item lookup: ``args[2]`` then arrives as that
  variable's *value* instead of the literal attribute name, and the
  grouping is silently wrong. djust auto-exposes public view attributes
  to the template context, so a collision with a common attribute name
  (``country``, ``category``, ``type``, ...) is plausible in real apps.
  The handler emits a ``logger.warning`` when the resolved ``<attr>``
  isn't a bare (dotted) identifier — the strongest signal we have from
  the post-resolution vantage point. Avoid naming context keys after a
  regroup attribute (Django never resolves the attribute against the
  outer context, so this is a djust-specific edge). The durable fix
  (pass the ``by``/``<attr>``/``as`` operands to the handler unresolved)
  is tracked in #2041.
"""

from __future__ import annotations

import json
import logging
import re
from itertools import groupby
from typing import Any, Dict, List

from . import AssignTagHandler, register_assign

logger = logging.getLogger(__name__)

# A bare dotted identifier: ``country`` or ``author.team``. The ``<attr>``
# operand should always match this; anything else means a context key
# shadowed the attribute name (see the module docstring WARNING).
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


@register_assign("regroup")
class RegroupTagHandler(AssignTagHandler):
    """Handler implementing ``{% regroup expr by attr as var %}``."""

    def render(self, args: List[str], context: Dict[str, Any]) -> Dict[str, Any]:
        # Expected args (post-resolution): [<expr-json>, "by", <attr>, "as", <var>].
        if len(args) < 5 or args[1] != "by" or args[3] != "as":
            # Malformed tag — Django raises TemplateSyntaxError at compile
            # time; the Rust parser has no such hook, so degrade to a
            # no-op merge rather than crashing the whole render.
            return {}

        expr, attr, var_name = args[0], args[2], args[4]

        # If the resolved <attr> operand isn't a bare identifier, a
        # context key most likely shadowed the attribute name (djust
        # resolves all assign-tag args before we run). The grouping would
        # be silently wrong, so surface it loudly. See module docstring
        # + #2041 for the durable fix.
        if not _IDENTIFIER_RE.match(attr):
            logger.warning(
                "regroup: attr operand %r resolved to a non-identifier; a "
                "context key may be shadowing the attribute name, producing "
                "an incorrect grouping (see #2041)",
                attr,
            )

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
