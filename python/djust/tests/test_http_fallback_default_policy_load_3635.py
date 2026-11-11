"""A page that loads after its first render leaves its loading state over HTTP (#3635).

On the default session policy (``view_state_in_session`` left ``True``) the GET
renders the page with the work ``mount()`` queued (``start_async`` /
``assign_async``) still unrun, and saves that loading state in the session. A
client with no live transport then POSTs the page: the saved state went back on
top, ``mount()`` did not run (or its tasks were dropped), so the page never
left its loading skeleton.

The GET cannot know the response has no live transport, so it records that the
state it saved is a loading state (``<view key>__mount_work``, a digest of the
saved state). The next POST that finds the saved state unchanged runs
``mount()`` again, puts the saved state back on top and runs the queued work
before the handler, after every refusal, exactly as the stateless policy does.
These tests drive the real ``LiveView.get`` / ``.post`` over a real session.
"""

from __future__ import annotations

import json
import sys

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler, rate_limit
from djust.rate_limit import reset_handler_buckets

pytestmark = pytest.mark.django_db

PATH = "/loading/"
MARKER = "liveview_/loading/__mount_work"
STATE_KEY = "liveview_/loading/"

LOG: list = []


class Loads(LiveView):
    """The page reads its rows after its first render."""

    template = "<div dj-root dj-id='0'><p>{{ status }}</p><p>{{ data }}</p><p>{{ token }}</p></div>"
    loads = 0
    mounts = 0

    def mount(self, request, **kwargs):
        type(self).mounts += 1
        self.status = "idle"
        self.data = "-"
        self.token = "t%d" % type(self).mounts  # differs on every mount()
        self._note = "from-mount"
        self.start_async(self._load, name="load")

    def _load(self):
        type(self).loads += 1
        self.data = "rows"

    @event_handler()
    def select(self, **kwargs):
        self.status = "saw:" + self.data

    @event_handler()
    def count(self, **kwargs):
        self.counter = getattr(self, "counter", 0) + 1
        self.status = "count:%d" % self.counter

    @event_handler()
    @rate_limit(rate=1, burst=1)
    def limited(self, **kwargs):
        self.status = "limited-ran"

    @event_handler()
    def needs_int(self, n: int = 0, **kwargs):
        self.status = "int:%d" % n

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status"] = self.status
        ctx["data"] = self.data
        ctx["token"] = self.token
        ctx["counter"] = getattr(self, "counter", 0)
        return ctx


class NoWork(LiveView):
    template = "<div dj-root dj-id='0'><p>{{ status }}</p></div>"

    def mount(self, request, **kwargs):
        self.status = "idle"

    @event_handler()
    def poke(self, **kwargs):
        self.status = "poked"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status"] = self.status
        return ctx


class ParamsLoads(Loads):
    def mount(self, request, **kwargs):
        type(self).mounts += 1
        self.status = "idle"
        self.data = "-"
        self.token = "t%d" % type(self).mounts

    def handle_params(self, params, uri):
        self.start_async(self._load_scope, params.get("scope", "all"), name="scope")

    def _load_scope(self, scope):
        type(self).loads += 1
        self.data = "scope:" + scope


class AssignLoads(Loads):
    def mount(self, request, **kwargs):
        type(self).mounts += 1
        self.status = "idle"
        self.data = "-"
        self.token = "t%d" % type(self).mounts
        self.assign_async("rows", self._rows)

    def _rows(self):
        type(self).loads += 1
        return "loaded"

    @event_handler()
    def select(self, **kwargs):
        self.status = "saw:%s:%s" % (self.rows.ok, self.rows.result)


class _Client:
    """A non-JSON private attribute: the session cannot hold it, so mount() reruns."""


class RebuiltLoads(Loads):
    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._client = _Client()


for _cls in (Loads, NoWork, ParamsLoads, AssignLoads, RebuiltLoads):
    setattr(sys.modules[__name__], _cls.__name__, _cls)


@pytest.fixture(autouse=True)
def _reset():
    for cls in (Loads, ParamsLoads, AssignLoads, RebuiltLoads):
        cls.loads = 0
        cls.mounts = 0
    reset_handler_buckets()
    yield
    reset_handler_buckets()


def _get(view_cls):
    request = RequestFactory().get(PATH)
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    view_cls().get(request)
    return request


def _post(view_cls, event, session_request, params=None, url=PATH):
    request = RequestFactory().post(
        url,
        data=json.dumps(params or {}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT=event,
    )
    request.user, request.session, request.tenant = (
        session_request.user,
        session_request.session,
        None,
    )
    view = view_cls()
    response = view.post(request)
    return view, response, response.content.decode("utf-8")


class TestTheLoadingSkeletonIsLeft:
    def test_the_first_post_runs_the_work_the_get_left_unrun(self):
        """The reproducer: before the fix ``loads`` stayed 0 and ``data`` stayed ``-``."""
        initial = _get(Loads)
        assert Loads.loads == 0, "the GET never runs mount's queued work"
        view, response, body = _post(Loads, "select", initial)
        assert response.status_code == 200, body
        assert Loads.loads == 1
        assert view.status == "saw:rows"
        assert '"text": "rows"' in body or ">rows<" in body, body

    def test_the_answer_does_not_bring_the_loading_state_back(self):
        initial = _get(Loads)
        _, _, body = _post(Loads, "select", initial)
        assert "saw:rows" in body, body

    def test_a_second_post_does_not_load_again(self):
        initial = _get(Loads)
        _post(Loads, "select", initial)
        view, _, _ = _post(Loads, "select", initial)
        assert Loads.loads == 1
        assert view.data == "rows", "the loaded state was saved by the first POST"

    def test_the_marker_is_gone_once_the_work_ran(self):
        initial = _get(Loads)
        assert MARKER in initial.session
        _post(Loads, "select", initial)
        assert MARKER not in initial.session

    def test_state_a_handler_set_survives_the_load(self):
        initial = _get(Loads)
        _post(Loads, "count", initial)
        view, _, body = _post(Loads, "count", initial)
        assert view.status == "count:2", body
        assert Loads.loads == 1


class TestMountRunsAgainButTheSavedPageWins:
    def test_a_value_mount_picked_for_the_get_is_the_one_the_post_sees(self):
        """``mount()`` reruns on the first POST, but the page the browser shows wins.

        ``token`` differs on every ``mount()`` call; the saved state goes back
        on top, so the first POST still sees the GET's value. (The task closure
        belongs to the second ``mount()``; only the attributes are the GET's.)
        """
        initial = _get(Loads)
        assert initial.session[STATE_KEY]["token"] == "t1"
        view, _, _ = _post(Loads, "select", initial)
        assert Loads.mounts == 2, "mount() runs again on the first POST, as on every stateless POST"
        assert view.token == "t1"
        assert view.data == "rows"


class TestOnlyWhenTheSavedStateIsStillTheLoadingState:
    def test_state_another_writer_saved_since_the_get_is_not_overwritten(self):
        """A socket turn that saved a loaded state in between leaves nothing to load."""
        initial = _get(Loads)
        saved = dict(initial.session[STATE_KEY])
        saved["data"] = "from-a-socket-turn"
        initial.session[STATE_KEY] = saved
        view, _, _ = _post(Loads, "select", initial)
        assert Loads.loads == 0
        assert view.data == "from-a-socket-turn"
        assert Loads.mounts == 1, "mount() does not run over state that is no longer loading"

    def test_a_change_to_private_state_alone_also_invalidates_the_marker(self):
        initial = _get(Loads)
        private = initial.session.get(STATE_KEY + "__private")
        assert private, "the view keeps a private attribute the marker must cover"
        initial.session[STATE_KEY + "__private"] = {**private, "_note": "changed-elsewhere"}
        _post(Loads, "select", initial)
        assert Loads.loads == 0

    def test_a_forged_marker_over_other_state_does_nothing(self):
        initial = _get(Loads)
        initial.session[MARKER] = "0" * 64
        view, _, _ = _post(Loads, "select", initial)
        assert Loads.loads == 0
        assert view.data == "-"

    @pytest.mark.parametrize("value", ["é" * 64, "\u2603", "\udcff"])
    def test_a_non_ascii_marker_is_ignored_not_a_500(self, value):
        """``hmac.compare_digest`` raises ``TypeError`` on a non-ASCII ``str``."""
        initial = _get(Loads)
        initial.session[MARKER] = value
        view, response, body = _post(Loads, "select", initial)
        assert response.status_code == 200, body
        assert Loads.loads == 0

    def test_a_marker_that_is_not_a_string_is_ignored(self):
        initial = _get(Loads)
        initial.session[MARKER] = {"x": 1}
        _post(Loads, "select", initial)
        assert Loads.loads == 0

    def test_a_view_with_no_queued_work_records_nothing(self):
        initial = _get(NoWork)
        assert MARKER not in initial.session
        keys = [k for k in initial.session.keys() if "mount_work" in k]
        assert keys == []

    def test_the_stateless_policy_saves_no_marker_and_still_loads(self):
        with override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False}):
            initial = _get(Loads)
            assert not [k for k in initial.session.keys() if k.startswith("liveview_")]
            view, _, _ = _post(Loads, "select", initial)
        assert Loads.loads == 1 and view.status == "saw:rows"


class TestEveryRefusalStillPrecedesTheWork:
    def test_an_invalid_parameter_starts_no_work_and_keeps_the_marker(self):
        initial = _get(Loads)
        _, response, _ = _post(Loads, "needs_int", initial, params={"n": "notanint"})
        assert response.status_code == 400
        assert Loads.loads == 0
        assert MARKER in initial.session, "a refused POST saves nothing, so the load is still owed"
        view, response, _ = _post(Loads, "needs_int", initial, params={"n": "3"})
        assert response.status_code == 200
        assert Loads.loads == 1 and view.data == "rows"

    def test_a_rate_limited_post_starts_no_work(self, frozen_rate_limit_clock):
        initial = _get(Loads)
        _, first, _ = _post(Loads, "limited", initial)
        _, second, _ = _post(Loads, "limited", initial)
        assert first.status_code == 200
        assert second.status_code == 429
        assert Loads.loads == 1, "only the admitted POST may start mount's reads"

    def test_refused_and_unknown_posts_rerun_mount_each_time_without_consuming_the_marker(self):
        """Documented cost: ``mount()`` runs on every such POST until one is admitted."""
        initial = _get(Loads)
        assert Loads.mounts == 1
        _post(Loads, "needs_int", initial, params={"n": "bad"})
        _post(Loads, "no_such_handler", initial)
        assert Loads.mounts == 3
        assert Loads.loads == 0
        assert MARKER in initial.session

    def test_an_unknown_event_starts_no_work_and_the_load_is_still_owed(self):
        initial = _get(Loads)
        _, response, _ = _post(Loads, "no_such_handler", initial)
        # Refused outright (400), as the socket transports do; nothing runs.
        assert response.status_code == 400
        assert Loads.loads == 0
        assert MARKER in initial.session, "the state saved is still a loading state"
        view, _, _ = _post(Loads, "select", initial)
        assert Loads.loads == 1 and view.status == "saw:rows"

    def test_a_denied_object_is_never_read(self):
        class Denied(Loads):
            def get_object(self):
                return object()

            def has_object_permission(self, request, obj):
                return False

        setattr(sys.modules[__name__], "Denied", Denied)
        Denied.loads = 0
        initial = _get(Denied)
        _, response, _ = _post(Denied, "select", initial)
        assert response.status_code == 403
        assert Denied.loads == 0


class TestOtherWaysToQueueWork:
    def test_work_handle_params_queued_runs_on_the_first_post(self):
        initial = _get(ParamsLoads)
        view, response, body = _post(ParamsLoads, "select", initial, url=PATH + "?scope=mine")
        assert response.status_code == 200, body
        assert view.status == "saw:scope:mine"

    def test_assign_async_is_loaded_on_the_first_post(self):
        initial = _get(AssignLoads)
        view, response, body = _post(AssignLoads, "select", initial)
        assert response.status_code == 200, body
        assert view.status == "saw:True:loaded", body
        assert AssignLoads.loads == 1

    def test_a_view_that_rebuilds_private_attributes_loads_once(self):
        """The branch that already reran ``mount()`` under saved state dropped its tasks."""
        initial = _get(RebuiltLoads)
        view, _, body = _post(RebuiltLoads, "select", initial)
        assert RebuiltLoads.loads == 1, body
        assert view.status == "saw:rows"
        _post(RebuiltLoads, "select", initial)
        assert RebuiltLoads.loads == 1, "the second POST has the loaded state saved"


class TestAnEmbeddedChildsEventLeavesThePageOwed:
    def test_a_child_event_does_not_run_or_consume_the_pages_load(self):
        initial = _get(ChildPage)
        assert MARKER in initial.session
        request = RequestFactory().post(
            PATH,
            data=json.dumps({"view_id": "kid"}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT="ping",
        )
        request.user, request.session, request.tenant = initial.user, initial.session, None
        response = ChildPage.as_view()(request)
        assert response.status_code == 200, response.content
        assert ChildPage.loads == 0, "the page is mounted only to register its children"
        assert MARKER in initial.session, "the page's state was not saved, so the load is owed"
        view, _, _ = _post(ChildPage, "select", initial)
        assert ChildPage.loads == 1 and view.data == "rows"


class Kid(LiveView):
    exposure_policy = "legacy"
    template = "<div><p>kid</p></div>"

    def mount(self, request, **kwargs):
        pass

    @event_handler()
    def ping(self, **kwargs):
        pass


class ChildPage(Loads):
    exposure_policy = "legacy"
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-id="0" dj-view="' + __name__ + '.ChildPage">'
        "<p>{{ data }}</p>"
        '{% live_render "' + __name__ + '.Kid" view_id="kid" %}'
        "</div>"
    )


for _cls in (Kid, ChildPage):
    setattr(sys.modules[__name__], _cls.__name__, _cls)


class TestExplicitExposureIsUntouched:
    def test_an_explicit_exposure_page_writes_no_marker_and_loads_every_post(self):
        class Explicit(Loads):
            exposure_policy = "explicit"

        setattr(sys.modules[__name__], "Explicit", Explicit)
        Explicit.loads = 0
        Explicit.mounts = 0
        initial = _get(Explicit)
        assert MARKER not in initial.session
        _, response, body = _post(Explicit, "select", initial)
        assert response.status_code == 200, body
        assert Explicit.loads == 1


class TestSiblingViewsDoNotTreatTheMarkerAsAnotherViews:
    def test_the_marker_is_one_of_a_views_own_session_keys(self):
        from djust._tenant_state import MOUNT_WORK_SUFFIX, _is_own

        assert MOUNT_WORK_SUFFIX == "__mount_work"
        assert _is_own("liveview_/a/" + MOUNT_WORK_SUFFIX, "liveview_/a/")
        assert not _is_own("liveview_/a/detail/" + MOUNT_WORK_SUFFIX, "liveview_/a/")
