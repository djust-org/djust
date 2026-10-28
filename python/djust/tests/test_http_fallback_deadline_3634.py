"""A deadline for the work the page-POST fallback runs inside the request (#3634).

#3630 made the fallback run ``start_async`` / ``@background`` work before it
renders its one answer. Nothing bounded a task: a ``mount()`` that starts a
watcher (``while not stop: sleep(...)``) held every POST open for as long as it
ran.

``LIVEVIEW_CONFIG["http_fallback_background_timeout"]`` (seconds, default
``None`` = no deadline) is the total time one POST waits for background work.
When it is set, each task runs on a worker thread the request does not join.
The request thread waits on that thread with what is left of the budget; when
it is spent the fallback stops waiting, cancels what can be cancelled (an
``async def`` callback), hands ``handle_async_result`` a ``TimeoutError`` and
answers.

Why a worker thread: a sync callback runs through ``async_to_sync`` /
``sync_to_async``, which put it on the *request* thread, so a timeout on the
coroutine that awaits it cannot free the request. These tests drive real
hanging sync and ``async def`` callbacks through the real ``LiveView.post``
with a short deadline. They assert ordering (the answer came back while the
callback was still blocked), never a duration.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import sys
import threading
import warnings
from contextvars import ContextVar
from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.mixins import request as request_mixin

pytestmark = pytest.mark.django_db

PATH = "/deadline/"
KEY = "http_fallback_background_timeout"
SHORT = override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False, KEY: 0.05})
OFF = override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False})

LOG: list = []
STARTED = threading.Event()
RELEASE = threading.Event()
FINISHED = threading.Event()
CANCELLED = threading.Event()
THREADS: dict = {}
LOOPS: list = []
CTX: ContextVar = ContextVar("deadline_ctx", default="unset")


class Watching(LiveView):
    template = "<div dj-root dj-id='0'><p>{{ status }}</p></div>"

    def mount(self, request, **kwargs):
        self.status = "idle"

    def handle_async_result(self, name, result=None, error=None):
        LOG.append(
            (
                "result",
                name,
                result,
                type(error).__name__ if error else None,
                str(error) if error else "",
            )
        )
        if error is not None:
            self.status = "failed:%s" % type(error).__name__

    # ----- tasks -----------------------------------------------------------
    def _hang_sync(self):
        THREADS["sync"] = threading.current_thread().name
        STARTED.set()
        RELEASE.wait(4)  # bounded so a regression cannot hang the suite
        self.status = "late-write"  # an abandoned thread's write
        LOG.append(("late",))
        FINISHED.set()
        return "late-result"

    async def _hang_async(self):
        THREADS["async"] = threading.current_thread().name
        STARTED.set()
        try:
            await asyncio.wait_for(asyncio.Event().wait(), 10)
        except asyncio.CancelledError:
            CANCELLED.set()
            raise

    async def _block_the_loop(self):
        """An ``async def`` that blocks the event loop: it cannot be cancelled."""
        STARTED.set()
        RELEASE.wait(4)
        FINISHED.set()

    def _fast(self):
        THREADS["fast"] = threading.current_thread().name
        self.status = "fast-done"
        return "fast-result"

    async def _fast_async(self):
        self.status = "fast-async-done"
        return "fast-async"

    def _second(self):
        LOG.append(("second-ran",))

    def _boom(self):
        raise ValueError("loader failed")

    # ----- handlers --------------------------------------------------------
    @event_handler()
    def kick_sync(self, **kwargs):
        self.status = "working"
        self.start_async(self._hang_sync, name="hang")

    @event_handler()
    def kick_async(self, **kwargs):
        self.status = "working"
        self.start_async(self._hang_async, name="hang")

    @event_handler()
    def kick_blocking_async(self, **kwargs):
        self.status = "working"
        self.start_async(self._block_the_loop, name="block")

    @event_handler()
    def kick_two(self, **kwargs):
        self.status = "working"
        self.start_async(self._hang_sync, name="first")
        self.start_async(self._second, name="second")

    @event_handler()
    def kick_fast(self, **kwargs):
        self.status = "working"
        self.start_async(self._fast, name="fast")

    @event_handler()
    def kick_fast_async(self, **kwargs):
        self.status = "working"
        self.start_async(self._fast_async, name="fast_async")

    @event_handler()
    def kick_failing(self, **kwargs):
        self.start_async(self._boom, name="boom")

    @event_handler()
    def kick_reads_context(self, **kwargs):
        self.start_async(self._read_ctx, name="ctx")

    def _read_ctx(self):
        LOG.append(("ctx", CTX.get()))

    @event_handler()
    def kick_cancelled(self, **kwargs):
        self.start_async(self._raise_cancelled, name="cancelled")

    def _raise_cancelled(self):
        raise asyncio.CancelledError

    @event_handler()
    def kick_exit(self, **kwargs):
        self.start_async(self._raise_exit, name="exit")

    def _raise_exit(self):
        raise SystemExit("callback asked to exit")

    @event_handler()
    def kick_loop(self, **kwargs):
        self.start_async(self._record_loop, name="loop")

    async def _record_loop(self):
        LOOPS.append(asyncio.get_running_loop())

    @event_handler()
    def kick_chain(self, **kwargs):
        self.start_async(self._hang_sync, name="first")

    @event_handler()
    def plain(self, **kwargs):
        self.status = "plain"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status"] = self.status
        return ctx


class MountWatcher(Watching):
    """``mount()`` starts a watcher, as in the issue."""

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.start_async(self._hang_sync, name="watch")


class MountHangThenHandlerWork(MountWatcher):
    """Mount's task hangs; the handler queues one more."""

    @event_handler()
    def then_more(self, **kwargs):
        self.start_async(self._second, name="after")


class ChainsOnTimeout(Watching):
    """``handle_async_result`` queues follow-up work when a task fails."""

    def handle_async_result(self, name, result=None, error=None):
        super().handle_async_result(name, result=result, error=error)
        if error is not None and name == "first":
            self.start_async(self._second, name="retry")


for _cls in (Watching, MountWatcher, MountHangThenHandlerWork, ChainsOnTimeout):
    setattr(sys.modules[__name__], _cls.__name__, _cls)


@pytest.fixture(autouse=True)
def _reset():
    for event in (STARTED, RELEASE, FINISHED, CANCELLED):
        event.clear()
    LOG.clear()
    THREADS.clear()
    LOOPS.clear()
    yield
    RELEASE.set()  # let any abandoned thread end
    for thread in threading.enumerate():
        if thread.name.startswith(request_mixin._HTTP_BACKGROUND_THREAD_PREFIX):
            thread.join(10)
    LOG.clear()


def _get_request():
    request = RequestFactory().get(PATH)
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    return request


def _post(view_cls, event, initial=None, params=None, url=PATH, get=True):
    initial = initial or _get_request()
    if get:
        view_cls().get(initial)
    request = RequestFactory().post(
        url,
        data=json.dumps(params or {}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT=event,
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    view = view_cls()
    response = view.post(request)
    return view, response, response.content.decode("utf-8")


def _workers():
    return [
        t
        for t in threading.enumerate()
        if t.name.startswith(request_mixin._HTTP_BACKGROUND_THREAD_PREFIX)
    ]


def _join_workers():
    RELEASE.set()
    for thread in _workers():
        thread.join(10)
    assert not _workers(), "a worker thread outlived the test"


class TestDefaultIsUnchanged:
    def test_no_deadline_by_default(self):
        from djust.config import http_fallback_background_timeout

        assert http_fallback_background_timeout() is None

    def test_without_a_deadline_the_callback_runs_on_the_request_thread(self):
        """Nothing about #3630's behaviour changes unless the key is set."""
        with OFF:
            view, response, body = _post(Watching, "kick_fast")
        assert response.status_code == 200, body
        assert THREADS["fast"] == threading.current_thread().name
        assert view.status == "fast-done"
        assert not _workers()


class TestAHangingSyncCallback:
    def test_the_answer_comes_back_while_the_callback_is_still_blocked(self):
        with SHORT:
            view, response, body = _post(Watching, "kick_sync")
            # Ordering, not a duration: the callback started, has not finished,
            # and the POST has already answered.
            assert STARTED.is_set()
            assert not FINISHED.is_set(), "the POST waited for the hanging callback"
        assert response.status_code == 200, body
        assert "working" in body or "failed:TimeoutError" in body, body
        _join_workers()

    def test_the_callback_runs_on_a_worker_thread_not_the_request_thread(self):
        with SHORT:
            _post(Watching, "kick_sync")
        assert THREADS["sync"] != threading.current_thread().name
        assert THREADS["sync"].startswith(request_mixin._HTTP_BACKGROUND_THREAD_PREFIX)
        _join_workers()

    def test_handle_async_result_receives_a_timeout_error(self):
        with SHORT:
            view, _, body = _post(Watching, "kick_sync")
        results = [entry for entry in LOG if entry[0] == "result"]
        assert len(results) == 1, LOG
        _, name, result, error_type, message = results[0]
        assert (name, result, error_type) == ("hang", None, "TimeoutError")
        assert "hang" in message and KEY in message, message
        assert view.status == "failed:TimeoutError"
        assert "failed:TimeoutError" in body, body
        _join_workers()

    def test_the_abandonment_is_logged_as_a_warning(self, caplog):
        with SHORT, caplog.at_level(logging.WARNING, logger="djust.mixins.request"):
            _post(Watching, "kick_sync")
        text = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("hang" in m and "Watching" in m and KEY in m for m in text), text
        _join_workers()

    def test_a_late_write_lands_on_the_view_but_not_in_the_answer(self):
        """The documented contract: an abandoned thread keeps running.

        It can still write into the view object the answer was rendered from.
        That object is discarded after the response, and what it writes now is in
        neither the answer nor the state saved for the next request.
        """
        with SHORT:
            view, _, body = _post(Watching, "kick_sync")
            assert view.status == "failed:TimeoutError"
            RELEASE.set()
            assert FINISHED.wait(10)
        _join_workers()
        assert view.status == "late-write", "an abandoned callback can still write to the view"
        assert "late-write" not in body

    def test_a_late_finish_is_not_delivered_to_handle_async_result(self):
        with SHORT:
            _post(Watching, "kick_sync")
            RELEASE.set()
            assert FINISHED.wait(10)
        _join_workers()
        assert [e for e in LOG if e[0] == "result"] == [
            ("result", "hang", None, "TimeoutError", mock.ANY)
        ]

    def test_the_worker_thread_ends_when_the_callback_does(self):
        with SHORT:
            _post(Watching, "kick_sync")
            assert _workers(), "the abandoned callback is still running on its thread"
        _join_workers()

    def test_the_workers_database_connections_are_closed_when_it_ends(self):
        from django.db import connections

        with SHORT, mock.patch.object(connections, "close_all") as close_all:
            _post(Watching, "kick_fast")
            _join_workers()
        assert close_all.called, "a worker thread must not leak its thread-local connection"


class TestAHangingAsyncDefCallback:
    def test_it_is_cancelled_and_the_thread_ends(self):
        with SHORT:
            view, response, body = _post(Watching, "kick_async")
            assert response.status_code == 200, body
            assert CANCELLED.wait(10), "an async def callback is cancelled at the deadline"
        _join_workers()
        assert view.status == "failed:TimeoutError"

    def test_handle_async_result_gets_the_same_timeout_error(self):
        with SHORT:
            _post(Watching, "kick_async")
            assert CANCELLED.wait(10)
        _join_workers()
        results = [e for e in LOG if e[0] == "result"]
        assert [(e[1], e[3]) for e in results] == [("hang", "TimeoutError")]

    def test_nothing_is_left_pending_or_unawaited(self, caplog):
        caplog.set_level(logging.DEBUG, logger="asyncio")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with SHORT:
                _post(Watching, "kick_async")
                assert CANCELLED.wait(10)
            _join_workers()
            gc.collect()
        bad = [
            str(w.message)
            for w in caught
            if "never awaited" in str(w.message) or "never retrieved" in str(w.message)
        ]
        assert bad == []
        pending = [r.getMessage() for r in caplog.records if "pending" in r.getMessage().lower()]
        assert pending == []

    def test_a_coroutine_that_blocks_the_loop_still_cannot_hold_the_post(self):
        """Cancellation needs the loop; the request thread stops waiting regardless."""
        with SHORT:
            view, response, body = _post(Watching, "kick_blocking_async")
            assert STARTED.is_set()
            assert not FINISHED.is_set(), "the POST waited for a callback blocking its loop"
        assert response.status_code == 200, body
        assert view.status == "failed:TimeoutError"
        _join_workers()


class TestTheBudgetIsPerRequest:
    def test_tasks_queued_behind_the_one_that_hangs_do_not_start(self):
        with SHORT:
            view, _, _ = _post(Watching, "kick_two")
        # Let every worker finish first: a task that was wrongly started has run by now.
        _join_workers()
        assert ("second-ran",) not in LOG, "the budget was spent: nothing more may start"

    def test_a_task_that_never_started_still_hears_about_it(self):
        with SHORT:
            _post(Watching, "kick_two")
        results = {e[1]: (e[3], e[4]) for e in LOG if e[0] == "result"}
        assert results["first"][0] == "TimeoutError"
        assert results["second"][0] == "TimeoutError"
        assert "not started" in results["second"][1], results["second"]
        _join_workers()

    def test_dropped_tasks_are_named_in_a_warning(self, caplog):
        with SHORT, caplog.at_level(logging.WARNING, logger="djust.mixins.request"):
            _post(Watching, "kick_two")
        text = [r.getMessage() for r in caplog.records]
        assert any("not started" in m and "second" in m and KEY in m for m in text), text
        _join_workers()

    def test_work_a_timeout_handler_queues_is_dropped_too(self):
        with SHORT:
            _post(ChainsOnTimeout, "kick_chain")
        _join_workers()
        assert ("second-ran",) not in LOG
        results = {e[1] for e in LOG if e[0] == "result"}
        assert results == {"first", "retry"}

    def test_the_handlers_work_does_not_get_a_fresh_budget_after_mount_s_hung(self):
        """One budget for the whole request, not one per call or per task."""
        with SHORT:
            view, response, _ = _post(MountHangThenHandlerWork, "then_more")
        assert response.status_code == 200
        _join_workers()
        assert ("second-ran",) not in LOG, "the handler's task got its own budget"
        results = {e[1]: e[3] for e in LOG if e[0] == "result"}
        assert results == {"watch": "TimeoutError", "after": "TimeoutError"}


class TestTheBudgetClock:
    """The budget is one clock for the request; driven here by a fake one."""

    def _budget(self, timeout=10.0):
        now = [100.0]
        return request_mixin._BackgroundBudget(timeout, clock=lambda: now[0]), now

    def test_the_clock_starts_at_the_first_task_not_when_the_budget_is_made(self):
        budget, now = self._budget()
        now[0] += 500  # a slow handler: the budget has not been asked for yet
        assert not budget.spent()
        assert budget.remaining() == 10.0
        assert not budget.spent()

    def test_it_counts_down_and_is_spent_at_the_deadline(self):
        budget, now = self._budget()
        budget.remaining()
        now[0] += 4
        assert budget.remaining() == 6.0 and not budget.spent()
        now[0] += 6
        assert budget.remaining() == 0.0 and budget.spent()

    def test_the_time_is_shared_not_restarted_by_a_second_ask(self):
        budget, now = self._budget()
        budget.remaining()
        now[0] += 7
        assert budget.remaining() == 3.0

    def test_no_budget_without_a_configured_deadline(self):
        with OFF:
            assert request_mixin._http_background_budget() is None
        with SHORT:
            assert request_mixin._http_background_budget().timeout == 0.05


class TestWorkThatFinishesInTime:
    def test_a_fast_task_is_delivered_as_before(self):
        with override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False, KEY: 60}):
            view, response, body = _post(Watching, "kick_fast")
        assert response.status_code == 200, body
        assert view.status == "fast-done"
        assert LOG == [("result", "fast", "fast-result", None, "")]
        assert "fast-done" in body and "working" not in body, body
        _join_workers()

    def test_a_fast_async_def_task_is_delivered_as_before(self):
        with override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False, KEY: 60}):
            view, _, body = _post(Watching, "kick_fast_async")
        assert view.status == "fast-async-done", body
        assert LOG == [("result", "fast_async", "fast-async", None, "")]
        _join_workers()

    def test_a_failing_task_still_goes_to_handle_async_result_not_a_500(self, caplog):
        with override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False, KEY: 60}):
            with caplog.at_level(logging.ERROR, logger="djust.mixins.request"):
                view, response, body = _post(Watching, "kick_failing")
        assert response.status_code == 200, body
        assert [(e[1], e[3]) for e in LOG] == [("boom", "ValueError")]
        assert any("start_async task boom" in r.getMessage() for r in caplog.records)
        _join_workers()

    def test_context_variables_reach_the_worker(self):
        """A turn-scoped value (tenant, request id) is still visible to the callback."""
        token = CTX.set("from-the-request")
        try:
            with override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False, KEY: 60}):
                _post(Watching, "kick_reads_context")
        finally:
            CTX.reset(token)
        assert ("ctx", "from-the-request") in LOG
        _join_workers()

    def test_no_noise_when_nothing_is_abandoned(self, caplog):
        with override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False, KEY: 60}):
            with caplog.at_level(logging.WARNING, logger="djust.mixins.request"):
                _post(Watching, "kick_fast")
        assert not [r for r in caplog.records if KEY in r.getMessage()]
        _join_workers()

    def test_a_request_with_no_background_work_starts_no_thread(self):
        with SHORT:
            view, _, _ = _post(Watching, "plain")
        assert view.status == "plain"
        assert not _workers()
        assert LOG == []


class TestEveryBranchOfThePostDispatch:
    def test_stateless_mount_watcher_is_bounded(self):
        """The issue's own case: a ``mount()`` that starts a watcher."""
        with SHORT:
            view, response, body = _post(MountWatcher, "plain")
            assert STARTED.is_set() and not FINISHED.is_set()
        assert response.status_code == 200, body
        assert view.status == "plain"
        _join_workers()

    def test_default_policy_loading_state_run_is_bounded(self):
        """#3635's first-POST run goes through the same budget."""
        short_default = override_settings(LIVEVIEW_CONFIG={KEY: 0.05})
        with short_default:
            initial = _get_request()
            MountWatcher().get(initial)
            view, response, body = _post(MountWatcher, "plain", initial=initial, get=False)
            assert STARTED.is_set() and not FINISHED.is_set()
        assert response.status_code == 200, body
        _join_workers()

    def test_a_deadline_during_mount_work_consumes_the_marker(self):
        """Documented: a slow loader is not retried by the next POST; the page stays loading."""
        marker = "liveview_%s__mount_work" % PATH
        with override_settings(LIVEVIEW_CONFIG={KEY: 0.05}):
            initial = _get_request()
            MountWatcher().get(initial)
            assert marker in initial.session
            _post(MountWatcher, "plain", initial=initial, get=False)
            assert STARTED.is_set()
            assert marker not in initial.session
            STARTED.clear()
            _post(MountWatcher, "plain", initial=initial, get=False)
            assert not STARTED.is_set(), "the abandoned load was started again"
        _join_workers()

    def test_explicit_exposure_mount_work_is_bounded(self):
        class Explicit(MountWatcher):
            exposure_policy = "explicit"

        setattr(sys.modules[__name__], "Explicit", Explicit)
        with SHORT:
            _, response, body = _post(Explicit, "plain")
            assert STARTED.is_set() and not FINISHED.is_set()
        assert response.status_code == 200, body
        _join_workers()

    def test_an_unknown_event_starts_nothing_so_nothing_waits(self):
        with SHORT:
            _, response, body = _post(MountWatcher, "no_such_handler")
        assert response.status_code == 200, body
        assert not STARTED.is_set()
        assert not _workers()

    def test_a_refused_request_starts_nothing(self):
        from djust.decorators import permission_required

        class Guarded(MountWatcher):
            @event_handler()
            @permission_required("auth.change_user")
            def guarded(self, **kwargs):
                pass

        setattr(sys.modules[__name__], "Guarded", Guarded)
        with SHORT:
            _, response, _ = _post(Guarded, "guarded")
        assert response.status_code == 403
        assert not STARTED.is_set()
        assert not _workers()

    def test_an_embedded_childs_work_is_bounded(self):
        with SHORT:
            initial = _get_request()
            HangPage.as_view()(initial)
            initial.session.save()
            request = RequestFactory().post(
                PATH,
                data=json.dumps({"view_id": "kid"}),
                content_type="application/json",
                HTTP_X_DJUST_EVENT="kick",
            )
            request.user, request.session, request.tenant = initial.user, initial.session, None
            response = HangPage.as_view()(request)
            assert STARTED.is_set() and not FINISHED.is_set()
        assert response.status_code == 200, response.content
        _join_workers()


class PokesPage(LiveView):
    """A child's handler queues the hanging work on the page that holds it."""

    exposure_policy = "legacy"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + __name__ + '.PokesPage">'
        '{% live_render "' + __name__ + '.PokingKid" view_id="kid" %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass

    def _hang_sync(self):
        STARTED.set()
        RELEASE.wait(4)
        FINISHED.set()


class PokingKid(Watching):
    exposure_policy = "legacy"
    template = "<div><p id='c'>{{ status }}</p></div>"

    @event_handler()
    def poke_page(self, **kwargs):
        self._parent_view.start_async(self._parent_view._hang_sync, name="page")


class HangKid(Watching):
    exposure_policy = "legacy"
    template = "<div><p id='c'>{{ status }}</p></div>"

    @event_handler()
    def kick(self, **kwargs):
        self.status = "working"
        self.start_async(self._hang_sync, name="hang")


class HangPage(LiveView):
    exposure_policy = "legacy"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + __name__ + '.HangPage">'
        '{% live_render "' + __name__ + '.HangKid" view_id="kid" %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass


setattr(sys.modules[__name__], "PokesPage", PokesPage)
setattr(sys.modules[__name__], "PokingKid", PokingKid)
setattr(sys.modules[__name__], "HangKid", HangKid)
setattr(sys.modules[__name__], "HangPage", HangPage)


class TestAChildHandlersWorkOnItsPageIsBoundedToo:
    def test_the_pages_hanging_task_does_not_hold_the_post(self):
        with SHORT:
            initial = _get_request()
            PokesPage.as_view()(initial)
            initial.session.save()
            request = RequestFactory().post(
                PATH,
                data=json.dumps({"view_id": "kid"}),
                content_type="application/json",
                HTTP_X_DJUST_EVENT="poke_page",
            )
            request.user, request.session, request.tenant = initial.user, initial.session, None
            response = PokesPage.as_view()(request)
            assert STARTED.is_set() and not FINISHED.is_set()
        assert response.status_code == 200, response.content
        _join_workers()


class TestTheConfigKey:
    @pytest.mark.parametrize("value", [0.5, 1, 30, 30.0, 3600])
    def test_a_positive_number_is_the_deadline(self, value):
        from djust.config import http_fallback_background_timeout

        with override_settings(LIVEVIEW_CONFIG={KEY: value}):
            assert http_fallback_background_timeout() == float(value)

    @pytest.mark.parametrize(
        "value",
        [None, 0, -1, 0.0, True, False, "5", "", float("nan"), float("inf"), [], {}],
    )
    def test_anything_else_is_no_deadline(self, value):
        from djust.config import http_fallback_background_timeout

        with override_settings(LIVEVIEW_CONFIG={KEY: value}):
            assert http_fallback_background_timeout() is None

    def test_djust_config_is_read_too(self):
        from djust.config import http_fallback_background_timeout

        with override_settings(DJUST_CONFIG={KEY: 7}):
            assert http_fallback_background_timeout() == 7.0

    def test_the_key_is_registered_with_a_default_of_none(self):
        from djust.config import LiveViewConfig

        assert LiveViewConfig._defaults[KEY] is None


HUGE_OR_BAD = [
    1e10,
    1e30,
    1e300,
    10**30,
    10**400,
    -(10**400),
    -1e300,
    # More than 4300 digits: ``repr()`` raises ``ValueError`` on it.
    pytest.param(10**5000, id="int-5000-digits"),
]


class TestTheValidatorIsTotal:
    """Values the reader, C028 and a real POST must all survive (review of #3641)."""

    @pytest.mark.parametrize("value", HUGE_OR_BAD)
    def test_the_reader_reads_no_deadline_and_does_not_raise(self, value):
        from djust.config import http_fallback_background_timeout

        with override_settings(LIVEVIEW_CONFIG={KEY: value}):
            assert http_fallback_background_timeout() is None

    def test_the_largest_wait_a_thread_accepts_is_the_largest_valid_value(self):
        from djust.config import (
            http_fallback_background_timeout,
            valid_http_fallback_background_timeout,
        )

        assert valid_http_fallback_background_timeout(threading.TIMEOUT_MAX)
        assert not valid_http_fallback_background_timeout(threading.TIMEOUT_MAX * 1.0001)
        with override_settings(LIVEVIEW_CONFIG={KEY: threading.TIMEOUT_MAX}):
            assert http_fallback_background_timeout() == threading.TIMEOUT_MAX

    @pytest.mark.parametrize("value", HUGE_OR_BAD)
    def test_c028_reports_it_and_does_not_raise(self, value):
        from djust.checks.configuration import _check_http_fallback_background_timeout

        with override_settings(LIVEVIEW_CONFIG={KEY: value}):
            errors: list = []
            _check_http_fallback_background_timeout(errors)
        assert [e.id for e in errors] == ["djust.C028"]
        assert "TIMEOUT_MAX" in errors[0].hint or "TIMEOUT_MAX" in errors[0].msg

    @pytest.mark.parametrize("value", HUGE_OR_BAD)
    def test_a_real_post_runs_the_task_with_no_deadline(self, value):
        """No 500, no OverflowError reported to the view: it is simply not bounded."""
        with override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False, KEY: value}):
            view, response, body = _post(Watching, "kick_fast")
        assert response.status_code == 200, body
        assert LOG == [("result", "fast", "fast-result", None, "")]
        assert THREADS["fast"] == threading.current_thread().name
        assert not _workers()

    def test_the_largest_valid_deadline_works_in_a_real_post(self):
        """``Event.wait`` accepts it: no OverflowError, the result is delivered."""
        cfg = {"view_state_in_session": False, KEY: threading.TIMEOUT_MAX}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            view, response, body = _post(Watching, "kick_fast")
        assert response.status_code == 200, body
        assert LOG == [("result", "fast", "fast-result", None, "")]
        _join_workers()


class TestAbandonedThreadsAreCapped:
    def test_the_cap_is_a_documented_constant(self):
        assert request_mixin._HTTP_BACKGROUND_MAX_ABANDONED == 16

    def test_a_worker_is_not_started_while_the_cap_of_abandoned_ones_are_alive(
        self, monkeypatch, caplog
    ):
        monkeypatch.setattr(request_mixin, "_HTTP_BACKGROUND_MAX_ABANDONED", 2)
        with SHORT, caplog.at_level(logging.WARNING, logger="djust.mixins.request"):
            _post(Watching, "kick_sync")
            _post(Watching, "kick_sync")
            assert len(_workers()) == 2
            STARTED.clear()
            LOG.clear()
            view, response, body = _post(Watching, "kick_sync")
            assert response.status_code == 200, body
            assert not STARTED.is_set(), "a third hanging worker was started past the cap"
            assert len(_workers()) == 2
        results = [e for e in LOG if e[0] == "result"]
        assert [(e[1], e[3]) for e in results] == [("hang", "TimeoutError")]
        assert "abandoned" in results[0][4] and "not started" in results[0][4], results[0]
        text = [r.getMessage() for r in caplog.records]
        assert any("abandoned" in m and "hang" in m and "2" in m for m in text), text
        assert view.status == "failed:TimeoutError"
        _join_workers()

    def test_the_cap_lifts_when_the_abandoned_threads_end(self, monkeypatch):
        monkeypatch.setattr(request_mixin, "_HTTP_BACKGROUND_MAX_ABANDONED", 1)
        with SHORT:
            _post(Watching, "kick_sync")
            _join_workers()  # the abandoned callback finishes
            STARTED.clear()
            RELEASE.clear()
            _post(Watching, "kick_sync")
            assert STARTED.is_set(), "a finished abandoned thread must not count against the cap"
        _join_workers()

    def test_a_task_that_finishes_in_time_never_counts(self, monkeypatch):
        monkeypatch.setattr(request_mixin, "_HTTP_BACKGROUND_MAX_ABANDONED", 1)
        cfg = {"view_state_in_session": False, KEY: 60}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            for _ in range(3):
                LOG.clear()
                _post(Watching, "kick_fast")
                assert LOG == [("result", "fast", "fast-result", None, "")]
        _join_workers()


class TestConcurrentPostsCannotOvershootTheCap:
    """The cap is an upper bound, not a check every in-flight POST passes first."""

    CAP = 4
    POSTS = 12

    def _concurrent_posts(self, event="kick_sync"):
        barrier = threading.Barrier(self.POSTS)
        prepared = []
        for _ in range(self.POSTS):
            initial = _get_request()
            Watching().get(initial)
            request = RequestFactory().post(
                PATH,
                data="{}",
                content_type="application/json",
                HTTP_X_DJUST_EVENT=event,
            )
            request.user, request.session, request.tenant = initial.user, initial.session, None
            prepared.append((Watching(), request))
        statuses = []

        def fire(view, request):
            barrier.wait(10)
            statuses.append(view.post(request).status_code)

        threads = [threading.Thread(target=fire, args=pair) for pair in prepared]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        return statuses

    def test_no_more_than_the_cap_start_and_the_rest_are_refused(self, monkeypatch):
        monkeypatch.setattr(request_mixin, "_HTTP_BACKGROUND_MAX_ABANDONED", self.CAP)
        started = []
        original = Watching._hang_sync

        def counting(view):
            started.append(1)
            return original(view)

        monkeypatch.setattr(Watching, "_hang_sync", counting)
        with SHORT:
            statuses = self._concurrent_posts()
            assert statuses == [200] * self.POSTS
            results = [e for e in LOG if e[0] == "result"]
            refused = [e for e in results if "not started" in e[4]]
            # A slot is reserved on the request thread, under a lock, before any worker
            # runs: how many POSTs were refused does not depend on scheduling, and it
            # is the cap's invariant. (`started` is NOT: the budget here is 0.05 s and
            # a worker abandoned before its coroutine began cancels its task without
            # running the callback, so under load fewer than CAP callbacks may start.)
            assert len(results) == self.POSTS
            assert len(refused) == self.POSTS - self.CAP, results
            assert len(_workers()) <= self.CAP
        assert all(e[3] == "TimeoutError" for e in results)
        _join_workers()  # every admitted worker has now begun or been cancelled
        assert len(started) <= self.CAP, "a worker was started past the cap"

    def test_slots_of_tasks_that_finish_are_released_under_concurrency(self, monkeypatch):
        monkeypatch.setattr(request_mixin, "_HTTP_BACKGROUND_MAX_ABANDONED", self.CAP)
        cfg = {"view_state_in_session": False, KEY: 60}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            self._concurrent_posts("kick_fast")
            LOG.clear()
            _post(Watching, "kick_fast")
        assert LOG == [("result", "fast", "fast-result", None, "")], "a slot leaked"
        _join_workers()


class TestACancelledErrorFromTheCallbackIsHandedBack:
    def test_it_reaches_the_request_thread_as_a_cancelled_task(self):
        """The worker hands ``(Exception, CancelledError)`` back; the request skips it."""
        cfg = {"view_state_in_session": False, KEY: 60}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            view, response, body = _post(Watching, "kick_cancelled")
        assert response.status_code == 200, body
        assert LOG == [], "a cancelled task has no result, as on the socket"
        _join_workers()

    def test_the_worker_catches_only_exception_and_cancelled_error(self):
        import inspect

        source = inspect.getsource(request_mixin._start_background_run)
        assert "except BaseException" not in source
        assert "except (Exception, asyncio.CancelledError)" in source


# ``threading`` ends a thread on ``SystemExit`` silently; only pytest's own hook reports it.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
class TestSystemExitFromTheCallbackIsNotSwallowed:
    """Same as with no deadline: it reaches the request thread, not a fake success."""

    def test_without_a_deadline_it_propagates(self):
        with OFF, pytest.raises(SystemExit):
            _post(Watching, "kick_exit")

    def test_with_a_deadline_it_propagates_identically(self):
        cfg = {"view_state_in_session": False, KEY: 60}
        with override_settings(LIVEVIEW_CONFIG=cfg), pytest.raises(SystemExit) as caught:
            _post(Watching, "kick_exit")
        assert "callback asked to exit" in str(caught.value)
        assert LOG == [], "handle_async_result must not hear of it as a success"
        _join_workers()

    def test_the_slot_is_released_when_it_propagates(self, monkeypatch):
        monkeypatch.setattr(request_mixin, "_HTTP_BACKGROUND_MAX_ABANDONED", 1)
        cfg = {"view_state_in_session": False, KEY: 60}
        with override_settings(LIVEVIEW_CONFIG=cfg):
            with pytest.raises(SystemExit):
                _post(Watching, "kick_exit")
            _join_workers()
            _post(Watching, "kick_fast")
        assert LOG == [("result", "fast", "fast-result", None, "")]
        _join_workers()


class TestAnAsyncCallbackRunsOnItsOwnLoop:
    """With the key set an ``async def`` callback leaves the server's event loop.

    Without it, under ASGI, ``async_to_sync`` hands the coroutine back to the
    loop the view runs under; with it the coroutine runs on a private loop in
    the worker thread, so a loop-bound object created elsewhere (an async
    client built at import, the channel layer) is the wrong loop there.
    """

    def _under_a_server_loop(self, cfg):
        from asgiref.sync import async_to_sync, sync_to_async

        seen = {}

        async def server():
            seen["loop"] = asyncio.get_running_loop()
            return await sync_to_async(_post)(Watching, "kick_loop")

        with override_settings(LIVEVIEW_CONFIG=cfg):
            async_to_sync(server)()
        return seen["loop"]

    def test_without_the_key_it_runs_on_the_server_loop(self):
        server_loop = self._under_a_server_loop({"view_state_in_session": False})
        assert LOOPS == [server_loop]

    def test_with_the_key_it_runs_on_a_private_loop(self):
        server_loop = self._under_a_server_loop({"view_state_in_session": False, KEY: 60})
        assert len(LOOPS) == 1
        assert LOOPS[0] is not server_loop
        _join_workers()


class TestSystemCheckC028:
    def _run(self, value, **settings_kw):
        from djust.checks.configuration import _check_http_fallback_background_timeout

        with override_settings(LIVEVIEW_CONFIG={KEY: value}, **settings_kw):
            errors: list = []
            _check_http_fallback_background_timeout(errors)
            return errors

    @pytest.mark.parametrize("value", [0, -3, "5", True, False, float("nan"), float("inf"), []])
    def test_an_invalid_value_is_an_error(self, value):
        found = self._run(value)
        assert [e.id for e in found] == ["djust.C028"]
        assert KEY in found[0].msg

    @pytest.mark.parametrize("value", [None, 0.5, 1, 30, 600.0])
    def test_a_valid_value_or_none_is_clean(self, value):
        assert self._run(value) == []

    def test_a_value_too_long_to_show_reads_properly(self):
        found = self._run(10**5000)
        assert "an integer with too many digits to show" in found[0].msg
        assert "a integer" not in found[0].msg

    def test_it_can_be_suppressed(self):
        assert self._run(0, DJUST_CONFIG={"suppress_checks": ["C028"]}) == []

    def test_the_check_is_wired_into_the_configuration_checks(self):
        import inspect

        from djust.checks import configuration

        assert "_check_http_fallback_background_timeout(errors)" in inspect.getsource(
            configuration.check_configuration
        )
