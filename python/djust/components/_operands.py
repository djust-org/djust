"""The one conversion of a resolved value into an operand of a native handler (#3882, #3883).

A native component handler (``djust.components.rust_handlers``) writes what it is handed into
attributes and text nodes and escapes it there. The operand contract it relies on:

* every operand is an exact ``str`` (``type(x) is str``: no ``SafeString``, no lazy string), an
  ``int``, ``float``, ``bool``, ``None``, a ``list`` or ``dict`` (exact-``str`` keys) of operands, or
  one of the stand-in types (``TupleValue`` / ``NamedTupleValue``, ``UUID``, ``PurePath``);
* a block handler's rendered body is the only operand that is markup.

Marked-safe text is not kept marked: when it reached a handler as text (the engine's string
arguments), it was plain, and a handler that is handed it still marked writes it raw into an
attribute (``conditional_escape`` leaves ``SafeData`` alone). ``plain_operand`` is the ONE place a
value becomes an operand, whatever carried it: the engine's typed channel, the context fallback,
and (for the Django-engine and Python-class entries that do not exist yet) the values Django hands
a tag. ``rust_handlers._parse_args`` and ``rust_handlers._passed_or_context`` are its two readers
for the Rust-engine entry; ``python/tests/test_native_operand_contract.py`` pins both.
"""

from __future__ import annotations

import decimal
import pathlib
import uuid
from typing import Any, Iterable, Mapping

from django.utils.functional import Promise


class TupleValue(list):  # type: ignore[type-arg]
    """A tuple a native handler was given (#3768).

    The engine hands a handler a ``tuple`` as a JSON list, and a handler reads a list slot with
    ``isinstance(rows, list)`` (~55 places): a real ``tuple`` there would be dropped as "not a
    list", turning ``rows=my_tuple_of_dicts`` (an ordinary thing to pass) into an empty table. So
    the handler keeps getting a ``list``, a subclass that prints the way the tuple does. A text
    slot then reads ``(1, 2)`` as Django's tag prints it, where it read ``[1, 2]``.

    ``NamedTupleValue`` is the same for a namedtuple (#3849).
    """

    __slots__ = ()

    def __repr__(self) -> str:
        # Iterative, like ``_from_typed``: a tuple nested a few hundred deep is legal input (it
        # printed under Django and under this engine before #3768), and a recursive ``repr`` here
        # would raise RecursionError one level at a time. Only TupleValue members recurse in
        # this walk; any other member is printed by its own ``repr``.
        out = [_opening(self)]
        stack: list[tuple[TupleValue, int]] = [(self, 0)]
        while stack:
            node, index = stack[-1]
            if index == len(node):
                if len(node) == 1 and not isinstance(node, NamedTupleValue):
                    out.append(",")
                out.append(")")
                stack.pop()
                continue
            stack[-1] = (node, index + 1)
            if index:
                out.append(", ")
            if isinstance(node, NamedTupleValue) and index < len(node._fields):
                out.append(node._fields[index] + "=")
            item = node[index]
            if isinstance(item, TupleValue):
                out.append(_opening(item))
                stack.append((item, 0))
            else:
                out.append(repr(item))
        return "".join(out)

    __str__ = __repr__


class NamedTupleValue(TupleValue):
    """A namedtuple a native handler was given, still a ``list`` (#3849).

    Prints as the namedtuple does (``P(x=1, y=2)``, which is what Django's tag prints), and is a
    ``list`` for the reason ``TupleValue`` is. The class it came from is gone (the engine holds a
    namedtuple as a name, its fields and its items), so a ``__repr__`` the class defined for itself
    is not reproduced: this prints the default one.
    """

    __slots__ = ("_name", "_fields")

    def __init__(self, items: Any = (), name: str = "tuple", fields: tuple[str, ...] = ()) -> None:
        super().__init__(items)
        self._name = name
        self._fields = fields


def _opening(node: TupleValue) -> str:
    """The text a ``TupleValue`` opens with: ``(`` for a tuple, ``P(`` for a namedtuple."""
    return node._name + "(" if isinstance(node, NamedTupleValue) else "("


def _namedtuple_shape(source: tuple[Any, ...]) -> tuple[str, tuple[str, ...]] | None:
    """``(name, fields)`` when ``source`` is a namedtuple, else ``None``.

    A ``_fields`` tuple with one name per member. This is NARROWER than the engine's rule when it
    reads a Python object (``Value::NamedTuple`` also accepts a ``list`` ``_fields``): it does not
    need to be wider, because the typed channel hands a handler the class the engine REBUILT with
    ``collections.namedtuple``, whose ``_fields`` is always a ``tuple``.
    """
    fields = getattr(source, "_fields", None)
    if type(fields) is tuple and len(fields) == len(source):
        return type(source).__name__, fields
    return None


def _empty_like(source: tuple[Any, ...]) -> TupleValue:
    shape = _namedtuple_shape(source)
    return TupleValue() if shape is None else NamedTupleValue((), *shape)


def _from_typed(value: object) -> object:
    """A typed operand as a handler is handed it: a ``tuple`` becomes a ``TupleValue``, a
    namedtuple a ``NamedTupleValue`` (#3849); anything else is returned as it is.

    Iterative, not recursive: the members of a tuple are converted too (a row of a tuple of
    tuples must be a list), and a recursive walk raised RecursionError for a tuple nested 500
    deep that rendered before. An explicit stack has no depth limit but memory.

    A tuple whose members are all plain leaves (``_PLAIN_LEAVES``: no tuple, no marked-safe or lazy
    string, no container), the usual case and the only one that can be large, is copied in one
    C-level pass (``list(value)``): the member types are collected with ``set(map(type, value))``,
    which runs no Python bytecode per member, and a per-member Python loop on a 2,000,000-element
    tuple was the cost the review of #3843 measured (#3849). Any other member goes through
    ``plain_operand`` (a marked-safe string inside a tuple is plain like any other).
    """
    if not isinstance(value, tuple):
        return value
    if set(map(type, value)) <= _PLAIN_LEAVES:
        shape = _namedtuple_shape(value)
        return TupleValue(value) if shape is None else NamedTupleValue(value, *shape)
    root = _empty_like(value)
    pending = [(value, root)]
    while pending:
        source, target = pending.pop()
        for item in source:
            if isinstance(item, tuple):
                child = _empty_like(item)
                target.append(child)
                pending.append((item, child))
            else:
                target.append(plain_operand(item))
    return root


#: The types a native handler is handed a stand-in for, which a Python tag tells apart from the
#: stand-in (#3768): a ``UUID`` or a ``Path`` arrives as its ``str`` (``int(uuid)`` works in
#: Python, ``int("...")`` does not), and a ``tuple`` (see ``TupleValue``) as the JSON list
#: ``[1, 2]`` where Django's tag prints ``(1, 2)``.
STANDIN_TYPES = (uuid.UUID, pathlib.PurePath)

#: Leaves ``plain_operand`` returns unchanged. Exact types: a subclass (``SafeString``, a
#: ``str`` subclass with ``__html__``, an ``IntEnum``) is not a plain leaf.
_PLAIN_LEAVES = frozenset({str, int, float, bool, type(None)})


class OperandRefused(TypeError):
    """A value with no conversion to an operand (an arbitrary object)."""


def plain_operand(value: Any) -> Any:
    """``value`` as a native handler may read it (the module docstring's contract).

    * a string, marked safe or not, is the exact ``str`` it spells. ``str(x)`` does not do that
      for a ``SafeString`` (its ``__str__`` returns itself); ``str.__str__`` does;
    * a lazy string (``gettext_lazy``) is the exact ``str`` it evaluates to;
    * a ``Decimal`` is the ``float`` the engine's text for it always gave a handler;
    * a tuple is a ``TupleValue`` (a namedtuple a ``NamedTupleValue``) whose members went through
      this function, ``_from_typed``;
    * a list or dict is a new one whose keys and members went through this function;
    * an ``int``, ``float``, ``bool``, ``None``, ``UUID`` or ``Path`` is itself (an ``int``
      subclass such as an ``IntEnum`` is its ``int``);
    * anything else is refused by name (``OperandRefused``): an object has no operand form here,
      and guessing one (its ``str``, its attributes) would decide on the handler's behalf what of
      it reaches the page.
    """
    kind = type(value)
    if kind in _PLAIN_LEAVES:
        return value
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, Promise):
        return plain_operand(str(value))
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, tuple):
        return _from_typed(value)
    if isinstance(value, (list, dict)):
        return _plain_container(value)
    if isinstance(value, STANDIN_TYPES):
        return value
    raise OperandRefused(
        "a native component handler cannot take a %s operand" % type(value).__name__
    )


def plain_operands(operands: Mapping[str, Any], markup: Iterable[str] = ()) -> dict[str, Any]:
    """Every operand of one handler call through ``plain_operand``, in order; the names in
    ``markup`` (a block's rendered body) are passed through as the markup they are."""
    keep = frozenset(markup)
    return {key: value if key in keep else plain_operand(value) for key, value in operands.items()}


def _plain_container(value: list[Any] | dict[Any, Any]) -> list[Any] | dict[Any, Any]:
    """``value`` rebuilt with each key and member through ``plain_operand``. Iterative: a list
    nested a thousand deep is legal input and must not raise ``RecursionError``."""
    root: list[Any] | dict[Any, Any] = [] if isinstance(value, list) else {}
    pending: list[tuple[Any, Any]] = [(value, root)]
    while pending:
        source, target = pending.pop()
        items = source.items() if isinstance(source, dict) else enumerate(source)
        for key, item in items:
            if isinstance(item, (list, dict)):
                converted: Any = [] if isinstance(item, list) else {}
                pending.append((item, converted))
            else:
                converted = plain_operand(item)
            if isinstance(target, dict):
                target[plain_operand(key)] = converted
            else:
                target.append(converted)
    return root
