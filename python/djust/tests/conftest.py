"""
Shared test fixtures and helpers for djust unit tests.

Anything used by 2+ test files belongs here. Keep imports minimal — this
module is loaded for every test in `python/djust/tests/`.
"""

from __future__ import annotations

from typing import Iterable

import pytest


@pytest.fixture(autouse=True)
def _reset_djust_globals():
    """Reset djust process-global mutable state BEFORE each test (#1883).

    Systemic cure for the shared-process-global flaky-test class: one autouse
    fixture clears djust's leak-prone process-globals (the Channels layer
    manager, Django's URLconf caches, djust's route-map cache, and the
    module-level id counters) so every test in an xdist worker starts from a
    clean slate. Retires the whack-a-mole class that produced #1862 (PR #1874),
    #1875 (PR #1881), and #1882 — see ``djust.test_isolation`` for the full
    inventory and the conservative-inclusion rationale.

    Mirrors the same autouse fixture in ``tests/conftest.py``; the shared reset
    logic lives in ``djust.test_isolation.reset_djust_globals`` (DRY, #1646) so
    both test roots stay in lock-step. Pre-yield (resets before the test runs)
    so tests that set up their own global state in their body still work.
    """
    from djust.test_isolation import reset_djust_globals

    reset_djust_globals()
    yield


def make_staff_user(
    username: str = "tester",
    *,
    is_staff: bool = True,
    perms: Iterable[str] = (),
    pk: int = 1,
):
    """Build a lightweight staff-user stand-in for auth-gated view tests.

    No DB hit — uses Django's User model in-memory and overrides
    ``has_perm``/``has_perms`` via instance attrs.

    Subsumes the duplicated ``_make_user`` factories that previously lived
    in ``test_admin_widgets_per_page.py``, ``test_bulk_progress.py``, and
    other admin-feature tests (#1028).

    Args:
        username: Display name (default ``"tester"``).
        is_staff: Whether the user is a Django staff user (default ``True``).
        perms: Permission strings to grant (default empty — user has no perms).
        pk: Primary key + id (default ``1``).
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User(username=username, is_staff=is_staff)
    user.pk = pk
    user.id = pk
    perm_set = set(perms)
    user.has_perm = lambda p: p in perm_set  # type: ignore[assignment]
    user.has_perms = lambda ps: all(p in perm_set for p in ps)  # type: ignore[assignment]
    return user
