"""Save frequency, step 6: what a developer writes.

* ``@event_handler(save=True)`` and ``self.save_state()``: this turn is saved
  before its reply, even with an interval set; one turn's request only.
* ``state_save_interval`` on a view: its own interval (None: the project's).
  Never saved as state, never seen by change detection.
* System checks ``djust.C040`` (an interval that cannot be used) and
  ``djust.C041`` (an interval set while the strict store makes it moot).
* Option A of the size limit: a flush over the limit keeps the last state
  that fit in the store, the view keeps its values, the page is told once.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, List

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import path as _path

from djust import LiveView, _automatic_state, _save_policy, runtime
from djust.decorators import background, event_handler
from djust.tests import _per_turn_harness as harness
from djust.tests import _save_frequency as sf

MODULE = __name__
INTERVAL = 30


class Surface(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><pre>{{ count }}</pre></div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self.blob = ""

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @event_handler(save=True)
    def inc_and_keep(self, **kwargs):
        self.count += 1

    @event_handler()
    def inc_then_save(self, **kwargs):
        self.count += 1
        self.save_state()

    @event_handler()
    def grow(self, **kwargs):
        self.count += 1
        self.blob = "x" * 4000

    @event_handler()
    def shrink(self, **kwargs):
        self.blob = ""

    @event_handler(save=True)
    @background
    def bg_keep(self, **kwargs):
        self.count += 10

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class OwnInterval(Surface):
    state_save_interval = INTERVAL


class NeverDeferred(Surface):
    state_save_interval = 0


class Small(Surface):
    automatic_state_max_kb = 2


urlpatterns = [_path(sf.PATH.strip("/") + "/", Surface.as_view())]


@pytest.fixture(autouse=True)
def _each(monkeypatch):
    sf.one_tab(monkeypatch)
    _save_policy._WARNED.clear()


@pytest.fixture
def runtimes(monkeypatch):
    built: List[Any] = []
    real = runtime.ViewRuntime.__init__

    def recording(self, *args, **kwargs):
        real(self, *args, **kwargs)
        built.append(self)

    monkeypatch.setattr(runtime.ViewRuntime, "__init__", recording)
    return built


def _settings(**extra):
    return harness.ws_settings(MODULE, ROOT_URLCONF=MODULE, **extra)


def _count(key: str) -> Any:
    found = sf.stored_envelopes(key)
    assert len(found) == 1, list(found)
    return next(iter(found.values()))["values"]


async def _values(key: str) -> dict:
    return await sync_to_async(_count)(key)


async def _socket(view_class: type) -> tuple:
    key = await sync_to_async(sf.page_get)(view_class)
    comm, _ = await harness.mount_view(view_class, key)
    return key, comm


# ---------------------------------------------------------------------------
# save=True and save_state()
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("event", ["inc_and_keep", "inc_then_save"])
async def test_a_turn_that_asks_is_saved_before_its_reply(runtimes, event):
    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key, comm = await _socket(Surface)
        rt = runtimes[-1]
        try:
            await harness.event(comm, "inc")
            assert (await _values(key))["count"] == 0  # deferred
            await harness.event(comm, event)
            # The whole view, with the earlier deferred turn's change.
            assert (await _values(key))["count"] == 2
            assert not rt.state_unsaved and rt._state_timer is None
            await harness.event(comm, "inc")  # one turn's request only
            assert (await _values(key))["count"] == 2 and rt.state_unsaved
        finally:
            await comm.disconnect()


def test_save_true_keeps_the_handler_a_handler():
    from djust.decorators import is_event_handler

    method = Surface.inc_and_keep
    assert is_event_handler(method)
    assert method._djust_saves_before_reply is True
    assert not hasattr(Surface.inc, "_djust_saves_before_reply")
    # Not in the metadata the page renders for the client (its bytes are unchanged).
    assert "save" not in method._djust_decorators["event_handler"]
    assert list(inspect.signature(method).parameters) == ["self", "kwargs"]

    class Async(LiveView):
        exposure_policy = "explicit"

        @event_handler(save=True)
        async def go(self, **kwargs):
            return 7

    assert inspect.iscoroutinefunction(Async.go)
    view = Async()
    assert asyncio.run(view.go()) == 7
    assert _save_policy.take_save_request(view) is True
    assert _save_policy.take_save_request(view) is False


def test_save_must_be_a_bool():
    with pytest.raises(TypeError):
        event_handler(save="yes")


def test_a_component_asks_for_its_view():
    class Holder:
        pass

    view, middle, leaf = Holder(), Holder(), Holder()
    middle._parent = view
    leaf._parent = middle
    _save_policy.request_save(leaf)
    assert _save_policy.take_save_request(view) is True
    assert not _save_policy.take_save_request(middle)
    assert not _save_policy.take_save_request(leaf)


def test_the_request_is_not_state_and_not_a_change():
    from djust.change_detection import CHANGE_DETECTION_IGNORED

    assert _save_policy.SAVE_FLAG in CHANGE_DETECTION_IGNORED
    assert "state_save_interval" in CHANGE_DETECTION_IGNORED
    assert "state_save_interval" in _automatic_state._NOT_STATE
    assert not _automatic_state.eligible_name(OwnInterval(), "state_save_interval")


# ---------------------------------------------------------------------------
# state_save_interval
# ---------------------------------------------------------------------------


def test_a_views_interval_overrides_the_projects(caplog):
    assert _save_policy.interval_for(Surface()) == 0.0
    assert _save_policy.interval_for(OwnInterval()) == float(INTERVAL)
    with override_settings(DJUST_STATE_SAVE_INTERVAL=5):
        assert _save_policy.interval_for(Surface()) == 5.0
        assert _save_policy.interval_for(NeverDeferred()) == 0.0
        assert _save_policy.interval_for(OwnInterval()) == float(INTERVAL)

    class Broken(Surface):
        state_save_interval = "5"

    assert _save_policy.interval_for(Broken()) == 0.0
    assert any("state_save_interval" in r.getMessage() for r in caplog.records)


def test_an_interval_over_half_the_lifetime_is_read_as_zero():
    with override_settings(DJUST_SERVER_STATE_MAX_AGE=60, DJUST_STATE_SAVE_INTERVAL=31):
        assert _save_policy.state_save_interval() == 0.0
    with override_settings(DJUST_SERVER_STATE_MAX_AGE=60, DJUST_STATE_SAVE_INTERVAL=30):
        assert _save_policy.state_save_interval() == 30.0


@pytest.mark.django_db(transaction=True)
async def test_a_view_with_its_own_interval_defers_with_none_set_for_the_project(runtimes):
    with _settings():
        key, comm = await _socket(OwnInterval)
        try:
            await harness.event(comm, "inc")
            assert (await _values(key))["count"] == 0
            assert runtimes[-1].state_unsaved
        finally:
            await comm.disconnect()
        assert (await _values(key))["count"] == 1  # the close flushed it


# ---------------------------------------------------------------------------
# C040 / C041
# ---------------------------------------------------------------------------


def _checks(monkeypatch, views, **settings) -> list:
    from djust.checks import configuration

    monkeypatch.setattr(configuration, "_walk_subclasses", lambda cls: iter(views))
    errors: list = []
    with override_settings(**settings):
        configuration._check_state_save_interval(errors)
    return errors


def test_c040_reports_an_interval_that_cannot_be_used(monkeypatch):
    for value in ("5", True, 0.1, 61):
        (found,) = _checks(monkeypatch, [], DJUST_STATE_SAVE_INTERVAL=value)
        assert found.id == "djust.C040" and "DJUST_STATE_SAVE_INTERVAL" in found.msg

    class Bad(Surface):
        state_save_interval = 120

    (found,) = _checks(monkeypatch, [Bad, OwnInterval])
    assert found.id == "djust.C040" and "Bad.state_save_interval" in found.msg
    assert "OwnInterval" not in found.msg
    (found,) = _checks(monkeypatch, [], DJUST_STATE_SAVE_INTERVAL=40, DJUST_SERVER_STATE_MAX_AGE=60)
    assert "half of DJUST_SERVER_STATE_MAX_AGE" in found.msg
    for value in (0, 0.5, 5, 60):
        assert (
            _checks(monkeypatch, [OwnInterval, NeverDeferred], DJUST_STATE_SAVE_INTERVAL=value)
            == []
        )
    assert (
        _checks(
            monkeypatch,
            [],
            DJUST_STATE_SAVE_INTERVAL="5",
            DJUST_CONFIG={"suppress_checks": ["C040"]},
        )
        == []
    )


def test_c041_says_a_strict_store_makes_the_interval_moot(monkeypatch):
    (found,) = _checks(monkeypatch, [], DJUST_STATE_SAVE_INTERVAL=5, DJUST_STATE_STORE_STRICT=True)
    assert found.id == "djust.C041" and "DJUST_STATE_SAVE_INTERVAL" in found.msg
    (found,) = _checks(monkeypatch, [OwnInterval], DJUST_STATE_STORE_STRICT=True)
    assert found.id == "djust.C041" and "OwnInterval" in found.msg
    assert _checks(monkeypatch, [NeverDeferred], DJUST_STATE_STORE_STRICT=True) == []
    assert _checks(monkeypatch, [], DJUST_STATE_SAVE_INTERVAL=5) == []


def test_both_checks_are_registered_with_the_configuration_family():
    from djust.checks import configuration

    source = inspect.getsource(configuration.check_configuration)
    assert "_check_state_save_interval(errors)" in source


# ---------------------------------------------------------------------------
# Option A: over the limit at a flush
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
async def test_a_flush_over_the_limit_keeps_the_last_fit_and_tells_the_page_once(runtimes):
    from djust.tests._ws_frames import receive_until

    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key, comm = await _socket(Small)
        rt = runtimes[-1]
        try:
            await harness.event(comm, "inc")
            assert await rt.flush_state()
            assert (await _values(key))["count"] == 1
            await harness.event(comm, "grow")  # answered: the change is live
            assert not await rt.flush_state()
            frames = await receive_until(
                comm,
                lambda got: any(f.get("code") == "state_too_large" for f in got),
                what="the over-limit notice",
            )
            notice = frames[-1]
            assert notice["type"] == "error" and notice["source"] == "async", notice
            assert (await _values(key))["count"] == 1  # the last state that fit
            assert rt.view_instance.count == 2 and rt.state_unsaved
            assert not await rt.flush_state()  # told once
            from djust.tests._ws_frames import drain_extra

            extra = await drain_extra(comm)
            assert not any(f.get("code") == "state_too_large" for f in extra), extra
            await harness.event(comm, "shrink")
            assert await rt.flush_state()
            assert (await _values(key))["count"] == 2
        finally:
            await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_over_the_limit_a_later_immediate_turn_is_served_too(runtimes):
    """Option A whatever the turn: an immediate turn (here ``save=True``) over
    the limit with saves deferred keeps serving, keeps the last state that
    fit, and tells the page no second time."""
    from djust.tests._ws_frames import drain_extra

    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key, comm = await _socket(Small)
        rt = runtimes[-1]
        try:
            await harness.event(comm, "inc")
            assert await rt.flush_state()
            await harness.event(comm, "grow")
            assert not await rt.flush_state()
            told = await drain_extra(comm)
            assert [f.get("code") for f in told].count("state_too_large") == 1
            reply = await harness.event(comm, "inc_and_keep")
            assert reply["type"] in ("patch", "html_update"), reply
            assert rt.view_instance.count == 3 and rt.state_unsaved
            assert (await _values(key))["count"] == 1
            extra = await drain_extra(comm)
            assert not any(f.get("code") == "state_too_large" for f in extra), extra
        finally:
            await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_save_true_over_background_saves_the_result_before_its_frame(runtimes):
    from djust.tests._ws_frames import has_frame, receive_until

    with _settings(DJUST_STATE_SAVE_INTERVAL=INTERVAL):
        key, comm = await _socket(Surface)
        rt = runtimes[-1]
        try:
            await comm.send_json_to({"type": "event", "event": "bg_keep", "params": {}, "ref": 3})
            await receive_until(comm, has_frame("patch", source="async"), what="the result")
            assert (await _values(key))["count"] == 10
            assert not rt.state_unsaved
        finally:
            await comm.disconnect()
