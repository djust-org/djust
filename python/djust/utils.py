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


def emit_one_shot_class_warning(cls: type, key: str, message: str, *args: Any) -> None:
    """Emit a logger.warning at most once per class for the given key.

    Reusable framework pattern for "framework can't help mechanically,
    tell the developer loudly". Sets a class-level sentinel attr
    ``_djust_warned_<key>`` so subsequent instances of the same class
    don't repeat the warning. Subclasses get their own sentinel via
    normal Python attribute lookup (each subclass is a distinct ``cls``),
    and the sentinel survives module reload (HVR) because the class
    object outlives the module reload.

    Use cases: snapshot truncation thresholds, missing get_object()
    on detail-view-shaped classes, deprecated decorator usage.

    Pattern from PR #1326 (snapshot truncation), canonicalized #1392.

    Args:
        cls: The class to attach the sentinel to (typically
            ``type(view_instance)``).
        key: Short identifier for the warning category (used to build the
            sentinel attr name); use ``snake_case`` and keep it stable.
        message: A ``%s``-style log format string (per djust logging rules).
        *args: Substitution args for the format string.
    """
    sentinel = f"_djust_warned_{key}"
    # Use cls.__dict__ (not getattr) so subclasses get their own sentinel
    # instead of inheriting the parent's "already warned" state.
    if cls.__dict__.get(sentinel, False):
        return
    setattr(cls, sentinel, True)
    logger.warning(message, *args)


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
        top_level_aliases: Mapping of top-level Django setting names
            (e.g. ``"DJUST_STATE_BACKEND"``) to the ``DJUST_CONFIG`` key
            they alias (e.g. ``"STATE_BACKEND"``). When the
            ``DJUST_CONFIG`` key is absent and the top-level setting is
            present, the top-level value is merged into the config dict
            before the factory runs (#1354). URL-shaped values for the
            primary ``config_key`` (``redis://``, ``rediss://``) are
            translated to ``backend_type="redis"`` and the URL is also
            stored under the ``REDIS_URL`` config key.
    """

    def __init__(
        self,
        config_key: str,
        default_type: str,
        factory: Callable[[str, dict], Any],
        name: str = "backend",
        top_level_aliases: Optional[dict] = None,
    ):
        self._config_key = config_key
        self._default_type = default_type
        self._factory = factory
        self._name = name
        self._top_level_aliases = top_level_aliases or {}
        self._backend: Optional[Any] = None

    # URL schemes that should auto-resolve to ``backend_type="redis"``
    # when the user sets a top-level URL-shaped value via
    # ``DJUST_STATE_BACKEND``. Covered cases:
    #
    # - ``redis://``           — plain TCP, the common case
    # - ``rediss://``          — TLS-wrapped Redis
    # - ``redis+sentinel://``  — Sentinel HA (common in production)
    #
    # ``unix://`` is intentionally left out for now because the
    # downstream Redis client library accepts Unix sockets via a
    # different parameter name (``unix_socket_path`` rather than
    # ``redis_url``); supporting it cleanly is a larger change than
    # this fix can justify. Users hitting that path can still set
    # ``DJUST_CONFIG["STATE_BACKEND"] = "redis"`` plus the appropriate
    # connection kwargs explicitly. TODO: widen to ``unix://`` and
    # other forms in a follow-up issue.
    _REDIS_URL_PREFIXES = ("redis://", "rediss://", "redis+sentinel://")

    def _merge_top_level_aliases(self, cfg: dict) -> dict:
        """Layer top-level Django settings on top of the cfg dict (#1354).

        Top-level settings only fill in keys that are NOT already in
        ``DJUST_CONFIG`` — backwards-compatible. URL-shaped values for the
        primary ``config_key`` are split into ``(backend_type="redis",
        REDIS_URL=<url>)``. Recognized URL schemes are listed in
        :attr:`_REDIS_URL_PREFIXES`. Unrecognized schemes (e.g.
        typos like ``redis:/host``) fall through to the factory which
        raises a clear ``Unknown backend type: <scheme>`` error.
        """
        if not self._top_level_aliases:
            return cfg

        try:
            from django.conf import settings
        except Exception:
            return cfg

        # Copy so we don't mutate the dict returned by get_djust_config.
        merged = dict(cfg)
        for setting_name, config_key in self._top_level_aliases.items():
            if config_key in merged:
                continue  # DJUST_CONFIG wins
            if not hasattr(settings, setting_name):
                continue
            value = getattr(settings, setting_name)
            if value is None:
                continue
            # URL-shaped value for the primary backend-selector key:
            # translate to (type="redis", REDIS_URL=<value>).
            if (
                config_key == self._config_key
                and isinstance(value, str)
                and value.startswith(self._REDIS_URL_PREFIXES)
            ):
                merged[self._config_key] = "redis"
                merged.setdefault("REDIS_URL", value)
            else:
                merged[config_key] = value
        return merged

    def get(self) -> Any:
        """Return the cached backend, creating it on first call."""
        if self._backend is not None:
            return self._backend

        from .config import get_djust_config

        cfg = get_djust_config()
        cfg = self._merge_top_level_aliases(cfg)
        backend_type = cfg.get(self._config_key, self._default_type)

        # Warn when production deploy silently falls back to the default
        # (typically in-memory) — common misconfig surfaced by #1354 where
        # NYC Claims set ``DJUST_STATE_BACKEND`` as a top-level setting but
        # the registry only honoured ``DJUST_CONFIG["STATE_BACKEND"]``,
        # silently downgrading to in-memory in production. Now both forms
        # are honoured (above), but if neither is set we still want a
        # production-mode warning.
        try:
            from django.conf import settings as _dj_settings

            _debug = getattr(_dj_settings, "DEBUG", True)
        except Exception:
            _debug = True
        if not _debug and backend_type == self._default_type:
            logger.warning(
                "Falling back to in-memory %s backend in production "
                "(DEBUG=False) — multi-process deployments will lose %s "
                "across replicas. Set DJUST_CONFIG[%r] or top-level "
                "settings.DJUST_%s to configure a shared backend.",
                self._name,
                self._name,
                self._config_key,
                self._config_key,
            )

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
