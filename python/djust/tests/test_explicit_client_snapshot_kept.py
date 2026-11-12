"""The explicit client snapshot keeps the guarantees the legacy files also pinned.

``test_snapshot_token_tenant_3328`` and ``test_skip_render_noop_snapshot_3246``
were mostly about the legacy signed snapshot, which 2.0 removed, and went with
it. Each also held cases for the explicit client snapshot
(``state(persist="client", client=True)``), which stays; those cases live here,
unchanged in what they assert:

* an explicit client token minted under one tenant is refused under another
  (#3328);
* a ``_skip_render`` turn on an explicit view answers a noop carrying a fresh
  token, on the view route and the component route, and a reconnect restores
  from it; a turn whose commit failed sends no token (#3246).
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.components.descriptors.base import LiveComponent as DescriptorComponent
from djust.decorators import event_handler, state

_MOD = __name__
_URL = "/explicit-snapshot-kept/"
TOKEN = "state_snapshot_signed"


def _new_session() -> SessionStore:
    store = SessionStore()
    store.create()
    return store


# --------------------------------------------------------------------------- #
# #3328: an explicit client token is bound to the tenant
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.tenants
class TestExplicitSnapshotIsTenantBound:
    def _request(self, tenant_id, session):
        from django.contrib.auth.models import AnonymousUser

        from djust.tenants.resolvers import TenantInfo

        request = RequestFactory().get(_URL)
        request.user = AnonymousUser()
        request.session = session
        request.tenant = TenantInfo(tenant_id=tenant_id)
        return request

    def test_explicit_client_token_from_tenant_a_is_refused_for_tenant_b(self):
        from djust._exposure_snapshots import snapshot_codec

        class Explicit(LiveView):
            exposure_policy = "explicit"
            template = "<div dj-root>{{ label }}</div>"
            label = state("initial", persist="client", client=True)

        session = _new_session()
        view = Explicit()
        view.label = "secret-of-acme"
        token = snapshot_codec(view, self._request("acme", session)).capture(view)

        assert snapshot_codec(Explicit(), self._request("acme", session)).restore(token) == {
            "label": "secret-of-acme"
        }
        assert snapshot_codec(Explicit(), self._request("globex", session)).restore(token) is None


# --------------------------------------------------------------------------- #
# #3246: an explicit skip-render noop carries the token (ViewRuntime harness)
# --------------------------------------------------------------------------- #
class Pinger(DescriptorComponent):
    template = "<b>ping</b>"

    def mount(self, **kwargs):
        pass

    def get_context_data(self):
        return {}

    @event_handler()
    def step(self, **kwargs):
        self.send_parent("stepped")


class QuietExplicitPage(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ pinger }}<span>{{ count }}</span><i>{{ step_name }}</i></div>"
    count = state(0, persist="server")
    step_name = state("initial", persist="client", client=True)
    pinger = Pinger()

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, step_name=self.step_name, **kwargs)

    def _step(self):
        self.count += 1
        self.step_name = "step-%d" % self.count
        self._skip_render = True

    def handle_component_event(self, component_id, event, data):
        self._step()

    @event_handler()
    def quiet_step(self, **kwargs):
        self._step()


async def _explicit_mount(request, **extra):
    from djust.runtime import ViewRuntime
    from djust.tests.test_exposure_runtime import make_request
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MOD]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": f"{_MOD}.QuietExplicitPage", "url": request.path, **extra}
        )
    assert not transport.errors, transport.errors
    return runtime, transport


def _explicit_event(view, route):
    if route == "view":
        return {"type": "event", "event": "quiet_step", "params": {}}
    return {
        "type": "event",
        "event": "step",
        "params": {"component_id": view.pinger.component_id},
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["view", "component"])
async def test_an_explicit_skip_render_noop_carries_the_token_and_a_reconnect_restores(route):
    from djust.tests.test_exposure_runtime import make_request

    request = await sync_to_async(make_request)()
    runtime, transport = await _explicit_mount(request)
    transport.sent.clear()

    await runtime.dispatch_event(_explicit_event(runtime.view_instance, route))
    assert not transport.errors, transport.errors
    [noop] = [f for f in transport.sent if f.get("type") in ("noop", "patch", "html_update")]
    assert noop["type"] == "noop", noop
    assert noop.get(TOKEN), noop

    fresh = await sync_to_async(make_request)(request.session.session_key)
    restored, _ = await _explicit_mount(
        fresh, state_snapshot={"view_slug": f"{_MOD}.QuietExplicitPage", "state_json": noop[TOKEN]}
    )
    view = restored.view_instance
    assert (view.count, view.step_name) == (1, "step-1")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["view", "component"])
async def test_an_explicit_skip_render_turn_whose_commit_failed_sends_no_token(route, monkeypatch):
    from djust.tests.test_exposure_runtime import make_request

    request = await sync_to_async(make_request)()
    runtime, transport = await _explicit_mount(request)
    transport.sent.clear()

    def refuse(view, request):
        raise OSError("storage down")

    monkeypatch.setattr("djust._exposure_sessions.save_server_state", refuse)
    await runtime.dispatch_event(_explicit_event(runtime.view_instance, route))
    assert transport.errors, "the failed commit must answer an error; vacuous"
    assert not [f for f in transport.sent if f.get("type") == "noop"], transport.sent
    assert not [f for f in transport.sent if f.get(TOKEN)], transport.sent
