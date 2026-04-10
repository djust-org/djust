"""
Utility functions for djust.
"""

import logging
from functools import lru_cache
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def is_model_list(value: Any) -> bool:
    """Check if value is a non-empty list of Django Model instances."""
    from django.db import models

    return isinstance(value, list) and len(value) > 0 and isinstance(value[0], models.Model)


def get_csp_nonce(request: Any) -> str:
    """
    Extract the CSP nonce from a Django request, if one is set.

    Returns the value of ``request.csp_nonce`` (set by ``django-csp``'s
    middleware when ``CSP_INCLUDE_NONCE_IN`` covers the relevant directive),
    or an empty string when:

      * ``request`` is ``None`` (call site doesn't have one, e.g. management
        command context or a unit test);
      * ``request`` is a dict (this happens when a djust template tag takes
        a context and the context isn't a full ``RequestContext``);
      * ``django-csp`` is not installed or not configured;
      * the attribute simply isn't set.

    The empty-string fallback is the key backward-compatibility contract:
    callers that format ``nonce="{nonce}"`` into their output get
    ``nonce=""`` when no nonce is available, which is equivalent to no
    nonce attribute at all under CSP's matching rules. Callers that want
    to skip the attribute entirely should check ``if nonce:`` first.

    See issue #655 (nonce-based CSP support).

    Args:
        request: A Django ``HttpRequest``, a template ``RequestContext``
            object with a ``request`` attribute, or ``None``.

    Returns:
        The nonce string, or ``""`` when no nonce is available.
    """
    if request is None:
        return ""
    # Support callers that pass a template Context instead of a request
    inner = getattr(request, "request", None)
    if inner is not None and hasattr(inner, "csp_nonce"):
        return str(getattr(inner, "csp_nonce", "") or "")
    return str(getattr(request, "csp_nonce", "") or "")


class BackendRegistry:
    """
    Generic singleton-style registry for lazily-initialised backends.

    Both ``state_backends.registry`` and ``backends.registry`` (presence)
    follow the same pattern: a module-level ``_backend`` variable, a
    ``get_backend()`` that reads config and instantiates on first call,
    ``set_backend()`` and ``reset_backend()``.  This class captures that
    pattern once.

    Args:
        config_key: The key inside ``DJUST_CONFIG`` that selects the
            backend type (e.g. ``"STATE_BACKEND"``, ``"PRESENCE_BACKEND"``).
        default_type: Value returned when the config key is absent
            (e.g. ``"memory"``).
        factory: A callable ``(backend_type: str, config: dict) -> backend``
            responsible for instantiating the concrete backend.
        name: Human-readable name for log messages (e.g. ``"state"``).
    """

    def __init__(
        self,
        config_key: str,
        default_type: str,
        factory: Callable[[str, dict], Any],
        name: str = "backend",
    ):
        self._config_key = config_key
        self._default_type = default_type
        self._factory = factory
        self._name = name
        self._backend: Optional[Any] = None

    def get(self) -> Any:
        """Return the cached backend, creating it on first call."""
        if self._backend is not None:
            return self._backend

        from .config import get_djust_config

        cfg = get_djust_config()
        backend_type = cfg.get(self._config_key, self._default_type)

        self._backend = self._factory(backend_type, cfg)
        logger.info("Initialized %s backend: %s", self._name, backend_type)
        return self._backend

    def set(self, backend: Any) -> None:
        """Manually set the backend (useful for testing)."""
        self._backend = backend

    def reset(self) -> None:
        """Reset to force re-initialisation on next access."""
        self._backend = None


@lru_cache(maxsize=1)
def _get_template_dirs_cached() -> tuple[str, ...]:
    """
    Internal cached implementation.

    Reads from settings.TEMPLATES directly for compatibility with tests
    that modify settings. Django's template.engines singleton doesn't
    reflect settings changes after first access.
    """
    from django.conf import settings
    from pathlib import Path

    template_dirs = []

    # Step 1: Add DIRS from all TEMPLATES configs
    for template_config in settings.TEMPLATES:
        if "DIRS" in template_config:
            template_dirs.extend(template_config["DIRS"])

    # Step 2: Add app template directories (only for DjangoTemplates with APP_DIRS=True)
    for template_config in settings.TEMPLATES:
        if template_config["BACKEND"] == "django.template.backends.django.DjangoTemplates":
            if template_config.get("APP_DIRS", False):
                from django.apps import apps

                for app_config in apps.get_app_configs():
                    templates_dir = Path(app_config.path) / "templates"
                    if templates_dir.exists():
                        template_dirs.append(str(templates_dir))

    return tuple(str(d) for d in template_dirs)


def get_template_dirs() -> list[str]:
    """
    Get template directories from Django settings in search order.

    Returns list of template directory paths in Django's search order:
    1. DIRS from each TEMPLATES config (in order)
    2. APP_DIRS (if enabled) - searches app templates in app order

    Used internally for {% include %} tag support in Rust rendering.

    Note: Results are cached for performance. In production, template
    directories don't change at runtime so this is safe. Call
    clear_template_dirs_cache() if you need to refresh the cache.
    """
    return list(_get_template_dirs_cached())


def clear_template_dirs_cache() -> None:
    """
    Clear the template directories cache.

    Call this if you dynamically modify TEMPLATES settings and need
    the changes to be reflected in template rendering.

    Note: This is rarely needed in production since template directories
    typically don't change at runtime.
    """
    _get_template_dirs_cached.cache_clear()
