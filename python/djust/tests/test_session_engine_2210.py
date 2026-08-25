"""Synthesized requests must honour ``settings.SESSION_ENGINE`` (#2210).

The bug
-------
Four places built a ``request`` for the ``live_redirect`` / ``url_change``
paths, and each imported ``django.contrib.sessions.backends.db.SessionStore``
**directly**. ``settings.SESSION_ENGINE`` appeared nowhere in the package. A
project on a cache-backed engine therefore got a store reading a
``django_session`` row that does not exist.

The issue filed this from a grep and said so, flagging that the impact was
"derived from reading the two call sites" and should be confirmed at runtime
first. It was, and the failure has **two** shapes rather than the one reported:

* the sessions migration HAS been run — the store finds no row and hands the
  view an **empty session**, with nothing raised anywhere;
* it has NOT been run, which a cache-only project has no reason to do — the
  store raises ``OperationalError: no such table: django_session``. Django's
  stores load lazily, so that lands wherever the view first *reads* the
  session, far from the code that built it.

Both are pinned below, because a fix verified only against the first would
still look correct while the second kept 500ing.

Why every case asserts a VALUE
------------------------------
The issue's own test-shape note calls this out: a test asserting only "a
session exists" passes either way, since the hardcoded DB store also produces
a session object. Every case here writes a value through the *configured*
engine and asserts the synthesized request reads **that value back**.
"""

import pytest
from django.test import override_settings

from djust.utils import build_session_for_request, get_session_store_class

CACHE_ENGINE = "django.contrib.sessions.backends.cache"
DB_ENGINE = "django.contrib.sessions.backends.db"
COOKIE_ENGINE = "django.contrib.sessions.backends.signed_cookies"


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    """``get_session_store_class`` memoizes the import; ``override_settings``
    changes the engine under it, so the cache must not outlive a test."""
    from djust import utils

    utils._import_session_store.cache_clear()
    utils._SESSION_WRITE_WARNED.clear()
    yield
    utils._import_session_store.cache_clear()
    utils._SESSION_WRITE_WARNED.clear()


def _write_through_configured_engine(**data):
    """Write a session the way Channels' SessionMiddlewareStack would, and
    return its key. This is the control arm: it uses whatever engine the test
    configured, never a hardcoded one."""
    store_cls = get_session_store_class()
    store = store_cls()
    for k, v in data.items():
        store[k] = v
    store.create()
    return store.session_key


# ---------------------------------------------------------------------------
# The bug.
# ---------------------------------------------------------------------------


@override_settings(
    SESSION_ENGINE=CACHE_ENGINE,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
def test_a_cache_backed_session_survives_the_synthesized_request():
    # The load-bearing case. Pre-fix the value was simply gone, because the DB
    # store looked in a table the cache engine never writes to.
    key = _write_through_configured_engine(tenant_id=42)
    session = build_session_for_request(key)
    assert session is not None
    assert session.get("tenant_id") == 42, (
        "the synthesized request must see the value the CONFIGURED engine "
        "wrote. Reading the DB store here returns {} instead — silently."
    )


@override_settings(
    SESSION_ENGINE=CACHE_ENGINE,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
def test_the_store_is_the_configured_engines_and_not_the_db_one():
    # Sharper than the value check, and immune to a fix that happened to work
    # because both engines were reachable: assert the CLASS.
    from django.contrib.sessions.backends.cache import SessionStore as CacheStore
    from django.contrib.sessions.backends.db import SessionStore as DbStore

    assert get_session_store_class() is CacheStore
    assert get_session_store_class() is not DbStore


@override_settings(SESSION_ENGINE=DB_ENGINE)
@pytest.mark.django_db
def test_the_db_engine_still_works_when_it_is_the_configured_one():
    # Guard: the fix must not break the majority configuration it replaces.
    key = _write_through_configured_engine(tenant_id=7)
    assert build_session_for_request(key).get("tenant_id") == 7


@override_settings(
    SESSION_ENGINE=CACHE_ENGINE,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
def test_no_database_access_at_all_for_a_cache_backed_session():
    """The second failure shape: a cache-only project need never have run the
    sessions migration, so the old code did not merely read an empty session —
    it raised ``OperationalError`` on first access.

    Pinned by forbidding DB access entirely rather than by dropping a table:
    ``django_db`` is deliberately NOT requested here, so any query at all
    raises. That also catches the performance half of the issue — the DB round
    trip a cache-backed project configured its way out of.
    """
    key = _write_through_configured_engine(tenant_id=1)
    session = build_session_for_request(key)
    assert session.get("tenant_id") == 1  # would need a DB query pre-fix


# ---------------------------------------------------------------------------
# signed_cookies — the case the issue flagged as needing a decision.
# ---------------------------------------------------------------------------


@override_settings(SESSION_ENGINE=COOKIE_ENGINE)
def test_signed_cookies_reads_work_and_the_write_limit_is_warned_once(caplog):
    """The issue suggested a "documented, logged refusal" for this engine.

    A refusal was rejected after probing what the engine actually does: the
    "session key" IS the signed payload, so **reads work** — strictly better
    than the empty session it got from the DB store before. Only writes cannot
    persist, because saving mints a new key that only a ``Set-Cookie`` can
    deliver and a WebSocket has no response to put one on. Refusing would throw
    away the working half to prevent the broken one.

    So: warn once per process, and keep the reads.
    """
    import logging

    from djust import utils

    store_cls = get_session_store_class()
    written = store_cls()
    written["tenant_id"] = 99
    written.save()

    with caplog.at_level(logging.WARNING, logger="djust.utils"):
        # The setup above already resolved the engine once, so reset BOTH the
        # once-per-process guard and the captured records — otherwise this
        # measures the setup's warning rather than the calls under test.
        utils._SESSION_WRITE_WARNED.clear()
        caplog.clear()
        session = build_session_for_request(written.session_key)
        first = [r for r in caplog.records if "signed_cookies" in r.getMessage()]
        build_session_for_request(written.session_key)
        total = [r for r in caplog.records if "signed_cookies" in r.getMessage()]

    assert session.get("tenant_id") == 99, "reads must keep working"
    assert len(first) == 1, "the write limitation must be surfaced"
    assert len(total) == 1, (
        "warned once per process, not once per synthesized request — a "
        "misconfigured project would otherwise log on every live_redirect"
    )


# ---------------------------------------------------------------------------
# Structural: all four sites share one resolver.
# ---------------------------------------------------------------------------


def test_no_module_imports_the_db_session_store_directly():
    """Pin the cure, not just this instance (#1646).

    Four independent copies is what made this bug possible, and a behavioural
    test on one path cannot see a fifth copy appearing in another. ``testing``
    is included deliberately: ``LiveViewTestClient`` exists to stand in for the
    production paths, so a test client on a different session engine than the
    code it models is its own quiet trap.
    """
    import pathlib

    import djust

    root = pathlib.Path(djust.__file__).parent
    needle = "from django.contrib.sessions.backends.db import SessionStore"
    offenders = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "tests" not in path.parts and needle in path.read_text()
    )
    assert offenders == [], (
        "these modules bypass settings.SESSION_ENGINE by importing the DB store "
        f"directly; use djust.utils.build_session_for_request instead: {offenders}"
    )
