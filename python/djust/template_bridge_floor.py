"""The serialization floor for the operands of a ``{% load %}``-bridged tag (#3771).

Why this exists
---------------
A native expression (``{{ obj.user.password }}``, ``{% with %}``, a native handler's operand) is
resolved by the Rust engine's ``Context::walk_live``, which re-protects after EVERY segment
(``djust.serialization._protect_sidecar_value``: a ``Model`` becomes a floor-enforcing proxy), so the
path is refused where it first meets the model (SECURE_DEFAULTS Pattern 1, #1986).

A bridged tag is different by design (``template_libraries``): it is handed its operands as TOKENS and
Django's OWN node resolves them, with ``Variable._resolve_lookup``, over the raw objects of the
handler dict. That walk had no floor: ``{% relative_time datetime=obj.user.password %}`` walked
``obj -> user -> password`` over raw Python and printed the hash. ``_protect_sidecar_value`` only wraps
a ``Model`` / ``Manager`` / ``QuerySet`` that is itself the root of a path; it does nothing for a
``Form``, a ``BoundField`` or a plain object that HOLDS a model.

The shape of the fix
--------------------
The node stays Django's (djust never re-implements ``parse_bits``, ``SimpleNode`` or ``InclusionNode``).
What changes is the WALK of every ``Variable`` resolved while a bridged node renders:

* :func:`floor_scope` registers the ``Context`` the bridge built for a node (``template_libraries``:
  ``_render_node``, the raw-block handler, the cache operand) for the duration of its render, in a
  ``ContextVar``.
* :func:`install` patches ``Variable._resolve_lookup`` once. The patch is inert unless the context it
  is called with IS the registered one; then it runs Django's OWN ``_resolve_lookup`` (same
  dict-then-attribute-then-index order, same callable rules, same ``string_if_invalid`` handling) against
  :class:`FlooredContext`, an alias of the context whose top-level lookups return :class:`FloorWalker`
  wrappers. Each step of the path goes through a walker, which re-protects the result with
  ``_protect_sidecar_value`` before Django takes the next step. That is the re-protection
  ``walk_live`` applies after each segment for ``{{ }}``, applied to the steps Django's resolver takes;
  it is a second route to the same floor, not the same code, and the two can differ for an exotic
  object (a proxy is walked by this module's walkers, and ``walk_live`` calls
  ``protect_sidecar_strict`` on the live handle).
* Why a patch of the resolver and not a ``Variable`` subclass or a ``Parser`` override: an operand
  reaches Django's resolver through several doors (a ``FilterExpression`` that ``compile_filter``
  builds, a ``template.Variable`` a raw tag constructs at compile time, one it constructs at render
  time). Only ``_resolve_lookup`` is common to all of them.
* The value the walk ENDS on is released (:func:`release`): a walker is unwrapped to the object it
  holds, so the tag is handed the real operand, not a proxy of it. A model the walk reached stays the
  floor-enforcing proxy, which is exactly what ``{% tag user=user %}`` is handed for a top-level
  ``user``, so the same operand is the same value however it is spelled. A container that holds a model
  is re-protected the way a top-level container is (``_protect_sidecar_tree``); one that holds none is
  the caller's own object.

What is deliberately NOT floored
--------------------------------
A tag author's own Python that does NOT go through ``Variable`` resolution: a ``takes_context`` tag
that reads ``context["obj"]`` itself receives the raw object, and a ``simple_tag`` function receives
its released operands and may do what it likes with them. That code is the application's, not the
template author's, and the floor is a boundary against what a TEMPLATE can reach. (A tag that resolves
a ``Variable`` against the context it was handed IS floored, because that is a template path.)
Resolutions against any other ``Context`` (``Context.new()`` for an inclusion tag's own template, a
Django engine elsewhere in the process, another thread or task) are Django's, untouched.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime
import decimal
import inspect
import logging
import uuid
from typing import Any, Callable, Iterator, List, Optional

from django.template.base import Variable
from django.template.context import BaseContext, Context
from django.utils.functional import Promise

from ._class_snapshot import attribute_names

logger = logging.getLogger(__name__)

#: Values with nothing to walk into: they cannot hold a model.
_LEAF_TYPES = (
    str,
    bytes,
    bytearray,
    int,
    float,
    complex,
    bool,
    decimal.Decimal,
    datetime.date,
    datetime.time,
    datetime.timedelta,
    uuid.UUID,
    Promise,
)

_protect_value: Optional[Callable[[Any], Any]] = None
_protect_tree: Optional[Callable[[Any], Any]] = None


def _floor() -> "tuple[Callable[[Any], Any], Callable[[Any], Any]]":
    """``djust.serialization``'s leaf floor and container floor (imported on first use: this module
    is imported by ``template_libraries`` at ``{% load %}`` time, before a render)."""
    global _protect_value, _protect_tree
    if _protect_value is None or _protect_tree is None:
        from djust.serialization import _protect_sidecar_tree, _protect_sidecar_value

        _protect_value = _protect_sidecar_value
        _protect_tree = _protect_sidecar_tree
    return _protect_value, _protect_tree


def _is_lazy_block(value: Any) -> bool:
    """Is ``value`` the engine's lazy ``block`` (``djust._rust.LazyBlock``, #2918)?

    It holds a rendered parent block and no model, and its ``__getitem__`` decides between "Django's
    resolver is asking" and "legacy dict-style user code" by inspecting its immediate Python caller's
    frame. A walker in between would turn Django's own resolver into "user code" and make
    ``block.super`` warn. It is the ONLY object the walk does not wrap.
    """
    try:
        from djust._rust import LazyBlock
    except ImportError:  # pragma: no cover - the extension is a hard dependency
        return False
    return isinstance(value, LazyBlock)


def wrap(value: Any) -> Any:
    """The value Django's next lookup step is handed.

    A leaf is itself. Everything else is a :class:`FloorWalker`, so that the NEXT step is
    re-protected: a ``Model`` / ``Manager`` / ``QuerySet`` is first turned into its floor proxy and the
    walker goes over THAT. The proxy is not terminal. It re-protects only its own immediate return value,
    and only if that value is itself a model or queryset, so a method or property of a model that returns
    a plain container (``qs.in_bulk`` -> ``{pk: Model}``, a ``@property`` returning ``[Model]``) hands
    Django a raw container whose next step would read a raw model. The walker over the proxy re-protects
    that step too, so the model inside the container is protected on the step that reaches it.
    """
    if value is None or isinstance(value, _LEAF_TYPES):
        return value
    if isinstance(value, FloorWalker) or _is_lazy_block(value):
        return value
    protect_value, _ = _floor()
    return _walker_for(protect_value(value))


def release(value: Any) -> Any:
    """The operand a walk ended on, as the tag is handed it: the object under a walker, with a
    container that holds a model re-protected exactly as a top-level container is."""
    if not isinstance(value, FloorWalker):
        return value
    raw = object.__getattribute__(value, "_FloorWalker__raw")
    if isinstance(raw, (list, tuple, dict, set, frozenset)):
        _, protect_tree = _floor()
        protected = protect_tree(raw)
        if _same_contents(raw, protected):
            return raw
        return protected
    return raw


def _same_contents(original: Any, protected: Any) -> bool:
    """Did the container floor change nothing, at any depth? (It always builds new containers;
    handing the caller's own object back when nothing needed protecting keeps identity and mutation
    semantics exactly as Django has them.)"""
    if original is protected:
        return True
    if isinstance(original, dict):
        return (
            isinstance(protected, dict)
            and len(original) == len(protected)
            and all(k in protected and _same_contents(v, protected[k]) for k, v in original.items())
        )
    if isinstance(original, (list, tuple)):
        return (
            isinstance(protected, (list, tuple))
            and len(original) == len(protected)
            and all(_same_contents(a, b) for a, b in zip(original, protected))
        )
    if isinstance(original, (set, frozenset)):
        return isinstance(protected, (set, frozenset)) and original == protected
    return False


class FloorWalker:
    """An object met during a template-author path, wrapped so the NEXT step is re-protected.

    Transparent to Django's ``_resolve_lookup``: attribute lookup, with the same exceptions;
    ``do_not_call_in_templates`` / ``alters_data`` behave as the wrapped object's. It has NO public
    attribute of its own, so every public name a template can spell reaches ``__getattr__``. It is
    never handed to a tag: :func:`release` unwraps it at the end of the walk.

    Django decides what to try from the TYPE of the object it holds: ``hasattr(type(current),
    "__getitem__")`` (a dictionary lookup is only attempted when the class has one, and
    ``block.super`` on a lazy block, which has none, must not be read as a legacy dict-style access)
    and ``callable(current)``. A walker therefore has exactly the protocol its object has, which is
    why there are four classes (:func:`_walker_for`): this one, and one each for subscriptable,
    callable, and both.
    """

    __slots__ = ("__raw",)

    def __init__(self, raw: Any) -> None:
        object.__setattr__(self, "_FloorWalker__raw", raw)

    def __getattr__(self, name: str) -> Any:
        # Called only for names the class does not define: every public name. A leading underscore
        # is refused outright (Django-parity: a template never resolves one), so `__wrapped__`,
        # `__class__`-style probes and the walker's own slot are not reachable through here.
        if name.startswith("_"):
            raise AttributeError(name)
        return wrap(getattr(object.__getattribute__(self, "_FloorWalker__raw"), name))

    def __dir__(self) -> List[str]:
        # Django: `bit in dir(current)` decides whether an AttributeError came from a @property.
        return attribute_names(object.__getattribute__(self, "_FloorWalker__raw"))

    def __repr__(self) -> str:
        return repr(object.__getattribute__(self, "_FloorWalker__raw"))

    def __str__(self) -> str:
        return str(object.__getattribute__(self, "_FloorWalker__raw"))


class _Subscript:
    __slots__ = ()

    def __getitem__(self, key: Any) -> Any:
        return wrap(object.__getattribute__(self, "_FloorWalker__raw")[key])


class _Call:
    __slots__ = ()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return wrap(object.__getattribute__(self, "_FloorWalker__raw")(*args, **kwargs))

    @property
    def __signature__(self) -> inspect.Signature:
        # Django: after a TypeError from the call, `signature(current).bind()` decides between
        # "arguments were required" (string_if_invalid) and "the call itself raised" (re-raise).
        return inspect.signature(object.__getattribute__(self, "_FloorWalker__raw"))


class FloorSubscriptWalker(_Subscript, FloorWalker):
    __slots__ = ()


class FloorCallableWalker(_Call, FloorWalker):
    __slots__ = ()


class FloorSubscriptCallableWalker(_Subscript, _Call, FloorWalker):
    __slots__ = ()


def _walker_for(value: Any) -> FloorWalker:
    subscript = hasattr(type(value), "__getitem__")
    if callable(value):
        return (FloorSubscriptCallableWalker if subscript else FloorCallableWalker)(value)
    return (FloorSubscriptWalker if subscript else FloorWalker)(value)


class FlooredContext(Context):
    """An ALIAS of a ``Context`` (it shares the original's ``__dict__``: same ``dicts`` stack, same
    ``render_context``, same ``template``) whose top-level lookups return :func:`wrap`ped values.

    It exists for exactly one reader, ``Variable._resolve_lookup``, and is never given to a node.
    """

    def __getitem__(self, key: Any) -> Any:
        return wrap(super().__getitem__(key))


def _alias(context: BaseContext) -> FlooredContext:
    root = FlooredContext.__new__(FlooredContext)
    root.__dict__ = context.__dict__
    return root


#: The ``Context`` a bridged node is being rendered on, while it is. ``None`` outside a bridged render.
_floor_root: contextvars.ContextVar[Optional[BaseContext]] = contextvars.ContextVar(
    "djust_bridge_floor_root", default=None
)


def _capture_original() -> Callable[..., Any]:
    """Django's own ``Variable._resolve_lookup``, read ONCE, when this module is imported.

    Import is serialised by the interpreter's import lock, so the read cannot race another thread's
    install, and nothing at install time reads the class attribute back (a check-then-read at install
    time could, on a lost race, store the already-patched function as "the original" and make every
    ``Variable.resolve`` in the process recurse forever). If this module is being imported again
    (``importlib.reload``) after a previous copy patched the class, the previous copy's function carries
    the genuine original.
    """
    current = Variable._resolve_lookup
    return getattr(current, "_djust_original", current)  # type: ignore[no-any-return]


#: Django's own ``Variable._resolve_lookup``: what the patched one delegates to and what
#: :func:`uninstall` puts back.
_ORIGINAL = _capture_original()


def floored_resolve_lookup(self: Variable, context: Any) -> Any:
    root = _floor_root.get()
    if root is None or context is not root:
        return _ORIGINAL(self, context)
    return release(_ORIGINAL(self, _alias(root)))


floored_resolve_lookup._djust_original = _ORIGINAL  # type: ignore[attr-defined]


def install() -> None:
    """Patch ``Variable._resolve_lookup`` (idempotent, and race-free by construction).

    The whole of it is one assignment of a constant: two threads installing at once both write the same
    value, and nothing is read back or remembered at install time (see :func:`_capture_original`).

    Why a patch and not a ``Variable`` subclass: an operand reaches Django's resolver through three
    doors, and only this method is common to all of them. ``Parser.compile_filter`` builds a
    ``FilterExpression`` (``simple_tag``, ``inclusion_tag``, ``{% if %}``); a raw ``@register.tag``
    often calls ``template.Variable(token)`` itself, at compile time (djust's own component tags,
    ``components/templatetags/_registry.py``) or at render time. A subclass reaches the first only.

    The patch is inert unless a bridged node is being rendered on EXACTLY the ``Context`` that
    :func:`floor_scope` registered: any other resolution, in any other template engine, any other
    thread, any ``Context.new()`` the node derives, runs Django's own code, untouched.
    """
    Variable._resolve_lookup = floored_resolve_lookup  # type: ignore[method-assign]


def uninstall() -> None:
    """Put Django's ``_resolve_lookup`` back (for tests that prove the patch is what holds the floor)."""
    Variable._resolve_lookup = _ORIGINAL  # type: ignore[method-assign]


@contextlib.contextmanager
def floor_scope(context: BaseContext) -> Iterator[None]:
    """Hold every ``Variable`` resolved against ``context`` to the floor, for the duration.

    Entered around ``node.render(context)`` by the bridge. Nests (a ``ContextVar`` token restores the
    previous root) and is exception safe.
    """
    install()
    token = _floor_root.set(context)
    try:
        yield
    finally:
        _floor_root.reset(token)
