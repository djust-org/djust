"""Every marked line must be rejected by BOTH supported type checkers.

Do not execute this file. The check runner verifies diagnostics by source line,
so an unrelated import failure cannot make the negative suite appear to pass.
"""

from prototype import DropdownMenu, PrototypeOwner
from positive import Page


class WrongChild(Page):
    def renamed_callback(self, component: DropdownMenu, value: int) -> None:  # error: override
        pass


class BadPage(PrototypeOwner):
    menu = DropdownMenu(label="Project", items=[])

    @menu.on.selected  # error: wrong payload type
    def wrong_value(self, component: DropdownMenu, value: int) -> None:
        pass

    @menu.on.selected  # error: missing payload
    def missing_value(self, component: DropdownMenu) -> None:
        pass

    @menu.on.selected  # error: wrong source
    def wrong_source(self, component: str, value: str) -> None:
        pass

    @menu.on.selected  # error: renamed keyword
    def wrong_name(self, component: DropdownMenu, val: str) -> None:
        pass

    @menu.on.selected  # error: invalid return
    def wrong_return(self, component: DropdownMenu, value: str) -> int:
        return 1

    @menu.on.toggled  # error: wrong observation
    async def wrong_toggle(self, component: DropdownMenu, open: str) -> None:
        pass

    @menu.on.selected  # error: required extra argument
    def extra(self, component: DropdownMenu, value: str, required: bool) -> None:
        pass

    @menu.on.selected  # error: payload cannot be keyword bound
    def positional(self, component: DropdownMenu, value: str, /) -> None:
        pass


page = Page()
page.menu.on.seleted  # error: misspelled output
page.projet_menu  # error: misspelled component
page.menu.open = "yes"  # error: state type
page.menu.key = "other"  # error: identity is read only
page.renamed_callback(page.menu, 2)  # error: decorator preserves payload type
page.renamed_callback("forged", "edit")  # error: decorator preserves source type
page.renamed_callback(page.menu)  # error: decorator preserves required arguments
DropdownMenu(label="Bad", items=[{"label": "Edit"}])  # error: missing value
DropdownMenu(label="Bad", items=[{"separator": False}])  # error: invalid separator
DropdownMenu(label="Bad", items=[{"label": "Edit", "value": 1}])  # error: item value type
projet_menu  # error: undefined declaration  # noqa: F821
DropdownMenu(label="Bad", items=[], visibility="automatic")  # error: invalid visibility mode
