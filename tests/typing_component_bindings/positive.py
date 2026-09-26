"""Valid callback types and executable identity assertions for ADR-034."""

import asyncio
from typing import assert_type

from prototype import DropdownMenu, PrototypeOwner


class Page(PrototypeOwner):
    menu = DropdownMenu(label="Project", items=[{"label": "Edit", "value": "edit"}])
    other = DropdownMenu(label="Account", items=[{"separator": True}])

    @menu.on.selected
    def renamed_callback(self, component: DropdownMenu, value: str) -> None:
        assert component is self.menu
        component.open = value == "edit"

    @menu.on.toggled
    async def observe(self, *, component: DropdownMenu, open: bool) -> None:
        assert component is self.menu
        component.open = open


class Child(Page):
    def renamed_callback(self, component: DropdownMenu, value: str) -> None:
        super().renamed_callback(component, value)


class AsyncPage(PrototypeOwner):
    menu = DropdownMenu(label="Project", items=[])

    @menu.on.selected
    async def selected(self, component: DropdownMenu, value: str) -> None:
        component.open = value == "Saved"

    @menu.on.toggled
    def toggled(self, component: DropdownMenu, open: bool) -> None:
        component.open = open


class RowsPage(PrototypeOwner):
    """ADR-034 C3: a keyed collection, typed like the fixed menu."""

    rows = DropdownMenu.collection()

    @rows.on.selected
    def on_rows_selected(self, component: DropdownMenu, value: str) -> None:
        component.open = value == component.key

    @rows.on.toggled
    async def on_rows_toggled(self, component: DropdownMenu, open: bool) -> None:
        pass


def verify_collection() -> None:
    page = RowsPage()
    page.rows.sync([("a", DropdownMenu(label="A", items=[{"label": "Go", "value": "a"}]))])
    member = page.rows.get("a")
    assert_type(member, DropdownMenu | None)
    assert_type(page.rows.values, tuple[DropdownMenu, ...])
    assert_type(len(page.rows), int)
    for item in page.rows:
        assert_type(item, DropdownMenu)
    assert member is not None and member.key == "a"
    assert page.rows.get("missing") is None
    assert list(page.rows) == [member] == list(page.rows.values)
    assert len(page.rows) == 1
    page.on_rows_selected(member, "a")
    assert member.open


def verify() -> None:
    page, other = Child(), Child()
    assert_type(page.menu, DropdownMenu)
    assert_type(Page.menu, DropdownMenu)
    assert_type(page.menu.open, bool)
    assert_type(page.menu.key, str)
    assert_type(page.renamed_callback(page.menu, "edit"), None)
    assert page.menu.open
    assert not page.other.open
    assert not other.menu.open
    assert not Page.menu.open
    assert page.menu is page.menu
    assert type(page.menu) is DropdownMenu
    assert page.menu.items is not other.menu.items
    assert page.menu.items[0] is not other.menu.items[0]
    asyncio.run(page.observe(component=page.menu, open=False))
    assert not page.menu.open
    async_page = AsyncPage()
    asyncio.run(async_page.selected(async_page.menu, "Saved"))
    assert async_page.menu.open
    async_page.toggled(async_page.menu, True)
    assert async_page.menu.open
    verify_collection()


if __name__ == "__main__":
    verify()
