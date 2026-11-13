"""Typed state descriptors; exposure permissions are a separate contract."""

import sys
from collections.abc import Callable
from copy import deepcopy
from typing import Any, Generic, TypeVar, cast, overload

from ._exposure import FieldExposure, Persistence, reserved_state_names

T = TypeVar("T")


class _Missing:
    pass


_MISSING = _Missing()


class StateProperty(Generic[T]):
    """A reactive value whose default is initialized once per owning instance.

    Under explicit exposure the field is template context by default
    (``render=True``, #4050). Rendering is not a grant: the descriptor does not
    grant persistence or browser-exposure permissions (``persist=``,
    ``client=True``).
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
        render: bool = True,
    ) -> None:
        if type(render) is not bool:
            raise TypeError("state render must be a boolean")
        if default is not _MISSING and default_factory is not None:
            raise TypeError("state accepts either default or default_factory, not both")
        if default_factory is not None and not callable(default_factory):
            raise TypeError("state default_factory must be callable")
        self.default = None if default is _MISSING else default
        self.default_factory = default_factory
        self.exposure = FieldExposure(persist=persist, client=client)
        # Template context only; not part of FieldExposure, so it does not
        # change the schema digest or count as an inherited grant.
        self.render = render
        self.attr_name: str | None = None
        self.public_name: str | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        if self.public_name is not None and self.public_name != name:
            raise TypeError("A state descriptor cannot be shared by different field names")
        # #4070: a field in a view's class body under a reserved name is refused
        # here as well as in ``LiveView.__init_subclass__``, which does not run
        # when a mixin earlier in the MRO skips ``super().__init_subclass__()``.
        live_view = sys.modules.get("djust.live_view")
        view_base = getattr(live_view, "LiveView", None)
        if isinstance(view_base, type) and issubclass(owner, view_base):
            if name in reserved_state_names():
                raise live_view.reserved_state_field_error(owner, owner, name)  # type: ignore[union-attr]
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
def state(
    default: T, *, persist: Persistence = None, client: bool = False, render: bool = True
) -> StateProperty[T]: ...


@overload
def state(
    *,
    default_factory: Callable[[], T],
    persist: Persistence = None,
    client: bool = False,
    render: bool = True,
) -> StateProperty[T]: ...


@overload
def state(
    *, persist: Persistence = None, client: bool = False, render: bool = True
) -> StateProperty[Any]: ...


def state(
    default: Any = _MISSING,
    *,
    default_factory: Callable[[], T] | None = None,
    persist: Persistence = None,
    client: bool = False,
    render: bool = True,
) -> StateProperty[T]:
    """Declare reactive state with a typed, instance-owned default.

    ``count = state(0)`` infers ``int`` on instance access. For mutable state,
    prefer ``items = state(default_factory=list)`` (annotate the descriptor with
    its item type when needed). Literal defaults are deep-copied on first read;
    factories run once per instance, unless a value was assigned first.

    Under explicit exposure (the default) a declared field is template
    context: ``{{ count }}`` renders without a ``get_context_data()``, which
    stays for derived and render-only values and can still override what the
    field renders as (#4050). ``render=False`` keeps a field out of the
    template context (a value only handlers use, or one too large to sync to
    the renderer for nothing). Rendering is not browser state: the field
    reaches the client state payload only with ``client=True``.

    ``persist`` and ``client`` are ADR-038 exposure grants, honored only by
    views with ``exposure_policy = "explicit"``: ``persist="server"`` saves the
    field in the bound server session envelope, ``client=True`` permits raw
    browser exposure, and ``persist="client"`` (which requires ``client=True``)
    makes it restorable from the signed back-navigation snapshot. A legacy view
    that declares a grant, or ``render=False``, is refused at construction, so
    a declaration never implies protection it does not get. Calls without
    these options keep legacy context and persistence.
    """
    return cast(
        StateProperty[T],
        StateProperty(
            default,
            default_factory=default_factory,
            persist=persist,
            client=client,
            render=render,
        ),
    )
