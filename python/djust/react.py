"""
React component integration for djust

This module provides a registry and integration system for using React components
within djust templates with server-side rendering and client-side hydration.
"""

from typing import Dict, Any, Callable, Optional
import json


class ReactComponentRegistry:
    """
    Registry for React components that can be used in djust templates.

    Components are registered with their name and rendering function, allowing
    server-side rendering with Rust and client-side hydration with React.
    """

    def __init__(self):
        self._components: Dict[str, Callable] = {}
        self._component_modules: Dict[str, str] = {}

    def register(
        self, name: str, module_path: Optional[str] = None, component_name: Optional[str] = None
    ):
        """
        Decorator to register a React component.

        Args:
            name: Component name as used in templates (e.g., "Button")
            module_path: Path to JavaScript module (e.g., "./components/Button.jsx")
            component_name: Name of export in module (defaults to name)

        Example:
            @react_components.register("Button", module_path="./components/Button.jsx")
            def button_renderer(props, children):
                return f'<button class="{props.get("className", "")}">{children}</button>'
        """

        def decorator(func: Callable):
            self._components[name] = func
            if module_path:
                self._component_modules[name] = {
                    "module": module_path,
                    "export": component_name or name,
                }
            return func

        return decorator

    def get(self, name: str) -> Optional[Callable]:
        """Get a registered component's renderer function."""
        return self._components.get(name)

    def get_module_info(self, name: str) -> Optional[Dict[str, str]]:
        """Get module information for client-side loading."""
        return self._component_modules.get(name)

    def render(self, name: str, props: Dict[str, Any], children: str = "") -> str:
        """
        Render a React component server-side.

        Args:
            name: Component name
            props: Component props as dictionary
            children: Rendered children content

        Returns:
            HTML string with data attributes for hydration
        """
        renderer = self.get(name)

        if renderer:
            # Call custom renderer
            content = renderer(props, children)
        else:
            # Default fallback renderer
            content = children

        # Wrap in container with hydration data
        module_info = self.get_module_info(name)
        props_json = json.dumps(props).replace('"', "&quot;")

        html = f'<div data-react-component="{name}" data-react-props="{props_json}"'

        if module_info:
            html += f' data-react-module="{module_info["module"]}"'
            html += f' data-react-export="{module_info["export"]}"'

        html += f">{content}</div>"

        return html

    def get_all_modules(self) -> Dict[str, Dict[str, str]]:
        """Get all registered component modules for client-side bundle."""
        return self._component_modules.copy()


# Global registry instance
react_components = ReactComponentRegistry()


def register_react_component(
    name: str, module_path: Optional[str] = None, component_name: Optional[str] = None
):
    """
    Convenience function to register React components.

    Example:
        @register_react_component("Button", module_path="./Button.jsx")
        def render_button(props, children):
            return f'<button>{children}</button>'
    """
    return react_components.register(name, module_path, component_name)


class ReactMixin:
    """
    Mixin for LiveView classes to add React component support.

    Add this to your LiveView to enable React components in templates:

    Example:
        class MyView(ReactMixin, LiveView):
            template = '''
                <div>
                    <Button onClick="handleClick">Click me!</Button>
                </div>
            '''
    """

    def get_react_components(self) -> ReactComponentRegistry:
        """Override to provide custom component registry."""
        return react_components

    def render_react_component(self, name: str, props: Dict[str, Any], children: str = "") -> str:
        """Render a React component within this view."""
        return self.get_react_components().render(name, props, children)
