"""ADR-038 E2-0/E2-1: the framework context provider manifest.

Every framework provider declares a ``ProviderContract`` that is folded into
the view's ``ExposureContract`` schema digest, and writes its keys through the
explicit render context, which refuses an application kwarg or write that would
replace a provider value (or be replaced by one). Legacy views keep their
current behaviour, which each explicit case pins as a control.

Only the staged construction gate is bypassed. Runtime cases use the real
``ViewRuntime`` with DB sessions.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.test import override_settings

from djust import LiveView, event_handler
from djust._exposure import ExposureContract, ExposureError, ProviderContract
from djust._exposure_sessions import server_state_adapter
from djust.audio import AudioMixin, Sound, SoundBank
from djust.decorators import action, state
from djust.drafts import DraftModeMixin
from djust.pwa.mixins import OfflineMixin, PWAMixin
from djust.runtime import ViewRuntime
from djust.tenants.mixin import TenantMixin
from djust.tenants.resolvers import TenantInfo
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport
from djust.wizard import WizardMixin


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


# ---------------------------------------------------------------------------
# Mixin views. Each provider value carries a sentinel; templates render only a
# marker derived from it, so frames prove the provider ran without echoing it.
# ---------------------------------------------------------------------------


class Base(LiveView):
    exposure_policy = "explicit"
    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)
    time_travel_enabled = True

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def increment(self):
        self.count += 1


class TenantView(TenantMixin, Base):
    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)
    template = "<div dj-root>{{ count }}{% if tenant %}|provided{% endif %}</div>"

    def resolve_tenant(self, request):
        return TenantInfo("alpha", name="TENANT_SENTINEL")


class StepForm(forms.Form):
    answer = forms.CharField(initial="WIZARD_SENTINEL")


class WizardView(WizardMixin, Base):
    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)
    wizard_steps = [{"name": "one", "title": "One", "form_class": StepForm}]
    template = "<div dj-root>{{ count }}{% if total_steps %}|provided{% endif %}</div>"


class DraftView(DraftModeMixin, Base):
    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)
    draft_key = "DRAFT_SENTINEL"
    template = "<div dj-root>{{ count }}{% if draft_key %}|provided{% endif %}</div>"


class AudioView(AudioMixin, Base):
    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)
    audio_banks = {"AUDIO_SENTINEL": SoundBank({"ding": Sound("ding.mp3")})}
    template = "<div dj-root>{{ count }}{% if djust_audio_manifest %}|provided{% endif %}</div>"


class PWAView(PWAMixin, Base):
    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)
    pwa_name = "PWA_SENTINEL"
    template = "<div dj-root>{{ count }}{% if pwa_config %}|provided{% endif %}</div>"


class OfflineView(OfflineMixin, Base):
    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)
    template = "<div dj-root>{{ count }}{% if offline_state %}|provided{% endif %}</div>"

    def get_offline_state(self):
        return {"is_online": True, "marker": "OFFLINE_SENTINEL"}


class RustRenderView(Base):
    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)
    template = "<div dj-root>{{ count }}{% if DATE_FORMAT %}|provided{% endif %}</div>"


MIXIN_VIEWS = {
    "tenant": (TenantView, "tenant"),
    "wizard": (WizardView, "form_data"),
    "drafts": (DraftView, "draft_key"),
    "audio": (AudioView, "djust_audio_manifest"),
    "pwa": (PWAView, "pwa_config"),
    "offline": (OfflineView, "offline_state"),
}


def _instance(view_class, policy, monkeypatch):
    monkeypatch.setattr(view_class, "exposure_policy", policy)
    view = view_class()
    if issubclass(view_class, TenantMixin):
        view._tenant = TenantInfo("alpha", name="TENANT_SENTINEL")
        view._tenant_resolved = True
    return view


# ---------------------------------------------------------------------------
# E2-0: the manifest type and the schema digest.
# ---------------------------------------------------------------------------


def test_provider_contract_is_validated_and_immutable():
    contract = ProviderContract("app.badge", rendered={"badge"}, tracked=["count"])
    assert contract.rendered == frozenset({"badge"})
    assert contract.tracked == frozenset({"count"})
    assert contract.persisted == frozenset() and contract.client == frozenset()
    with pytest.raises(AttributeError):
        contract.rendered = frozenset()
    for bad in (
        {"name": "App Badge", "rendered": {"badge"}},
        {"name": "app.badge", "rendered": {"view"}},
        {"name": "app.badge", "rendered": "badge"},
        {"name": "app.badge", "rendered": {"__proto__"}},
        {"name": "app.badge", "rendered": {"badge"}, "persisted": {"other"}},
        {"name": "app.badge", "rendered": {"badge"}, "client": {"other"}},
        {"name": "app.badge", "rendered": {"badge"}, "codec": "pickle"},
    ):
        with pytest.raises(ExposureError):
            ProviderContract(**bad)


def test_view_contract_registers_the_existing_framework_providers():
    from djust.components.descriptors.base import LiveComponent, TypedState

    class Toggle(LiveComponent):
        class State(TypedState):
            active: bool = False

    class Page(LiveView):
        exposure_policy = "explicit"
        menu = Toggle()

        @action
        def save_item(self):
            return None

    providers = {p.name: p for p in ExposureContract.from_view_class(Page).providers}
    assert providers["djust.components"].rendered == {"menu"}
    assert providers["djust.actions"].rendered == {"save_item"}
    assert providers["djust.streams"].rendered == {"streams"}
    assert providers["djust.rust_render"].rendered == {"csrf_token", "DATE_FORMAT", "TIME_FORMAT"}
    tenant = {p.name: p for p in ExposureContract.from_view_class(TenantView).providers}
    assert tenant["djust.tenants"].rendered == {"tenant"}


def _page(*, component=False, act=False):
    """Same owner name each call, so only the providers can differ."""
    from djust.components.descriptors.base import LiveComponent, TypedState

    class Toggle(LiveComponent):
        class State(TypedState):
            active: bool = False

    namespace = {"exposure_policy": "explicit", "count": state(0, persist="server")}
    if component:
        namespace["menu"] = Toggle()
    if act:

        def save_item(self):
            return None

        namespace["save_item"] = action(save_item)
    page = type("Page", (LiveView,), namespace)
    page.__qualname__ = "Page"
    return page


@pytest.mark.parametrize("change", ["component", "action", "mixin_key", "mixin_contract"])
def test_schema_digest_changes_when_a_provider_contract_changes(monkeypatch, change):
    if change in ("component", "action"):
        before = ExposureContract.from_view_class(_page()).schema
        changed_page = _page(component=True) if change == "component" else _page(act=True)
        after = ExposureContract.from_view_class(changed_page).schema
    elif change == "mixin_key":
        before = ExposureContract.from_view_class(TenantView).schema
        monkeypatch.setattr(TenantView, "tenant_context_name", "organization")
        after = ExposureContract.from_view_class(TenantView).schema
    else:
        from djust.wizard import WIZARD_PROVIDER

        before = ExposureContract.from_view_class(WizardView).schema
        changed = ProviderContract(
            WIZARD_PROVIDER.name,
            rendered=WIZARD_PROVIDER.rendered,
            tracked=WIZARD_PROVIDER.tracked | {"wizard_extra"},
        )
        from djust.wizard import WizardMixin as Mixin

        monkeypatch.setattr(Mixin, "_djust_context_providers", (changed,))
        after = ExposureContract.from_view_class(WizardView).schema
    assert before != after


def test_contract_without_providers_keeps_its_digest():
    from djust._exposure import FieldExposure

    plain = ExposureContract("app.View", {"a": FieldExposure()})
    assert plain.providers == ()
    assert plain.schema == ExposureContract("app.View", {"a": FieldExposure()}, providers=()).schema
    with_provider = ExposureContract(
        "app.View",
        {"a": FieldExposure()},
        providers=(ProviderContract("app.badge", rendered={"badge"}),),
    )
    assert with_provider.schema != plain.schema


def test_two_providers_cannot_declare_the_same_key():
    class OtherTenantMixin:
        _djust_context_providers = (ProviderContract("app.tenant", rendered={"tenant"}),)

    class Clash(OtherTenantMixin, TenantView):
        count = state(0, persist="server")
        navigation = state("NAV", persist="client", client=True)

    with pytest.raises(ExposureError, match="collision"):
        ExposureContract.from_view_class(Clash)


class BadgeMixin:
    _djust_context_providers = (ProviderContract("app.badge", rendered={"badge"}),)

    def get_context_data(self, **kwargs):
        from djust._exposure_providers import provide_context

        context = super().get_context_data(**kwargs)
        provide_context(self, context, "app.badge", "badge", "BADGE")
        return context


class BadgeView(BadgeMixin, Base):
    count = state(0, persist="server")
    navigation = state("NAV", persist="client", client=True)
    template = "<div dj-root>{{ count }}{{ badge }}</div>"


# ---------------------------------------------------------------------------
# Runtime helpers.
# ---------------------------------------------------------------------------


async def _mount(request, view_class, **extra):
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": __name__ + "." + view_class.__name__,
                "url": request.path,
                **extra,
            }
        )
    return runtime, transport


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_provider_change_invalidates_the_stored_envelope(staged, monkeypatch):
    request = await sync_to_async(make_request)()
    runtime, transport = await _mount(request, BadgeView)
    assert not transport.errors, transport.errors
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert runtime.view_instance.count == 1
    assert "BADGE" in json.dumps(transport.sent), "the provider must have rendered"

    # Control: an unchanged provider restores the stored state.
    same, _ = await _mount(
        await sync_to_async(make_request)(request.session.session_key), BadgeView
    )
    assert same.view_instance.count == 1

    monkeypatch.setattr(
        BadgeMixin,
        "_djust_context_providers",
        (ProviderContract("app.badge", rendered={"badge"}, tracked={"count"}),),
    )
    changed, changed_transport = await _mount(
        await sync_to_async(make_request)(request.session.session_key), BadgeView
    )
    assert not changed_transport.errors, changed_transport.errors
    assert changed.view_instance.count == 0, "a provider change must invalidate stored state"


# ---------------------------------------------------------------------------
# E2-1: framework mixins register their keys.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mixin", sorted(MIXIN_VIEWS))
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_mixin_provider_key_as_application_kwarg(staged, monkeypatch, mixin, policy):
    view_class, key = MIXIN_VIEWS[mixin]
    view = _instance(view_class, policy, monkeypatch)
    if policy == "legacy":
        # Control: the provider silently replaces the application's value.
        context = view.get_context_data(**{key: "APP_SENTINEL"})
        assert key in context and context[key] != "APP_SENTINEL"
    else:
        with pytest.raises(ExposureError, match="collision"):
            view.get_context_data(**{key: "APP_SENTINEL"})


@pytest.mark.parametrize("mixin", sorted(MIXIN_VIEWS))
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_mixin_provider_key_written_after_super(staged, monkeypatch, mixin, policy):
    view_class, key = MIXIN_VIEWS[mixin]

    class Writer(view_class):
        count = state(0, persist="server")
        navigation = state("NAV", persist="client", client=True)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context[key] = "APP_SENTINEL"
            return context

    view = _instance(Writer, policy, monkeypatch)
    if policy == "legacy":
        # Control: the application's write silently replaces the provider.
        assert view.get_context_data()[key] == "APP_SENTINEL"
    else:
        with pytest.raises(ExposureError, match="collision"):
            view.get_context_data()


@pytest.mark.parametrize("mixin", sorted(MIXIN_VIEWS))
def test_explicit_provider_keys_are_reserved_from_context_processors(
    staged, monkeypatch, rf, mixin
):
    view_class, key = MIXIN_VIEWS[mixin]
    view = _instance(view_class, "explicit", monkeypatch)
    context = view.get_context_data()
    assert key in view._explicit_context_provider_keys
    view._get_context_processors = lambda: []
    view._get_resolved_processors = lambda paths: [lambda request: {key: "PROCESSOR"}]
    with pytest.raises(ExposureError, match="reserved|collision"):
        view._apply_context_processors(context, rf.get("/"))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("key", ["csrf_token", "DATE_FORMAT"])
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_rust_render_keys_are_framework_providers(staged, monkeypatch, key, policy):
    class Supplier(RustRenderView):
        count = state(0, persist="server")
        navigation = state("NAV", persist="client", client=True)
        template = "<div dj-root>{{ " + key + " }}</div>"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context[key] = "APP_SENTINEL"
            return context

    monkeypatch.setattr(Supplier, "exposure_policy", policy)
    monkeypatch.setitem(globals(), "Supplier", Supplier)
    request = await sync_to_async(make_request)()
    runtime, transport = await _mount(request, Supplier)
    frames = json.dumps(transport.sent)
    if policy == "legacy":
        # Control: the framework defers to the application's value.
        assert not transport.errors, transport.errors
        assert "APP_SENTINEL" in frames
    else:
        assert "APP_SENTINEL" not in frames
        assert not any(frame.get("type") == "mount" for frame in transport.sent)
        assert any(frame.get("type") == "error" for frame in transport.sent)


def _stored(request):
    from django.contrib.sessions.backends.db import SessionStore

    return json.dumps(dict(SessionStore(request.session.session_key).load()))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mixin", sorted(MIXIN_VIEWS) + ["rust_render"])
async def test_explicit_provider_values_never_reach_state_destinations(staged, mixin, rf):
    from djust._exposure import explicit_debug_projection
    from djust._exposure_snapshots import snapshot_codec
    from djust.observability.registry import register_view, unregister_view
    from djust.observability.views import view_assigns

    view_class = RustRenderView if mixin == "rust_render" else MIXIN_VIEWS[mixin][0]
    request = await sync_to_async(make_request)()
    with override_settings(DEBUG=True):
        runtime, transport = await _mount(request, view_class)
        assert not transport.errors, transport.errors
        await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert not transport.errors, transport.errors
    view = runtime.view_instance
    frames = json.dumps(transport.sent)
    # The provider ran: its render-only marker reached the page.
    assert "|provided" in frames
    sentinels = ["SENTINEL"]
    csrf = getattr(view, "_cached_csrf_token", None)
    if mixin == "rust_render":
        assert csrf, "the Rust bridge must have provided a csrf token"
        sentinels.append(csrf)

    persisted = await sync_to_async(make_request)(request.session.session_key)
    persisted.tenant = getattr(view.request, "tenant", None)
    adapter = await sync_to_async(server_state_adapter)(view, persisted)
    assert await adapter.aload() == {"count": 1}
    stored = await sync_to_async(_stored)(request)
    token = next(
        frame["state_snapshot_signed"]
        for frame in reversed(transport.sent)
        if frame.get("state_snapshot_signed")
    )
    codec = await sync_to_async(snapshot_codec)(view, request)
    assert codec.restore(token) == {"navigation": "NAV"}
    debug = json.dumps(explicit_debug_projection(view))
    history = json.dumps([snapshot.to_dict() for snapshot in view._time_travel_buffer._buf])
    assert history != "[]", "time travel must have recorded the event"
    with override_settings(DEBUG=True):
        register_view("exposure-provider-test", view)
        try:
            response = await sync_to_async(view_assigns)(
                rf.get("/debug/", {"session_id": "exposure-provider-test"})
            )
        finally:
            unregister_view("exposure-provider-test")
    assert response.status_code == 200
    for sentinel in sentinels:
        assert sentinel not in stored
        assert sentinel not in token
        assert sentinel not in frames
        assert sentinel not in debug
        assert sentinel not in history
        assert sentinel not in response.content.decode()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.__setitem__("tenant", "APP"),
        lambda c: c.update({"tenant": "APP"}),
        lambda c: c.update(tenant="APP"),
        lambda c: c.setdefault("tenant", "APP"),
        lambda c: c.pop("tenant"),
        lambda c: c.__delitem__("tenant"),
        lambda c: c.__ior__({"tenant": "APP"}),
        lambda c: c.clear(),
        lambda c: c.popitem(),
    ],
    ids=[
        "setitem",
        "update",
        "update-kwargs",
        "setdefault",
        "pop",
        "del",
        "ior",
        "clear",
        "popitem",
    ],
)
def test_explicit_render_context_refuses_every_mutation_of_a_provider_key(
    staged, monkeypatch, mutate
):
    view = _instance(TenantView, "explicit", monkeypatch)
    context = view.get_context_data()
    provided = context["tenant"]
    with pytest.raises(ExposureError):
        mutate(context)
    assert context["tenant"] is provided
    # Ordinary application keys stay freely writable.
    context["note"] = "ok"
    context.update(other=1)
    assert context.pop("note") == "ok"
