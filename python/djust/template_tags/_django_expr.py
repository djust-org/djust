"""Django's own expression compiler, for handlers that lift a Django tag verbatim.

``{% lorem %}``, ``{% querystring %}`` (and any future ``simple_tag``-shaped
built-in, #2547) take their operands the way Django does: through
``parser.compile_filter(token)``, so a literal, a dotted variable, ``None`` /
``True`` / ``False`` and a filter chain (``page_obj.number|add:1``) all
resolve exactly as they do on Django's engine. Re-deriving that grammar here
would be a second copy of Django's ``FilterExpression`` (CLAUDE.md #1646), so
the handlers declare ``RESOLVE_ARG_POSITIONS = frozenset()`` — every operand
arrives as the TOKEN the template wrote — and hand each token to the one
compiler Django has.

The ``Parser`` is built once from a fresh ``Engine``'s builtins: it needs no
``TEMPLATES`` entry, so a djust-only configuration (no ``DjangoTemplates``
backend) works too.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.template import Context as DjangoContext
from django.template.base import FilterExpression, Parser, Template
from django.template.engine import Engine

_PARSER: Optional[Parser] = None
_TEMPLATE: Optional[Template] = None


def django_parser() -> Parser:
    """One Django ``Parser`` over the default builtins, built on first use."""
    global _PARSER
    if _PARSER is None:
        _PARSER = Parser([], builtins=Engine().template_builtins)
    return _PARSER


def compile_expr(token: str) -> FilterExpression:
    """``parser.compile_filter(token)`` — Django's, not a copy."""
    return django_parser().compile_filter(token)


def _bound_template() -> Template:
    """An empty ``Template`` to bind the context to.

    ``FilterExpression.resolve`` answers a missing variable with
    ``context.template.engine.string_if_invalid`` — the same ``""`` the
    Rust engine renders — and a bare ``Context`` has no ``template`` bound, so
    ``{% lorem two p %}`` would raise ``'NoneType' object has no attribute
    'engine'`` instead of resolving to ``""`` and falling back to ``1``.
    """
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = Template("", engine=Engine())
    return _TEMPLATE


def django_context(context: Dict[str, Any]) -> DjangoContext:
    """The handler's context dict as a Django ``Context``.

    A ``Context`` carries the ``True`` / ``False`` / ``None`` builtins Django's
    ``Variable`` resolves through, which is how ``{% querystring a=None %}``
    deletes a key rather than setting it to the string ``"None"``. When the
    render carried a ``request`` (the plain backend since #2556, the LiveView
    sidecar since #1145) it is attached as ``.request`` so a Django tag that
    reads ``context.request`` finds it — and raises Django's own
    ``AttributeError`` text when it does not.
    """
    ctx = DjangoContext(dict(context))
    ctx.template = _bound_template()
    if "request" in context:
        ctx.request = context["request"]
    return ctx
