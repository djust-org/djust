"""Interactive components with typed outputs (ADR-034). Available from djust 1.3.

An interactive component owns its own mechanics (a dropdown opens, closes and
validates a selection itself) and tells its view what happened through typed
outputs that the view subscribes to with ``@<component>.on.<output>``::

    from djust import LiveView
    from djust.components.interactive import DropdownMenu

    class ProjectView(LiveView):
        project_menu = DropdownMenu(
            label="Project",
            items=[{"label": "Edit", "value": "edit"}, {"separator": True}],
        )

        @project_menu.on.selected
        def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
            self.selected_action = value

This ``DropdownMenu`` is not the legacy plain renderer of the same name in
``djust.components.components``; system check ``djust.Q004`` flags a module
that imports both. Interactive components are not supported on
``use_actors = True`` views (``djust.V020``).
"""

from ._interactive import ActionItem, DropdownMenu, SeparatorItem

__all__ = ["ActionItem", "DropdownMenu", "SeparatorItem"]
