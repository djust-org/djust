"""A page whose mount() queues work leaves its loading state over HTTP (#3635).

The GET renders the page with the work ``mount()`` queued (``start_async`` /
``assign_async``) still unrun. A client with no live transport then POSTs the
page: 2.0 keeps no legacy view state between requests (the ``__mount_work``
marker over the saved state went with it), so every POST runs ``mount()``,
then the queued work right before the handler, after every refusal. These
tests drive the real ``LiveView.get`` / ``.post`` over a real session.
"""

from __future__ import annotations

import json
import sys

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler, rate_limit
from djust.rate_limit import reset_handler_buckets

pytestmark = pytest.mark.django_db

PATH = "/loading/"

LOG: list = []


class Loads(LiveView):
    """The page reads its rows after its first render."""

    exposure_policy = "legacy"
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
    exposure_policy = "legacy"
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

    def test_every_post_loads_again(self):
        """2.0 keeps no legacy view state: each POST mounts and runs the work."""
        initial = _get(Loads)
        for expected in (1, 2):
            view, response, body = _post(Loads, "select", initial)
            assert response.status_code == 200, body
            assert Loads.loads == expected
            assert view.status == "saw:rows"
        assert not [k for k in initial.session.keys() if k.startswith("liveview_")]


class TestEveryRefusalStillPrecedesTheWork:
    def test_an_invalid_parameter_starts_no_work(self):
        initial = _get(Loads)
        _, response, _ = _post(Loads, "needs_int", initial, params={"n": "notanint"})
        assert response.status_code == 400
        assert Loads.loads == 0
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

    def test_refused_and_unknown_posts_rerun_mount_each_time(self):
        """Documented cost: ``mount()`` runs on every such POST until one is admitted."""
        initial = _get(Loads)
        assert Loads.mounts == 1
        _post(Loads, "needs_int", initial, params={"n": "bad"})
        _post(Loads, "no_such_handler", initial)
        assert Loads.mounts == 3
        assert Loads.loads == 0

    def test_an_unknown_event_starts_no_work(self):
        initial = _get(Loads)
        _, response, _ = _post(Loads, "no_such_handler", initial)
        # Refused outright (400), as the socket transports do; nothing runs.
        assert response.status_code == 400
        assert Loads.loads == 0
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


class TestAnEmbeddedChildsEventDoesNotLoadThePage:
    def test_a_child_event_does_not_run_the_pages_load(self):
        initial = _get(ChildPage)
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


class TestExplicitExposure:
    def test_an_explicit_exposure_page_loads_every_post(self):
        class Explicit(Loads):
            exposure_policy = "explicit"

        setattr(sys.modules[__name__], "Explicit", Explicit)
        Explicit.loads = 0
        Explicit.mounts = 0
        initial = _get(Explicit)
        _, response, body = _post(Explicit, "select", initial)
        assert response.status_code == 200, body
        assert Explicit.loads == 1
