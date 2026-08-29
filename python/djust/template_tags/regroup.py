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
the context and hands it over JSON-encoded, which the handler decodes back
into the source value. Since #2385 that encoding covers a **string** too
(``'"ab"'``, quoted) and not only the structured list/object shapes: this
channel's contract is "unresolved ⇒ pass the raw token", so a resolved
string and an unresolved bare name were otherwise the same bytes and the
handler could only guess between them. See :meth:`_decode_source`.
The ``by`` / ``<attr>`` / ``as`` / ``<var>`` operands
(positions 1-4) arrive **unresolved**, as literal tokens, matching Django
(``RegroupNode`` never resolves the attribute against the outer context).
This makes the attribute-name shadowing bug impossible: before #2041 the
engine resolved *every* arg, so a top-level context variable named like
the ``<attr>`` token (``country``, ``category``, ``type``, ... — djust
auto-exposes public view attributes) shadowed the per-item lookup,
silently corrupting the grouping.

Known limitations vs. Django:

* The ``<expr>`` source must resolve to a JSON-encodable value
  (django-normalised context values always are).
* A source Python cannot iterate — an ``int``, a ``bool``, a ``Decimal`` —
  renders an empty region where Django raises ``TypeError``. Never more
  permissive than Django; pinned in
  ``python/tests/test_regroup_string_source_2385_2394.py``.

Filter expressions on the source (``cities|dictsort:"country"``) ARE
supported as of #2333 — the renderer's ``resolve_tag_operand`` resolves a
pipe-bearing operand through ``get_value``, the same filter-aware resolver
``{{ }}`` uses. Before that this channel asked for a variable literally NAMED
``cities|dictsort:"country"``, missed, and handed the handler the template's
own source text, so ``{{ g|length }}`` rendered ``0`` and every ``{% for %}``
over the groups rendered nothing — silently. Django's own ``regroup`` docs
open by noting the input usually needs sorting first, so that idiom is close
to canonical.

Filter expressions on the ``by`` operand (``by k|upper``) are supported as of
#2355 — see :meth:`RegroupTagHandler._grouper`. Django compiles that operand
as ``<var>.<attr>`` with ``parser.compile_filter``, so the chain is a per-ITEM
filter expression, not part of the attribute NAME. Before that this handler
looked up an attribute literally called ``k|upper``, missed, and grouped every
row under ``None`` — one group where Django builds several, and every
``{{ x.grouper }}`` empty. That is the #2333 class on the tag's second
operand, and it was invisible because the parity differential built no
``regroup`` cell at all until #2355.
"""

from __future__ import annotations

import json
from itertools import groupby
from typing import Any, ClassVar, Dict, List, Optional, Set

from . import AssignTagHandler, register_assign

#: The temp name a `by` filter chain is compiled against — Django's `regroup`
#: does the same thing with the tag's own `<var>` name.
#:
#: No leading underscore: Django's `Variable` REFUSES a name that starts with
#: one ("Variables and attributes may not begin with underscores"), so the
#: obvious `__djust_…` spelling raises at `compile_filter` time. Collision is
#: not a risk either way — the chain resolves against a fresh one-key
#: `Context`, never the render's own.
_GROUPER_VAR = "djust_regroup_grouper"

#: Compiled `by` chains, keyed by the chain text. A template's `by` operand is
#: fixed at parse time, so this is bounded by the number of distinct chains in
#: the project's templates.
_FILTER_EXPRESSION_CACHE: Dict[str, Any] = {}


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
            for key, vals in groupby(items, key=lambda item: self._grouper(item, attr))
        ]
        return {var_name: groups}

    @classmethod
    def _grouper(cls, item: Any, attr: str) -> Any:
        """The value one item groups BY — a dotted path, then a filter chain.

        The ``by`` operand is a filter expression in Django, not a bare path:
        ``regroup`` compiles ``<var>.<attr>`` with ``parser.compile_filter``,
        so ``{% regroup p by k|upper as g %}`` compiles ``g.k|upper`` and the
        chain runs per ITEM. Before #2355 this handler did a dotted lookup for
        an attribute literally named ``k|upper``, missed, and grouped every row
        under ``None`` — one silent group where Django builds several, with
        every ``{{ x.grouper }}`` empty.

        #2333 fixed the same class on the SOURCE operand. This is the `by` one,
        and it was invisible because the differential built no ``regroup`` cell
        at all.
        """
        path, pipe, chain = attr.partition("|")
        value = cls._lookup(item, path)
        return cls._apply_filters(value, chain) if pipe else value

    @staticmethod
    def _apply_filters(value: Any, chain: str) -> Any:
        """Run ``chain`` over ``value`` using Django's own filter machinery.

        Django's, deliberately: this operand is compiled by Django's
        ``compile_filter`` in the Python engine, so matching it means running
        the same ``FilterExpression``. An unknown filter raises
        ``TemplateSyntaxError`` here exactly as it does there — a raise from a
        Rust-dispatched handler surfaces as a template error and the Python
        fallback then raises Django's own, rather than a render silently
        grouping on the wrong value.
        """
        from django.template import Context, Engine
        from django.template.base import Parser

        expression = _FILTER_EXPRESSION_CACHE.get(chain)
        if expression is None:
            parser = Parser([], builtins=Engine.get_default().template_builtins)
            expression = parser.compile_filter(f"{_GROUPER_VAR}|{chain}")
            _FILTER_EXPRESSION_CACHE[chain] = expression
        return expression.resolve(Context({_GROUPER_VAR: value}))

    @classmethod
    def _decode_source(cls, expr: str, context: Dict[str, Any]) -> List[Any]:
        """Resolve the source expression into a concrete list.

        Two shapes are accepted:

        * **Rust engine path** — a resolved arg arrives JSON-encoded
          (``"[{...}, ...]"`` for a list, and — since #2385 — ``'"ab"'`` for a
          STRING, quoted precisely so it is not mistaken for the next case);
          decode it back into the source value.
        * **Direct/fallback path** — an unresolved bare name (missing
          variable, or a direct handler call) is looked up as a
          variable / dotted path in ``context``.

        The result is then iterated with **Python's own semantics**, which is
        what Django does: ``RegroupNode.render`` runs ``groupby(obj_list, …)``
        over whatever ``self.target.resolve`` returned, so a ``str`` yields its
        characters, a ``dict`` yields its keys, and a missing variable yields
        nothing. Before #2385 this matched only ``list``/``tuple`` and answered
        ``[]`` for everything else, so ``{% regroup s by k as g %}`` over
        ``s = "ab"`` built ZERO groups where Django builds one — silently, with
        ``{{ g|length }}`` rendering ``0`` and every ``{% for %}`` over the
        groups rendering nothing (#2394, #2385). Every operand spelling that
        resolves to a string was affected, not just the bare-variable one:
        ``p.0``, ``p.a``, ``p|first``, ``p|upper``, ``p|slice:':2'`` and a
        quoted literal all landed here as text.

        A value Python cannot iterate — ``None``, an ``int``, a ``Decimal`` —
        stays ``[]``. Django's answer for ``None`` is also no groups (its
        ``ignore_failures`` branch); for the others Django raises ``TypeError``
        and djust renders an empty region instead. That divergence predates
        this fix, is never MORE permissive than Django, and is deliberately
        left alone (CLAUDE.md #1079).
        """
        try:
            decoded = json.loads(expr)
        except (ValueError, TypeError):
            decoded = cls._lookup(context, expr)
        try:
            return list(decoded)
        except TypeError:
            # Not iterable (None / int / bool / Decimal / a model instance).
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
