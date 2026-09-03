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

``template_debug`` is ``None`` here: Django's debug page reads it with
``getattr(exc, "template_debug", None)`` (``django/views/debug.py``) and
renders the plain traceback when it is ``None``. Filling the dict (positions,
source excerpt) is #2557's job, not this class's.
"""

from typing import Any

from django.template import TemplateSyntaxError

__all__ = ["DjustTemplateSyntaxError"]


class DjustTemplateSyntaxError(TemplateSyntaxError, RuntimeError):
    """A template the Rust engine refused to parse, raised at construction."""

    #: Django's debug-page contract; ``None`` until #2557 fills it.
    template_debug: dict | None = None

    def __init__(self, message: str, origin: Any | None = None):
        super().__init__(message)
        #: The ``django.template.Origin`` the source was loaded from, or
        #: ``None`` for ``from_string``. Named as Django's ``Template`` names it.
        self.origin = origin
