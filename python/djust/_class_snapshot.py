"""Thread-safe snapshots of class namespaces (#3151).

djust caches per-class facts on the class itself the first time they are
needed (``_djust_descriptor_fields_cache``, ``_djust_template_hash_slot``,
``_djust_component_opaque``, ``_djust_warned_*``). On free-threaded CPython
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
