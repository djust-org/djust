"""Typed state descriptors; exposure permissions are a separate contract."""

from collections.abc import Callable
from copy import deepcopy
from typing import Any, Generic, TypeVar, cast, overload

from ._exposure import FieldExposure, Persistence

T = TypeVar("T")


class _Missing:
    pass


_MISSING = _Missing()

#: S1 SPIKE (docs/reviews/2026-11-12-s1-assignment-tracking-spike.md). When
#: True, an explicit view's event turn takes the names recorded by
#: ``StateProperty.__set__`` as the changed set for declared state, and the
#: structural walks of declared state are skipped. Prototype only; default off.
ASSIGNMENT_TRACKING = False
if __import__("os").environ.get("DJUST_S1_ASSIGNMENT_TRACKING") == "1":  # spike test runs only
    ASSIGNMENT_TRACKING = True

#: S1 SPIKE: how an in-place mutation of declared state is found while
#: ``ASSIGNMENT_TRACKING`` is on. One of:
#:   ""        not at all (``self.touch("items")`` or reassign)
#:   "tracked" the descriptor stores tracked list/dict subclasses (option i)
#:   "debug"   the old pre/post walk still runs and WARNS about a missed change (ii)
#:   "read"    container fields the handler READ are fingerprinted before/after (iii)
MUTATION_MODE = __import__("os").environ.get("DJUST_S1_MUTATION", "")

#: S1 SPIKE measurement: with this on, the event gate uses the assigned names
#: ONLY (no residual snapshot of undeclared attributes, provider keys,
#: component state or the stream epoch). Shows which consumers need more.
NAMES_ONLY = __import__("os").environ.get("DJUST_S1_NAMES_ONLY") == "1"

#: Armed by the event spine for the handler's duration ("read" mode).
READ_FPS_ATTR = "_djust_read_fps"

#: Per-turn names (cleared by the event spine before the handler runs).
ASSIGNED_ATTR = "_djust_assigned"
#: Names assigned since the last Rust state sync (cleared by the sync).
UNSYNCED_ATTR = "_djust_unsynced"

_SCALARS = (str, int, float, bool, bytes, type(None))


def record_assignment(obj: Any, name: str) -> None:
    """Record *name* as assigned on *obj* (spike; also ``LiveView.touch``)."""
    d = obj.__dict__
    for attr in (ASSIGNED_ATTR, UNSYNCED_ATTR):
        names = d.get(attr)
        if names is None:
            names = d[attr] = set()
        names.add(name)


def _trace(where: str) -> None:
    """Spike measurement: which tests reached an assignment-tracked path."""
    import os

    path = os.environ.get("DJUST_S1_TRACE")
    if path:
        with open(path, "a") as fh:
            fh.write("%s\t%s\n" % (where, os.environ.get("PYTEST_CURRENT_TEST", "?")))


def s1_before_handler(view: Any) -> Any:
    """Arm per-turn tracking; returns the token ``s1_after_handler`` needs."""
    d = view.__dict__
    d[ASSIGNED_ATTR] = set()
    _trace("spine")
    if MUTATION_MODE == "read":
        d[READ_FPS_ATTR] = {}
    elif MUTATION_MODE == "debug":
        from .websocket import _snapshot_assigns

        return _snapshot_assigns(view)
    return None


def s1_after_handler(view: Any, token: Any) -> None:
    """Fold what the selected mutation mode found into the assigned names."""
    d = view.__dict__
    assigned = d.get(ASSIGNED_ATTR)
    if assigned is None:
        return
    fields = declared_state_fields(type(view))
    if MUTATION_MODE == "read":
        fps = d.pop(READ_FPS_ATTR, None) or {}
        from .change_detection import deep_fingerprint

        for name, (before, truncated) in fps.items():
            if name in assigned:
                continue
            value = d.get(fields[name], _MISSING)
            if value is _MISSING:
                continue
            after, truncated_after = deep_fingerprint(value)
            if truncated or truncated_after or after != before:
                record_assignment(view, name)
    elif MUTATION_MODE == "debug" and token is not None:
        import logging

        from .websocket import _compute_changed_keys, _snapshot_assigns

        slots = {slot: name for name, slot in fields.items()}
        missed = sorted(
            slots[key]
            for key in _compute_changed_keys(token, _snapshot_assigns(view))
            if key in slots and slots[key] not in assigned
        )
        if missed:
            logging.getLogger("djust.state").warning(
                "%s: state field(s) %s changed without an assignment; the page was not "
                "updated. Reassign the field or call self.touch(...).",
                type(view).__qualname__,
                ", ".join(missed),
            )


def declared_state_fields(cls: type) -> "dict[str, str]":
    """``{public name: storage slot}`` for every ``state()`` field of *cls*."""
    cached = cls.__dict__.get("_djust_s1_fields")
    if cached is None:
        cached = {}
        for klass in reversed(cls.__mro__):
            for name, value in vars(klass).items():
                if isinstance(value, StateProperty) and value.attr_name:
                    cached[name] = value.attr_name
        type.__setattr__(cls, "_djust_s1_fields", cached)
    return cached


class StateProperty(Generic[T]):
    """A reactive value whose default is initialized once per owning instance.

    This descriptor does not grant persistence or browser-exposure permissions.
    Nested mutations remain visible to the runtime's content fingerprinting;
    assignment also adds the field name to the existing reactive-state registry.
    """

    # Marker: dirty tracking and the private-session save recognise the
    # ``_state_<name>`` slot as this field's storage (#2956, #2959).
    _djust_state_field = True

    def __init__(
        self,
        default: Any = _MISSING,
        *,
        default_factory: Callable[[], T] | None = None,
        persist: Persistence = None,
        client: bool = False,
    ) -> None:
        if default is not _MISSING and default_factory is not None:
            raise TypeError("state accepts either default or default_factory, not both")
        if default_factory is not None and not callable(default_factory):
            raise TypeError("state default_factory must be callable")
        self.default = None if default is _MISSING else default
        self.default_factory = default_factory
        self.exposure = FieldExposure(persist=persist, client=client)
        self.attr_name: str | None = None
        self.public_name: str | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        if self.public_name is not None and self.public_name != name:
            raise TypeError("A state descriptor cannot be shared by different field names")
        self.attr_name = f"_state_{name}"
        self.public_name = name

    @overload
    def __get__(self, obj: None, objtype: type | None = None) -> "StateProperty[T]": ...

    @overload
    def __get__(self, obj: object, objtype: type | None = None) -> T: ...

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        assert self.attr_name is not None
        try:
            value = getattr(obj, self.attr_name)
            if ASSIGNMENT_TRACKING and MUTATION_MODE == "read":
                fps = obj.__dict__.get(READ_FPS_ATTR)
                if fps is not None and self.public_name not in fps:
                    if type(value) in (list, dict, tuple, set):
                        from .change_detection import deep_fingerprint

                        fps[self.public_name] = deep_fingerprint(value)
            return value
        except AttributeError:
            value = (
                self.default_factory()
                if self.default_factory is not None
                else deepcopy(self.default)
            )
            setattr(obj, self.attr_name, value)
            return value

    def __set__(self, obj: Any, value: T) -> None:
        assert self.attr_name is not None
        if ASSIGNMENT_TRACKING:
            # Assigning an equal scalar is not a change; assigning a container
            # always is, including the same object (``self.items = self.items``
            # is then the spelling for "I mutated this in place").
            old = obj.__dict__.get(self.attr_name, _MISSING)
            if not (type(value) in _SCALARS and type(old) is type(value) and old == value):
                record_assignment(obj, cast(str, self.public_name))
            if MUTATION_MODE == "tracked":
                from ._s1_tracked import track

                value = track(value, obj, cast(str, self.public_name))
        setattr(obj, self.attr_name, value)
        if not hasattr(obj, "_reactive_state"):
            obj._reactive_state = set()
        obj._reactive_state.add(self.public_name)


@overload
def state(default: T, *, persist: Persistence = None, client: bool = False) -> StateProperty[T]: ...


@overload
def state(
    *, default_factory: Callable[[], T], persist: Persistence = None, client: bool = False
) -> StateProperty[T]: ...


@overload
def state(*, persist: Persistence = None, client: bool = False) -> StateProperty[Any]: ...


def state(
    default: Any = _MISSING,
    *,
    default_factory: Callable[[], T] | None = None,
    persist: Persistence = None,
    client: bool = False,
) -> StateProperty[T]:
    """Declare reactive state with a typed, instance-owned default.

    ``count = state(0)`` infers ``int`` on instance access. For mutable state,
    prefer ``items = state(default_factory=list)`` (annotate the descriptor with
    its item type when needed). Literal defaults are deep-copied on first read;
    factories run once per instance, unless a value was assigned first.

    ``persist`` and ``client`` are ADR-038 exposure grants, honored only by
    views with ``exposure_policy = "explicit"``: ``persist="server"`` saves the
    field in the bound server session envelope, ``client=True`` permits raw
    browser exposure, and ``persist="client"`` (which requires ``client=True``)
    makes it restorable from the signed back-navigation snapshot. A legacy view
    that declares a grant is refused at construction, so a grant never implies
    protection it does not get. Calls without these options keep legacy
    context and persistence.
    """
    return cast(
        StateProperty[T],
        StateProperty(default, default_factory=default_factory, persist=persist, client=client),
    )
