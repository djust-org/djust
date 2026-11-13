"""Thread-safe snapshots of class namespaces (#3151).

djust caches per-class facts on the class itself the first time they are
needed (``_djust_descriptor_fields_cache``, ``_djust_component_opaque``, ``_djust_warned_*``). On free-threaded CPython
(3.14t, GIL off) such a ``setattr(cls, ...)`` can land while another thread is
walking the same class's ``__dict__``, and the walk raises ``RuntimeError:
dictionary changed size during iteration`` -- the first simultaneous page loads
after a deploy returned 500s.

Measured on 3.14t with the GIL off, only ``mappingproxy.copy()`` and
``list(mappingproxy.items())`` read a class namespace atomically. Iterating it,
and ``dict()``, ``list()``, ``tuple()`` or ``set.update()`` over it, all race;
so does ``dir()`` of a class or of an instance, which merges every class dict
in the MRO the same way. Request-path code therefore walks a copy, taken with
:func:`namespace`, and lists attribute names with :func:`attribute_names`
instead of ``dir()``. Both cost one dict copy per class; neither takes a lock.
"""

from __future__ import annotations

import operator
from typing import Any, Dict, List

_OBJECT_DIR = object.__dir__
_TYPE_DIR = type.__dir__


def namespace(obj: Any) -> Dict[str, Any]:
    """A private copy of ``vars(obj)``, safe to iterate while other threads
    write attributes to ``obj``.

    ``mappingproxy.copy()`` delegates to ``dict.copy()``, which holds the dict's
    lock for the whole copy on a free-threaded build.
    """
    return obj.__dict__.copy()  # type: ignore[no-any-return]


def _merge_classes(names: set, cls: type) -> None:
    for klass in cls.__mro__:
        names.update(klass.__dict__.copy())


def attribute_names(obj: Any) -> List[str]:
    """``dir(obj)``, built from namespace snapshots.

    Same result as ``dir()`` for classes and ordinary instances: the instance's
    own attributes plus every class dict in the MRO, sorted. An object whose
    type customises ``__dir__`` (or whose ``__class__`` is not a class) gets
    ``dir()`` itself, which is the only thing that knows its answer.
    """
    kind = type(obj)
    names: set = set()
    if isinstance(obj, type):
        if kind.__dir__ is not _TYPE_DIR:
            return dir(obj)
        _merge_classes(names, obj)
        return sorted(names)
    if kind.__dir__ is not _OBJECT_DIR:
        return dir(obj)
    cls = getattr(obj, "__class__", kind)
    if not isinstance(cls, type):
        return dir(obj)
    try:
        # As ``object.__dir__`` does: an ordinary attribute read, any error
        # meaning "no instance attributes".
        own = obj.__dict__
    except Exception:  # noqa: BLE001 — mirrors CPython's PyErr_Clear()
        own = None
    if isinstance(own, dict):
        names.update(own.copy())
    _merge_classes(names, cls)
    return sorted(names)


# ``Py_TPFLAGS_IMMUTABLETYPE``: a class whose dictionary can never change
# (``object``, ``type``, the builtins), so it needs no re-check.
_IMMUTABLE_TYPE = 1 << 8


class ClassSnapshot:
    """The class dictionaries a per-class fact was derived from, and whether
    they still say the same thing (#3997).

    The same rule as ``_parameter_metadata._ClassPlan.fresh``: the MRO, then
    every class dictionary in it, keys in order and values by identity. A
    hot-replaced view (a new class object), a reassigned ``__bases__``, and an
    added, deleted or swapped class attribute on the class or on any base
    therefore fail :meth:`fresh`, and the fact is derived again. The metaclass
    chain is included because ``inspect.getattr_static`` on a class reads it.

    Each value is also held with its TYPE, so reassigning ``__class__`` on an
    object a class dictionary holds (a ``state()`` descriptor turned into
    something else, or the reverse) fails the check though the dictionary still
    holds the same object. And the class's ``__name__`` and ``__qualname__``
    are compared: they live in type slots, not in the class dictionary.

    :meth:`is_for` says whether the snapshot was taken of a given class OBJECT.
    A table of snapshots must ask it on every hit and must be keyed by
    ``id()``: a metaclass may define ``__eq__`` / ``__hash__`` (by name, say),
    and a table keyed by the class itself would then hand one class another
    class's facts.

    What it cannot see is a change INSIDE an object a class attribute holds
    (appending to a class-level list, editing a function's ``__dict__``): the
    attribute still holds the same object. A fact that depends on such
    contents must check them itself.

    Take the snapshot BEFORE deriving the fact, so a class changed while the
    fact is being derived fails the next :meth:`fresh` instead of never.
    """

    __slots__ = ("cls", "meta", "mro", "meta_mro", "names", "dicts")

    def __init__(self, cls: type) -> None:
        self.cls = cls
        self.meta = type(cls)
        self.mro = cls.__mro__
        self.meta_mro = self.meta.__mro__
        self.names = (cls.__name__, cls.__qualname__)
        classes = dict.fromkeys(self.mro + self.meta_mro)
        dicts = []
        for klass in classes:
            if klass.__flags__ & _IMMUTABLE_TYPE:
                continue
            # One atomic copy, so keys and values come from the same instant (#3151).
            current = namespace(klass)
            values = tuple(current.values())
            dicts.append((klass, tuple(current), values, tuple(map(type, values))))
        self.dicts = tuple(dicts)

    def is_for(self, cls: Any) -> bool:
        """Whether this is the snapshot of *cls* itself, by identity."""
        return self.cls is cls

    @staticmethod
    def _same(left: tuple, right: tuple) -> bool:
        """Element-wise identity; never calls a class's ``__eq__``."""
        return len(left) == len(right) and all(map(operator.is_, left, right))

    def fresh(self) -> bool:
        cls = self.cls
        if type(cls) is not self.meta:
            return False
        if not self._same(cls.__mro__, self.mro) or not self._same(
            self.meta.__mro__, self.meta_mro
        ):
            return False
        if (cls.__name__, cls.__qualname__) != self.names:
            return False
        for klass, keys, values, kinds in self.dicts:
            current = namespace(klass)
            if len(current) != len(values) or tuple(current) != keys:
                return False
            for now, then, kind in zip(current.values(), values, kinds):
                if now is not then or type(now) is not kind:
                    return False
        return True
