"""The merging session save (#3347) and the tenant-scoped session keys (#2973, #3328).

``djust._session_merge`` recognises djust's own session keys by prefix
(``_OWN_PREFIXES``): a value under such a key is written explicitly by a save
and never compared for in-place changes, and the early return of
``refresh_other_views_state`` for a tracked session relies on the merge writing
only the keys this copy changed. The tenant and slot scopes put their own
segments *after* the ``liveview_`` prefix, never in front of it; these cases
pin that, and that a tenant's saved state still round-trips through
``save_merged`` beside a concurrent write from another ``SessionStore``.
"""

from __future__ import annotations

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust._session_merge import _OWN_PREFIXES, _watches, is_tracked, save_merged, track_session
from djust._tenant_state import (
    refresh_other_views_state,
    scoped_path,
    session_view_key,
    state_scope,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.tenants]

_URL = "/merge-tenant-binding/"
_CONFIG = {"TENANT_RESOLVER": "subdomain"}


@pytest.fixture(autouse=True)
def _settings():
    with override_settings(
        ALLOWED_HOSTS=[".example.com", "testserver"],
        DJUST_CONFIG=_CONFIG,
        LIVEVIEW_ALLOWED_MODULES=[__name__],
    ):
        yield


def _new_session() -> SessionStore:
    store = SessionStore()
    store["idle_ts"] = "OLD"
    store.create()
    return store


def _stored(key):
    return SessionStore(key).load()


def _elsewhere(key, **changes):
    """Another request's store writes ``changes`` (``None`` deletes the key)."""
    other = SessionStore(key)
    for name, value in changes.items():
        if value is None:
            other.pop(name, None)
        else:
            other[name] = value
    other.save()


# --------------------------------------------------------------------------- #
# 1. Every key the scopes write is one the merge recognises as djust's own
# --------------------------------------------------------------------------- #
class _Scoped:
    """A view stand-in with the two hooks the scope helpers read."""

    def __init__(self, tenant=None, slot=None):
        self._tenant_id = tenant
        if slot is not None:
            self._djust_slot_target = slot

    def get_state_key_prefix(self):
        return f"tenant:{self._tenant_id}" if self._tenant_id else ""


class _Unhooked:
    def __init__(self, slot=None):
        if slot is not None:
            self._djust_slot_target = slot


_VIEWS = {
    "plain": _Unhooked(),
    "slot": _Unhooked(slot="#side"),
    "tenant": _Scoped(tenant="acme"),
    "tenant+slot": _Scoped(tenant="acme", slot="#side"),
}


@pytest.mark.parametrize("name", sorted(_VIEWS))
def test_every_scoped_session_key_starts_with_an_own_prefix(name):
    view = _VIEWS[name]
    key = session_view_key(view, _URL)
    sticky = scoped_path(view, _URL)
    assert key is not None and sticky is not None
    siblings = [
        key,
        f"{key}__private",
        f"{key}_components",
        f"{key}__sticky_ids",
        f"{key}__sticky__child",
        f"liveview_{sticky}__sticky__child__private",
    ]
    for name_ in siblings:
        assert name_.startswith(_OWN_PREFIXES), name_
        # An own key's value is written explicitly and never compared in place.
        assert _watches(name_, {"x": 1}) is False, name_
    # The scope goes after the prefix: it never turns the key into a foreign one.
    assert state_scope(view) is not None and not key.startswith(("tenant:", "slot:"))


# --------------------------------------------------------------------------- #
# 3. An own key's value is not compared in place (this is what the prefix buys)
# --------------------------------------------------------------------------- #
def test_an_in_place_change_to_a_tenant_state_value_is_not_picked_up_by_the_merge():
    view = _Scoped(tenant="acme")
    key = session_view_key(view, _URL)
    store = SessionStore()
    store[key] = {"secret": "saved"}
    store.create()
    session = SessionStore(store.session_key)
    assert track_session(session)
    held = session.get(key)
    held["secret"] = "mutated-without-assignment"
    save_merged(session, store.session_key)
    # Own keys are written explicitly; an unassigned in-place change stays in memory.
    assert _stored(store.session_key)[key] == {"secret": "saved"}


# --------------------------------------------------------------------------- #
# 4. refresh_other_views_state: the tracked early return leaves no gap
# --------------------------------------------------------------------------- #
def _two_views_state(tenant_other="globex"):
    page = _Scoped(tenant="acme")
    slot = _Scoped(tenant="acme", slot="#side")
    other_tenant = _Scoped(tenant=tenant_other)
    return (
        session_view_key(page, _URL),
        session_view_key(slot, _URL),
        session_view_key(other_tenant, _URL),
    )


def _save_own(session, own_key, value, *, refresh=True):
    """What a view's post-event save does at the session: refresh, write, save."""
    if refresh:
        refresh_other_views_state(session, own_key)
    session[own_key] = value
    session[f"{own_key}__private"] = {"p": value["n"]}
    save_merged(session, session.session_key)


def test_tracked_save_keeps_other_views_latest_state_and_the_tenant_entry():
    page_key, slot_key, other_key = _two_views_state()
    base = SessionStore()
    base.update({page_key: {"n": 0}, slot_key: {"n": 0}, other_key: {"n": 0}, "app": "a"})
    base.create()
    key = base.session_key

    session = SessionStore(key)
    assert track_session(session)
    # The slot view and a second tenant save elsewhere after this copy loaded.
    _elsewhere(key, **{slot_key: {"n": 7}, other_key: {"n": 9}, "app": "b", "late": "x"})
    _save_own(session, page_key, {"n": 1})

    stored = _stored(key)
    assert stored[page_key] == {"n": 1} and stored[f"{page_key}__private"] == {"p": 1}
    assert stored[slot_key] == {"n": 7}  # not rolled back to this copy's load
    assert stored[other_key] == {"n": 9}
    assert stored["app"] == "b" and stored["late"] == "x"
    # And the live copy has caught up, so the slot view's next read is current.
    assert session[slot_key] == {"n": 7}


def test_tracked_save_does_not_resurrect_a_view_entry_removed_elsewhere():
    page_key, slot_key, _ = _two_views_state()
    base = SessionStore()
    base.update({page_key: {"n": 0}, slot_key: {"n": 0}})
    base.create()
    key = base.session_key

    session = SessionStore(key)
    assert track_session(session)
    _elsewhere(key, **{slot_key: None})  # the slot view went away
    _save_own(session, page_key, {"n": 1})
    assert slot_key not in _stored(key)


def test_the_untracked_path_still_refreshes_other_views_and_matches_the_tracked_result():
    page_key, slot_key, other_key = _two_views_state()
    results = []
    for tracked in (True, False):
        base = SessionStore()
        base.update({page_key: {"n": 0}, slot_key: {"n": 0}, other_key: {"n": 0}, "app": "a"})
        base.create()
        key = base.session_key
        session = SessionStore(key)
        if tracked:
            assert track_session(session)
        else:
            session.load()
            assert not is_tracked(session)
        _elsewhere(key, **{slot_key: {"n": 7}, other_key: {"n": 9}})
        session["handler_wrote"] = "kept"  # an application value from this turn
        _save_own(session, page_key, {"n": 1})
        results.append(_stored(key))
    tracked_result, untracked_result = results
    for stored in results:
        assert stored[page_key] == {"n": 1}
        assert stored[slot_key] == {"n": 7} and stored[other_key] == {"n": 9}
        assert stored["handler_wrote"] == "kept"
    assert tracked_result == untracked_result
