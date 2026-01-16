"""
Base classes for djust components.

Provides Component (stateless) and LiveComponent (stateful) base classes for creating
reusable, reactive components with automatic performance optimization.
"""

from typing import Dict, Any, Optional, Type
from abc import ABC, abstractmethod
from django.utils.safestring import mark_safe


class Component(ABC):
    """
    Base class for stateless presentation components with automatic performance optimization.

    The Component class implements a performance waterfall that automatically selects
    the fastest available rendering method:

    1. Pure Rust implementation (if available) → ~1μs per render (fastest)
    2. template with Rust rendering → ~5-10μs per render (fast)
    3. _render_custom() Python method → ~50-100μs per render (flexible)

    This unified design allows components to start simple (Python) and be optimized
    incrementally (hybrid → Rust) without changing the API.

    Usage - Hybrid (Recommended):
        class Badge(Component):
            # Use Rust template rendering (10x faster than Python)
            template = '<span class="badge bg-{{ variant }}">{{ text }}</span>'

            def __init__(self, text: str, variant: str = "primary"):
                super().__init__(text=text, variant=variant)
                self.text = text
                self.variant = variant

            def get_context_data(self) -> dict:
                return {'text': self.text, 'variant': self.variant}

    Usage - Pure Python (Maximum Flexibility):
        class ComplexCard(Component):
            def __init__(self, data: dict):
                super().__init__(data=data)
                self.data = data

            def _render_custom(self) -> str:
                # Complex Python logic
                framework = config.get('css_framework')
                if framework == 'bootstrap5':
                    return self._render_bootstrap()
                elif framework == 'tailwind':
                    return self._render_tailwind()
                else:
                    return self._render_plain()

    Usage - Rust Optimized (Maximum Performance):
        from djust._rust import RustBadge

        class Badge(Component):
            # Link to Rust implementation (used if available)
            _rust_impl_class = RustBadge

            # Fallback to hybrid
            template = '<span class="badge bg-{{ variant }}">{{ text }}</span>'

            def __init__(self, text: str, variant: str = "primary"):
                super().__init__(text=text, variant=variant)
                self.text = text
                self.variant = variant

    Key Features:
        - Automatic performance optimization
        - Graceful degradation (Rust → Hybrid → Python)
        - Single consistent API
        - Zero overhead (no runtime detection)
        - Framework-agnostic

    Attributes:
        _rust_impl_class: Optional Rust implementation class
        template: Optional template for hybrid rendering
    """

    # Class attribute: Optional Rust implementation
    _rust_impl_class: Optional[Type] = None

    # Class attribute: Optional template string for hybrid rendering
    template: Optional[str] = None

    # Class-level counter for auto-generating component keys
    _component_counter = 0

    def __init__(self, _component_key: Optional[str] = None, id: Optional[str] = None, **kwargs):
        """
        Initialize component.

        If Rust implementation exists (_rust_impl_class), creates Rust instance.
        Otherwise, stores kwargs for Python/hybrid rendering.

        Args:
            _component_key: Optional unique key for VDOM matching (like React key)
            id: Optional explicit ID for the component (used in HTML id attribute)
            **kwargs: Component properties
        """
        self._rust_instance = None

        # Store explicit ID if provided (used by id property)
        self._explicit_id = id

        # Set component key for stable VDOM matching
        if _component_key is not None:
            self._component_key = _component_key
        else:
            # Auto-generate key based on component type + counter
            Component._component_counter += 1
            self._component_key = f"{self.__class__.__name__}_{Component._component_counter}"

        # Try to create Rust instance if implementation exists
        if self._rust_impl_class is not None:
            try:
                # Import config here to avoid circular imports
                from djust.config import config

                # Pass framework from config to Rust (if Rust component supports it)
                framework = config.get("css_framework", "bootstrap5")

                # Try to create Rust instance with framework
                try:
                    self._rust_instance = self._rust_impl_class(**kwargs, framework=framework)
                except TypeError:
                    # Rust component doesn't accept framework, try without it
                    self._rust_instance = self._rust_impl_class(**kwargs)

            except Exception:
                # Fall back to Python/hybrid implementation
                # Silently fail - Rust might not be built, which is OK
                self._rust_instance = None

        # Store kwargs as attributes for Python/hybrid rendering
        if self._rust_instance is None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def update(self, **kwargs) -> "Component":
        """
        Update component properties after initialization.

        For Rust-backed components, creates a new Rust instance with updated properties.
        For Python/hybrid components, updates instance attributes.

        This allows in-place component updates without recreating the component instance,
        which is important for VDOM stability.

        Args:
            **kwargs: Properties to update

        Returns:
            self (for method chaining)

        Example:
            # In a LiveView event handler
            def toggle_switch(self):
                self.switch_enabled = not self.switch_enabled
                # Update the component in-place
                self.switch_component.update(checked=self.switch_enabled)
        """
        # Update Rust instance if exists
        if self._rust_impl_class is not None:
            try:
                from djust.config import config

                framework = config.get("css_framework", "bootstrap5")

                # Get current properties by inspecting instance attributes
                current_props = {}
                for key, value in self.__dict__.items():
                    if not key.startswith("_"):
                        current_props[key] = value

                # CRITICAL: Include the 'id' property value (it's a property, not in __dict__)
                # This ensures Rust instance is created with the correct ID
                if hasattr(self, "id"):
                    current_props["id"] = self.id

                # Merge with updates
                current_props.update(kwargs)

                # Recreate Rust instance with updated properties
                try:
                    self._rust_instance = self._rust_impl_class(
                        **current_props, framework=framework
                    )
                except TypeError:
                    self._rust_instance = self._rust_impl_class(**current_props)

            except Exception:
                # Fall back to Python/hybrid - just update attributes
                self._rust_instance = None
                for key, value in kwargs.items():
                    setattr(self, key, value)
        else:
            # Python/hybrid component - update attributes directly
            for key, value in kwargs.items():
                setattr(self, key, value)

        return self

    @property
    def id(self) -> str:
        """
        Compute component ID using waterfall approach:
        1. Explicit id parameter if provided
        2. _auto_id if set by LiveView (e.g., "navbar_example")
        3. Class name as default (e.g., "navbar", "tabs")

        This provides stable, deterministic IDs for HTTP-only mode while
        supporting explicit IDs when needed.

        Returns:
            Component ID string

        Example:
            # In LiveView:
            self.navbar_example = NavBar(...)
            # → navbar_example.id = "navbar-navbar_example"

            # With explicit ID:
            NavBar(id="main-nav")
            # → id = "main-nav"
        """
        if self._explicit_id:
            return self._explicit_id
        elif hasattr(self, "_auto_id"):
            return f"{self.__class__.__name__.lower()}-{self._auto_id}"
        else:
            return self.__class__.__name__.lower()

    def render(self) -> str:
        """
        Render component using fastest available method.

        Performance waterfall:
        1. Rust implementation (fastest: ~1μs)
        2. template with Rust rendering (fast: ~5-10μs)
        3. _render_custom() override (flexible: ~50-100μs)

        Returns:
            HTML string marked as safe for Django templates

        Raises:
            NotImplementedError: If no rendering method is available

        Note:
            When writing template, avoid using {% elif %} due to a known bug
            in the Rust template engine. Use separate {% if %} blocks instead.
        """
        # 1. Try pure Rust implementation (fastest)
        if self._rust_instance is not None:
            return mark_safe(self._rust_instance.render())

        # 2. Try hybrid: template with Rust rendering (fast)
        if self.template is not None:
            try:
                from djust._rust import render_template

                # Get context and inject component key
                context = self.get_context_data()
                context["_component_key"] = self._component_key
                return mark_safe(render_template(self.template, context))
            except (ImportError, AttributeError):
                # Rust not available, fall back to Django template rendering
                from django.template import Context, Template

                template = Template(self.template)
                # Get context and inject component key
                context_data = self.get_context_data()
                context_data["_component_key"] = self._component_key
                context = Context(context_data)
                return mark_safe(template.render(context))

        # 3. Fall back to custom Python rendering (flexible)
        return mark_safe(self._render_custom())

    def get_context_data(self) -> Dict[str, Any]:
        """
        Override to provide template context for hybrid rendering.

        Note: The component key is automatically injected as '_component_key' by the
        render() method, so you don't need to include it here. It's available in
        templates for optional use (e.g., data-component-key="{{ _component_key }}").

        Returns:
            Dictionary of template variables

        Example:
            def get_context_data(self):
                return {
                    'text': self.text,
                    'variant': self.variant,
                    'size': self.size,
                }
        """
        return {}

    def _render_custom(self) -> str:
        """
        Override for custom Python rendering.

        Only called if no Rust implementation and no template.

        Returns:
            HTML string

        Raises:
            NotImplementedError: If method not overridden and no other render method

        Example:
            def _render_custom(self):
                framework = config.get('css_framework')
                if framework == 'bootstrap5':
                    return f'<span class="badge bg-{self.variant}">{self.text}</span>'
                elif framework == 'tailwind':
                    return f'<span class="rounded px-2 py-1">{self.text}</span>'
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must define either:\n"
            f"  - _rust_impl_class (for pure Rust)\n"
            f"  - template (for hybrid rendering)\n"
            f"  - _render_custom() method (for custom Python)"
        )

    def __str__(self):
        """Allow {{ component }} in templates to render automatically"""
        return self.render()


class LiveComponent(ABC):
    """
    Base class for creating reusable, reactive components.

    Components are self-contained UI elements with their own state and event handlers.
    They can be embedded in LiveViews or other components.

    Automatic Component ID Management:
        Components automatically receive a stable `component_id` based on the attribute
        name used when assigning them in your LiveView. This eliminates manual ID management.

        Example:
            class MyView(LiveView):
                def mount(self, request):
                    # component_id is automatically set to "alert_success"
                    self.alert_success = AlertComponent(
                        message="Success!",
                        type="success"
                    )

        The framework automatically:
        1. Sets component.component_id = "alert_success" (the attribute name)
        2. Persists this ID across renders and WebSocket events
        3. Includes it in HTML as data-component-id="alert_success"
        4. Routes events back to the correct component instance

        This means you can reference components by their attribute names in event handlers:
            def dismiss(self, component_id: str = None):
                if component_id and hasattr(self, component_id):
                    getattr(self, component_id).dismiss()

    Usage:
        class AlertComponent(LiveComponent):
            template_name = 'components/alert.html'

            def mount(self, **kwargs):
                self.message = kwargs.get('message', '')
                self.type = kwargs.get('type', 'info')
                self.visible = True

            def dismiss(self):
                self.visible = False

            def get_context(self):
                return {
                    'message': self.message,
                    'type': self.type,
                    'visible': self.visible,
                }

        # In template:
        {{ alert.render }}
    """

    # Component configuration
    template_name: Optional[str] = None
    component_id: Optional[str] = None

    def __init__(self, component_id: Optional[str] = None, **kwargs):
        """
        Initialize component.

        Args:
            component_id: Unique identifier for this component instance
            **kwargs: Component initialization parameters
        """
        self.component_id = component_id or self._generate_id()
        self._mounted = False
        self._parent = None

        # Mount with provided kwargs
        self.mount(**kwargs)
        self._mounted = True

    def _generate_id(self) -> str:
        """Generate a unique component ID"""
        import uuid

        return f"{self.__class__.__name__.lower()}_{uuid.uuid4().hex[:8]}"

    @abstractmethod
    def mount(self, **kwargs):
        """
        Initialize component state.

        This method is called when the component is created.
        Override to set up initial state.

        Args:
            **kwargs: Initialization parameters
        """
        pass

    @abstractmethod
    def get_context(self) -> Dict[str, Any]:
        """
        Get template context for rendering.

        Returns:
            Dictionary of context variables

        Example:
            return {
                'title': self.title,
                'items': self.items,
                'count': len(self.items),
            }
        """
        pass

    def render(self) -> str:
        """
        Render the component to HTML.

        Returns:
            HTML string (marked as safe for Django templates)

        Raises:
            ValueError: If template_name is not set
        """
        if not self.template_name:
            raise ValueError(f"{self.__class__.__name__} must set template_name")

        from django.template.loader import render_to_string
        from django.utils.safestring import mark_safe

        context = self.get_context()
        context["component_id"] = self.component_id

        return mark_safe(render_to_string(self.template_name, context))

    def set_parent(self, parent):
        """
        Set the parent LiveView for this component.

        Args:
            parent: Parent LiveView instance
        """
        self._parent = parent

    def trigger_update(self):
        """
        Trigger a re-render of the parent LiveView.

        This notifies the parent that the component state has changed
        and the view should be re-rendered.
        """
        if self._parent and hasattr(self._parent, "_trigger_update"):
            self._parent._trigger_update()

    def __str__(self):
        """Allow {{ component }} in templates and JSON serialization"""
        return self.render()
