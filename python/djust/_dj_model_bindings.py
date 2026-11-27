"""The ``dj-model`` bindings a Django-engine render emits from template text.

Embedded ``{% live_render %}`` children render through the configured Django
template engine, not the Rust ``RustLiveView``. Their dj-model write allowlist is the
child template's static bindings narrowed to the ones this render actually
emitted, and "emitted" is read from the template text that ran — Django's
``TextNode`` — never from the rendered HTML, which rendered data can shape
(an unquoted ``value={{ v }}`` attribute, ``|safe`` content).

:func:`record_bindings` collects, for one render, the ``dj-model="<field>"``
attributes in every ``TextNode`` that rendered (includes and ``{% extends %}``
parents too). A ``TextNode`` inside an ``{% if %}`` branch that was not taken
does not render, so it grants nothing. The scan matches the Rust template
walker (``scan_dj_model_in_text``): a standalone ``dj-model`` attribute name,
``=``, then a quoted, non-empty value inside one text run. With djust's own
template backend the render is in Rust; see :func:`suspend_recording`.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, List, Optional, Set

_ATTR = "dj-model"
_active: ContextVar[Optional[Set[str]]] = ContextVar("djust_dj_model_bindings", default=None)
_install_lock = threading.Lock()
_installed = False


def scan_dj_model_bindings(text: str) -> List[str]:
    """The ``dj-model="<field>"`` values of one run of template text."""
    fields: List[str] = []
    start = text.find(_ATTR)
    while start != -1:
        after = start + len(_ATTR)
        next_from = after
        if (start == 0 or text[start - 1] in " \t\n\r\x0c") and text[after : after + 1] == "=":
            quote = text[after + 1 : after + 2]
            if quote in ('"', "'"):
                end = text.find(quote, after + 2)
                if end != -1:
                    if end > after + 2:
                        fields.append(text[after + 2 : end])
                    next_from = end + 1
        start = text.find(_ATTR, next_from)
    return fields


def _install() -> None:
    """Make Django's ``TextNode`` report its bindings while a recording runs.

    Outside :func:`record_bindings` the wrapper costs one ``ContextVar`` read.
    """
    global _installed
    if _installed:
        return
    with _install_lock:
        if _installed:
            return
        from django.template.base import TextNode

        def render(self: TextNode, context: object) -> str:
            fields = _active.get()
            if fields is not None and _ATTR in self.s:
                fields.update(scan_dj_model_bindings(self.s))
            return self.s

        TextNode.render = render  # type: ignore[method-assign]
        TextNode.render_annotated = render  # type: ignore[method-assign]
        _installed = True


@contextmanager
def suspend_recording() -> Iterator[Optional[Set[str]]]:
    """For djust's own template backend: hand over the active recording.

    That backend renders in Rust, where no ``TextNode`` runs; it passes the
    yielded set to ``render_template_with_dirs(dj_model_bindings=...)``, which
    adds the template-text bindings of that render. The recording is paused
    meanwhile, so a template rendered inside it (an inclusion tag's) adds
    nothing. Yields ``None`` when nothing is recording.
    """
    fields = _active.get()
    if fields is None:
        yield None
        return
    token = _active.set(None)
    try:
        yield fields
    finally:
        _active.reset(token)


@contextmanager
def record_bindings() -> Iterator[Set[str]]:
    """Collect the template-text dj-model bindings rendered inside the block.

    Nested recordings (a child rendering a grandchild) are independent: the
    inner render's text is not added to the outer set.
    """
    _install()
    fields: Set[str] = set()
    token = _active.set(fields)
    try:
        yield fields
    finally:
        _active.reset(token)
