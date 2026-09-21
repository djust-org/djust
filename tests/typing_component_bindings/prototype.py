"""ADR-034 typing experiment, NOT an exported or dispatch-capable component.

Keep this independent of LiveComponent: its legacy proxy is not the public type
promised by the ADR. Production registry, persistence and subscription validation
must be implemented separately before this API can ship.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Awaitable, Literal, Never, NotRequired, Protocol, TypeVar, TypedDict


class ActionItem(TypedDict):
    label: str
    value: str
    disabled: NotRequired[bool]


class SeparatorItem(TypedDict):
    separator: Literal[True]


class SelectedCallback(Protocol):
    def __call__(
        self, owner: Never, /, *, component: DropdownMenu, value: str
    ) -> None | Awaitable[None]: ...


class ToggledCallback(Protocol):
    def __call__(
        self, owner: Never, /, *, component: DropdownMenu, open: bool
    ) -> None | Awaitable[None]: ...


# Never is only a compatibility constraint for the arbitrary method receiver.
# It is NOT a dispatch interface: no value is supplied as a Never at runtime.
# Keeping the original callable type preserves method binding and signatures.
Selected = TypeVar("Selected", bound=SelectedCallback)
Toggled = TypeVar("Toggled", bound=ToggledCallback)


class Outputs:
    def selected(self, callback: Selected) -> Selected:
        return callback

    def toggled(self, callback: Toggled) -> Toggled:
        return callback


class PrototypeOwner:
    def __init__(self) -> None:
        self.bindings: dict[DropdownMenu, DropdownMenu] = {}


class DropdownMenu:
    def __init__(self, *, label: str, items: list[ActionItem | SeparatorItem]) -> None:
        self.label = label
        self.items = deepcopy(items)
        self.open = False
        self.on = Outputs()
        self._key = ""

    @property
    def key(self) -> str:
        return self._key

    def __set_name__(self, owner: type[PrototypeOwner], name: str) -> None:
        self._key = name

    def __get__(
        self, instance: PrototypeOwner | None, owner: type[PrototypeOwner] | None = None
    ) -> DropdownMenu:
        if instance is None:
            return self
        if self not in instance.bindings:
            bound = DropdownMenu(label=self.label, items=self.items)
            bound._key = self.key
            instance.bindings[self] = bound
        return instance.bindings[self]
