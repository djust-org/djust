"""
ModelBindingMixin — Server-side support for dj-model two-way data binding.

Provides a default `update_model` event handler that sets attributes on the
view instance when the client sends dj-model changes.
"""

import logging
from typing import Any

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

    def update_model(self, field: str = "", value: Any = None, **kwargs):
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
            logger.warning(f"[dj-model] Blocked attempt to set private field: {field}")
            return

        if field in FORBIDDEN_MODEL_FIELDS:
            logger.warning(f"[dj-model] Blocked attempt to set forbidden field: {field}")
            return

        if self.allowed_model_fields is not None:
            if field not in self.allowed_model_fields:
                logger.warning(f"[dj-model] Field '{field}' not in allowed_model_fields")
                return

        # Only update existing attributes (don't create new ones)
        if not hasattr(self, field):
            logger.warning(
                f"[dj-model] Field '{field}' does not exist on " f"{self.__class__.__name__}"
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
                    f"[dj-model] Could not coerce '{value}' to "
                    f"{type(current).__name__} for field '{field}'"
                )
                return

        setattr(self, field, value)
        logger.debug(f"[dj-model] Set {self.__class__.__name__}.{field} = {value!r}")
