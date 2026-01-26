"""
Reusable LiveView components
"""

import uuid
from typing import Dict, Any, Optional, Callable
from ._rust import RustLiveView
from .utils import get_template_dirs


class Component:
    """
    Base class for reusable stateless LiveView components.

    Stateless components are simple presentational components that don't
    maintain state or handle events. Use `template` attribute for the template.

    For stateful components with lifecycle and event handling, use `LiveComponent`
    instead (which also uses `template` attribute).

    Usage:
        class Button(Component):
            template = '<button @click="on_click">{{ label }}</button>'

            def __init__(self, label="Click me"):
                super().__init__()
                self.label = label

            def on_click(self):
                print(f"Button {self.label} clicked!")
    """

    template: str = ""

    def __init__(self, **kwargs):
        self._rust_view: Optional[RustLiveView] = None
        self._parent = None

        # Set initial properties
        for key, value in kwargs.items():
            setattr(self, key, value)

    def get_context_data(self) -> Dict[str, Any]:
        """Get context data for rendering"""
        context = {}
        for key in dir(self):
            if not key.startswith("_") and not callable(getattr(self, key)):
                context[key] = getattr(self, key)
        return context

    def render(self) -> str:
        """Render the component to HTML"""
        if self._rust_view is None:
            self._rust_view = RustLiveView(self.template, get_template_dirs())

        context = self.get_context_data()
        self._rust_view.update_state(context)
        return self._rust_view.render()

    def __str__(self) -> str:
        """Allow {{ component }} in templates and JSON serialization"""
        return self.render()

    def update(self):
        """Trigger a re-render (will notify parent if set)"""
        if self._parent:
            self._parent.update()


class ComponentRegistry:
    """Registry for component definitions"""

    _components: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str, component_class: type):
        """Register a component"""
        cls._components[name] = component_class

    @classmethod
    def get(cls, name: str) -> Optional[type]:
        """Get a registered component"""
        return cls._components.get(name)

    @classmethod
    def create(cls, name: str, **kwargs) -> Optional[Component]:
        """Create an instance of a registered component"""
        component_class = cls.get(name)
        if component_class:
            return component_class(**kwargs)
        return None


def register_component(name: str):
    """
    Decorator to register a component.

    Usage:
        @register_component('my-button')
        class MyButton(Component):
            template = '<button>Click me</button>'
    """

    def decorator(component_class: type):
        ComponentRegistry.register(name, component_class)
        return component_class

    return decorator


class LiveComponent(Component):
    """
    Stateful component with lifecycle methods and parent-child communication.

    LiveComponents manage their own state independently and can communicate
    with parent views through events.

    Attributes:
        template (str): The Django template for this component.

    Lifecycle:
        1. mount(**props) - Called when component is created
        2. update(**props) - Called when parent updates props
        3. unmount() - Called when component is destroyed

    Communication:
        - Props Down: Parent passes data via mount() and update()
        - Events Up: Component sends events via send_parent()

    Usage:
        class TodoListComponent(LiveComponent):
            template = '''
                <div class="todo-list">
                    {% for item in items %}
                    <div @click="toggle_todo" data-id="{{ item.id }}">
                        <input type="checkbox" {% if item.completed %}checked{% endif %}>
                        {{ item.text }}
                    </div>
                    {% endfor %}
                </div>
            '''

            def mount(self, items=None):
                '''Initialize component state'''
                self.items = items or []
                self.filter = "all"

            def update(self, items=None, **props):
                '''Called when parent updates props'''
                if items is not None:
                    self.items = items

            def toggle_todo(self, id: str = None, **kwargs):
                '''Event handler for checkbox toggle'''
                item = next(i for i in self.items if i['id'] == int(id))
                item['completed'] = not item['completed']
                # Notify parent of change
                self.send_parent("todo_toggled", {"id": int(id)})

            def unmount(self):
                '''Cleanup when component is destroyed'''
                # Release resources, clear timers, etc.
                pass
    """

    template: str = ""

    def __init__(self, **props: Any) -> None:
        """
        Initialize LiveComponent with props.

        Args:
            **props: Initial properties passed from parent
        """
        super().__init__(**props)

        # Generate unique component ID with class name prefix for better debugging
        self.component_id = f"{self.__class__.__name__}-{uuid.uuid4()}"

        # Track lifecycle state
        self._mounted = False
        self._parent_callback: Optional[Callable] = None

        # Debug logging
        from .config import config

        if config.get("debug_components", False):
            import sys

            print(
                f"[Component] {self.__class__.__name__} ({self.component_id[:20]}...) created with props: {list(props.keys())}",
                file=sys.stderr,
            )

        # Call mount with initial props
        self.mount(**props)
        self._mounted = True

        # Note: Template validation happens in render() to allow
        # component creation without a template (useful for testing)

    def mount(self, **props: Any) -> None:
        """
        Called when component is first created.

        Override this method to initialize component state.

        Args:
            **props: Initial properties from parent

        Example:
            def mount(self, items=None, filter="all"):
                self.items = items or []
                self.filter = filter
        """
        # Debug logging
        from .config import config

        if config.get("debug_components", False):
            import sys

            print(
                f"[Component] {self.__class__.__name__} ({self.component_id[:20]}...) mount() called with props: {list(props.keys())}",
                file=sys.stderr,
            )

        # Default implementation: set props as instance attributes
        for key, value in props.items():
            setattr(self, key, value)

    def update(self, **props: Any) -> None:
        """
        Called when parent updates component props.

        Override this method to react to prop changes.

        Args:
            **props: Updated properties from parent

        Example:
            def update(self, items=None, **props):
                if items is not None:
                    self.items = items
                    self._refresh_filtered_items()
        """
        # Debug logging
        from .config import config

        if config.get("debug_components", False):
            import sys

            print(
                f"[Component] {self.__class__.__name__} ({self.component_id[:20]}...) update() called with props: {list(props.keys())}",
                file=sys.stderr,
            )

        # Default implementation: update instance attributes
        for key, value in props.items():
            setattr(self, key, value)

    def unmount(self) -> None:
        """
        Called when component is destroyed.

        Override this method to cleanup resources.

        Example:
            def unmount(self):
                # Clear timers
                if self.timer:
                    self.timer.cancel()

                # Close connections
                if self.websocket:
                    self.websocket.close()
        """
        # Debug logging
        from .config import config

        if config.get("debug_components", False):
            import sys

            print(
                f"[Component] {self.__class__.__name__} ({self.component_id[:20]}...) unmount() called",
                file=sys.stderr,
            )

        self._mounted = False
        self._parent_callback = None

    def send_parent(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        """
        Send event to parent view.

        Args:
            event: Event name (e.g., "item_selected", "form_submitted")
            data: Optional event payload

        Example:
            # In child component
            def on_click(self, item_id: str, **kwargs):
                self.send_parent("item_clicked", {"item_id": item_id})

            # In parent view
            def handle_component_event(self, component_id, event, data):
                if event == "item_clicked":
                    self.selected_id = data['item_id']
        """
        # Debug logging
        from .config import config

        if config.get("debug_components", False):
            import sys

            print(
                f"[Component] {self.__class__.__name__} ({self.component_id[:20]}...) send_parent('{event}', {list(data.keys()) if data else 'None'})",
                file=sys.stderr,
            )

        if self._parent_callback:
            event_data = {
                "component_id": self.component_id,
                "event": event,
                "data": data or {},
            }
            self._parent_callback(event_data)

    def _set_parent_callback(self, callback: Callable) -> None:
        """
        Internal method to set parent callback.

        Called by parent view to register event handler.

        Args:
            callback: Function to call when component sends events
        """
        self._parent_callback = callback

    def get_context_data(self) -> Dict[str, Any]:
        """
        Get context data for rendering.

        Returns context with all public instance attributes.

        Returns:
            Dictionary of context variables for template
        """
        context = {}
        for key in dir(self):
            if not key.startswith("_") and not callable(getattr(self, key)):
                context[key] = getattr(self, key)
        return context

    def render(self) -> str:
        """
        Render component to HTML.

        Returns:
            HTML string
        """
        if not self._mounted:
            raise RuntimeError(f"Cannot render unmounted component {self.__class__.__name__}")

        # Inject data-component-id into template BEFORE rendering
        # This ensures it's part of VDOM, keeping client and server in sync
        if self._rust_view is None:
            if not self.template:
                raise ValueError(
                    f"Component {self.__class__.__name__} must define 'template' attribute. "
                    f"Add: template = '...' as a class attribute."
                )

            # Inject component_id into template's root element
            import re

            template = self.template
            match = re.search(r"(<[a-zA-Z][a-zA-Z0-9]*)([\s>])", template)
            if match:
                end = match.end(1)
                template = (
                    template[:end] + f' data-component-id="{self.component_id}"' + template[end:]
                )

            self._rust_view = RustLiveView(template, get_template_dirs())

        context = self.get_context_data()
        self._rust_view.update_state(context)
        return self._rust_view.render()

    def __str__(self) -> str:
        """Allow {{ component }} in templates and JSON serialization"""
        return self.render()

    def __del__(self):
        """Destructor to ensure unmount is called."""
        if self._mounted:
            self.unmount()
