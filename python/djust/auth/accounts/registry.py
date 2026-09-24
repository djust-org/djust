"""Resolve ``DJUST_CONFIG["ACCOUNTS"]`` to a backend instance (BackendRegistry convention)."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

from djust.utils import BackendRegistry

from .base import AccountBackend

#: Short names accepted by ``DJUST_CONFIG["ACCOUNTS"]["BACKEND"]``.
ALIASES = {
    "django": "djust.auth.accounts.backends.django.DjangoBackend",
    "allauth": "djust.auth.accounts.backends.allauth.AllauthBackend",
}


def backend_path(value: Any) -> str:
    """The dotted path a ``DJUST_CONFIG["ACCOUNTS"]`` value selects (aliases resolved)."""
    spec = value if isinstance(value, dict) else {"BACKEND": value}
    name = spec.get("BACKEND") or "django"
    return ALIASES.get(name, name)


def _factory(value: Any, config: dict) -> AccountBackend:
    spec = value if isinstance(value, dict) else {"BACKEND": value}
    path = backend_path(spec)
    try:
        cls = import_string(path)
    except ImportError as exc:
        raise ImproperlyConfigured(
            f'DJUST_CONFIG["ACCOUNTS"]["BACKEND"] = {path!r} could not be imported: {exc}'
        ) from exc
    if not (isinstance(cls, type) and issubclass(cls, AccountBackend)):
        raise ImproperlyConfigured(f"{path!r} is not an AccountBackend subclass.")
    return cls(spec.get("OPTIONS"))


_registry = BackendRegistry("ACCOUNTS", "django", _factory, name="accounts", warn_on_default=False)


def get_account_backend() -> AccountBackend:
    """The configured account backend (created on first use, then cached)."""
    backend: AccountBackend = _registry.get()
    return backend


def reset_account_backend() -> None:
    """Forget the cached backend; the next call re-reads the settings."""
    _registry.reset()


@receiver(setting_changed)
def _reset_on_settings_change(setting: str, **kwargs: Any) -> None:
    if setting == "DJUST_CONFIG":
        _registry.reset()
