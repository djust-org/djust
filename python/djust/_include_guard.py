"""The template-call guard on Django's own ``{% include %}`` (#3880).

#3815 / #3879 made ``{% include obj %}`` refuse an object whose ``render`` carries
``alters_data`` or ``do_not_call_in_templates`` on every render the Rust engine and
``DjustTemplateBackend`` own (``djust._template_guards.refused_marker``). The templates
of a CHILD view -- an embedded child, a sticky child, a ``{% live_render %}`` child, a
streamed fragment, a pending layout -- are rendered by Django's stock engine instead,
whose ``IncludeNode.render`` calls ``template.render(context)`` on whatever the operand
resolved to. This module is the same rule applied where those renders happen, without
changing the engine (so no change to filters, tags or escaping).

The shape (the one ``template_bridge_floor`` uses for ``Variable._resolve_lookup``)
-------------------------------------------------------------------------------------
* :func:`include_guard_scope` marks the current thread / task as "rendering on behalf of a
  view or component" for the duration of one render call, in a ``ContextVar``.
* :func:`install` patches ``IncludeNode.render`` once. The patch is inert unless the scope
  is active -- any other Django render in the process (a project's own page, the admin,
  another thread or task) runs Django's code, untouched, and keeps Django's behaviour.
* Inside the scope the operand is resolved ONCE, here. An object that quacks like a
  template (Django's own test: a callable ``render``) has the method it will be called
  through bound ONCE (``obj.template.render`` for a backend wrapper, ``obj.render``
  otherwise -- exactly the two Django would call), its markers read, and is refused or
  handed to Django's own ``IncludeNode.render`` pinned to that very bound method, so the
  callable whose markers were read is the callable that runs. Everything else -- names,
  lists of names, ``with`` / ``only``, the per-node template cache, relative paths -- is
  Django's code, not a copy of it: the original ``render`` runs on a stand-in for the
  node whose operand is the value already resolved.

A patch of ``IncludeNode.render`` made by someone else does not switch the guard off once a
scope is entered after it: the guard is put over it and delegates to it (#3931). One swapped in
while a scope is active is unguarded until the next scope entry.

A refusal renders ``""`` with one WARNING per (type, method) per process, the same as the
five entries #3879 guards (``warn_refused_once``); a marker read that raises refuses.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import logging
from typing import Any, Callable, Iterator

from django.template.base import FilterExpression
from django.template.loader_tags import IncludeNode

from djust._template_guards import refused_marker, warn_refused_once

logger = logging.getLogger(__name__)

#: True while a render on behalf of a view or component is in progress on this thread / task.
_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "djust_include_guard_active", default=False
)


def _capture_original() -> Callable[..., Any]:
    """Django's own ``IncludeNode.render``, read ONCE, when this module is imported.

    Import is serialised by the interpreter's import lock, so the read cannot race another
    thread's :func:`install`, and nothing at install time reads the class attribute back (a
    check-then-read could, on a lost race, keep the already-patched function as "the
    original" and recurse forever). If this module is imported again after a previous copy
    patched the class, the previous copy's function carries the genuine original.
    """
    current = IncludeNode.render
    return getattr(current, "_djust_original", current)  # type: ignore[no-any-return]


#: Django's own ``IncludeNode.render``: what the patched one delegates to and what
#: :func:`uninstall` puts back.
_ORIGINAL = _capture_original()


_NO_INNER = object()


class _Resolved:
    """An operand that is already resolved: ``resolve`` hands back the value, once more."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def resolve(self, context: Any) -> Any:
        return self.value


class _Pinned:
    """A template whose ``render`` is the bound method that was checked."""

    __slots__ = ("render",)

    def __init__(self, render: Callable[..., Any]) -> None:
        self.render = render


class _NodeWithResolvedOperand:
    """The ``IncludeNode`` being rendered, with its template operand already resolved.

    Reads of any other attribute (``origin``, ``extra_context``, ``isolated_context``) go to
    the node. Stand-ins of one node hash and compare equal, so the per-render template cache
    Django keeps keyed on the node is one cache across the loop iterations of a render.
    """

    __slots__ = ("_node", "template")

    def __init__(self, node: IncludeNode, value: Any) -> None:
        self._node = node
        self.template = _Resolved(value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._node, name)

    def __hash__(self) -> int:
        return hash(self._node)

    def __eq__(self, other: object) -> bool:
        # Only another stand-in is ever compared: inside one scope every cache key Django stores
        # is a stand-in, and a render context is never shared between a scope and a plain render.
        return isinstance(other, _NodeWithResolvedOperand) and other._node is self._node


def _call(delegate: Any, node: Any, context: Any) -> Any:
    """Call ``delegate`` as ``IncludeNode.render`` would have been called, with ``node`` as self.

    ``delegate`` is the raw class-dict entry: a function binds, a ``staticmethod`` does not, a
    callable instance without ``__get__`` is called with the context alone.
    """
    getter = getattr(type(delegate), "__get__", None)
    if getter is None:
        return delegate(context)
    return getter(delegate, node, IncludeNode)(context)


def _make_guard(delegate: Any) -> Callable[..., Any]:
    """The guard, delegating to ``delegate``: Django's own ``render`` or a patch found in its place.

    Outside a scope it is ``delegate``, untouched. Inside one it resolves and checks the operand,
    then hands ``delegate`` the node with the operand already resolved (and pinned to the bound
    ``render`` it checked), so a patch that wraps Django's ``render`` keeps working and is held to
    the guard too (#3931).
    """

    def guarded_include_render(self: IncludeNode, context: Any) -> Any:
        if not _active.get():
            return _call(delegate, self, context)
        operand: FilterExpression = self.template
        value = operand.resolve(context)
        # Django's own "does this quack like a Template" test; a name or a list of names does not.
        if callable(getattr(value, "render", None)):
            # Django calls the INNER template of a backend wrapper (`obj.template.render`), else
            # `obj.render`. Bind that one callable, read its markers, and run exactly it.
            inner = getattr(value, "template", _NO_INNER)  # read ONCE, as `render` is below
            target = value if inner is _NO_INNER else inner
            render = target.render
            marker = refused_marker(render)
            if marker is not None:
                warn_refused_once("{% include %}", target, "render", marker)
                return ""
            value = _Pinned(render)
        return _call(delegate, _NodeWithResolvedOperand(self, value), context)

    # The mark is the function ITSELF, so a ``functools.wraps`` wrapper (which copies ``__dict__``)
    # is not taken for the guard; it is followed to the guard through ``__wrapped__``.
    guarded_include_render._djust_include_guard = guarded_include_render  # type: ignore[attr-defined]
    # ... and the scope flag it reads: a guard left on the class by a copy of this module that was
    # loaded again reads THAT copy's flag, which this copy's scopes do not set, so it is not ours.
    guarded_include_render._djust_active = _active  # type: ignore[attr-defined]
    guarded_include_render._djust_original = _ORIGINAL  # type: ignore[attr-defined]
    if delegate is not _ORIGINAL:
        guarded_include_render.__wrapped__ = delegate  # type: ignore[attr-defined]
    return guarded_include_render


#: The guard over Django's own ``render``: what :func:`install` puts on a stock class.
guarded_include_render = _make_guard(_ORIGINAL)


def _is_guard(render: Any) -> bool:
    try:
        return (
            getattr(render, "_djust_include_guard", None) is render
            and getattr(render, "_djust_active", None) is _active
        )
    except Exception:  # noqa: BLE001 -- an attribute that raises: not ours
        return False


@functools.lru_cache(maxsize=None)
def _note_wrapped(replacement: str) -> None:
    """Log, once per process and replacement type, that the guard was put over a patch."""
    logger.debug(
        "IncludeNode.render was replaced by a %s that does not carry the template-call "
        "guard; the guard now wraps it and delegates to it (#3931)",
        replacement,
    )


def install() -> None:
    """Make sure ``IncludeNode.render`` carries the guard. The class is the only state.

    Read from the live class on every call, so nothing can go stale (a test's
    ``mock.patch.object`` restoring Django's ``render`` after a scope wrote the guard is
    simply installed again on the next entry). Three cases:

    * this module's guard is the OUTERMOST ``render``: nothing to do;
    * ``render`` is Django's own: the guard is installed (two threads doing so write the same
      constant);
    * anything else is on top (a patch of Django's ``render``, with or without
      ``functools.wraps``, or a wrapper put over the guard): a guard that DELEGATES to it is put
      on top (#3931). Outside a scope the patch runs exactly as before; inside one the operand is
      checked and the patch is handed the resolved, pinned operand. Only the outermost function
      is trusted: a ``functools.wraps(guard)`` wrapper may call Django's ``render`` instead of the
      guard it names (review of #4068). A wrapper that does call the guard is wrapped too; the
      operand is then checked twice, which changes nothing.

    Not covered: a patch swapped in WHILE a scope is active. The guard is (re)installed when a
    scope is entered, so until the next entry such a patch renders unguarded (pinned by
    ``test_a_patch_swapped_in_during_an_active_scope_is_unguarded_until_the_next_entry``).
    """
    current = IncludeNode.render
    if _is_guard(current):
        return
    if current is _ORIGINAL:
        IncludeNode.render = guarded_include_render  # type: ignore[method-assign]
        return
    raw = vars(IncludeNode).get("render", current)  # unbound as stored: a staticmethod stays one
    IncludeNode.render = _make_guard(raw)  # type: ignore[method-assign]
    _note_wrapped(type(raw).__name__)


def uninstall() -> None:
    """Put Django's ``IncludeNode.render`` back (for tests that prove the patch is what holds)."""
    IncludeNode.render = _ORIGINAL  # type: ignore[method-assign]


@contextlib.contextmanager
def include_guard_scope() -> Iterator[None]:
    """Hold every ``{% include %}`` rendered by Django's engine in this block to the guard.

    Wrap the ONE render call a view's or component's context goes into
    (``tmpl.render(...)``, ``render_to_string(...)``). Nests, and is exception safe.
    """
    install()
    token = _active.set(True)
    try:
        yield
    finally:
        _active.reset(token)
