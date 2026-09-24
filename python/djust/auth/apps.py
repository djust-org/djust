import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class DjustAuthConfig(AppConfig):
    name = "djust.auth"
    default_auto_field = "django.db.models.BigAutoField"
    verbose_name = "Djust Auth"
    label = "djust_auth"

    def ready(self) -> None:
        _configure_allauth_backend()


def _configure_allauth_backend() -> None:
    """When ``DJUST_CONFIG["ACCOUNTS"]`` selects the allauth backend, apply its
    secure defaults and connect the signal bridge (ADR-039). A no-op otherwise."""
    from django.conf import settings
    from django.utils.module_loading import import_string

    from djust.config import get_djust_config

    spec = get_djust_config().get("ACCOUNTS")
    if not spec:
        return
    from .accounts.registry import backend_path

    try:
        from .accounts.backends.allauth import (
            AllauthBackend,
            apply_allauth_defaults,
            connect_signal_bridge,
        )
    except ImportError:  # allauth not installed: nothing to configure (check A101 reports misuse)
        return
    try:
        cls = import_string(backend_path(spec))
    except ImportError:  # check A100 reports an unimportable backend
        return
    if not (isinstance(cls, type) and issubclass(cls, AllauthBackend)):
        return
    options = (spec.get("OPTIONS") if isinstance(spec, dict) else None) or {}
    applied = apply_allauth_defaults(settings, options)
    connect_signal_bridge()
    logger.debug("djust accounts: applied allauth defaults %s", applied)
