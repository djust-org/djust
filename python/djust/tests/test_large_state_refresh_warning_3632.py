"""``InMemoryStateBackend.refresh()`` reports a view that grew large (#3632, part 3).

The ``Large LiveView state`` warning came only from ``set()``, which a view calls
once at creation (empty state) and again when a stored snapshot is re-used. The
post-render store is ``refresh()``, so a view that grew large during its life was
not reported until the next reload. ``refresh()`` already serialises the view, so
the size it needs is ``len(snapshot)``: no extra serialisation.

The warning fires once per cache key per time the key crosses the threshold, not
once per event.
"""

from __future__ import annotations

import time
import warnings

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust._rust import RustLiveView
from djust.decorators import event_handler
from djust.state_backends.base import DjustPerformanceWarning, state_key_label
from djust.state_backends.memory import InMemoryStateBackend

pytestmark = [pytest.mark.django_db]

MODULE = __name__
TEMPLATE = "<div dj-root>{% for r in rows %}<p>{{ r }}</p>{% endfor %}</div>"


def _view(rows: int) -> RustLiveView:
    view = RustLiveView(TEMPLATE, [])
    view.update_state({"rows": ["row-%d-" % i + "z" * 400 for i in range(rows)]})
    view.render_with_diff()
    return view


def _without_stamp(view: RustLiveView) -> list:
    import msgpack

    return msgpack.unpackb(view.serialize_msgpack(), raw=False, strict_map_key=False)[:4]


def _large_warnings(caught) -> list[str]:
    return [str(w.message) for w in caught if issubclass(w.category, DjustPerformanceWarning)]


def _record():
    ctx = warnings.catch_warnings(record=True)
    caught = ctx.__enter__()
    warnings.simplefilter("always")
    return ctx, caught


@pytest.fixture
def recorded():
    ctx, caught = _record()
    yield caught
    ctx.__exit__(None, None, None)


class TestRefreshWarns:
    def test_a_view_that_grew_past_the_threshold_is_reported_by_refresh(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        view = _view(2)
        backend.set("k", view)  # small at creation: no warning
        assert _large_warnings(recorded) == []
        view.update_state({"rows": ["row-%d-" % i + "z" * 400 for i in range(200)]})
        view.render_with_diff()
        backend.refresh("k", view)
        messages = _large_warnings(recorded)
        assert len(messages) == 1, messages
        assert f"Large LiveView state detected for {state_key_label('k')}" in messages[0]
        assert "'k'" not in messages[0]
        assert "rows " in messages[0], "the breakdown (largest key) is part of the refresh warning"

    def test_it_warns_once_per_key_not_once_per_event(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        view = _view(200)
        backend.set("k", _view(2))
        for _ in range(5):
            backend.refresh("k", view)
        assert len(_large_warnings(recorded)) == 1

    def test_set_having_warned_the_first_refresh_does_not_repeat_it(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        view = _view(200)
        backend.set("k", view)
        assert len(_large_warnings(recorded)) == 1
        backend.refresh("k", view)
        backend.refresh("k", view)
        assert len(_large_warnings(recorded)) == 1

    def test_a_key_that_shrinks_and_grows_again_is_reported_again(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        big, small = _view(200), _view(2)
        backend.set("k", small)
        backend.refresh("k", big)
        backend.refresh("k", small)
        backend.refresh("k", big)
        assert len(_large_warnings(recorded)) == 2

    def test_keys_are_reported_independently(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        view = _view(200)
        backend.set("a", _view(2))
        backend.set("b", _view(2))
        backend.refresh("a", view)
        backend.refresh("b", view)
        messages = _large_warnings(recorded)
        assert len(messages) == 2
        assert state_key_label("a") != state_key_label("b")
        assert any(state_key_label("a") in m for m in messages)
        assert any(state_key_label("b") in m for m in messages)

    def test_no_warning_below_the_threshold(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=100)
        backend.set("k", _view(2))
        for _ in range(3):
            backend.refresh("k", _view(3))
        assert _large_warnings(recorded) == []

    def test_refresh_of_a_key_that_was_never_stored_warns_nothing_and_stores_nothing(
        self, recorded
    ):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        backend.refresh("absent", _view(200))
        assert _large_warnings(recorded) == []
        assert backend.get("absent") is None

    def test_a_deleted_key_is_reported_again_when_it_comes_back_large(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        view = _view(200)
        backend.set("k", view)
        backend.delete("k")
        backend.set("k", view)
        assert len(_large_warnings(recorded)) == 2

    def test_a_deleted_keys_warned_state_does_not_outlive_it(self, recorded):
        """``set(..., warn_on_large_state=False)`` neither warns nor clears the flag, so
        only ``delete`` can have: a refresh of the re-created key must warn."""
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        view = _view(200)
        backend.set("k", view)  # warns, records the key
        backend.delete("k")
        backend.set("k", view, warn_on_large_state=False)
        before = len(_large_warnings(recorded))
        backend.refresh("k", view)
        assert len(_large_warnings(recorded)) == before + 1

    def test_the_warning_is_attributed_to_the_caller_not_the_backend(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        backend.set("k", _view(2))
        backend.refresh("k", _view(200))
        (warning,) = [w for w in recorded if issubclass(w.category, DjustPerformanceWarning)]
        assert not warning.filename.endswith("state_backends/memory.py"), warning.filename

    def test_refresh_still_stores_the_snapshot_and_keeps_the_timestamp(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        backend.set("k", _view(2))
        _clone, stamped = backend.get("k")
        time.sleep(0.01)
        view = _view(200)
        backend.refresh("k", view)
        clone, timestamp = backend.get("k")
        assert timestamp == stamped
        # The last array element is a wall-clock stamp taken at each serialisation.
        assert _without_stamp(clone) == _without_stamp(view)


class _CountingView:
    """A ``RustLiveView`` stand-in that counts serialisations."""

    def __init__(self, inner: RustLiveView) -> None:
        self._inner = inner
        self.serialised = 0

    def serialize_msgpack(self) -> bytes:
        self.serialised += 1
        return self._inner.serialize_msgpack()


class TestTheWarningNamesNoCredential:
    """The cache key is ``{tenant}{session_key}_{view_key}_t<hash>``: the session key is
    a bearer credential and the view key can carry a request path with ids."""

    KEY = "tenant:7:abcd1234sessionsecret_/accounts/12345/invoices_tdeadbeef"

    def test_neither_the_session_key_nor_the_path_reaches_the_message(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        backend.set(self.KEY, _view(2))
        backend.refresh(self.KEY, _view(200))
        (message,) = _large_warnings(recorded)
        for secret in ("sessionsecret", "/accounts/12345", "tenant:7", "deadbeef", self.KEY):
            assert secret not in message, secret
        assert state_key_label(self.KEY) in message

    def test_set_does_not_leak_it_either(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        backend.set(self.KEY, _view(200))
        (message,) = _large_warnings(recorded)
        assert "sessionsecret" not in message and "/accounts/12345" not in message

    def test_a_snapshot_failure_log_names_the_label_not_the_key(self, caplog):
        class Broken:
            def serialize_msgpack(self):
                raise RuntimeError("boom")

        backend = InMemoryStateBackend(state_size_warning_kb=10)
        backend.set(self.KEY, _view(2))
        with caplog.at_level("DEBUG", logger="djust.state_backends.memory"):
            backend.refresh(self.KEY, Broken())
        assert "sessionsecret" not in caplog.text
        assert state_key_label(self.KEY) in caplog.text

    def test_the_label_is_stable_short_and_not_the_key(self):
        label = state_key_label(self.KEY)
        assert label == state_key_label(self.KEY)
        assert len(label) == len("state-") + 8
        assert label != state_key_label(self.KEY + "x")


class _Broken:
    def serialize_msgpack(self):
        raise RuntimeError("boom")


class TestTheWarnedSetDoesNotOutliveItsEntry:
    """``_warned_large`` is keyed by cache key, so every way an entry leaves must clear
    it, or a long-lived server grows it by one key per large session."""

    def _warned(self, backend, key="k", ttl_stamp=None):
        backend.set(key, _view(200))
        assert key in backend._warned_large
        if ttl_stamp is not None:
            snapshot, _ = backend._cache[key]
            backend._cache[key] = (snapshot, ttl_stamp)

    def test_cleanup_expired_clears_it(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        self._warned(backend, ttl_stamp=time.time() - 10_000)
        assert backend.cleanup_expired(ttl=60) == 1
        assert len(backend._warned_large) == 0

    def test_the_amortised_sweep_clears_it(self, recorded):
        backend = InMemoryStateBackend(default_ttl=60, state_size_warning_kb=10)
        self._warned(backend, ttl_stamp=time.time() - 10_000)
        backend._next_sweep = 0
        backend.set("other", _view(2))  # set() sweeps at most once per interval
        assert "k" not in backend._cache
        assert len(backend._warned_large) == 0

    def test_delete_all_clears_it(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        self._warned(backend)
        backend.delete_all()
        assert len(backend._warned_large) == 0

    def test_a_get_that_finds_the_entry_expired_clears_it(self, recorded):
        backend = InMemoryStateBackend(default_ttl=60, state_size_warning_kb=10)
        self._warned(backend, ttl_stamp=time.time() - 10_000)
        assert backend.get("k") is None
        assert len(backend._warned_large) == 0

    def test_a_snapshot_failure_in_refresh_clears_it(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        self._warned(backend)
        backend.refresh("k", _Broken())
        assert "k" not in backend._cache
        assert len(backend._warned_large) == 0

    def test_a_snapshot_failure_in_set_clears_it(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        self._warned(backend)
        backend.set("k", _Broken())
        assert "k" not in backend._cache
        assert len(backend._warned_large) == 0

    def test_delete_clears_it(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        self._warned(backend)
        backend.delete("k")
        assert len(backend._warned_large) == 0

    def test_a_set_under_the_threshold_re_arms_the_key(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        big, small = _view(200), _view(2)
        backend.set("k", big)  # warns, flagged
        backend.set("k", small)  # shrank: re-armed
        assert len(backend._warned_large) == 0
        backend.refresh("k", big)
        assert len(_large_warnings(recorded)) == 2

    def test_refresh_records_the_size_for_the_stats(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        backend.set("k", _view(2))
        small = backend._state_sizes["k"]
        big = _view(200)
        backend.refresh("k", big)
        assert backend._state_sizes["k"] == len(big.serialize_msgpack()) > small


class TestRefreshIsRobust:
    def test_under_an_error_filter_refresh_logs_and_does_not_raise(self, caplog):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        backend.set("k", _view(2))
        view = _view(200)
        with warnings.catch_warnings():
            warnings.simplefilter("error", DjustPerformanceWarning)
            with caplog.at_level("WARNING", logger="djust.state_backends.memory"):
                backend.refresh("k", view)  # must not raise into the render path
        assert "Large LiveView state detected" in caplog.text
        # the cache was updated, and the next refresh stays quiet (flagged once)
        assert backend.get("k") is not None
        caplog.clear()
        with warnings.catch_warnings():
            warnings.simplefilter("error", DjustPerformanceWarning)
            backend.refresh("k", view)
        assert "Large LiveView state detected" not in caplog.text

    def test_under_an_error_filter_set_raises_but_leaves_entry_and_flag_in_agreement(self):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        with warnings.catch_warnings():
            warnings.simplefilter("error", DjustPerformanceWarning)
            with pytest.raises(DjustPerformanceWarning):
                backend.set("k", _view(200))
        assert "k" in backend._cache and "k" in backend._warned_large
        backend.delete("k")
        assert len(backend._warned_large) == 0

    def test_refresh_honours_warn_on_large_state_false(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        backend.set("k", _view(2))
        big = _view(200)
        backend.refresh("k", big, warn_on_large_state=False)
        assert _large_warnings(recorded) == []
        assert len(backend._warned_large) == 0
        # silenced refreshes do not use up the one report
        backend.refresh("k", big)
        assert len(_large_warnings(recorded)) == 1


class TestRefreshCost:
    """The cost argument is structural, not a stopwatch: a timing ratio is flaky under
    load (the repo's rule for pass/fail gates), but the work done per render is
    countable. ``refresh`` already serialises once per call, so the check reuses
    that snapshot (``len``); the breakdown, which decodes the snapshot, is built only
    on the render that crosses the threshold."""

    def test_refresh_serialises_once_per_call_whether_or_not_it_warns(self, recorded):
        backend = InMemoryStateBackend(state_size_warning_kb=10)
        backend.set("k", _view(2))
        counting = _CountingView(_view(200))
        for _ in range(4):
            backend.refresh("k", counting)
        assert counting.serialised == 4
        assert len(_large_warnings(recorded)) == 1

    def test_the_breakdown_is_decoded_only_on_the_render_that_crosses_the_threshold(self, recorded):
        from unittest.mock import patch

        from djust.state_backends import base

        backend = InMemoryStateBackend(state_size_warning_kb=10)
        backend.set("k", _view(2))
        big = _view(200)
        with patch.object(base, "describe_snapshot", wraps=base.describe_snapshot) as spy:
            for _ in range(10):
                backend.refresh("k", big)
        assert spy.call_count == 1

    def test_a_view_under_the_threshold_never_decodes_the_snapshot(self, recorded):
        from unittest.mock import patch

        from djust.state_backends import base

        backend = InMemoryStateBackend(state_size_warning_kb=100)
        backend.set("k", _view(2))
        with patch.object(base, "describe_snapshot", wraps=base.describe_snapshot) as spy:
            for _ in range(10):
                backend.refresh("k", _view(3))
        assert spy.call_count == 0


# --------------------------------------------------------------------------
# The real consumer: a view that is small at mount and grows during events
# --------------------------------------------------------------------------


class Grows(LiveView):
    template = (
        f'<div dj-view="{MODULE}.Grows" dj-id="0">'
        "{% for r in rows %}<p>{{ r }}</p>{% endfor %}|{{ n }}</div>"
    )

    def mount(self, request, **kwargs):
        self.rows = []
        self.n = 0

    @event_handler()
    def grow(self, **kwargs):
        self.n += 1
        self.rows = [*self.rows, *("row-%d-" % i + "z" * 400 for i in range(60))]


async def _mount_then_grow(events: int):
    from djust.websocket import LiveViewConsumer

    from .test_ws_send_version_1788 import _receive_until

    def make_key():
        session = SessionStore()
        session.create()
        return session.session_key

    key = await sync_to_async(make_key)()

    class Session:
        session_key = key

    communicator = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
    )
    communicator.scope["session"] = Session()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
        connected, _ = await communicator.connect()
        assert connected
        await communicator.receive_json_from(timeout=2)
        await communicator.send_json_to({"type": "mount", "view": f"{MODULE}.Grows", "url": "/"})
        await _receive_until(communicator, "mount")
        for ref in range(1, events + 1):
            await communicator.send_json_to(
                {"type": "event", "event": "grow", "params": {}, "ref": ref}
            )
            await _receive_until(communicator, "patch")
        await communicator.disconnect()
    return key


@pytest.mark.asyncio
class TestTheRealConsumer:
    async def test_a_view_that_grows_during_its_life_is_reported_once(self):
        from djust import state_backends

        backend = InMemoryStateBackend(state_size_warning_kb=20)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(state_backends, "get_backend", lambda: backend)
                session_key = await _mount_then_grow(events=6)
        messages = _large_warnings(caught)
        assert len(messages) == 1, messages
        assert "Large LiveView state detected" in messages[0]
        # The cache key holds the Django session key (a bearer credential); the
        # warning must not carry it. Prove the key really is in the cache key.
        assert any(session_key in cache_key for cache_key in backend._cache), list(backend._cache)
        assert session_key not in messages[0]
        for cache_key in backend._cache:
            assert cache_key not in messages[0]

    async def test_a_view_that_stays_small_is_never_reported(self):
        from djust import state_backends

        backend = InMemoryStateBackend(state_size_warning_kb=20)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(state_backends, "get_backend", lambda: backend)
                await _mount_then_grow(events=0)
        assert _large_warnings(caught) == []
