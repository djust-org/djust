"""
Base classes for djust components.

Provides Component (stateless) and LiveComponent (stateful) base classes for creating
reusable, reactive components with automatic performance optimization.
"""

from typing import Dict, Any, Optional, Type
from abc import ABC
from django.utils.safestring import mark_safe


def _render_template_with_fallback(template_str: str, context: Dict[str, Any]) -> str:
    """
    Render a template string with Rust acceleration, falling back to Django templates.

    Tries Rust template rendering first for performance. Falls back to Django's
    Template engine if Rust is unavailable or encounters an error (e.g., for
    {% include %} tags that Rust doesn't support).

    Args:
        template_str: Template string to render
        context: Context dictionary for template variables

    Returns:
        Rendered HTML string (not marked as safe - caller should mark_safe if needed)
    """
    try:
        from djust._rust import render_template

        return render_template(template_str, context)
    except (ImportError, AttributeError, RuntimeError):
        # Rust not available or template error, fall back to Django templates
        from django.template import Context, Template

        template = Template(template_str)
        django_context = Context(context)
        return template.render(django_context)


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

    def _create_rust_instance(self, **props) -> None:
        """
        Create a Rust instance with fallback for missing framework parameter.

        Attempts to create a Rust component instance with the configured CSS
        framework. Falls back to creation without framework if the Rust
        component doesn't accept that parameter.

        Args:
            **props: Properties to pass to the Rust constructor
        """
        if self._rust_impl_class is None:
            return

        try:
            from djust.config import config

            framework = config.get("css_framework", "bootstrap5")
            try:
                self._rust_instance = self._rust_impl_class(**props, framework=framework)
            except TypeError:
                # Rust component doesn't accept framework parameter
                self._rust_instance = self._rust_impl_class(**props)
        except Exception:
            # Fall back to Python/hybrid implementation
            self._rust_instance = None

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
        self._create_rust_instance(**kwargs)

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
            self._create_rust_instance(**current_props)

            # If Rust instance creation failed, fall back to Python/hybrid
            if self._rust_instance is None:
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

        # 2. Try hybrid: template with Rust rendering (fast, with Django fallback)
        if self.template is not None:
            context = self.get_context_data()
            context["_component_key"] = self._component_key
            return mark_safe(_render_template_with_fallback(self.template, context))

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


class LiveComponent:
    """
    Base class for creating reusable, reactive components.

    Components are self-contained UI elements with their own state and event handlers.
    They can be declared as class attributes on LiveViews (descriptor pattern) or
    instantiated in mount().

    Descriptor Pattern (preferred)::

        class Accordion(LiveComponent):
            class State(TypedState):
                active: str = ""
                multiple: bool = False

            class Meta:
                event = "accordion_toggle"

            def toggle(self, state, value="", **kwargs):
                state.active = "" if state.active == value else value

        class MyView(LiveView):
            faq = Accordion(active="q1")
            settings = Accordion()

            # self.faq.active → "q1" (typed, IDE autocomplete)
            # accordion_toggle handler auto-registered, routed by component_id

    Legacy Pattern (still supported)::

        class AlertComponent(LiveComponent):
            template_name = 'components/alert.html'

            def mount(self, **kwargs):
                self.message = kwargs.get('message', '')

            def get_context_data(self):
                return {'message': self.message}

        class MyView(LiveView):
            def mount(self, request):
                self.alert = AlertComponent(message="Success!")

    Descriptor Protocol:
        When declared as a class attribute, LiveComponent acts as a Python descriptor:
        - ``__set_name__``: registers component in ``_component_descriptors`` on the owner class,
          auto-registers event handlers
        - ``__get__``: returns the component's State (a TypedState dict subclass) for the instance
        - ``__set__``: accepts a plain dict and converts to the State class

        The attribute name becomes the component_id. State is stored in
        ``obj.__dict__["_component_{name}"]`` (underscore prefix excludes it from
        djust's context pipeline; the public attribute via ``__get__`` is included).
    """

    # Component configuration
    template_name: Optional[str] = None
    template: Optional[str] = None  # Inline template string
    component_id: Optional[str] = None

    # ── Descriptor support ──

    def __init__(self, component_id: Optional[str] = None, **kwargs):
        """
        Initialize component.

        When used as a descriptor (class attribute), kwargs are stored as defaults
        and state is created lazily on first access. When instantiated directly
        (legacy pattern), mount() is called immediately.

        Args:
            component_id: Unique identifier for this component instance
            **kwargs: Component initialization parameters or state defaults
        """
        # Check if this is being used as a descriptor (no owner yet)
        # vs direct instantiation (legacy pattern)
        self._descriptor_defaults = kwargs
        self._descriptor_attr_name = None
        self._descriptor_storage_key = None

        if component_id is not None or not kwargs:
            # Legacy instantiation path — mount immediately
            self.component_id = component_id or self._generate_id()
            self._mounted = False
            self._parent = None
            self._parent_callback = None
            if hasattr(self, "mount") and callable(self.mount):
                self.mount(**kwargs)
            self._mounted = True
        else:
            # Descriptor path — defer mounting, store defaults
            self._mounted = False
            self._parent = None
            self._parent_callback = None

    def __set_name__(self, owner, name):
        """Called when this component is assigned as a class attribute.

        Registers the component in the owner's ``_component_descriptors`` dict
        and auto-registers event handlers if defined in ``Meta.event``.
        """
        self._descriptor_attr_name = name
        self._descriptor_storage_key = f"_component_{name}"

        # Build class-level registry
        if not hasattr(owner, "_component_descriptors"):
            owner._component_descriptors = {}
        # Copy to avoid sharing across subclasses
        elif "_component_descriptors" not in owner.__dict__:
            owner._component_descriptors = dict(owner._component_descriptors)
        owner._component_descriptors[name] = self

        # Auto-register event handler on the owner class
        meta = getattr(self.__class__, "Meta", None)
        event_name = getattr(meta, "event", None)
        if event_name and not hasattr(owner, event_name):
            setattr(owner, event_name, self._make_event_handler(event_name))

    def __get__(self, obj, objtype=None):
        """Return the component's State for this view instance.

        On first access, creates the State with defaults. On subsequent access,
        returns the cached State. After djust deserialization (state becomes a
        plain dict), rehydrates it back to the State class.
        """
        if obj is None:
            return self  # Class-level access returns the descriptor

        state_cls = getattr(self.__class__, "State", None)
        if state_cls is None:
            # No State inner class — legacy component, return self
            return self

        state = obj.__dict__.get(self._descriptor_storage_key)
        if state is None:
            # First access — create State with defaults
            state = state_cls(**self._descriptor_defaults)
            state["component_id"] = self._descriptor_attr_name
            obj.__dict__[self._descriptor_storage_key] = state
        elif isinstance(state, dict) and not isinstance(state, state_cls):
            # Rehydrate from plain dict after djust deserialization
            state = state_cls.from_dict(state)
            state["component_id"] = self._descriptor_attr_name
            obj.__dict__[self._descriptor_storage_key] = state
        return state

    def __set__(self, obj, value):
        """Accept a plain dict and convert to the component's State class."""
        state_cls = getattr(self.__class__, "State", None)
        if state_cls is not None and isinstance(value, dict) and not isinstance(value, state_cls):
            value = state_cls.from_dict(value)
            if self._descriptor_attr_name:
                value["component_id"] = self._descriptor_attr_name
        if self._descriptor_storage_key:
            obj.__dict__[self._descriptor_storage_key] = value
        else:
            # Legacy path — direct attribute set
            obj.__dict__[self._descriptor_attr_name or "component"] = value

    def _make_event_handler(self, event_name):
        """Create an event handler that routes to the correct component instance."""
        component_type = type(self)

        def handler(view_self, value="", component_id="", **kwargs):
            # Auto-resolve if only one instance of this component type
            if not component_id:
                descriptors = getattr(type(view_self), "_component_descriptors", {})
                matches = [n for n, d in descriptors.items() if isinstance(d, component_type)]
                if len(matches) == 1:
                    component_id = matches[0]
            if not component_id:
                return

            state = getattr(view_self, component_id, None)
            if state is None:
                return

            # Find the component's action method (e.g., toggle, set, open, close)
            # Convention: the first non-private, non-dunder method that isn't
            # mount/render/get_context_data is the action
            descriptor = getattr(type(view_self), component_id, None)
            if descriptor and hasattr(descriptor, "_handle_event"):
                descriptor._handle_event(state, value=value, **kwargs)

        # Preserve the event name for djust dispatch
        handler.__name__ = event_name
        handler.__qualname__ = event_name

        # Mark as event_handler if the decorator is available
        try:
            from djust.decorators import event_handler as eh_decorator

            handler = eh_decorator(handler)
        except ImportError:
            pass

        return handler

    def _handle_event(self, state, **kwargs):
        """Override in subclasses to handle events.

        Args:
            state: The TypedState instance for this component
            **kwargs: Event parameters (value, etc.)
        """
        pass

    def _generate_id(self) -> str:
        """Generate a unique component ID"""
        import uuid

        return f"{self.__class__.__name__.lower()}_{uuid.uuid4().hex[:8]}"

    def mount(self, **kwargs):
        """
        Initialize component state.

        Override to set up initial state. Optional when using the descriptor
        pattern with a State inner class.

        Args:
            **kwargs: Initialization parameters
        """
        pass

    def get_context_data(self) -> Dict[str, Any]:
        """
        Get template context for rendering.

        Returns:
            Dictionary of context variables.  Optional when using the descriptor
            pattern — the State dict is used directly.
        """
        return {}

    def render(self) -> str:
        """
        Render the component to HTML.

        Returns:
            HTML string (marked as safe for Django templates)

        Raises:
            ValueError: If template or template_name is not set
            RuntimeError: If component has been unmounted
        """
        if not self._mounted:
            raise RuntimeError("Cannot render unmounted component")

        from django.utils.safestring import mark_safe

        context = self.get_context_data()
        context["component_id"] = self.component_id

        # Use inline template if available (with Rust acceleration and Django fallback)
        if self.template:
            from django.utils.html import format_html

            html = _render_template_with_fallback(self.template, context)
            # Wrap with component ID for LiveComponent tracking (html is already safe from template engine)
            return format_html(
                '<div data-component-id="{}">{}</div>', self.component_id, mark_safe(html)
            )

        # Fall back to template_name (file-based template)
        if self.template_name:
            from django.template.loader import render_to_string

            return mark_safe(render_to_string(self.template_name, context))

        raise ValueError(
            f"{self.__class__.__name__} must define 'template' attribute or set 'template_name'"
        )

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

    def _set_parent_callback(self, callback):
        """
        Set the callback function for communicating with parent LiveView.

        Args:
            callback: Function to call when sending events to parent
        """
        self._parent_callback = callback

    def send_parent(self, event: str, data: Optional[Dict[str, Any]] = None):
        """
        Send an event to the parent LiveView.

        Args:
            event: Event name
            data: Optional event data dictionary
        """
        if self._parent_callback:
            self._parent_callback(
                {
                    "component_id": self.component_id,
                    "event": event,
                    "data": data or {},
                }
            )

    def unmount(self):
        """
        Clean up component when it's being removed.

        Override this method to perform cleanup actions.
        """
        self._mounted = False
        self._parent_callback = None

    def __str__(self):
        """Allow {{ component }} in templates and JSON serialization"""
        return self.render()
