"""Typed state descriptors; exposure permissions are a separate contract."""

from collections.abc import Callable
from copy import deepcopy
from typing import Any, Generic, TypeVar, cast, overload

from ._exposure import FieldExposure, Persistence

T = TypeVar("T")


class _Missing:
    pass


_MISSING = _Missing()


class StateProperty(Generic[T]):
    """A reactive value whose default is initialized once per owning instance.

    This descriptor does not grant persistence or browser-exposure permissions.
    Nested mutations remain visible to the runtime's content fingerprinting;
    assignment also adds the field name to the existing reactive-state registry.
    """

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
            return getattr(obj, self.attr_name)
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

    ``persist`` and ``client`` currently declare experimental ADR-038 metadata
    only. LiveView rejects non-default grants until its explicit-policy runtime
    is implemented; they must never imply protection under the legacy policy.
    Existing calls without these options retain legacy context/persistence.
    """
    return cast(
        StateProperty[T],
        StateProperty(default, default_factory=default_factory, persist=persist, client=client),
    )
