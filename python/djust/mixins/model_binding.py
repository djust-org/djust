"""
ModelBindingMixin — Server-side support for dj-model two-way data binding.

Provides a default `update_model` event handler that sets attributes on the
view instance when the client sends dj-model changes.
"""

import logging

try:
    from ..decorators import event_handler
except (ImportError, SystemError):
    # Fallback for direct-file imports (e.g. test_model_binding.py)
    def event_handler(fn=None, **kw):  # type: ignore[misc]
        return fn if fn is not None else (lambda f: f)


logger = logging.getLogger(__name__)

# Attributes that cannot be set via dj-model for security
FORBIDDEN_MODEL_FIELDS = frozenset(
    {
        "template_name",
        "request",
        "kwargs",
        "args",
        "session",
        "use_actors",
        "temporary_assigns",
        "_handler_metadata",
        "_rust_view",
        "_session_id",
        "_channel_name",
        "_view_path",
    }
)


class ModelBindingMixin:
    """
    Provides automatic `update_model` handler for dj-model bindings.

    Any LiveView attribute can be updated via dj-model unless it's in
    FORBIDDEN_MODEL_FIELDS or starts with underscore.

    Usage in template:
        <input type="text" dj-model="search_query">

    The view just needs the attribute defined:
        class MyView(LiveView):
            search_query = ""
    """

    # Optional: subclasses can restrict which fields are bindable
    allowed_model_fields = None  # None = all non-forbidden fields allowed

    @event_handler
    def update_model(self, field: str = "", value=None, **kwargs):
        """
        Default handler for dj-model changes from the client.

        Args:
            field: The attribute name to update
            value: The new value
        """
        if not field or not isinstance(field, str):
            logger.warning("[dj-model] Missing or invalid field name")
            return

        # Security checks
        if field.startswith("_"):
            logger.warning("[dj-model] Blocked attempt to set private field: %s", field)
            return

        if field in FORBIDDEN_MODEL_FIELDS:
            logger.warning("[dj-model] Blocked attempt to set forbidden field: %s", field)
            return

        if self.allowed_model_fields is not None:
            if field not in self.allowed_model_fields:
                logger.warning("[dj-model] Field '%s' not in allowed_model_fields", field)
                return

        # Only update existing attributes (don't create new ones)
        if not hasattr(self, field):
            logger.warning(
                "[dj-model] Field '%s' does not exist on %s", field, self.__class__.__name__
            )
            return

        # Type coercion: try to match the existing attribute's type
        current = getattr(self, field)
        if current is not None and value is not None:
            try:
                # bool check MUST be before int (bool is subclass of int)
                if isinstance(current, bool) and not isinstance(value, bool):
                    value = str(value).lower() in ("true", "1", "yes", "on")
                elif (
                    isinstance(current, int)
                    and not isinstance(current, bool)
                    and not isinstance(value, int)
                ):
                    value = int(value)
                elif isinstance(current, float) and not isinstance(value, float):
                    value = float(value)
            except (ValueError, TypeError):
                logger.warning(
                    "[dj-model] Could not coerce '%s' to %s for field '%s'",
                    value,
                    type(current).__name__,
                    field,
                )
                return

        setattr(self, field, value)
        logger.debug("[dj-model] Set %s.%s = %r", self.__class__.__name__, field, value)

        # Skip re-render: update_model only stores a value on the view
        # instance — no DOM change is needed. Re-rendering would cause a
        # wasteful server round-trip that replaces the DOM and loses focus
        # on the input the user is actively typing in.
        self._skip_render = True
