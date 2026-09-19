"""ADR-038 must never silently opt an explicit-policy view into legacy exports."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from djust import LiveView
from djust.decorators import state


class ExplicitPolicyView(LiveView):
    exposure_policy = "explicit"
    template = "<div>{{ internal_note }}</div>"

    def mount(self, request, **kwargs):
        self.internal_note = "EXPLICIT_POLICY_INTERNAL_SENTINEL"


def test_explicit_policy_fails_before_any_legacy_initialization():
    with pytest.raises(ImproperlyConfigured, match="not yet available"):
        ExplicitPolicyView()


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


def test_inherited_explicit_policy_is_rejected():
    class Child(ExplicitPolicyView):
        pass

    with pytest.raises(ImproperlyConfigured, match="not yet available"):
        Child()


@pytest.mark.parametrize(
    "options", [{"persist": "server"}, {"client": True}, {"persist": "client", "client": True}]
)
def test_legacy_views_cannot_silently_ignore_exposure_grants(options):
    class View(LiveView):
        value = state(0, **options)

    with pytest.raises(ImproperlyConfigured, match="cannot honor"):
        View()


def test_guard_does_not_evaluate_properties_or_factories():
    def fail():
        raise AssertionError("configuration check must not evaluate state")

    class View(LiveView):
        value = state(default_factory=fail)

        @property
        def service(self):
            return fail()

    View()


def test_http_view_cannot_render_using_an_unsupported_policy(rf):
    with pytest.raises(ImproperlyConfigured, match="not yet available"):
        ExplicitPolicyView.as_view()(rf.get("/explicit/"))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_websocket_reports_unsupported_policy_without_mounting():
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False):
        comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        connected, _ = await comm.connect()
        assert connected
        try:
            await comm.receive_json_from(timeout=5)
            await comm.send_json_to(
                {"type": "mount", "view": f"{__name__}.ExplicitPolicyView", "url": "/x/"}
            )
            response = await comm.receive_json_from(timeout=5)
            assert response["type"] == "error", response
            assert "EXPLICIT_POLICY_INTERNAL_SENTINEL" not in str(response)
        finally:
            await comm.disconnect()
