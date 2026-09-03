"""Django's ``{% querystring [query_dict] [key=value …] %}`` for the Rust engine (#2556).

Django ≥ 5.1 registers ``defaulttags.querystring`` as a
``simple_tag(takes_context=True)``. This handler is that function, called
VERBATIM (CLAUDE.md #1077): the operands are parsed with Django's own
``parse_bits`` against ``querystring``'s real signature, each one is compiled
with Django's own ``compile_filter`` (so ``None`` deletes a key, a list sets
several, ``page_obj.next_page_number|add:1`` resolves through the chain), and
the call is one line. What comes back is a plain ``str``: the Rust registry's
``escape_handler_return`` supplies the ``conditional_escape`` a ``simple_tag``
gets from ``library.py``, which is why ``&`` renders ``&amp;``.

``context.request`` is what Django's tag reads when no ``query_dict`` is
given. The plain backend carries a ``RequestContext``'s request since #2556
(the request slice of #2550, ``rendering.py``); the LiveView path has had it
in the sidecar since #1145 — there ``request.GET`` is the MOUNT-time GET,
which is Django's own semantics within one request. Without either, Django's
``AttributeError: 'Context' object has no attribute 'request'`` surfaces
unchanged.

Registered only when ``django.VERSION >= (5, 1)`` (``template_tags/__init__``),
so on an older Django the tag stays unsupported on both engines.
"""

from __future__ import annotations

from inspect import getfullargspec
from typing import Any, ClassVar, Dict, List, Optional, Set

from django.template import TemplateSyntaxError
from django.template.defaulttags import querystring as django_querystring
from django.template.library import parse_bits

from . import TagHandler, register
from ._django_expr import django_context, django_parser

_SPEC = getfullargspec(django_querystring)


@register("querystring")
class QuerystringTagHandler(TagHandler):
    """``{% querystring %}`` — ``defaulttags.querystring``, Django's, one call."""

    #: Operands are TOKENS: ``key=expr`` names are never resolved (Django
    #: never resolves a kwarg's name), and every expression goes through
    #: Django's compiler, not the engine's pre-resolution (#2041, #2423).
    RESOLVE_ARG_POSITIONS: ClassVar[Optional[Set[int]]] = frozenset()  # type: ignore[assignment]

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        # `simple_tag`'s `… as var` binds a context variable, which an inline
        # `TagHandler` cannot do — that is the `simple_tag(takes_context=True)`
        # bridge's job (#2547). Refuse loudly rather than treat `as` as a
        # positional and emit something Django would not.
        if len(args) >= 2 and args[-2] == "as":
            raise TemplateSyntaxError(
                "'querystring ... as var' is not supported by the Rust engine yet (#2547); "
                "use {% querystring %} inline"
            )
        ctx = django_context(context)
        # `SimpleNode`'s compile step: Django's `parse_bits` against the real
        # signature, so "too many positional arguments" / "unexpected
        # keyword" are Django's messages too.
        positional, keyword = parse_bits(
            django_parser(),
            args,
            _SPEC.args,
            _SPEC.varargs,
            _SPEC.varkw,
            _SPEC.defaults,
            _SPEC.kwonlyargs,
            _SPEC.kwonlydefaults,
            takes_context=True,
            name="querystring",
        )
        # `SimpleNode.get_resolved_arguments`, minus the `context` it prepends.
        resolved_args = [expr.resolve(ctx) for expr in positional]
        resolved_kwargs = {k: expr.resolve(ctx) for k, expr in keyword.items()}
        return django_querystring(ctx, *resolved_args, **resolved_kwargs)
