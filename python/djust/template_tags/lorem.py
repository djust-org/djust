"""Django's ``{% lorem [count] [method] [random] %}`` for the Rust engine (#2556).

Everything that produces text is Django's own: ``django.utils.lorem_ipsum``'s
``words`` / ``paragraphs`` are called verbatim (CLAUDE.md #1077), and this
handler never touches ``random`` itself. That is what makes the output
byte-identical to Django's — both engines consume the same global ``random``
stream, so a seeded render agrees to the byte
(``python/tests/test_remaining_builtin_tags_2556.py``).

The operand grammar is ``defaulttags.lorem`` read from the END — the ``random``
flag, then the method, then the count — with the count compiled through
Django's own ``compile_filter`` so ``{% lorem n w %}`` and ``{% lorem two p %}``
(a non-integer, which Django turns into ``1``) behave as on Django. Anything
left over is Django's ``Incorrect format for 'lorem' tag``, surfaced at render
until #2549 types the parse-time channel.

The result is ``mark_safe``: Django inserts ``LoremNode``'s output raw, and the
only markup in it is the ``<p>`` the ``p`` method adds.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional, Set

from django.template import TemplateSyntaxError
from django.utils.lorem_ipsum import paragraphs, words
from django.utils.safestring import SafeString, mark_safe

from . import TagHandler, register
from ._django_expr import compile_expr, django_context


@register("lorem")
class LoremTagHandler(TagHandler):
    """``{% lorem %}`` — ``defaulttags.LoremNode`` on Django's own generators."""

    #: Every operand is a TOKEN: ``random`` / ``w`` / ``p`` / ``b`` are flags,
    #: not names to resolve (#2041), and the count goes through Django's
    #: compiler so a variable or a filter chain resolves as on Django.
    RESOLVE_ARG_POSITIONS: ClassVar[Optional[Set[int]]] = frozenset()  # type: ignore[assignment]

    def render(self, args: List[str], context: Dict[str, Any]) -> SafeString:
        # `defaulttags.lorem`, with `bits[0]` the tag name.
        bits: List[str] = ["lorem", *args]
        common = bits[-1] != "random"
        if not common:
            bits.pop()
        if bits[-1] in ("w", "p", "b"):
            method = bits.pop()
        else:
            method = "b"
        count_token = bits.pop() if len(bits) > 1 else "1"
        count_expr = compile_expr(count_token)
        if len(bits) != 1:
            raise TemplateSyntaxError("Incorrect format for 'lorem' tag")

        # `LoremNode.render`.
        try:
            count = int(count_expr.resolve(django_context(context)))
        except (ValueError, TypeError):
            count = 1
        if method == "w":
            return mark_safe(words(count, common=common))
        paras = paragraphs(count, common=common)
        if method == "p":
            paras = ["<p>%s</p>" % p for p in paras]
        return mark_safe("\n\n".join(paras))
