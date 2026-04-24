"""Tests for BulkActionProgressWidget + @admin_action_with_progress.

Per Action Tracker #124, the three "rule" tests here
(``test_bulk_progress_job_cancel_flips_done_and_cancelled_flags``,
``test_bulk_progress_non_owner_user_gets_403``,
``test_bulk_progress_non_staff_gets_403``) were written BEFORE the
implementation in ``djust/admin_ext/progress.py``.
"""

from __future__ import annotations

import threading

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase

pytestmark = pytest.mark.admin


def _make_user(username, *, is_staff=True, pk=1):
    """Build a lightweight user stand-in (no DB required)."""
    User = get_user_model()
    user = User(username=username, is_staff=is_staff)
    user.pk = pk
    user.id = pk
    return user


class TestJobModel(TestCase):
    """Tests for the Job dataclass."""

    def test_job_update_appends_log_and_caps_at_50(self):
        """Progress updates should append to the log and cap at 50 entries."""
        from djust.admin_ext.progress import Job

        job = Job(
            job_id="x",
            action_label="Test",
            user_id=1,
            admin_site_name="djust_admin",
            redirect_url="/",
        )
        for i in range(60):
            job.update(current=i, total=60, message=f"step {i}")
        assert job.current == 59
        assert job.total == 60
        assert len(job.log) == 50
        # Oldest entries trimmed, newest kept.
        assert job.log[-1] == "step 59"
        assert job.log[0] == "step 10"

    def test_job_update_truncates_overlong_message(self):
        """Very long messages are clamped to avoid unbounded memory growth."""
        from djust.admin_ext.progress import Job, _MAX_MESSAGE_CHARS

        job = Job(
            job_id="trunc",
            action_label="T",
            user_id=1,
            admin_site_name="djust_admin",
            redirect_url="/",
        )
        long_msg = "x" * (_MAX_MESSAGE_CHARS + 500)
        job.update(message=long_msg)
        assert len(job.message) == _MAX_MESSAGE_CHARS
        assert job.message.endswith("...")

    def test_jobs_dict_lru_evicts_beyond_cap(self):
        """Inserting more than ``_MAX_JOBS`` entries into ``_JOBS`` evicts
        the oldest (FIFO)."""
        from djust.admin_ext.progress import (
            Job,
            _JOBS,
            _MAX_JOBS,
            _store_job,
        )

        _JOBS.clear()
        try:
            # Fill to cap + 1.
            first_id = None
            for i in range(_MAX_JOBS + 1):
                jid = f"job-{i}"
                if first_id is None:
                    first_id = jid
                _store_job(
                    jid,
                    Job(
                        job_id=jid,
                        action_label="T",
                        user_id=1,
                        admin_site_name="djust_admin",
                        redirect_url="/",
                    ),
                )
            assert len(_JOBS) == _MAX_JOBS, "_JOBS should never exceed _MAX_JOBS; got %d" % len(
                _JOBS
            )
            # Oldest entry (job-0) should have been evicted.
            assert first_id not in _JOBS
            # Newest entry should be present.
            assert f"job-{_MAX_JOBS}" in _JOBS
        finally:
            _JOBS.clear()


class TestAdminActionWithProgressDecorator(TestCase):
    """Tests for @admin_action_with_progress."""

    def _setup_site(self):
        from djust.admin_ext import DjustAdminSite, DjustModelAdmin
        from djust.admin_ext.progress import _JOBS, admin_action_with_progress

        # Use User as a stand-in model since it ships with Django.
        User = get_user_model()

        site = DjustAdminSite(name="djust_admin")

        class MyAdmin(DjustModelAdmin):
            @admin_action_with_progress(description="Do thing")
            def do_thing(self, request, queryset, progress):
                progress.update(current=1, total=1, message="done")

            actions = ["do_thing"]

        site.register(User, MyAdmin)
        return site, User, MyAdmin, _JOBS

    def test_decorator_redirects_to_progress_url(self):
        """Calling the decorated action returns an HTTP redirect."""
        from django.http import HttpResponseRedirect

        site, model, admin_cls, jobs = self._setup_site()
        # Resolve URLs using the site's urlpatterns.

        # We can't easily include urls. Instead: call the action and verify
        # it raises NoReverseMatch (acceptable — decorator tried to reverse)
        # or completes with a valid redirect. To keep the test hermetic, we
        # monkey-patch django.urls.reverse to return a dummy URL.
        from djust.admin_ext import progress as progress_mod

        original_reverse = progress_mod.reverse

        def fake_reverse(name, kwargs=None, *a, **kw):
            if kwargs:
                return "/fake/%s/" % list(kwargs.values())[0]
            return "/fake/"

        progress_mod.reverse = fake_reverse
        try:
            admin_instance = admin_cls(model, site)
            request = RequestFactory().post("/")
            request.user = _make_user("tester")
            qs = model.objects.none()
            response = admin_instance.do_thing(request, qs)
            assert isinstance(response, HttpResponseRedirect)
            assert response.url.startswith("/fake/")
        finally:
            progress_mod.reverse = original_reverse
            jobs.clear()

    def test_background_thread_runs_to_completion_and_sets_done(self):
        """After the action fires, the background thread should complete
        and flip ``done=True`` on the job."""
        from djust.admin_ext import progress as progress_mod

        site, model, admin_cls, jobs = self._setup_site()
        done_event = threading.Event()

        class TrackingAdmin(admin_cls):
            @progress_mod.admin_action_with_progress(description="Track")
            def do_thing(self, request, queryset, progress):
                progress.update(current=5, total=5, message="complete")
                done_event.set()

            actions = ["do_thing"]

        original_reverse = progress_mod.reverse
        progress_mod.reverse = lambda *a, **kw: "/fake/"
        try:
            admin_instance = TrackingAdmin(model, site)
            request = RequestFactory().post("/")
            request.user = _make_user("tester2", pk=2)
            admin_instance.do_thing(request, model.objects.none())
            assert done_event.wait(timeout=2), "Background thread did not finish"
            # Find the freshly created job.
            job = next(iter(jobs.values()))
            # Wait briefly for the finally-clause to set done=True.
            for _ in range(20):
                if job.done:
                    break
                threading.Event().wait(0.05)
            assert job.done is True
            assert job.current == 5
        finally:
            progress_mod.reverse = original_reverse
            jobs.clear()

    def test_progress_error_captured_in_job_error(self):
        """An exception in the decorated body flips ``job.error`` to the
        generic user-facing message, and the raw text is kept in the
        private ``_error_raw`` slot for server-side diagnostics."""
        from djust.admin_ext import progress as progress_mod

        site, model, admin_cls, jobs = self._setup_site()
        error_event = threading.Event()

        class FailingAdmin(admin_cls):
            @progress_mod.admin_action_with_progress(description="Fail")
            def do_thing(self, request, queryset, progress):
                error_event.set()
                raise ValueError("boom")

            actions = ["do_thing"]

        original_reverse = progress_mod.reverse
        progress_mod.reverse = lambda *a, **kw: "/fake/"
        try:
            admin_instance = FailingAdmin(model, site)
            request = RequestFactory().post("/")
            request.user = _make_user("tester3", pk=3)
            admin_instance.do_thing(request, model.objects.none())
            assert error_event.wait(timeout=2)
            job = next(iter(jobs.values()))
            for _ in range(20):
                if job.done:
                    break
                threading.Event().wait(0.05)
            assert job.done is True
            # Public ``error`` is the generic user-facing message --
            # raw exception text must NOT leak to the browser.
            assert job.error is not None
            assert "boom" not in job.error
            # Raw exception text is retained privately for ops.
            assert job._error_raw is not None
            assert "boom" in job._error_raw
        finally:
            progress_mod.reverse = original_reverse
            jobs.clear()

    def test_progress_error_logged_at_error_level(self):
        """Exceptions in the action body must be logged at ERROR level
        with traceback (via ``logger.exception``). Locks Action Tracker
        #124 — the doc claim was ambiguous in earlier versions."""
        from djust.admin_ext import progress as progress_mod

        site, model, admin_cls, jobs = self._setup_site()
        error_event = threading.Event()

        class FailingAdmin(admin_cls):
            @progress_mod.admin_action_with_progress(description="Fail")
            def do_thing(self, request, queryset, progress):
                error_event.set()
                raise ValueError("boom-err-level")

            actions = ["do_thing"]

        original_reverse = progress_mod.reverse
        progress_mod.reverse = lambda *a, **kw: "/fake/"
        try:
            admin_instance = FailingAdmin(model, site)
            request = RequestFactory().post("/")
            request.user = _make_user("tester-err-level", pk=99)
            with self.assertLogs("djust.admin_ext.progress", level="ERROR") as log_ctx:
                admin_instance.do_thing(request, model.objects.none())
                assert error_event.wait(timeout=2)
                # Wait for the background thread's finally to flip done=True.
                job = next(iter(jobs.values()))
                for _ in range(40):
                    if job.done:
                        break
                    threading.Event().wait(0.05)
                assert job.done is True
            # Confirm the expected ERROR record is present.
            err_records = [r for r in log_ctx.records if r.levelname == "ERROR"]
            assert err_records, "expected at least one ERROR-level record; got %r" % [
                r.levelname for r in log_ctx.records
            ]
            # logger.exception() attaches exc_info to the record.
            assert any(r.exc_info is not None for r in err_records), (
                "expected an ERROR record with exc_info (i.e. via "
                "logger.exception); got %r" % err_records
            )
            # The raw exception message is present in the server log --
            # only the user-facing job.error is generic.
            assert any(
                "boom-err-level" in r.getMessage()
                or (r.exc_info and "boom-err-level" in str(r.exc_info[1]))
                for r in err_records
            )
        finally:
            progress_mod.reverse = original_reverse
            jobs.clear()

    def test_action_error_user_facing_message_is_generic(self):
        """Raw sensitive messages (e.g. credentials) must never surface
        to the user via ``job.error``; the raw text lives only in
        the server log + private ``_error_raw``."""
        from djust.admin_ext import progress as progress_mod

        site, model, admin_cls, jobs = self._setup_site()
        error_event = threading.Event()
        SECRET = "DB password is hunter2 — do NOT leak"

        class LeakyAdmin(admin_cls):
            @progress_mod.admin_action_with_progress(description="Leak")
            def do_thing(self, request, queryset, progress):
                error_event.set()
                raise RuntimeError(SECRET)

            actions = ["do_thing"]

        original_reverse = progress_mod.reverse
        progress_mod.reverse = lambda *a, **kw: "/fake/"
        try:
            admin_instance = LeakyAdmin(model, site)
            request = RequestFactory().post("/")
            request.user = _make_user("leak-tester", pk=7)
            with self.assertLogs("djust.admin_ext.progress", level="ERROR") as log_ctx:
                admin_instance.do_thing(request, model.objects.none())
                assert error_event.wait(timeout=2)
                job = next(iter(jobs.values()))
                for _ in range(40):
                    if job.done:
                        break
                    threading.Event().wait(0.05)
                assert job.done is True
            # User-facing error: generic, no sensitive substring.
            assert SECRET not in (job.error or "")
            assert "server logs" in (job.error or "").lower()
            # Private slot retains raw text for operator debugging.
            assert SECRET in (job._error_raw or "")
            # Server log captured the raw exception (via logger.exception).
            found = any(
                SECRET in r.getMessage() or (r.exc_info and SECRET in str(r.exc_info[1]))
                for r in log_ctx.records
            )
            assert found, "expected raw exception text in server log records"
        finally:
            progress_mod.reverse = original_reverse
            jobs.clear()

    def test_action_body_checking_cancelled_exits_early(self):
        """Cooperative cancellation: when the action body polls
        ``progress.cancelled`` and returns early, ``current`` remains
        below ``total`` and the cancel message propagates to ``job.message``."""
        from djust.admin_ext import progress as progress_mod

        site, model, admin_cls, jobs = self._setup_site()
        started = threading.Event()
        proceed = threading.Event()

        class CoopAdmin(admin_cls):
            @progress_mod.admin_action_with_progress(description="Coop cancel")
            def do_thing(self, request, queryset, progress):
                total = 1000
                progress.update(current=0, total=total, message="starting")
                started.set()
                # Block until the test flips cancelled from outside.
                proceed.wait(timeout=2)
                for i in range(total):
                    if progress.cancelled:
                        progress.update(message="Cancelled by user.")
                        return
                    progress.update(current=i + 1, total=total)

            actions = ["do_thing"]

        original_reverse = progress_mod.reverse
        progress_mod.reverse = lambda *a, **kw: "/fake/"
        try:
            admin_instance = CoopAdmin(model, site)
            request = RequestFactory().post("/")
            request.user = _make_user("coop", pk=77)
            admin_instance.do_thing(request, model.objects.none())
            assert started.wait(timeout=2), "action body didn't start"
            job = next(iter(jobs.values()))
            # Simulate the user clicking Cancel on the progress page.
            job.cancelled = True
            proceed.set()
            for _ in range(40):
                if job.done:
                    break
                threading.Event().wait(0.05)
            assert job.done is True
            assert job.current < 1000, "cooperative cancel should have short-circuited the loop"
            assert "cancel" in job.message.lower()
        finally:
            progress_mod.reverse = original_reverse
            jobs.clear()


class TestBulkProgressWidgetAuth(TestCase):
    """Tests for BulkActionProgressWidget mount-time auth checks."""

    def _make_job(self, user_pk=1, job_id="abc"):
        from djust.admin_ext.progress import Job, _JOBS

        job = Job(
            job_id=job_id,
            action_label="Test",
            user_id=user_pk,
            admin_site_name="djust_admin",
            redirect_url="/",
        )
        _JOBS[job_id] = job
        return job

    def test_bulk_progress_non_staff_gets_403(self):
        """RULE #3: Non-staff users get PermissionDenied, even if the job
        exists. ``is_staff`` must be re-checked on top of
        ``login_required=True``."""
        from djust.admin_ext.progress import BulkActionProgressWidget, _JOBS

        self._make_job(user_pk=42, job_id="job-nonstaff")
        try:
            widget = BulkActionProgressWidget()
            request = RequestFactory().get("/")
            request.user = _make_user("nonstaff", is_staff=False, pk=42)
            with pytest.raises(PermissionDenied):
                widget.mount(request, job_id="job-nonstaff")
        finally:
            _JOBS.clear()

    def test_bulk_progress_non_owner_user_gets_403(self):
        """RULE #2: User B hitting user A's progress URL gets 403.
        Job IDs are UUIDs, but we still verify the owner."""
        from djust.admin_ext.progress import BulkActionProgressWidget, _JOBS

        self._make_job(user_pk=100, job_id="job-owner")
        try:
            widget = BulkActionProgressWidget()
            request = RequestFactory().get("/")
            # A different staff user (pk=101) tries to view user 100's job.
            request.user = _make_user("intruder", is_staff=True, pk=101)
            with pytest.raises(PermissionDenied):
                widget.mount(request, job_id="job-owner")
        finally:
            _JOBS.clear()

    def test_bulk_progress_job_cancel_flips_done_and_cancelled_flags(self):
        """RULE #1: cancellation is terminal — both ``done`` and
        ``cancelled`` must be set."""
        from djust.admin_ext.progress import BulkActionProgressWidget, _JOBS

        job = self._make_job(user_pk=50, job_id="job-cancel")
        try:
            widget = BulkActionProgressWidget()
            request = RequestFactory().get("/")
            request.user = _make_user("owner", is_staff=True, pk=50)
            # Prevent the background polling thread from being spawned
            # during this unit test.
            widget.start_async = lambda *a, **kw: None
            widget.mount(request, job_id="job-cancel")
            # Simulate the user clicking "Cancel".
            widget.cancel()
            assert job.done is True
            assert job.cancelled is True
        finally:
            _JOBS.clear()


class TestStoreJobConcurrency(TestCase):
    """Concurrency guarantees for ``_store_job`` (LRU + lock)."""

    def test_store_job_concurrent_inserts_stay_under_cap(self):
        """Many concurrent inserters must never push _JOBS over _MAX_JOBS.

        Without the lock, N threads could each observe
        ``len(_JOBS) > _MAX_JOBS`` at the same time and each
        ``popitem`` — over-evicting past the FIFO invariant. This test
        spawns 50 threads x 20 inserts (1000 total) and asserts the
        final size is EXACTLY _MAX_JOBS.
        """
        from djust.admin_ext.progress import (
            Job,
            _JOBS,
            _MAX_JOBS,
            _store_job,
        )

        _JOBS.clear()
        try:
            threads = []
            errors: list = []

            def _insert(thread_idx: int) -> None:
                try:
                    for i in range(20):
                        jid = f"t{thread_idx}-j{i}"
                        _store_job(
                            jid,
                            Job(
                                job_id=jid,
                                action_label="T",
                                user_id=1,
                                admin_site_name="djust_admin",
                                redirect_url="/",
                            ),
                        )
                except Exception as exc:  # pragma: no cover — diagnostic
                    errors.append(exc)

            for t in range(50):
                th = threading.Thread(target=_insert, args=(t,))
                threads.append(th)
                th.start()
            for th in threads:
                th.join(timeout=5)

            assert not errors, f"unexpected exceptions from worker threads: {errors!r}"
            # Exactly at the cap — never over (that's the lock's job)
            # and never under (we inserted 1000, far more than 500).
            assert len(_JOBS) == _MAX_JOBS, (
                "expected _JOBS size == _MAX_JOBS under concurrent inserts; "
                f"got {len(_JOBS)} (likely lock missing or broken)"
            )
        finally:
            _JOBS.clear()


class TestActionLabelBounded(TestCase):
    """Fix #4 — ``action_label`` must respect _MAX_MESSAGE_CHARS."""

    def test_action_label_truncated_at_max_chars(self):
        """A multi-MB ``short_description`` must not blow out memory.

        The decorator's ``description`` (which becomes
        ``wrapper.short_description`` and then ``job.action_label``) is
        clamped to _MAX_MESSAGE_CHARS at Job construction, matching the
        existing per-message cap.
        """
        from djust.admin_ext import DjustAdminSite, DjustModelAdmin
        from djust.admin_ext import progress as progress_mod
        from djust.admin_ext.progress import (
            _JOBS,
            _MAX_MESSAGE_CHARS,
            admin_action_with_progress,
        )

        User = get_user_model()
        site = DjustAdminSite(name="djust_admin_label_trunc")

        huge = "x" * 10_000

        class HugeLabelAdmin(DjustModelAdmin):
            @admin_action_with_progress(description=huge)
            def do_thing(self, request, queryset, progress):
                progress.update(current=1, total=1)

            actions = ["do_thing"]

        site.register(User, HugeLabelAdmin)

        original_reverse = progress_mod.reverse
        progress_mod.reverse = lambda *a, **kw: "/fake/"
        try:
            admin_instance = HugeLabelAdmin(User, site)
            request = RequestFactory().post("/")
            request.user = _make_user("label-trunc", pk=333)
            admin_instance.do_thing(request, User.objects.none())
            job = next(iter(_JOBS.values()))
            assert len(job.action_label) == _MAX_MESSAGE_CHARS
            assert job.action_label.endswith("...")
        finally:
            progress_mod.reverse = original_reverse
            _JOBS.clear()


class TestRunActionRedirectIntercept(TestCase):
    """Fix #1 — ``ModelListView.run_action`` must convert an action's
    HttpResponseRedirect return value into a client-side ``redirect``
    push_event, since bare HTTP responses can't flow over the LiveView
    WebSocket dispatcher."""

    def test_admin_action_with_progress_triggers_client_redirect(self):
        """End-to-end: decorating an action with @admin_action_with_progress
        and dispatching it via ``run_action`` must queue a
        ``push_event('redirect', {'url': <progress_url>})`` targeting the
        progress page. Covers the blocker identified in the Stage 11
        review: an HttpResponseRedirect returned to the WS handler is
        otherwise silently dropped, leaving the user stuck on the
        changelist."""
        from django.http import HttpResponseRedirect

        from djust.admin_ext import DjustAdminSite, DjustModelAdmin
        from djust.admin_ext import progress as progress_mod
        from djust.admin_ext.progress import _JOBS, admin_action_with_progress
        from djust.admin_ext.views import (
            ModelListView,
            register_admin_view,
        )

        User = get_user_model()
        site = DjustAdminSite(name="djust_admin_redir")

        class MyAdmin(DjustModelAdmin):
            @admin_action_with_progress(description="Do thing")
            def do_thing(self, request, queryset, progress):
                progress.update(current=1, total=1)

            actions = ["do_thing"]

        site.register(User, MyAdmin)

        original_reverse = progress_mod.reverse
        progress_mod.reverse = lambda *a, **kw: "/djust-admin/djust-progress/abc/"
        try:
            admin_instance = MyAdmin(User, site)

            view_id = "test-run-action-redir"
            register_admin_view(view_id, admin_site=site, model=User, model_admin=admin_instance)

            view = ModelListView()
            view._view_registry_id = view_id
            request = RequestFactory().post("/")
            request.user = _make_user("runner", pk=501)
            view.request = request
            view.selected_ids = [1, 2, 3]
            view.select_all = False
            view._pending_push_events = []

            # ``@event_handler`` is pure metadata (returns the function
            # unchanged), so we call the method directly — the same
            # code path the WebSocket dispatcher walks.
            view.run_action(action_name="do_thing")

            # A redirect push_event must have been queued...
            assert view._pending_push_events, (
                "run_action should have queued a 'redirect' push_event; got empty list"
            )
            ev_name, payload = view._pending_push_events[0]
            assert ev_name == "redirect", f"expected 'redirect' event, got {ev_name!r}"
            assert "url" in payload
            # ...and the URL must target the progress page.
            assert "djust-progress" in payload["url"], (
                f"expected djust-progress URL, got {payload['url']!r}"
            )

            # Sanity: the raw result (before push_event conversion) was
            # an HttpResponseRedirect — confirms we tested the right code path.
            job = next(iter(_JOBS.values()))
            assert job is not None
            # And verify HttpResponseRedirect is still the return type
            # of the decorator (for backwards compatibility with stock
            # Django admin actions).
            admin_instance2 = MyAdmin(User, site)
            raw_result = admin_instance2.do_thing(request, User.objects.none())
            assert isinstance(raw_result, HttpResponseRedirect)
        finally:
            progress_mod.reverse = original_reverse
            _JOBS.clear()
