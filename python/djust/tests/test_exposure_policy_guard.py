"""ADR-038's construction guard, after activation (E6).

``exposure_policy="explicit"`` is an enabled policy. The guard still refuses
every configuration that would silently widen exposure or fall back to legacy
exports: an unknown policy, exposure grants on a legacy view, and (decision
D-o) actors combined with the explicit policy. None of these checks may
evaluate a property or a state factory. Configuration errors are
``ExposureConfigurationError`` (an ``ImproperlyConfigured``), whose messages
are framework-authored and carry no view values.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured

from djust import LiveView
from djust._exposure import ExposureConfigurationError
from djust.decorators import state


class ExplicitPolicyView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ shown }}</div>"
    shown = state("SHOWN")

    def mount(self, request, **kwargs):
        self.internal_note = "EXPLICIT_POLICY_INTERNAL_SENTINEL"

    def get_context_data(self, **kwargs):
        return super().get_context_data(shown=self.shown, **kwargs)


def test_explicit_policy_constructs():
    view = ExplicitPolicyView()
    assert view.exposure_policy == "explicit"


def test_inherited_explicit_policy_constructs():
    class Child(ExplicitPolicyView):
        pass

    assert Child().exposure_policy == "explicit"


@pytest.mark.parametrize("policy", ["explict", "", None, [], True])
def test_unknown_policy_is_not_treated_as_legacy(policy):
    with pytest.raises(ImproperlyConfigured, match="exposure_policy"):
        LiveView(exposure_policy=policy)


def test_legacy_policy_preserves_current_context_behavior():
    view = LiveView(exposure_policy="legacy")
    view.title = "Visible"
    assert view.get_context_data()["title"] == "Visible"
    assert "exposure_policy" not in view.get_context_data()
    assert "exposure_policy" not in view.get_state()
    assert "exposure_policy" not in view._capture_snapshot_state()


@pytest.mark.parametrize(
    "options", [{"persist": "server"}, {"client": True}, {"persist": "client", "client": True}]
)
def test_legacy_views_cannot_silently_ignore_exposure_grants(options):
    class View(LiveView):
        value = state(0, **options)

    with pytest.raises(ExposureConfigurationError, match="cannot honor"):
        View()


@pytest.mark.parametrize(
    "options", [{"persist": "server"}, {"client": True}, {"persist": "client", "client": True}]
)
def test_explicit_views_honor_exposure_grants(options):
    class View(LiveView):
        exposure_policy = "explicit"
        value = state(0, **options)

    View()


def test_explicit_policy_refuses_actors():
    # Decision D-o: actors are excluded from explicit exposure in v1.
    class ActorView(LiveView):
        exposure_policy = "explicit"
        use_actors = True

    with pytest.raises(ExposureConfigurationError, match="actors"):
        ActorView()


def test_guard_does_not_evaluate_properties_or_factories():
    def fail():
        raise AssertionError("configuration check must not evaluate state")

    for policy in ("legacy", "explicit"):

        class View(LiveView):
            exposure_policy = policy
            value = state(default_factory=fail)

            @property
            def service(self):
                return fail()

        View()


def test_http_view_renders_under_the_explicit_policy(rf, settings, db):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    settings.DEBUG = True
    request = rf.get("/explicit/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    response = ExplicitPolicyView.as_view()(request)
    assert response.status_code == 200
    body = response.content.decode()
    assert "SHOWN" in body
    assert "EXPLICIT_POLICY_INTERNAL_SENTINEL" not in body


def test_http_view_reports_an_invalid_policy_to_the_developer(rf, settings):
    class InvalidPolicyView(LiveView):
        exposure_policy = "explict"
        template = "<div dj-root></div>"

    settings.DEBUG = True
    # The guard's error is framework-authored and value-free, so it reaches
    # the developer through the protected HTTP entry (decision D-a).
    with pytest.raises(ImproperlyConfigured, match="exposure_policy"):
        InvalidPolicyView.as_view()(rf.get("/invalid/"))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_websocket_mounts_an_explicit_view():
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    session = SessionStore()
    await sync_to_async(session.create)()
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        comm.scope["session"] = SessionStore(session.session_key)
        comm.scope["user"] = AnonymousUser()
        connected, _ = await comm.connect()
        assert connected
        try:
            await comm.receive_json_from(timeout=5)
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}.ExplicitPolicyView", "url": "/x/"}
            )
            response = await comm.receive_json_from(timeout=5)
            assert response["type"] == "mount", response
            assert "SHOWN" in response["html"]
            assert "EXPLICIT_POLICY_INTERNAL_SENTINEL" not in str(response)
        finally:
            await comm.disconnect()
