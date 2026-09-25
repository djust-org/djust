"""The ADR-034 interactive dropdown; import it from ``djust.components.interactive``.

Real concrete per-owner instances use the existing LiveComponent registry and
transport actions. The output-authoring pieces here (``Outputs``, the output
contracts and ``_emit``), the local action names and the observation markup are
framework-internal (owner decision Q4); only the names re-exported by
``djust.components.interactive`` are public.
"""

from __future__ import annotations

from collections.abc import Awaitable, Iterator, Sequence
from copy import deepcopy
import inspect
import re
from types import FunctionType
from typing import Literal, NoReturn, Protocol, TypeVar, TypedDict
from uuid import uuid4

from django.utils.html import format_html, format_html_join

from djust._component_subscriptions import (
    SOURCE_PARAMETER,
    ComponentDeclaration,
    OutputContract,
    subscribe,
)
from djust.decorators import event_handler
from djust.live_view import LiveView
from .base import LiveComponent


class _ActionRequired(TypedDict):
    label: str
    value: str


class ActionItem(_ActionRequired, total=False):
    disabled: bool


class SeparatorItem(TypedDict):
    separator: Literal[True]


class SelectedCallback(Protocol):
    def __call__(
        self, owner: NoReturn, /, *, component: DropdownMenu, value: str
    ) -> None | Awaitable[None]: ...


class ToggledCallback(Protocol):
    def __call__(
        self, owner: NoReturn, /, *, component: DropdownMenu, open: bool
    ) -> None | Awaitable[None]: ...


Selected = TypeVar("Selected", bound=SelectedCallback)
Toggled = TypeVar("Toggled", bound=ToggledCallback)
D = TypeVar("D", bound="DropdownMenu")
_SELECTED = OutputContract("selected", (("value", str),))
_TOGGLED = OutputContract("toggled", (("open", bool),))
_SOURCE = frozenset({SOURCE_PARAMETER})


def _binding_values(state: object) -> tuple[str, bool, str]:
    """Validate a persisted record without materializing or mutating a binding."""
    if type(state) is not dict or set(state) != {"binding_id", "open", "selected"}:
        raise ValueError("Invalid interactive component snapshot")
    identity, opened, selected = state["binding_id"], state["open"], state["selected"]
    if (
        type(identity) is not str
        or re.fullmatch(r"cmp_[0-9a-f]{32}", identity) is None
        or type(opened) is not bool
        or type(selected) is not str
    ):
        raise ValueError("Invalid interactive component snapshot")
    return identity, opened, selected


class Outputs:
    def __init__(self, source: ComponentDeclaration) -> None:
        self._source = source

    def selected(self, callback: Selected) -> Selected:
        """After a valid selection, with the chosen item's value."""
        return subscribe(self._source, _SELECTED, callback)

    def toggled(self, callback: Toggled) -> Toggled:
        """After the menu opens or closes, with its new visibility."""
        return subscribe(self._source, _TOGGLED, callback)


class DropdownMenu(ComponentDeclaration, LiveComponent):
    """Concrete descriptor and bound object share their actual Python type."""

    _djust_fingerprint_state = True
    component_id: str

    def __init__(
        self,
        *,
        label: str,
        items: list[ActionItem | SeparatorItem],
        visibility: Literal["server", "client"] = "server",
    ) -> None:
        if visibility not in ("server", "client"):
            raise ValueError("Dropdown visibility must be server or client")
        if type(label) is not str:
            raise TypeError("Dropdown label must be a string")
        values: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise TypeError("Dropdown items must be typed action or separator dictionaries")
            if "separator" in item:
                if set(item) != {"separator"} or item.get("separator") is not True:
                    raise TypeError("Separators contain only separator=True")
                continue
            if set(item) - {"label", "value", "disabled"} or not {"label", "value"} <= set(item):
                raise TypeError("Action items require label/value and optional disabled")
            value = item.get("value")
            if type(value) is not str or not value or type(item.get("label")) is not str:
                raise TypeError("Action labels and nonempty values must be strings")
            if type(item.get("disabled", False)) is not bool:
                raise TypeError("Action disabled must be a boolean")
            if value in values:
                raise ValueError("Dropdown action values must be unique")
            values.add(value)
        ComponentDeclaration.__init__(self, type(self), (_SELECTED, _TOGGLED))
        LiveComponent.__init__(self, component_id="cmp_" + uuid4().hex)
        self._label = label
        self._items = deepcopy(items)
        self._open = False
        self._selected = ""
        self._visibility = visibility
        self._observation_lifetime = "obs_" + uuid4().hex
        self._observation_sequence = 0
        self._observation_registered = False
        self._declaration: DropdownMenu | None = None
        # Keyed-collection membership (ADR-034 C3). A member is created by
        # its bound collection's ``sync()``, never by a class declaration.
        self._collection: DropdownMenuCollection | None = None
        self._member_key = ""
        self.on = Outputs(self)

    @classmethod
    def collection(cls) -> DropdownMenuCollection:
        """Declare a keyed collection of dropdowns (ADR-034 D5, owner decision Q6).

        Members are supplied by ``self.<name>.sync([(key, DropdownMenu(...)), ...])``;
        ``@<name>.on.selected`` receives whichever member emitted, whose
        ``component.key`` is its collection key.
        """
        if cls is not DropdownMenu:
            raise TypeError("DropdownMenu.collection() must be called on DropdownMenu itself")
        return DropdownMenuCollection()

    def __get__(self: D, obj: LiveView | None, objtype: type[LiveView] | None = None) -> D:
        if obj is None:
            return self
        if not isinstance(obj, LiveView) or self._owner not in type(obj).__mro__:
            raise TypeError("Interactive components require their declaring LiveView owner")
        if getattr(obj, "use_actors", False):
            raise NotImplementedError("Interactive bindings are not yet supported by actor views")
        existing = obj._component_bindings.get(self._name)
        if existing is not None:
            if not isinstance(existing, type(self)) or existing._declaration is not self:
                raise RuntimeError("Interactive binding no longer matches its declaration")
            return existing
        bound = type(self)(label=self.label, items=self.items, visibility=self.visibility)
        bound._declaration = self
        bound.set_parent(obj)
        obj._component_bindings[self._name] = bound
        obj._register_component(bound, attr_name=self._name)
        return bound

    def __set__(self, obj: object, value: object) -> None:
        raise TypeError(
            "Interactive bindings cannot be replaced by application or restored attributes"
        )

    @property
    def key(self) -> str:
        """Read-only: the declared attribute name, or the collection key of a member."""
        if self._collection is not None:
            return self._member_key
        return self._declaration._name if self._declaration is not None else self._name

    @property
    def _source_name(self) -> str:
        """The declaration a subscription names: this menu's, or its collection's."""
        if self._collection is not None:
            declaration = self._collection._declaration
            return declaration._name if declaration is not None else ""
        return self.key

    @property
    def label(self) -> str:
        """The trigger button's text."""
        return self._label

    @property
    def items(self) -> list[ActionItem | SeparatorItem]:
        # Configuration is constructor-owned for the fixed-component pilot.
        # Do not expose shared mutable containers or replay stale configuration.
        return deepcopy(self._items)

    @property
    def visibility(self) -> Literal["server", "client"]:
        """Who owns open/closed: the server, or the browser's native popover."""
        return self._visibility

    @property
    def open(self) -> bool:
        """Whether a server-owned menu is open; Python may read and set it."""
        if self.visibility == "client":
            raise ValueError("Visibility is client-owned; use the toggled observation payload")
        return self._open

    @open.setter
    def open(self, value: bool) -> None:
        self._bound_owner()
        if self.visibility == "client":
            raise ValueError("Visibility is client-owned; Python cannot assign open")
        if type(value) is not bool:
            raise TypeError("Dropdown open must be a boolean")
        self._open = value

    @property
    def selected(self) -> str:
        """The last selected item's value, or ``""``."""
        return self._selected

    @property
    def state(self) -> dict[str, object]:
        return {
            "open": self._open if self.visibility == "server" else False,
            "selected": self.selected,
        }

    def _observes_toggle(self) -> bool:
        from djust._component_subscriptions import compile_subscriptions

        owner = self._bound_owner()
        return any(
            binding.component == self._source_name and binding.output == "toggled"
            for binding in compile_subscriptions(type(owner))
        )

    def _renew_observation_lifetime(self) -> None:
        self._bound_owner()
        self._observation_lifetime = "obs_" + uuid4().hex
        self._observation_sequence = 0
        self._observation_registered = False

    @event_handler(parameter_policy="strict", coerce_types=False)
    async def observe_toggle(self, open: bool, sequence: int, lifetime: str) -> None:
        """Report a client-owned popover's visibility; emits ``toggled`` if observed."""
        self._bound_owner()
        if self.visibility != "client":
            raise ValueError("Visibility observations require client-owned mode")
        if not self._observes_toggle():
            raise ValueError("Visibility observation has no declared subscription")
        if (
            type(open) is not bool
            or type(sequence) is not int
            or not 1 <= sequence <= 2**53 - 1
            or type(lifetime) is not str
        ):
            raise ValueError("Invalid visibility observation")
        if lifetime != self._observation_lifetime or sequence <= self._observation_sequence:
            return
        from asgiref.sync import sync_to_async
        from djust.state_backends import get_backend

        if not await sync_to_async(get_backend()._claim_observation)(lifetime, sequence):
            return
        self._observation_sequence = sequence
        await self._emit(_TOGGLED, {"open": open})

    def _bound_owner(self) -> LiveView:
        owner = self._parent
        collection = self._collection
        if collection is not None:
            if (
                not isinstance(owner, LiveView)
                or not self._mounted
                or collection._view is not owner
                or collection._members.get(self._member_key) is not self
                or owner._components.get(self.component_id) is not self
            ):
                raise RuntimeError(
                    "Interactive component is unbound, stale or belongs to another owner"
                )
            collection._bound_owner()
            return owner
        if (
            not isinstance(owner, LiveView)
            or self._declaration is None
            or not self._mounted
            or owner._component_bindings.get(self.key) is not self
            or owner._components.get(self.component_id) is not self
            or inspect.getattr_static(type(owner), self.key, None) is not self._declaration
        ):
            raise RuntimeError(
                "Interactive component is unbound, stale or belongs to another owner"
            )
        return owner

    async def _emit(self, output: OutputContract, payload: dict[str, object]) -> None:
        from djust.validation import get_strict_handler_contract
        from djust.websocket_utils import _call_handler
        from djust._component_subscriptions import compile_subscriptions, is_component_subscription

        owner = self._bound_owner()
        if not any(item is output for item in self.outputs):
            raise TypeError("Output contract does not belong to this component")
        if set(payload) != {name for name, _ in output.payload} or any(
            type(payload[name]) is not kind for name, kind in output.payload
        ):
            raise TypeError("Output payload does not match its declared contract")
        # Revalidate effective class code before delivery (e.g. development
        # reload/replacement). Never execute an arbitrary callback descriptor.
        bindings = compile_subscriptions(type(owner))
        for binding in bindings:
            if binding.component != self._source_name or binding.output != output.name:
                continue
            definition = inspect.getattr_static(owner, binding.callback, None)
            if (
                not isinstance(definition, FunctionType)
                or definition is not inspect.getattr_static(type(owner), binding.callback)
                or not is_component_subscription(definition)
            ):
                raise RuntimeError("Output callback no longer matches its validated declaration")
            callback = definition.__get__(owner, type(owner))
            # ADR-036 D5/D6: the callback's strict contract binds only the
            # declared output payload; the source component is framework context.
            bound = get_strict_handler_contract(callback, _SOURCE).bind(
                dict(payload), coerce=False, trusted={SOURCE_PARAMETER: self}
            )
            result = await _call_handler(callback, bound.kwargs, positional_args=bound.args)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                raise TypeError("Output callbacks must return None")
            return

    @event_handler(parameter_policy="strict", coerce_types=False)
    async def toggle(self) -> None:
        """Open or close a server-owned menu; emits ``toggled``."""
        self._bound_owner()
        self.open = not self.open
        await self._emit(_TOGGLED, {"open": self.open})

    @event_handler(parameter_policy="strict", coerce_types=False)
    async def close(self) -> None:
        """Close an open server-owned menu; emits ``toggled``."""
        self._bound_owner()
        if self.open:
            self.open = False
            await self._emit(_TOGGLED, {"open": False})

    def _allowed(self, value: str) -> bool:
        return any(
            item.get("value") == value and not item.get("disabled", False) for item in self._items
        )

    @event_handler(parameter_policy="strict", coerce_types=False)
    async def select(self, value: str) -> None:
        """Choose an enabled item: validate it, record it, close; emits ``selected``."""
        self._bound_owner()
        if type(value) is not str:
            raise TypeError("Selection must be a string")
        if not self._allowed(value):
            raise ValueError("Unknown or disabled dropdown selection")
        self._selected, self._open = value, False
        await self._emit(_SELECTED, {"value": value})

    def _dump_binding(self) -> dict[str, object]:
        self._bound_owner()
        return {"binding_id": self.component_id, **self.state}

    def _dump_session_binding(self) -> dict[str, object]:
        state = self._dump_binding()
        if self.visibility == "client":
            state["observation"] = {
                "lifetime": self._observation_lifetime,
                "sequence": self._observation_sequence,
            }
        return state

    def _restore_session_binding(self, state: dict[str, object]) -> None:
        if type(state) is not dict:
            raise ValueError("Invalid interactive component snapshot")
        record = dict(state)
        observation = record.pop("observation", None)
        if "observation" in state and (
            type(observation) is not dict
            or set(observation) != {"lifetime", "sequence"}
            or type(observation["lifetime"]) is not str
            or re.fullmatch(r"obs_[0-9a-f]{32}", observation["lifetime"]) is None
            or type(observation["sequence"]) is not int
            or not 0 <= observation["sequence"] <= 2**53 - 1
        ):
            raise ValueError("Invalid visibility observation snapshot")
        self._restore_binding(record)
        if type(observation) is dict and self.visibility == "client":
            self._observation_lifetime = observation["lifetime"]
            self._observation_sequence = observation["sequence"]
            # A restored session cannot resurrect an expired/evicted ledger
            # entry. Only a fresh rendered binding lifetime may register one.
            self._observation_registered = True

    def _restore_state(self, state: object) -> None:
        """Restore state within this lifetime, without callbacks or identity changes."""
        self._bound_owner()
        if (
            type(state) is not dict
            or set(state) != {"open", "selected"}
            or type(state["open"]) is not bool
            or type(state["selected"]) is not str
        ):
            raise ValueError("Invalid interactive component state")
        opened, selected = state["open"], state["selected"]
        self._open = opened if self.visibility == "server" else False
        self._selected = selected if self._allowed(selected) else ""

    def _restore_binding(self, state: dict[str, object]) -> None:
        owner = self._bound_owner()
        identity, opened, selected = _binding_values(state)
        collision = owner._components.get(identity)
        if collision is not None and collision is not self:
            raise ValueError("Interactive component identity collision")
        owner._components.pop(self.component_id)
        self.component_id = identity
        owner._components[identity] = self
        self._open = opened if self.visibility == "server" else False
        self._selected = selected if self._allowed(selected) else ""

    def render(self) -> str:
        self._bound_owner()
        client_owned = self.visibility == "client"
        popover_id = "popover-" + self.component_id
        trigger = (
            format_html(
                '<button type="button" class="dj-dropdown-menu__trigger" popovertarget="{}">{}</button>',
                popover_id,
                self.label,
            )
            if client_owned
            else format_html(
                '<button type="button" class="dj-dropdown-menu__trigger" dj-click="toggle" aria-expanded="{}">{}</button>',
                "true" if self.open else "false",
                self.label,
            )
        )
        content = format_html("{}", "")
        if client_owned or self.open:
            buttons = []
            for item in self._items:
                if "separator" in item:
                    buttons.append(format_html('<hr class="dj-dropdown-menu__divider"{}>', ""))
                else:
                    buttons.append(
                        format_html(
                            '<button type="button" class="dj-dropdown-menu__item" dj-click="select" dj-value-value="{}"{}>{}</button>',
                            item["value"],
                            format_html(" disabled{}", "") if item.get("disabled") else "",
                            item["label"],
                        )
                    )
            contents = format_html_join("", "{}", ((button,) for button in buttons))
            if client_owned:
                observation_attrs = format_html("{}", "")
                if self._observes_toggle():
                    if not self._observation_registered:
                        from djust.state_backends import get_backend

                        get_backend()._register_observation(self._observation_lifetime)
                        self._observation_registered = True
                    observation_attrs = format_html(
                        ' data-dj-observe-toggle="observe_toggle" data-dj-observe-lifetime="{}" data-dj-observe-sequence="{}"',
                        self._observation_lifetime,
                        self._observation_sequence,
                    )
                content = format_html(
                    '<div id="{}" popover="auto" class="dj-dropdown-menu__content" data-dj-native-dropdown="" style="position:fixed;inset:0;margin:auto;width:max-content;height:max-content"{}>{}</div>',
                    popover_id,
                    observation_attrs,
                    contents,
                )
            else:
                content = format_html('<div class="dj-dropdown-menu__content">{}</div>', contents)
        rendered: str = format_html(
            '<div class="dj-dropdown-menu{}" data-component-id="{}">{}{}</div>',
            " dj-dropdown-menu--open" if not client_owned and self.open else "",
            self.component_id,
            trigger,
            content,
        )
        return rendered


C = TypeVar("C", bound="DropdownMenuCollection")
_MEMBER_FIELDS = frozenset(
    {"key", "label", "items", "visibility", "binding_id", "open", "selected"}
)


class DropdownMenuCollection(ComponentDeclaration):
    """A keyed collection of interactive dropdowns (ADR-034 D5, C3).

    Declared with ``DropdownMenu.collection()``; the class attribute is the
    declaration and ``self.<name>`` is this view's own bound collection. Its
    members come only from ``sync()``: ordered ``(key, DropdownMenu(...))``
    pairs whose declarations are configuration, copied and never bound
    (owner decision C3-Q5). ``@<name>.on.selected`` and ``.on.toggled`` receive
    the member that emitted; ``component.key`` is its collection key.
    """

    _djust_fingerprint_state = True
    #: Renders as a context value and persists as one session record.
    _djust_component_collection = True

    def __init__(self) -> None:
        ComponentDeclaration.__init__(self, DropdownMenu, (_SELECTED, _TOGGLED))
        self._declaration: DropdownMenuCollection | None = None
        self._view: LiveView | None = None
        self._members: dict[str, DropdownMenu] = {}
        self._order: list[str] = []
        self.on = Outputs(self)

    def __get__(self: C, obj: LiveView | None, objtype: type[LiveView] | None = None) -> C:
        if obj is None:
            return self
        if not isinstance(obj, LiveView) or self._owner not in type(obj).__mro__:
            raise TypeError("Interactive collections require their declaring LiveView owner")
        if getattr(obj, "use_actors", False):
            raise NotImplementedError("Interactive bindings are not yet supported by actor views")
        existing = obj._component_bindings.get(self._name)
        if existing is not None:
            if not isinstance(existing, type(self)) or existing._declaration is not self:
                raise RuntimeError("Interactive binding no longer matches its declaration")
            return existing
        bound = type(self)()
        bound._declaration = self
        bound._view = obj
        obj._component_bindings[self._name] = bound
        return bound

    def __set__(self, obj: object, value: object) -> None:
        raise TypeError(
            "Interactive bindings cannot be replaced by application or restored attributes"
        )

    def _bound_owner(self) -> LiveView:
        view, declaration = self._view, self._declaration
        if (
            not isinstance(view, LiveView)
            or declaration is None
            or view._component_bindings.get(declaration._name) is not self
            or inspect.getattr_static(type(view), declaration._name, None) is not declaration
        ):
            raise RuntimeError(
                "Interactive collection is unbound, stale or belongs to another owner"
            )
        return view

    # -- the public collection API (owner decisions Q6, C3-Q2) -----------------

    @property
    def values(self) -> tuple[DropdownMenu, ...]:
        """The live members, in ``sync()`` order."""
        return tuple(self._members[key] for key in self._order)

    def get(self, key: str) -> DropdownMenu | None:
        """The live member for ``key``, or ``None`` if it is unknown or removed."""
        if type(key) is not str:
            return None
        return self._members.get(key)

    def __len__(self) -> int:
        """The number of current members."""
        return len(self._order)

    def __iter__(self) -> Iterator[DropdownMenu]:
        """The live members, in ``sync()`` order."""
        return iter(self.values)

    def sync(self, pairs: Sequence[tuple[str, DropdownMenu]]) -> None:
        """Reconcile the members with ordered ``(key, declaration)`` pairs.

        Everything is validated before anything changes. A retained key keeps
        its live member and state and takes the new label and items; a
        selection the new items no longer allow is cleared without emitting.
        A new key (or a retained key whose ``visibility`` changed) starts a new
        member lifetime; a missing key is removed, and its later events are
        refused. Reordering moves members, never their state.
        """
        view = self._bound_owner()
        if isinstance(pairs, (str, bytes)) or not isinstance(pairs, Sequence):
            raise TypeError("sync() takes a sequence of (key, DropdownMenu) pairs")
        plan: list[tuple[str, str, list[ActionItem | SeparatorItem], Literal["server", "client"]]]
        plan = []
        seen: set[str] = set()
        for pair in pairs:
            if type(pair) is not tuple or len(pair) != 2:
                raise TypeError("sync() takes a sequence of (key, DropdownMenu) pairs")
            key, declaration = pair
            if type(key) is not str or not key:
                raise TypeError("Collection keys must be nonempty strings")
            if key in seen:
                raise ValueError("Duplicate collection key in sync()")
            if not isinstance(declaration, DropdownMenu):
                raise TypeError("sync() members must be DropdownMenu declarations")
            seen.add(key)
            plan.append((key, declaration.label, declaration.items, declaration.visibility))

        members: dict[str, DropdownMenu] = {}
        created: list[DropdownMenu] = []
        updates: list[tuple[DropdownMenu, str, list[ActionItem | SeparatorItem]]] = []
        for key, label, items, visibility in plan:
            member = self._members.get(key)
            if member is not None and member.visibility == visibility:
                updates.append((member, label, items))
            else:
                member = self._new_member(key, label, items, visibility)
                created.append(member)
            members[key] = member
        for key, member in self._members.items():
            if members.get(key) is not member:
                self._retire(view, member)
        for member, label, items in updates:
            member._label, member._items = label, items
            if not member._allowed(member._selected):
                member._selected = ""
        for member in created:
            self._install(view, member)
        self._members = members
        self._order = [key for key, *_ in plan]

    # -- members -----------------------------------------------------------------

    def _new_member(
        self,
        key: str,
        label: str,
        items: list[ActionItem | SeparatorItem],
        visibility: Literal["server", "client"],
    ) -> DropdownMenu:
        member = DropdownMenu(label=label, items=items, visibility=visibility)
        member._collection = self
        member._member_key = key
        return member

    def _install(self, view: LiveView, member: DropdownMenu) -> None:
        if view._components.get(member.component_id) is not None:
            raise ValueError("Interactive component identity collision")
        member.set_parent(view)
        declaration = self._declaration
        view._register_component(member, attr_name=declaration._name if declaration else None)

    def _retire(self, view: LiveView, member: DropdownMenu) -> None:
        if view._components.get(member.component_id) is member:
            view._components.pop(member.component_id)
        LiveComponent.unmount(member)

    @property
    def state(self) -> dict[str, object]:
        """Membership, order, configuration and member state (change detection)."""
        return {
            "members": [
                (key, m.component_id, m.label, m._items, m.visibility, m.state)
                for key, m in ((key, self._members[key]) for key in self._order)
            ]
        }

    def _renew_observation_lifetimes(self) -> None:
        for member in self.values:
            if member.visibility == "client":
                member._renew_observation_lifetime()

    # -- server session persistence (legacy policy) ---------------------------

    def _dump_session_collection(self) -> dict[str, object]:
        self._bound_owner()
        return {
            "version": 1,
            "members": [
                {
                    "key": member.key,
                    "label": member.label,
                    "items": member.items,
                    "visibility": member.visibility,
                    **member._dump_session_binding(),
                }
                for member in self.values
            ],
        }

    def _restore_session_collection(self, state: object) -> None:
        """Replace the members with a saved record, validated in full first.

        The record is server-written session state: the membership of the last
        ``sync()``, never a client snapshot. No callback or class is read from
        it; an invalid record changes nothing.
        """
        view = self._bound_owner()
        if (
            type(state) is not dict
            or set(state) != {"version", "members"}
            or state["version"] != 1
            or type(state["members"]) is not list
        ):
            raise ValueError("Invalid interactive collection snapshot")
        restored: list[DropdownMenu] = []
        keys: set[str] = set()
        identities: set[str] = set()
        current = set(id(member) for member in self._members.values())
        for record in state["members"]:
            if type(record) is not dict or not _MEMBER_FIELDS <= set(record):
                raise ValueError("Invalid interactive collection snapshot")
            if set(record) - _MEMBER_FIELDS - {"observation"}:
                raise ValueError("Invalid interactive collection snapshot")
            key = record["key"]
            if type(key) is not str or not key or key in keys:
                raise ValueError("Invalid interactive collection snapshot")
            if record["visibility"] not in ("server", "client") or type(record["label"]) is not str:
                raise ValueError("Invalid interactive collection snapshot")
            try:
                member = self._new_member(
                    key, record["label"], record["items"], record["visibility"]
                )
            except (TypeError, ValueError):
                raise ValueError("Invalid interactive collection snapshot") from None
            identity, opened, selected = _binding_values(
                {name: record[name] for name in ("binding_id", "open", "selected")}
            )
            occupied = view._components.get(identity)
            if identity in identities or (occupied is not None and id(occupied) not in current):
                raise ValueError("Interactive component identity collision")
            observation = record.get("observation")
            if "observation" in record and (
                type(observation) is not dict
                or set(observation) != {"lifetime", "sequence"}
                or type(observation["lifetime"]) is not str
                or re.fullmatch(r"obs_[0-9a-f]{32}", observation["lifetime"]) is None
                or type(observation["sequence"]) is not int
                or not 0 <= observation["sequence"] <= 2**53 - 1
            ):
                raise ValueError("Invalid visibility observation snapshot")
            member.component_id = identity
            member._open = opened if member.visibility == "server" else False
            member._selected = selected if member._allowed(selected) else ""
            if type(observation) is dict and member.visibility == "client":
                member._observation_lifetime = observation["lifetime"]
                member._observation_sequence = observation["sequence"]
                member._observation_registered = True
            keys.add(key)
            identities.add(identity)
            restored.append(member)
        for member in self.values:
            self._retire(view, member)
        for member in restored:
            self._install(view, member)
        self._members = {member.key: member for member in restored}
        self._order = [member.key for member in restored]
