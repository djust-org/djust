"""``LIVEVIEW_CONFIG["http_status_of_state"]`` and the V025 check (#3706, follow-up to #3688).

#3688 shipped ``http_status_of_state`` as a class attribute, set once on a base
view. A project with several base views, or views from a package it does not
own, wanted the map once for the whole project. The rule, decided in #3706:

* **the view's own attribute wins, then the config**; there is no merge, so a
  view that declares a map answers by that map alone;
* an attribute the view does not declare is read from ``LIVEVIEW_CONFIG``; a
  view that declares ``{}`` declares "no map" and opts out of the project's;
* neither set: the page answers 200 as before.

V025 reports a view that overrides ``get_http_status()`` while a project-wide
map is configured, so the per-view copies the map exists to remove do not creep
back.
"""

from __future__ import annotations

import logging
import sys

import pytest
from django.test import Client
from django.urls import path

from djust import LiveView
from djust.checks.components import check_get_http_status_overrides
from djust.config import config, valid_http_status_map
from djust import state

PROJECT_MAP = {"not_found": 404, "gone": 410}


class ProjectView(LiveView):
    """Declares nothing: the project's map applies."""

    exposure_policy = "explicit"
    state = state()
    login_required = False
    template = "<div dj-root>{{ state }}</div>"

    def mount(self, request, **kwargs):
        self.state = request.GET.get("s", "not_found")


class OwnMapView(ProjectView):
    """Declares its own map: it wins, and the project's is not merged in."""

    http_status_of_state = {"not_found": 403}


class OptOutView(ProjectView):
    """``{}`` is a declaration: this view answers 200 whatever the project says."""

    http_status_of_state = {}


class InheritsOptOut(OptOutView):
    pass


class InheritsFromBase(ProjectView):
    pass


class OverridesView(ProjectView):
    def get_http_status(self):
        return 202


class StreamedProjectView(ProjectView):
    streaming_render = True


for _cls in (
    ProjectView,
    OwnMapView,
    OptOutView,
    InheritsOptOut,
    InheritsFromBase,
    OverridesView,
    StreamedProjectView,
):
    setattr(sys.modules[__name__], _cls.__name__, _cls)

urlpatterns = [
    path("project/", ProjectView.as_view()),
    path("own/", OwnMapView.as_view()),
    path("optout/", OptOutView.as_view()),
    path("inherits-optout/", InheritsOptOut.as_view()),
    path("inherits/", InheritsFromBase.as_view()),
    path("overrides/", OverridesView.as_view()),
    path("streamed/", StreamedProjectView.as_view()),
]


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]


@pytest.fixture
def project_map(settings):
    """The map in ``LIVEVIEW_CONFIG``, the way a project writes it."""
    settings.LIVEVIEW_CONFIG = {
        **getattr(settings, "LIVEVIEW_CONFIG", {}),
        "http_status_of_state": PROJECT_MAP,
    }
    return PROJECT_MAP


pytestmark = pytest.mark.django_db


class TestPrecedence:
    def test_neither_set_answers_200(self):
        assert Client().get("/project/").status_code == 200

    def test_the_config_applies_to_a_view_that_declares_nothing(self, project_map):
        assert Client().get("/project/").status_code == 404
        assert Client().get("/project/?s=gone").status_code == 410
        assert Client().get("/project/?s=ready").status_code == 200

    def test_a_subclass_of_such_a_view_reads_the_config_too(self, project_map):
        assert Client().get("/inherits/").status_code == 404

    def test_the_views_own_attribute_wins_over_the_config(self, project_map):
        assert Client().get("/own/").status_code == 403  # not the project's 404

    def test_the_view_attribute_is_not_merged_with_the_config(self, project_map):
        """``gone`` is only in the project's map: a view that declares a map answers by it alone."""
        assert Client().get("/own/?s=gone").status_code == 200

    def test_the_view_attribute_alone_still_works_with_no_config(self):
        assert Client().get("/own/").status_code == 403

    def test_a_view_declaring_an_empty_map_opts_out(self, project_map):
        assert Client().get("/optout/").status_code == 200

    def test_the_opt_out_is_inherited(self, project_map):
        assert Client().get("/inherits-optout/").status_code == 200

    def test_an_instance_assignment_is_the_views_own_declaration(self, project_map):
        view = ProjectView()
        view.state = "not_found"
        assert view.get_http_status() == 404
        view.http_status_of_state = {"not_found": 410}
        assert view.get_http_status() == 410
        view.http_status_of_state = {}
        assert view.get_http_status() is None

    def test_an_overriding_view_keeps_its_own_answer(self, project_map):
        assert Client().get("/overrides/").status_code == 202

    def test_the_default_class_attribute_is_still_an_empty_mapping(self):
        assert LiveView.http_status_of_state == {}
        assert not LiveView.http_status_of_state

    def test_an_empty_map_declared_on_a_view_is_not_the_framework_default(self):
        """The precedence rule tells "declared empty" from "not declared" by identity."""
        assert OptOutView.http_status_of_state is not LiveView.http_status_of_state
        assert ProjectView.http_status_of_state is LiveView.http_status_of_state


class TestOtherPaths:
    def test_the_streaming_render_answers_the_configured_status(self, project_map):
        assert Client().get("/streamed/").status_code == 404

    def test_the_status_is_read_in_one_place(self):
        """``get_http_status`` is the only reader of the attribute and of the config key, so no
        transport reads the map itself (an AST walk: comments and docstrings do not count)."""
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        attribute_readers, key_readers = set(), set()
        for source in root.rglob("*.py"):
            if "tests" in source.parts:
                continue
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                where = str(source.relative_to(root))
                if isinstance(node, ast.Attribute) and node.attr == "http_status_of_state":
                    attribute_readers.add(where)
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "http_status_of_state"
                ):
                    key_readers.add(where)
        assert sorted(attribute_readers) == ["mixins/request.py"], attribute_readers
        assert sorted(key_readers) == ["checks/components.py", "mixins/request.py"], key_readers

    @pytest.mark.asyncio
    async def test_the_socket_mount_carries_no_status_and_still_mounts(self, project_map):
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
            {"type": "mount", "view": f"{__name__}.ProjectView", "url": "/project/", "params": {}}
        )
        frame = None
        for _ in range(10):
            frame = await communicator.receive_json_from()
            if frame.get("type") in {"mount", "error"}:
                break
        assert frame is not None and frame["type"] == "mount", frame
        await communicator.disconnect()


class TestConfigValidation:
    @pytest.fixture(autouse=True)
    def _restore(self):
        yield
        config.reset()

    @pytest.mark.parametrize(
        "value",
        [
            {},
            {"not_found": 404},
            {"gone": 410, "forbidden": 403, "teapot": 418},
            {"ok": 200},
        ],
    )
    def test_a_valid_map(self, value):
        assert valid_http_status_map(value)

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "404",
            404,
            [("not_found", 404)],
            {"not_found": "404"},
            {"not_found": True},
            {"not_found": 404.0},
            {"not_found": 99},
            {"not_found": 600},
            {"not_found": None},
            {404: "not_found"},
            {1: 404},
        ],
    )
    def test_an_invalid_map(self, value):
        assert not valid_http_status_map(value)

    def test_the_default_is_an_empty_map(self):
        assert config.get("http_status_of_state") == {}

    def test_set_stores_a_valid_map(self):
        config.set("http_status_of_state", {"not_found": 404})
        assert config.get("http_status_of_state") == {"not_found": 404}

    def test_update_stores_a_valid_map(self):
        config.update({"http_status_of_state": {"not_found": 404}})
        assert config.get("http_status_of_state") == {"not_found": 404}

    @pytest.mark.parametrize("bad", ["404", {"not_found": "404"}, {"not_found": 42}, [1]])
    def test_set_replaces_an_invalid_map_by_the_default_and_says_so(self, bad, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            config.set("http_status_of_state", bad)
        assert config.get("http_status_of_state") == {}
        assert "http_status_of_state" in caplog.text

    def test_update_replaces_an_invalid_map_by_the_default_and_says_so(self, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            config.update({"http_status_of_state": {"not_found": "404"}})
        assert config.get("http_status_of_state") == {}
        assert "http_status_of_state" in caplog.text

    def test_the_settings_value_is_validated_when_it_loads(self, settings, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            settings.LIVEVIEW_CONFIG = {"http_status_of_state": {"not_found": "404"}}
        assert config.get("http_status_of_state") == {}
        assert "http_status_of_state" in caplog.text

    def test_a_reload_replays_the_sanitized_value_not_the_written_one(self, settings):
        config.set("http_status_of_state", {"not_found": "bad"})
        config.reload_from_settings()
        assert config.get("http_status_of_state") == {}

    def test_update_records_the_sanitized_value_so_a_reload_replays_it(self):
        config.update({"http_status_of_state": {"not_found": "bad"}})
        config.reload_from_settings()
        assert config.get("http_status_of_state") == {}
        config.update({"http_status_of_state": {"gone": 410}})
        config.reload_from_settings()
        assert config.get("http_status_of_state") == {"gone": 410}

    def test_a_write_to_another_key_keeps_the_map(self):
        config.set("http_status_of_state", {"not_found": 404})
        config.set("css_framework", "plain")
        config.update({"render_labels": False})
        assert config.get("http_status_of_state") == {"not_found": 404}

    def test_override_settings_follows_the_map_in_and_out(self, settings):
        settings.LIVEVIEW_CONFIG = {"http_status_of_state": {"not_found": 404}}
        assert config.get("http_status_of_state") == {"not_found": 404}
        settings.LIVEVIEW_CONFIG = {}
        assert config.get("http_status_of_state") == {}

    def test_the_stored_map_is_a_copy(self):
        config.set("http_status_of_state", {"not_found": 404})
        config.get("http_status_of_state")["injected"] = 500
        assert config.get("http_status_of_state") == {"not_found": 404}

    def test_an_invalid_config_page_answers_200_not_500(self, settings):
        settings.LIVEVIEW_CONFIG = {"http_status_of_state": "not a map"}
        assert Client().get("/project/").status_code == 200


class PlainView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"


class OverridesDirectly(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    def get_http_status(self):
        return 404


class OverridesViaMixin:
    def get_http_status(self):
        return 404


class MixinUser(OverridesViaMixin, LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"


class AbstractBaseOverride(LiveView):
    exposure_policy = "explicit"
    abstract = True

    def get_http_status(self):
        return 404


class ChildOfAbstractOverride(AbstractBaseOverride):
    template = "<div dj-root></div>"


class SubOverridesAgain(OverridesDirectly):
    def get_http_status(self):
        return 410


def _v025(*classes):
    labels = {"%s.%s" % (c.__module__, c.__qualname__) for c in classes}
    return [
        m
        for m in check_get_http_status_overrides(None)
        if any(m.msg.startswith(label + " ") for label in labels)
    ]


class TestV025:
    def test_silent_without_a_project_map(self):
        assert _v025(OverridesDirectly, MixinUser, ChildOfAbstractOverride) == []

    def test_silent_for_a_view_that_does_not_override(self, project_map):
        assert _v025(PlainView, ProjectView) == []

    def test_an_override_is_reported_when_a_map_is_configured(self, project_map):
        (message,) = _v025(OverridesDirectly)
        assert message.id == "djust.V025"
        assert message.level == 30  # WARNING: an override can be a legitimate special case
        assert "get_http_status" in message.msg
        assert "http_status_of_state" in message.msg
        assert "super()" in message.hint

    def test_an_override_inherited_from_a_mixin_is_reported_against_the_mixin(self, project_map):
        """The class whose body defines the method is the one to edit, and it is named once."""
        assert _v025(MixinUser) == []  # MixinUser defines nothing itself
        (message,) = _v025(OverridesViaMixin)
        assert message.msg.startswith("%s.OverridesViaMixin " % __name__)

    def test_an_override_on_an_abstract_base_is_reported_once_for_the_base(self, project_map):
        found = _v025(AbstractBaseOverride, ChildOfAbstractOverride)
        assert len(found) == 1
        assert found[0].msg.startswith("%s.AbstractBaseOverride " % __name__)

    def test_each_overriding_class_is_reported(self, project_map):
        assert len(_v025(OverridesDirectly, SubOverridesAgain)) == 2

    def test_it_follows_the_config_in_and_out(self, settings):
        assert _v025(OverridesDirectly) == []
        settings.LIVEVIEW_CONFIG = {"http_status_of_state": PROJECT_MAP}
        assert len(_v025(OverridesDirectly)) == 1
        settings.LIVEVIEW_CONFIG = {"http_status_of_state": {}}
        assert _v025(OverridesDirectly) == []

    def test_it_can_be_suppressed(self, project_map, settings):
        settings.DJUST_CONFIG = {"suppress_checks": ["V025"]}
        assert _v025(OverridesDirectly) == []

    def test_the_frameworks_own_default_is_never_reported(self, project_map):
        text = " ".join(m.msg for m in check_get_http_status_overrides(None))
        assert "djust.mixins.request" not in text
        assert "djust.live_view.LiveView " not in text
