"""Staged ADR-034 server-owned dropdown; not yet a public component import.

Real concrete per-owner instances use the existing LiveComponent registry and
transport actions. Client-owned observations, collections and public rollout
remain separate acceptance gates.
"""

from __future__ import annotations

from collections.abc import Awaitable
from copy import deepcopy
import inspect
import re
from types import FunctionType
from typing import Literal, NoReturn, Protocol, TypeVar, TypedDict
from uuid import uuid4

from django.utils.html import format_html, format_html_join

from djust._component_subscriptions import ComponentDeclaration, OutputContract, subscribe
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
        return subscribe(self._source, _SELECTED, callback)

    def toggled(self, callback: Toggled) -> Toggled:
        return subscribe(self._source, _TOGGLED, callback)


class DropdownMenu(ComponentDeclaration, LiveComponent):
    """Concrete descriptor and bound object share their actual Python type."""

    _djust_fingerprint_state = True
    component_id: str

    def __init__(self, *, label: str, items: list[ActionItem | SeparatorItem]) -> None:
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
        self._declaration: DropdownMenu | None = None
        self.on = Outputs(self)

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
        bound = type(self)(label=self.label, items=self.items)
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
        return self._declaration._name if self._declaration is not None else self._name

    @property
    def label(self) -> str:
        return self._label

    @property
    def items(self) -> list[ActionItem | SeparatorItem]:
        # Configuration is constructor-owned for the fixed-component pilot.
        # Do not expose shared mutable containers or replay stale configuration.
        return deepcopy(self._items)

    @property
    def open(self) -> bool:
        return self._open

    @open.setter
    def open(self, value: bool) -> None:
        self._bound_owner()
        if type(value) is not bool:
            raise TypeError("Dropdown open must be a boolean")
        self._open = value

    @property
    def selected(self) -> str:
        return self._selected

    @property
    def state(self) -> dict[str, object]:
        return {"open": self.open, "selected": self.selected}

    def _bound_owner(self) -> LiveView:
        owner = self._parent
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
            if binding.component != self.key or binding.output != output.name:
                continue
            definition = inspect.getattr_static(owner, binding.callback, None)
            if (
                not isinstance(definition, FunctionType)
                or definition is not inspect.getattr_static(type(owner), binding.callback)
                or not is_component_subscription(definition)
            ):
                raise RuntimeError("Output callback no longer matches its validated declaration")
            callback = definition.__get__(owner, type(owner))
            result = await _call_handler(callback, {"component": self, **payload})
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                raise TypeError("Output callbacks must return None")
            return

    @event_handler(parameter_policy="strict", coerce_types=False)
    async def toggle(self) -> None:
        self._bound_owner()
        self.open = not self.open
        await self._emit(_TOGGLED, {"open": self.open})

    @event_handler(parameter_policy="strict", coerce_types=False)
    async def close(self) -> None:
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
        self._open = opened
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
        self._open = opened
        self._selected = selected if self._allowed(selected) else ""

    def render(self) -> str:
        self._bound_owner()
        trigger = format_html(
            '<button type="button" class="dj-dropdown-menu__trigger" dj-click="toggle" aria-expanded="{}">{}</button>',
            "true" if self.open else "false",
            self.label,
        )
        content = format_html("{}", "")
        if self.open:
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
            content = format_html(
                '<div class="dj-dropdown-menu__content">{}</div>',
                format_html_join("", "{}", ((button,) for button in buttons)),
            )
        rendered: str = format_html(
            '<div class="dj-dropdown-menu{}" data-component-id="{}">{}{}</div>',
            " dj-dropdown-menu--open" if self.open else "",
            self.component_id,
            trigger,
            content,
        )
        return rendered
