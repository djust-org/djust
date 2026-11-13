"""
ComponentMixin - Component lifecycle and management for LiveView.
"""

from typing import TYPE_CHECKING, Any, Dict, Optional


if TYPE_CHECKING:
    from django.http import HttpRequest

    from ..components.base import LiveComponent


class ComponentMixin:
    """Component methods: mount, handle_component_event, update_component, etc."""

    if TYPE_CHECKING:
        # Cooperating attribute supplied by the host class (LiveView, set in
        # __init__). Declared type-only so the strict-island mypy run resolves
        # it on the mixin without a runtime change — this mixin is never
        # instantiated standalone.
        _components: Dict[str, "LiveComponent"]

    def mount(self, request: "HttpRequest", **kwargs: Any) -> None:
        """
        Called when the view is mounted. Override to set initial state.

        Args:
            request: The Django request object
            **kwargs: URL parameters
        """
        pass

    def handle_component_event(self, component_id: str, event: str, data: Dict[str, Any]) -> None:
        """
        Handle events sent from child components.

        Override this method to respond to component events sent via send_parent().
        """
        pass

    def update_component(self, component_id: str, **props: Any) -> None:
        """
        Update a child component's props.

        Args:
            component_id: ID of the component to update
            **props: New prop values to pass to component
        """
        from ..components.base import BoundComponent, LiveComponent

        component = self._components.get(component_id)
        if component and isinstance(component, LiveComponent):
            # ``LiveComponent.update`` (base.py) sets each prop as an instance
            # attribute; subclasses may override it for coercion or
            # selective-prop logic. See #1947.
            component.update(**props)
        elif isinstance(component, BoundComponent):
            for key, value in props.items():
                setattr(component, key, value)

    def _register_component(self, component: Any, attr_name: Optional[str] = None) -> None:
        """
        Register a child component for event handling.

        ``attr_name`` is the view attribute the component is held under
        (``self.table`` → ``"table"``); it is recorded on the component so
        ``LiveComponent.trigger_update`` can hand it to ``set_changed_keys``
        (#2779 — without it a component's in-place mutation never re-synced
        ``{{ table.render }}`` to the Rust context).
        """
        from ..components.base import LiveComponent

        if isinstance(component, LiveComponent):
            # component_id is Optional[str] on the class but always set by the
            # time a component is registered (descriptor/auto-id assignment).
            self._components[component.component_id] = component  # type: ignore[index]
            if component._parent is None:
                component._parent = self
            if attr_name:
                component._parent_attr = attr_name

            def component_callback(event_data: Dict[str, Any]) -> None:
                self.handle_component_event(
                    event_data["component_id"],
                    event_data["event"],
                    event_data["data"],
                )

            component._set_parent_callback(component_callback)

    def _assign_component_ids(self) -> None:
        """
        Automatically assign IDs to components based on their attribute names.
        """
        from ..components.base import Component, LiveComponent

        for key, value in self.__dict__.items():
            if isinstance(value, (Component, LiveComponent)) and not key.startswith("_"):
                # _auto_id is a dynamic framework slot read via hasattr in
                # components/base.py (_generate_id); not declared on the class.
                value._auto_id = key  # type: ignore[union-attr]
