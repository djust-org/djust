"""
ComponentMixin — base class for per-component interactive mixins.

Provides the instance registry pattern: each mixin manages multiple
instances of the same component type, routed by component_id.

State is stored as TypedState subclasses — dict subclasses with typed
property access.  They serialize as plain dicts through djust's pipeline
while giving IDE autocomplete and type safety.

Mixin state should only contain UI state (which item is open, which tab
is active), never application data or secrets.
"""

__all__ = ["ComponentMixin", "TypedState"]


class TypedState(dict):
    """Dict subclass with typed property access from class annotations.

    Subclasses declare annotated class attributes with defaults::

        class AccordionState(TypedState):
            active: str = ""
            multiple: bool = False

    Instances behave as dicts (JSON-serializable through djust's pipeline)
    while providing typed attribute access::

        state = AccordionState(active="s1")
        state.active        # "s1"  — IDE autocomplete works
        state["active"]     # "s1"  — dict access also works
        state.active = "s2" # sets both the property and the dict key

    **Dirty tracking:** Mutations via ``__setitem__`` set ``_dirty = True``
    when the value actually changes.  The render caching system checks this
    flag to skip re-rendering unchanged components.  ``_cached_html`` is
    cleared on any mutation, and ``_render_hash`` tracks the state hash
    of the last successful render.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name in list(cls.__annotations__):
            if name.startswith("_"):
                continue
            default = getattr(cls, name, None)
            cls._make_property(name, default)

    @classmethod
    def _make_property(cls, name, default):
        def getter(self, _name=name, _default=default):
            return self.get(_name, _default)

        def setter(self, value, _name=name):
            self[_name] = value

        setattr(cls, name, property(getter, setter))

    def __setitem__(self, key, value):
        if not key.startswith("_"):
            old = self.get(key)
            if old != value:
                object.__setattr__(self, "_dirty", True)
                object.__setattr__(self, "_cached_html", None)
        super().__setitem__(key, value)

    def __init__(self, **kwargs):
        super().__init__()
        object.__setattr__(self, "_dirty", True)
        object.__setattr__(self, "_cached_html", None)
        object.__setattr__(self, "_render_hash", None)
        # Set defaults from annotations, then override with kwargs
        for name in type(self).__annotations__:
            if name.startswith("_"):
                continue
            default = getattr(type(self), name, None)
            if isinstance(default, property):
                # Get the actual default from the closure
                self[name] = default.fget(self)
            else:
                self[name] = default
        self.update(kwargs)
        # Clean after initialization — first render will pick up defaults
        object.__setattr__(self, "_dirty", True)

    @classmethod
    def from_dict(cls, d):
        """Rehydrate a plain dict (from djust deserialization) into a TypedState."""
        if isinstance(d, cls):
            return d
        return cls(**d)


class ComponentMixin:
    """Base for per-component interactive mixins.

    Subclasses set ``component_name`` (e.g. ``"accordion"``) and declare
    a class-level ``{name}_instances = None`` attribute.  The base class
    provides helpers to initialise and look up instance state.
    """

    component_name = ""

    def _instances_attr(self):
        """Return the attribute name for this mixin's instance dict."""
        return f"{self.component_name}_instances"

    def _get_instances(self):
        """Return the current instances dict, or empty dict if unset."""
        return getattr(self, self._instances_attr()) or {}

    def _get_instance(self, instance_id):
        """Return state for a single instance, or empty dict."""
        return self._get_instances().get(instance_id, {})

    def _get_typed_instance(self, instance_id, state_class):
        """Return a typed instance, rehydrating from plain dict if needed.

        After djust serializes and deserializes state, TypedState subclasses
        become plain dicts.  This method converts them back and stores the
        rehydrated object so subsequent access is fast.

        When multiple mixins are composed on the same class, ``component_name``
        may resolve to only one of them via the MRO.  To handle this, the
        method first tries the ``component_name``-based dict and, if the
        instance is not found there, scans all ``*_instances`` attributes.
        """
        instances = self._get_instances()
        inst = instances.get(instance_id)
        if inst is None:
            # Fallback: search all *_instances dicts (multi-mixin composition)
            for attr in dir(self):
                if attr.endswith("_instances") and attr != self._instances_attr():
                    other = getattr(self, attr, None)
                    if isinstance(other, dict) and instance_id in other:
                        instances = other
                        inst = other[instance_id]
                        break
            if inst is None:
                return None
        if not isinstance(inst, state_class):
            inst = state_class.from_dict(inst)
            instances[instance_id] = inst
        return inst

    def _resolve_component_id(self, component_id, instances_attr=None):
        """Resolve component_id, falling back to the sole instance if empty.

        When only one instance of a component type is registered and no
        component_id is provided (common when a page has a single accordion,
        modal, etc.), automatically route to that instance rather than
        requiring the caller to pass component_id explicitly.

        Args:
            component_id: The explicitly provided component_id.
            instances_attr: Optional attribute name to use instead of the
                default ``_instances_attr()`` (for multi-mixin correctness).
        """
        if component_id:
            return component_id
        if instances_attr:
            instances = getattr(self, instances_attr, None) or {}
        else:
            instances = self._get_instances()
        if len(instances) == 1:
            return next(iter(instances))
        return component_id

    def _init_instances(self):
        """Initialise the instances dict if it is None.

        Returns the (possibly newly created) instances dict.
        """
        attr = self._instances_attr()
        if getattr(self, attr) is None:
            setattr(self, attr, {})
        return getattr(self, attr)
