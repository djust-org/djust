"""Lazy, non-sticky and mixed-policy explicit children (ADR-038 E3-5, D-m).

* A non-sticky explicit child is supported as transient: it renders and handles
  events under the reuse-identity check, and nothing about it is persisted.
* ``lazy=True`` on an explicit child renders a placeholder, and the fill mounts
  the child as a transient one with the same checks as a direct mount, against
  the session as stored when the fill runs (#4154).
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
    exposure_policy = "legacy"
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


def _fill(parent):
    """Run the page's one lazy thunk, as the fill does; (view_id, status, body)."""
    import re

    from asgiref.sync import async_to_sync

    [(view_id, thunk)] = parent._lazy_thunks
    envelope = async_to_sync(thunk)().decode("utf-8")
    match = re.search(r'data-status="([a-z]+)"[^>]*>(.*)</template>', envelope, re.S)
    assert match is not None, envelope
    return view_id, match.group(1), match.group(2)


@pytest.mark.parametrize("lazy", ["True", "'visible'"])
def test_lazy_explicit_child_renders_a_placeholder_then_fills_as_transient(rf, lazy, monkeypatch):
    """#4154: no refusal. The page render emits the placeholder and mounts
    nothing; the fill mounts, registers and renders the child, with no raw
    ``view`` and nothing undeclared in its HTML, and persists nothing."""
    mounts = []
    real_mount = TransientChild.mount
    monkeypatch.setattr(
        TransientChild,
        "mount",
        lambda self, request, **kw: mounts.append(self) or real_mount(self, request, **kw),
    )
    parent = ExplicitParent()
    session = SessionStore()
    session.create()
    request = make_request(rf, session)
    html = render(parent, request, f'{{% live_render "{MODULE}.TransientChild" lazy={lazy} %}}')
    assert "<dj-lazy-slot" in html and "Transient=" not in html
    assert mounts == [] and parent._get_all_child_views() == {}

    view_id, status, body = _fill(parent)
    assert status == "ok", body
    assert "Transient=1" in body and f'data-djust-embedded="{view_id}"' in body
    assert "TRANSIENT_SECRET_SENTINEL" not in body
    [child] = mounts
    assert parent._get_child_view(view_id) is child
    assert child._explicit_child_reuse_identity is not None
    assert child_keys(SessionStore(session.session_key).load()) == []


def test_lazy_explicit_child_cannot_declare_persisted_state(rf):
    """A lazy explicit child is transient, so the D-m refusal of persisted
    state applies to it before any placeholder or thunk exists."""
    parent = ExplicitParent()
    with pytest.raises(TemplateSyntaxError):
        render(
            parent,
            make_request(rf, SessionStore()),
            f'{{% live_render "{MODULE}.PersistedNonSticky" lazy=True %}}',
        )
    assert not getattr(parent, "_lazy_thunks", None)


class DeniedLazyChild(TransientChild):
    def check_permissions(self, request):
        return False


class ObjectDeniedLazyChild(TransientChild):
    def get_object(self):
        return "the-object"

    def has_object_permission(self, request, obj):
        return False


def _log_in(session, user):
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY

    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()


class ObjectCheckedLazyChild(TransientChild):
    def get_object(self):
        return "the-object"

    def has_object_permission(self, request, obj):
        request.path = "/elsewhere/"  # the object check moved the fill's identity
        return True


OBJECT_CHECKS = []


class ObjectRecordingLazyChild(TransientChild):
    def get_object(self):
        return "the-object"

    def has_object_permission(self, request, obj):
        OBJECT_CHECKS.append(self)
        return True


@pytest.mark.parametrize(
    "case",
    [
        "unchanged",
        "another_user",
        "another_tenant",
        "revoked_session",
        "permission",
        "object",
        "changed_in_mount",
        "changed_in_authorization",
    ],
)
def test_lazy_explicit_fill_is_authorized_like_a_direct_mount(rf, monkeypatch, case):
    """#4154: the fill re-reads the session and re-checks the identity the
    page rendered under, view auth before ``mount`` and object permission
    after it. Every refusal is the same envelope and registers nothing."""
    from django.contrib.auth.models import User

    from djust.tenants.resolvers import TenantInfo

    child_cls = {
        "permission": DeniedLazyChild,
        "object": ObjectDeniedLazyChild,
        "changed_in_authorization": ObjectCheckedLazyChild,
        "changed_in_mount": ObjectRecordingLazyChild,
    }.get(case, TransientChild)
    OBJECT_CHECKS.clear()
    mounts = []

    def mount(self, request, **kwargs):
        mounts.append(self)
        if case == "changed_in_mount":
            request.path = "/elsewhere/"

    monkeypatch.setattr(child_cls, "mount", mount)
    alice = User.objects.create_user("alice-4154")
    bob = User.objects.create_user("bob-4154")
    session = SessionStore()
    _log_in(session, alice)
    request = make_request(rf, session)
    request.user = alice
    parent = ExplicitParent()
    path = f"{MODULE}.{child_cls.__name__}"
    render(parent, request, f'{{% live_render "{path}" lazy=True %}}')

    if case == "another_user":
        _log_in(SessionStore(session.session_key), bob)
    elif case == "another_tenant":
        request.tenant = TenantInfo(tenant_id="other-4154")
    elif case == "revoked_session":
        SessionStore(session.session_key).delete()

    view_id, status, body = _fill(parent)
    if case == "unchanged":
        assert status == "ok" and "Transient=" in body, body
        assert parent._get_child_view(view_id) is not None
        return
    assert status == "error", body
    assert body == f'<dj-error aria-live="polite">child &#x27;{path}&#x27; denied access</dj-error>'
    assert parent._get_all_child_views() == {}
    # Only the object check and the identity re-checks run after mount(), as
    # on a direct mount.
    after_mount = {"object", "changed_in_mount", "changed_in_authorization"}
    assert len(mounts) == (1 if case in after_mount else 0)
    # An identity changed by mount() is refused before the object check runs.
    assert OBJECT_CHECKS == []


class RaisingLazyChild(TransientChild):
    raises = None

    def mount(self, request, **kwargs):
        raise type(self).raises


class LegacyRaisingLazyChild(LegacyChild):
    raises = None

    def mount(self, request, **kwargs):
        raise type(self).raises


@pytest.mark.parametrize(
    "raised,refusal",
    [
        ("http404", True),
        ("permission_denied", True),
        ("value_error", False),
        ("permission_error", False),
    ],
)
def test_a_lazy_explicit_mount_that_raises_shows_no_application_detail(
    rf, monkeypatch, raised, refusal
):
    """#4154 review: an explicit child whose mount() raises ``Http404`` or
    ``PermissionDenied`` gets the same envelope as a refusal, so "not yours"
    cannot be told from a denial. Anything else is a failure, with neither the
    exception's type nor its text."""
    from django.core.exceptions import PermissionDenied
    from django.http import Http404

    exc = {
        "http404": Http404("SECRET_404"),
        "permission_denied": PermissionDenied("SECRET_DENIED"),
        "value_error": ValueError("SECRET_VALUE"),
        "permission_error": PermissionError("SECRET_OS"),
    }[raised]
    monkeypatch.setattr(RaisingLazyChild, "raises", exc)
    parent = ExplicitParent()
    session = SessionStore()
    session.create()
    path = f"{MODULE}.RaisingLazyChild"
    render(parent, make_request(rf, session), f'{{% live_render "{path}" lazy=True %}}')
    view_id, status, body = _fill(parent)
    assert status == "error"
    assert "SECRET" not in body and type(exc).__name__ not in body
    if refusal:
        assert body == (
            f'<dj-error aria-live="polite">child &#x27;{path}&#x27; denied access</dj-error>'
        )
    else:
        assert body == f'<dj-error aria-live="polite">Lazy child {view_id} failed.</dj-error>'
    assert parent._get_all_child_views() == {}


def test_a_lazy_legacy_mount_that_raises_keeps_its_failure_message(rf, monkeypatch):
    """Legacy control: the legacy fill's envelope is unchanged."""
    from django.http import Http404

    monkeypatch.setattr(LegacyRaisingLazyChild, "raises", Http404("x"))
    parent = _LegacyLiveView()
    render(
        parent,
        make_request(rf, SessionStore()),
        f'{{% live_render "{MODULE}.LegacyRaisingLazyChild" lazy=True %}}',
    )
    view_id, status, body = _fill(parent)
    assert status == "error"
    assert body == f'<dj-error aria-live="polite">Lazy child {view_id} failed: Http404</dj-error>'


class InputsRewritingLazyChild(TransientChild):
    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._explicit_child_mount_inputs = '{"object_id":99}'


def test_a_lazy_explicit_mount_that_rewrites_its_inputs_is_refused(rf):
    """After mount() the fill compares the inputs the child recorded, which
    event routing reads, with the tag's: a rewrite is refused (#4154 review)."""
    parent = ExplicitParent()
    session = SessionStore()
    session.create()
    path = f"{MODULE}.InputsRewritingLazyChild"
    render(parent, make_request(rf, session), f'{{% live_render "{path}" lazy=True %}}')
    _, status, body = _fill(parent)
    assert status == "error" and "denied access" in body, body
    assert parent._get_all_child_views() == {}


@pytest.mark.parametrize("gone", [None, "deleted", "expired"])
def test_reload_request_session_refuses_a_session_that_is_gone(rf, gone):
    """``reload_request_session``'s own refusal (#4154 review): called alone,
    with no identity or binding comparison after it, a session deleted or
    expired since the request read it raises instead of becoming an anonymous
    request with no key. Its callers also compare the key afterwards; this pins
    the helper's contract for any caller that does not."""
    from datetime import timedelta

    from django.contrib.auth.models import User
    from django.utils import timezone

    from djust._exposure_auth import reload_request_session

    alice = User.objects.create_user("alice-reload-4154")
    session = SessionStore()
    _log_in(session, alice)
    request = make_request(rf, session)
    request.user = alice
    if gone == "deleted":
        SessionStore(session.session_key).delete()
    elif gone == "expired":
        from django.contrib.sessions.models import Session

        Session.objects.filter(session_key=session.session_key).update(
            expire_date=timezone.now() - timedelta(seconds=1)
        )
    if gone is None:
        fresh = reload_request_session(request)
        assert fresh is not request and fresh.user.pk == alice.pk
        assert fresh.session.session_key == session.session_key
        return
    with pytest.raises(ExposureError):
        reload_request_session(request)


class _LegacyLiveView(LiveView):
    """A bare LiveView under the legacy policy, whatever the framework default is."""

    exposure_policy = "legacy"


def test_lazy_legacy_child_keeps_its_placeholder(rf):
    """Legacy control: the lazy placeholder path is unchanged."""
    parent = _LegacyLiveView()
    request = make_request(rf, SessionStore())
    html = render(parent, request, f'{{% live_render "{MODULE}.LegacyChild" lazy=True %}}')
    assert "<dj-lazy-slot" in html
    assert len(parent._lazy_thunks) == 1


def test_explicit_server_persistence_under_legacy_parent_is_refused(rf):
    parent = _LegacyLiveView()
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


class RawViewProbe(TransientChild):
    count = state(1)
    secret = state("TRANSIENT_SECRET_SENTINEL")
    template = "<div>Count={{ count }}|{{ view.raw_attr }}|{{ view.secret }}</div>"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.raw_attr = "RAW_VIEW_ATTR_SENTINEL"


class LegacyRawViewProbe(LiveView):
    exposure_policy = "legacy"
    template = "<div>{{ view.raw_attr }}</div>"

    def mount(self, request, **kwargs):
        self.raw_attr = "RAW_VIEW_ATTR_SENTINEL"


def test_transient_explicit_child_template_gets_no_raw_view(rf):
    """ADR-038 D2: the raw instance is not a default template value in explicit mode."""
    parent = ExplicitParent()
    request = make_request(rf, SessionStore())
    html = render(parent, request, f'{{% live_render "{MODULE}.RawViewProbe" view_id="p" %}}')
    assert "Count=1" in html
    assert "RAW_VIEW_ATTR_SENTINEL" not in html
    assert "TRANSIENT_SECRET_SENTINEL" not in html


def test_legacy_non_sticky_child_template_still_gets_view(rf):
    """Legacy control: the Phase A non-sticky render keeps ``view`` in context."""
    parent = LiveView()
    request = make_request(rf, SessionStore())
    html = render(parent, request, f'{{% live_render "{MODULE}.LegacyRawViewProbe" %}}')
    assert "RAW_VIEW_ATTR_SENTINEL" in html
