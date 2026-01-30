"""
ComponentMixin - Component lifecycle and management for LiveView.
"""

import json
from typing import Any, Dict

from ..serialization import DjangoJSONEncoder


class ComponentMixin:
    """Component methods: mount, handle_component_event, update_component, etc."""

    def mount(self, request, **kwargs):
        """
        Called when the view is mounted. Override to set initial state.

        Args:
            request: The Django request object
            **kwargs: URL parameters
        """
        pass

    def handle_component_event(self, component_id: str, event: str, data: Dict[str, Any]):
        """
        Handle events sent from child components.

        Override this method to respond to component events sent via send_parent().
        """
        pass

    def update_component(self, component_id: str, **props):
        """
        Update a child component's props.

        Args:
            component_id: ID of the component to update
            **props: New prop values to pass to component
        """
        from ..components.base import LiveComponent

        component = self._components.get(component_id)
        if component and isinstance(component, LiveComponent):
            component.update(**props)

    def _register_component(self, component):
        """
        Register a child component for event handling.
        """
        from ..components.base import LiveComponent

        if isinstance(component, LiveComponent):
            self._components[component.component_id] = component

            def component_callback(event_data):
                self.handle_component_event(
                    event_data["component_id"],
                    event_data["event"],
                    event_data["data"],
                )

            component._set_parent_callback(component_callback)

    def _extract_component_state(self, component) -> dict:
        """
        Extract state from a component for session storage.
        """
        import json as json_module

        state = {}
        for key in dir(component):
            if not key.startswith("_") and key not in ("template_name",):
                try:
                    value = getattr(component, key)
                    if not callable(value):
                        try:
                            json_module.dumps(value)
                            state[key] = value
                        except (TypeError, ValueError):
                            pass
                except (AttributeError, TypeError):
                    pass
        return state

    def _restore_component_state(self, component, state: dict):
        """
        Restore state to a component from session storage.
        """
        for key, value in state.items():
            if not key.startswith("_"):
                try:
                    setattr(component, key, value)
                except (AttributeError, TypeError):
                    pass

    def _assign_component_ids(self):
        """
        Automatically assign IDs to components based on their attribute names.
        """
        from ..components.base import Component, LiveComponent

        for key, value in self.__dict__.items():
            if isinstance(value, (Component, LiveComponent)) and not key.startswith("_"):
                value._auto_id = key

    def _save_components_to_session(self, request, context: dict):
        """
        Save component state to session with stable IDs.
        """
        from ..components.base import Component, LiveComponent

        view_key = f"liveview_{request.path}"
        component_state = {}

        for key, component in context.items():
            if isinstance(component, (Component, LiveComponent)):
                component.component_id = key
                component_state[key] = self._extract_component_state(component)

        component_state_json = json.dumps(component_state, cls=DjangoJSONEncoder)
        component_state_serializable = json.loads(component_state_json)
        request.session[f"{view_key}_components"] = component_state_serializable
        request.session.modified = True
