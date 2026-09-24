"""Shared helpers: the accounts URLconf snapshots the backend at import, so
tests that switch backends must remount it — and restore it afterwards."""

import importlib

import pytest
from django.urls import clear_url_caches

from djust.auth.accounts import reset_account_backend

_URL_MODULES = (
    # allauth builds its URL list from its settings at import (e.g. the
    # confirm-email/<key>/ route exists only for link verification).
    "allauth.account.urls",
    "allauth.urls",
    "djust.auth.accounts.urls",
    "djust.tests.accounts.urls_accounts",
    "djust.tests.accounts.urls_accounts_allauth",
)


def remount_accounts() -> None:
    """Rebuild the accounts URLconf for the currently configured backend."""
    reset_account_backend()
    for name in _URL_MODULES:
        try:
            module = importlib.import_module(name)
        except ImportError:  # allauth not installed
            continue
        importlib.reload(module)
    clear_url_caches()


@pytest.fixture(autouse=True)
def _restore_accounts_urlconf():
    yield
    # settings overrides are undone by now; rebuild for the default backend.
    remount_accounts()
