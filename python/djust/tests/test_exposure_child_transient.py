"""Lazy, non-sticky and mixed-policy explicit children (ADR-038 E3-5, D-m).

* A non-sticky explicit child is supported as transient: it renders and handles
  events under the reuse-identity check, and nothing about it is persisted.
* ``lazy=True`` on an explicit child is refused with a static tag error, before
  any placeholder, thunk or registration exists.
* Explicit server persistence under a legacy parent stays refused.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.template import Context, Template, TemplateSyntaxError

from djust import LiveView, event_handler
from djust._exposure import ExposureError
from djust.decorators import state
from djust.tests.test_exposure_child_events import mount
from djust.tests.test_exposure_child_mount import make_request

pytestmark = [pytest.mark.django_db(transaction=True)]

MODULE = __name__


class TransientChild(LiveView):
    exposure_policy = "explicit"
    count = state(1)
    secret = state("TRANSIENT_SECRET_SENTINEL")
    template = '<div>Transient={{ count }}<button dj-click="increment">+</button></div>'

    def mount(self, request, **kwargs):
        self.count = 1
        self.mounts = getattr(self, "mounts", 0) + 1

    @event_handler()
    def increment(self):
        self.count += 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class PersistedNonSticky(TransientChild):
    count = state(1, persist="server")


class StickyPersisted(TransientChild):
    sticky = True
    sticky_id = "persisted"
    count = state(1, persist="server")


class LegacyChild(LiveView):
    template = "<div>Legacy child</div>"


class TransientParent(LiveView):
    exposure_policy = "explicit"
    n = state(0, persist="server")
    template = (
        "<div dj-root>{% load live_tags %}Parent={{ n }}{% live_render "
        f'"{MODULE}.TransientChild" view_id="ns" %}}</div>'
    )

    @event_handler()
    def bump(self):
        self.n += 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(n=self.n, **kwargs)


class AutoIdParent(TransientParent):
    n = state(0, persist="server")
    template = (
        "<div dj-root>{% load live_tags %}Parent={{ n }}{% live_render "
        f'"{MODULE}.TransientChild" %}}</div>'
    )


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [MODULE, "djust.tests.test_exposure_child_events"]


def child_keys(stored):
    return [key for key in stored if key.startswith("_djust_explicit_child_")]


@pytest.mark.asyncio
async def test_transient_child_renders_handles_events_and_writes_no_state():
    runtime, transport, request = await mount(view_class=TransientParent)
    root = runtime.view_instance
    child = root._get_child_view("ns")
    assert child is not None and child._parent_view is root
    assert "Transient=1" in transport.sent[0]["html"]
    assert "TRANSIENT_SECRET_SENTINEL" not in json.dumps(transport.sent)

    await runtime.dispatch_event(
        {"type": "event", "event": "increment", "params": {"view_id": "ns"}}
    )
    assert not transport.errors, transport.errors
    updates = [f for f in transport.sent if f.get("type") == "embedded_update"]
    assert len(updates) == 1 and updates[0]["view_id"] == "ns"
    assert "Transient=2" in updates[0]["html"]

    # A parent turn saves the parent (the persistence path ran) and keeps the
    # identity-checked transient instance, but writes nothing for the child.
    await runtime.dispatch_event({"type": "event", "event": "bump", "params": {}})
    assert not transport.errors, transport.errors
    assert root._get_child_view("ns") is child and child.count == 2 and child.mounts == 1
    stored = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert child_keys(stored) == []
    assert "TRANSIENT_SECRET_SENTINEL" not in json.dumps(stored, default=str)
    assert any(
        isinstance(v, dict) and v.get("state", {}).get("values", {}).get("n") == 1
        for v in stored.values()
    ), "control: the parent's own server state must have been persisted"

    # A fresh connection gets a fresh mount: the transient state was not kept.
    restored, _, _ = await mount(request.session.session_key, view_class=TransientParent)
    assert restored.view_instance._get_child_view("ns").count == 1


@pytest.mark.asyncio
async def test_transient_child_event_is_refused_when_identity_changes():
    runtime, transport, _ = await mount(view_class=TransientParent)
    child = runtime.view_instance._get_child_view("ns")
    child._explicit_child_mount_inputs = '{"object_id": 99}'
    await runtime.dispatch_event(
        {"type": "event", "event": "increment", "params": {"view_id": "ns"}}
    )
    assert child.count == 1
    assert transport.errors and transport.errors[-1]["code"] == "permission_denied"
    assert not any(f.get("type") == "embedded_update" for f in transport.sent)


@pytest.mark.asyncio
async def test_auto_id_transient_child_is_replaced_not_leaked_on_parent_render():
    runtime, transport, _ = await mount(view_class=AutoIdParent)
    root = runtime.view_instance
    [(first_id, first)] = root._get_all_child_views().items()
    await runtime.dispatch_event({"type": "event", "event": "bump", "params": {}})
    assert not transport.errors, transport.errors
    [(second_id, second)] = root._get_all_child_views().items()
    assert second is not first and second_id != first_id
    assert first._djust_child_disposed


def render(parent, request, source):
    parent.request = request
    return Template("{% load live_tags %}" + source).render(
        Context({"view": parent, "request": request})
    )


class ExplicitParent(LiveView):
    exposure_policy = "explicit"


def test_non_sticky_explicit_child_cannot_declare_persisted_state(rf):
    parent = ExplicitParent()
    request = make_request(rf, SessionStore())
    with pytest.raises(TemplateSyntaxError) as exc:
        render(parent, request, f'{{% live_render "{MODULE}.PersistedNonSticky" %}}')
    assert "PersistedNonSticky" not in str(exc.value)
    assert parent._get_all_child_views() == {}
    assert request.session.session_key is None


@pytest.mark.parametrize("lazy", ["True", "'visible'"])
def test_lazy_explicit_child_is_refused_without_placeholder(rf, lazy):
    parent = ExplicitParent()
    request = make_request(rf, SessionStore())
    with pytest.raises(TemplateSyntaxError) as exc:
        render(parent, request, f'{{% live_render "{MODULE}.TransientChild" lazy={lazy} %}}')
    assert str(exc.value) == (
        "{% live_render %} lazy= is not supported for explicit-exposure children."
    )
    assert parent._get_all_child_views() == {}
    assert not getattr(parent, "_lazy_thunks", None)


def test_lazy_legacy_child_keeps_its_placeholder(rf):
    """Legacy control: the lazy placeholder path is unchanged."""
    parent = LiveView()
    request = make_request(rf, SessionStore())
    html = render(parent, request, f'{{% live_render "{MODULE}.LegacyChild" lazy=True %}}')
    assert "<dj-lazy-slot" in html
    assert len(parent._lazy_thunks) == 1


def test_explicit_server_persistence_under_legacy_parent_is_refused(rf):
    parent = LiveView()
    request = make_request(rf, SessionStore())
    with pytest.raises(ExposureError):
        render(parent, request, f'{{% live_render "{MODULE}.StickyPersisted" sticky=True %}}')
    assert parent._get_all_child_views() == {}
    assert request.session.session_key is None


def test_explicit_server_persistence_under_explicit_parent_is_stored(rf):
    """Control for the mixed refusal: the same child persists under explicit ancestry."""
    parent = ExplicitParent()
    request = make_request(rf, SessionStore())
    html = render(parent, request, f'{{% live_render "{MODULE}.StickyPersisted" sticky=True %}}')
    assert "Transient=1" in html
    stored = SessionStore(request.session.session_key).load()
    assert len(child_keys(stored)) == 1
