"""``http_status_of_state``: a page's HTTP status is decided once, from its state (#3688).

``LiveView.get_http_status()`` (#3558) answers ``None`` (200) unless a view
overrides it, so a page that draws its own not-found state answered 200 unless
every such view remembered the override. The issue's own repro is
``ArticleView``: ``GET /articles/missing/`` renders "No article at this address"
under a 200.

``http_status_of_state`` maps a state name to a status and ``state_attribute``
names the attribute that holds the state. A project sets the map once on its
base view; the default stays empty, so no existing view changes.
"""

from __future__ import annotations

import sys

import pytest
from django.test import Client
from django.urls import path

from djust import LiveView


class ArticleView(LiveView):
    """The issue's repro, with the status declared instead of overridden."""

    login_required = False
    http_status_of_state = {"not_found": 404, "forbidden": 403, "gone": 410}
    template = (
        '<div dj-root>{% if state == "not_found" %}<h1>No article at this address</h1>'
        "{% else %}<h1>{{ article }}</h1>{% endif %}</div>"
    )

    def mount(self, request, slug=None, **kwargs):
        self.article = "Hello" if slug == "hello" else ""
        self.state = "ready" if self.article else "not_found"


class ArticleBase(LiveView):
    """A project's base view: the map is set once, here."""

    abstract = True
    login_required = False
    http_status_of_state = {"not_found": 404}


class InheritingView(ArticleBase):
    template = "<div dj-root>{{ state }}</div>"

    def mount(self, request, **kwargs):
        self.state = "not_found"


class CustomAttributeView(LiveView):
    login_required = False
    http_status_of_state = {"missing": 404}
    state_attribute = "load_state"
    template = "<div dj-root>{{ load_state }}</div>"

    def mount(self, request, **kwargs):
        # ``state`` is something else on this view: it must not be consulted.
        self.state = "missing"
        self.load_state = "missing" if request.GET.get("gone") else "ready"


class NoMapView(LiveView):
    """No map declared: a state named ``not_found`` changes nothing (back-compat)."""

    login_required = False
    template = "<div dj-root>{{ state }}</div>"

    def mount(self, request, **kwargs):
        self.state = "not_found"


class OverridingView(LiveView):
    """A view with a real special case keeps its own ``get_http_status()``."""

    login_required = False
    http_status_of_state = {"not_found": 404}
    template = "<div dj-root>{{ state }}</div>"

    def mount(self, request, **kwargs):
        self.state = "not_found"

    def get_http_status(self):
        return 202


class OverridingNoneView(OverridingView):
    def get_http_status(self):
        return None


class OddStateView(LiveView):
    """A state that is not a string never matches (and never raises)."""

    login_required = False
    http_status_of_state = {"not_found": 404}
    template = "<div dj-root>odd</div>"
    odd_state = None

    def mount(self, request, kind="", **kwargs):
        self.state = {
            "none": None,
            "int": 404,
            "list": ["not_found"],
            "dict": {"not_found": 1},
            "missing-key": "unmapped",
        }[kind]


class StreamingArticleView(ArticleView):
    streaming_render = True


for _cls in (
    ArticleView,
    ArticleBase,
    InheritingView,
    CustomAttributeView,
    NoMapView,
    OverridingView,
    OverridingNoneView,
    OddStateView,
    StreamingArticleView,
):
    setattr(sys.modules[__name__], _cls.__name__, _cls)

urlpatterns = [
    path("articles/<slug:slug>/", ArticleView.as_view()),
    path("inherit/", InheritingView.as_view()),
    path("custom/", CustomAttributeView.as_view()),
    path("nomap/", NoMapView.as_view()),
    path("override/", OverridingView.as_view()),
    path("override-none/", OverridingNoneView.as_view()),
    path("odd/<str:kind>/", OddStateView.as_view()),
    path("streamed/<slug:slug>/", StreamingArticleView.as_view()),
]


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]


pytestmark = pytest.mark.django_db


class TestTheIssuesRepro:
    def test_a_missing_article_answers_404_and_keeps_its_own_page(self):
        response = Client().get("/articles/missing/")
        assert response.status_code == 404
        assert b"No article at this address" in response.content

    def test_a_found_article_still_answers_200(self):
        response = Client().get("/articles/hello/")
        assert response.status_code == 200
        assert b"Hello" in response.content

    @pytest.mark.parametrize("state, status", [("forbidden", 403), ("gone", 410)])
    def test_every_status_in_the_map_is_used(self, state, status):
        view = ArticleView()
        view.state = state
        assert view.get_http_status() == status


class TestWhereTheMapIsSet:
    def test_a_map_on_the_base_view_applies_to_its_subclasses(self):
        assert Client().get("/inherit/").status_code == 404

    def test_state_attribute_names_the_attribute_that_holds_the_state(self):
        assert Client().get("/custom/?gone=1").status_code == 404
        assert Client().get("/custom/").status_code == 200

    def test_the_default_map_is_empty(self):
        assert LiveView.http_status_of_state == {}
        assert LiveView.state_attribute == "state"


class TestExistingViewsAreUnchanged:
    def test_a_view_that_declares_no_map_still_answers_200(self):
        assert Client().get("/nomap/").status_code == 200

    def test_an_overriding_view_keeps_its_own_status(self):
        assert Client().get("/override/").status_code == 202

    def test_an_override_returning_none_beats_the_map(self):
        """The override replaces the default entirely, as it always did."""
        assert Client().get("/override-none/").status_code == 200

    @pytest.mark.parametrize("kind", ["none", "int", "list", "dict", "missing-key"])
    def test_a_state_that_is_not_a_mapped_string_answers_200(self, kind):
        assert Client().get(f"/odd/{kind}/").status_code == 200

    def test_a_view_without_the_state_attribute_answers_200(self):
        class Stateless(ArticleView):
            def mount(self, request, **kwargs):
                pass

        assert Stateless().get_http_status() is None


class TestOtherTransports:
    def test_the_streaming_render_answers_the_mapped_status_too(self):
        """``streaming_render`` wraps the response after the status is set."""
        response = Client().get("/streamed/missing/")
        assert response.status_code == 404
        assert Client().get("/streamed/hello/").status_code == 200

    @pytest.mark.asyncio
    async def test_the_socket_mount_carries_no_status_and_still_mounts(self):
        """A socket frame has no status: the map is never read there, the page mounts."""
        pytest.importorskip("channels")
        from asgiref.sync import sync_to_async
        from channels.testing import WebsocketCommunicator
        from django.contrib.sessions.backends.db import SessionStore

        from djust.websocket import LiveViewConsumer

        def _session():
            store = SessionStore()
            store.create()
            return store.session_key

        key = await sync_to_async(_session)()

        class _Scope:
            session_key = key

        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        communicator.scope["session"] = _Scope()
        connected, _ = await communicator.connect()
        assert connected
        await communicator.receive_json_from()
        await communicator.send_json_to(
            {
                "type": "mount",
                "view": f"{__name__}.ArticleView",
                "url": "/articles/missing/",
                "params": {},
            }
        )
        frame = None
        for _ in range(10):
            frame = await communicator.receive_json_from()
            if frame.get("type") in {"mount", "error"}:
                break
        assert frame is not None and frame["type"] == "mount", frame
        assert "No article at this address" in frame.get("html", "")
        await communicator.disconnect()
