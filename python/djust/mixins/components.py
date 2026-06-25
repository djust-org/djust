"""
ComponentMixin - Component lifecycle and management for LiveView.
"""

from typing import TYPE_CHECKING, Any, Dict

from ..serialization import normalize_django_value

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
        from ..components.base import LiveComponent

        component = self._components.get(component_id)
        if component and isinstance(component, LiveComponent):
            # ``LiveComponent.update`` (base.py) sets each prop as an instance
            # attribute; subclasses may override it for coercion or
            # selective-prop logic. See #1947.
            component.update(**props)

    def _register_component(self, component: Any) -> None:
        """
        Register a child component for event handling.
        """
        from ..components.base import LiveComponent

        if isinstance(component, LiveComponent):
            # component_id is Optional[str] on the class but always set by the
            # time a component is registered (descriptor/auto-id assignment).
            self._components[component.component_id] = component  # type: ignore[index]

            def component_callback(event_data: Dict[str, Any]) -> None:
                self.handle_component_event(
                    event_data["component_id"],
                    event_data["event"],
                    event_data["data"],
                )

            component._set_parent_callback(component_callback)

    def _extract_component_state(self, component: Any) -> Dict[str, Any]:
        """
        Extract state from a component for session storage.
        """
        import json as json_module

        state: Dict[str, Any] = {}
        for key in dir(component):
            if not key.startswith("_") and key not in ("template_name",):
                try:
                    value = getattr(component, key)
                    if not callable(value):
                        try:
                            json_module.dumps(value)
                            state[key] = value
                        except (TypeError, ValueError):
                            pass  # Value not JSON-serializable; skip
                except (AttributeError, TypeError):
                    pass  # Attribute not accessible; skip
        return state

    def _restore_component_state(self, component: Any, state: Dict[str, Any]) -> None:
        """
        Restore state to a component from session storage.
        """
        for key, value in state.items():
            if not key.startswith("_"):
                try:
                    setattr(component, key, value)
                except (AttributeError, TypeError):
                    pass

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

    def _save_components_to_session(self, request: "HttpRequest", context: Dict[str, Any]) -> None:
        """
        Save component state to session with stable IDs.
        """
        from ..components.base import Component, LiveComponent

        view_key = f"liveview_{request.path}"
        component_state: Dict[str, Any] = {}

        for key, component in context.items():
            if isinstance(component, (Component, LiveComponent)):
                # component_id is declared on LiveComponent; on the plain
                # Component branch it is set dynamically here (stable session ID).
                component.component_id = key  # type: ignore[union-attr]
                component_state[key] = self._extract_component_state(component)

        component_state_serializable = normalize_django_value(component_state)
        request.session[f"{view_key}_components"] = component_state_serializable
        request.session.modified = True
