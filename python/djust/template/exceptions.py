"""Exceptions raised by the djust template backend.

``DjustTemplateSyntaxError`` is the construction-time refusal (#2549):
``DjustTemplate.__init__`` parses the source through the Rust engine and
raises this when the parse fails, which is where Django's
``Engine.from_string`` / ``get_template`` raise ``TemplateSyntaxError``.

It is BOTH a ``django.template.TemplateSyntaxError`` — so Django's own
handling (``assertRaises(TemplateSyntaxError)``, the debug view's
``template_debug`` lookup, loader ``except`` clauses) sees the type it
expects — AND a ``RuntimeError``, the type every Rust engine failure has
crossed to Python as until now (``crates/djust_core/src/errors.rs``), so a
caller that catches ``RuntimeError`` around template construction keeps
catching it. Django's ``TemplateSyntaxError`` and ``RuntimeError`` share
``Exception``'s layout, so the double inheritance is sound.

``build_template_debug`` fills the dict Django's debug page reads with
``getattr(exc, "template_debug", None)`` (``django/views/debug.py:329``) —
the ``name`` / ``line`` / ``during`` / ``source_lines`` a technical-500 page
renders (#2557). It is a port of ``django.template.base.Template``'s
``get_exception_info``, key for key.
"""

from typing import Any

from django.template import TemplateSyntaxError

__all__ = ["DjustTemplateSyntaxError", "build_template_debug", "UNKNOWN_SOURCE"]

#: Django's own window: ``get_exception_info`` shows this many source lines
#: either side of the offending one (``django/template/base.py:243``).
CONTEXT_LINES = 10

#: Django's ``django.template.base.UNKNOWN_SOURCE`` — the origin name a
#: template built with ``from_string`` carries, and what the debug page's
#: "In template ..." heading shows instead of a literal ``None``.
UNKNOWN_SOURCE = "<unknown source>"


def _linebreak_iter(source: str):
    """Yield the offset just past each newline, then one past the end.

    Django's ``django.template.base.linebreak_iter``, verbatim — the exact
    boundaries ``get_exception_info`` walks, so a ported ``line`` number and
    a ported ``source_lines`` split agree with Django's on every input,
    including a source that does and does not end in a newline.
    """
    yield 0
    p = source.find("\n")
    while p >= 0:
        yield p + 1
        p = source.find("\n", p + 1)
    yield len(source) + 1


def _byte_offset_to_char(source: str, offset: int) -> int:
    """Translate a Rust BYTE offset into the CHARACTER offset Django uses.

    Rust slices the template as UTF-8 bytes; Python slices it as characters.
    The two agree exactly while the source is ASCII, which is why this is
    short-circuited there — and differ on any template with a non-ASCII
    character before the offending token, which is precisely where a wrong
    number would put the excerpt on the wrong line.
    """
    if source.isascii():
        return min(offset, len(source))
    encoded = source.encode("utf-8")
    return len(encoded[: min(offset, len(encoded))].decode("utf-8", "ignore"))


def build_template_debug(
    source: str,
    name: str | None,
    start: int,
    end: int,
    message: str,
) -> dict:
    """Build Django's ``template_debug`` dict for a token at ``[start, end)``.

    A port of ``django.template.base.Template.get_exception_info``
    (``django/template/base.py:205-282`` in Django 5.2.16), key for key and
    slice for slice: ``message``, ``source_lines``, ``before``, ``during``,
    ``after``, ``top``, ``bottom``, ``total``, ``line``, ``name``, ``start``,
    ``end``. Django's technical-500 template reads every one of them, so a
    dict missing any key renders a broken page rather than no page.

    ``start``/``end`` arrive from the Rust lexer as BYTE offsets and are
    converted to character offsets here — the unit Django's ``self.source``
    slicing uses.

    A ``None`` ``name`` becomes ``<unknown source>``. ``get_template()`` always
    supplies a real path, but ``from_string()`` supplies no origin at all, and
    the debug page interpolates the value straight into its heading — so the
    ``None`` rendered literally as ``In template None, error at line 1``.
    Django's ``Template.__init__`` defaults the same case to
    ``Origin(UNKNOWN_SOURCE)`` (``django/template/base.py``).
    """
    start = _byte_offset_to_char(source, start)
    end = _byte_offset_to_char(source, end)

    line = 0
    upto = 0
    source_lines = []
    before = during = after = ""
    for num, next_break in enumerate(_linebreak_iter(source)):
        if start >= upto and end <= next_break:
            line = num
            before = source[upto:start]
            during = source[start:end]
            after = source[end:next_break]
        source_lines.append((num, source[upto:next_break]))
        upto = next_break
    total = len(source_lines)

    # Django's loop above can only locate a token that lies WITHIN one line,
    # because its ``tag_re`` has no ``re.DOTALL`` — a Django token never spans
    # a newline, so ``end <= next_break`` always holds for the line ``start``
    # is on. djust's lexer has no such bound and the engine accepts a
    # multi-line tag (``{% if x\n %}``, ``{% include "a.html"\n with b=1 %}``),
    # and an unterminated ``{%`` scans to the next ``%}`` anywhere in the file.
    # For those the ported condition never fires and the loop falls through
    # with its initial values — ``line: 0``, an empty ``during`` and the
    # excerpt clamped to lines 1..11, which on a long template points a
    # developer at the wrong ten lines with false confidence. Re-locate on the
    # line ``start`` is on and clamp the highlight to that line's break, which
    # is the line ``Token.lineno`` names and the one a human would point at.
    #
    # Guarded on ``end > start`` so a degenerate empty span at offset 0 — which
    # the loop above DOES match, at line 0 — keeps Django's answer untouched.
    if line == 0 and end > start:
        upto = 0
        for num, next_break in enumerate(_linebreak_iter(source)):
            if upto <= start < next_break:
                clamped = min(end, next_break)
                line = num
                before = source[upto:start]
                during = source[start:clamped]
                after = source[clamped:next_break]
                # Keep the line's own newline OUT of the highlight: Django's
                # ``during`` can never contain one, and
                # ``before + during + after`` still reassembles the line.
                if during.endswith("\n"):
                    during, after = during[:-1], "\n" + after
                break
            upto = next_break

    top = max(1, line - CONTEXT_LINES)
    bottom = min(total, line + 1 + CONTEXT_LINES)

    return {
        "message": message,
        "source_lines": source_lines[top:bottom],
        "before": before,
        "during": during,
        "after": after,
        "top": top,
        "bottom": bottom,
        "total": total,
        "line": line,
        "name": name if name is not None else UNKNOWN_SOURCE,
        "start": start,
        "end": end,
    }


class DjustTemplateSyntaxError(TemplateSyntaxError, RuntimeError):
    """A template the Rust engine refused to parse, raised at construction."""

    #: Django's debug-page contract. ``None`` when the engine could not say
    #: WHERE the failure was — the debug view falls back to the plain
    #: traceback for a ``None``, which is what every djust parse error got
    #: before #2557.
    template_debug: dict | None = None

    def __init__(self, message: str, origin: Any | None = None):
        super().__init__(message)
        #: The ``django.template.Origin`` the source was loaded from, or
        #: ``None`` for ``from_string``. Named as Django's ``Template`` names it.
        self.origin = origin
