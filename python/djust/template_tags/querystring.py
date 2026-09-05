"""Django's ``{% querystring [query_dict] [key=value …] [as var] %}`` for the Rust engine (#2556, #2591).

Django ≥ 5.1 registers ``defaulttags.querystring`` as a
``simple_tag(takes_context=True)``. This handler is Django's OWN compiled
``SimpleNode`` through the shared built-in bridge (``_builtin`` /
``template_libraries.LibraryTagHandler``, the same channel ``{% url %}`` and
``{% now %}`` ride): the operands reach ``simple_tag``'s ``parse_bits`` as
raw tokens, so ``None`` deletes a key, a list sets several,
``page_obj.next_page_number|add:1`` resolves through Django's compiler, and
``as var`` is ``SimpleNode.target_var`` — the node writes the context, the
bridge's bindings diff carries the write to the sibling nodes, and the tag
emits nothing (#2591 — before it, an inline ``TagHandler`` could not bind a
name and refused the form). The ``conditional_escape`` a ``simple_tag`` gets
from ``library.py`` runs inside the node, which is why ``&`` renders
``&amp;``.

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

from django.template.defaulttags import register as _defaulttags

from . import register
from ._builtin import DjangoBuiltinTagHandler


@register("querystring")
class QuerystringTagHandler(DjangoBuiltinTagHandler):
    """``{% querystring %}`` — ``defaulttags.querystring``'s compile function, Django's."""

    def __init__(self) -> None:
        super().__init__("querystring", _defaulttags.tags["querystring"])
