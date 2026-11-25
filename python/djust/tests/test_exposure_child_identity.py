"""Native render/reuse regression coverage for staged explicit child identity."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.template import Context, Template, TemplateSyntaxError

from djust import LiveView
from djust._exposure import ExposureError
from djust.decorators import state


class IdentityParent(LiveView):
    exposure_policy = "explicit"


class OtherParent(IdentityParent):
    pass


class IdentityChild(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "identity"
    count = state(1)
    template = "<div>{{ count }}</div>"

    def mount(self, request, inputs=None):
        self._inputs = inputs
        self._unmounts = 0
        self._cleanups = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    def _on_sticky_unmount(self):
        self._unmounts += 1
        super()._on_sticky_unmount()

    def _cleanup_on_unregister(self):
        self._cleanups += 1


class StoredChild(IdentityChild):
    count = state(1, persist="server")


class OtherChild(IdentityChild):
    count = state(1)


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings, db):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [__name__]


def request_for(rf, session=None, path="/page/"):
    request = rf.get(path)
    request.user = AnonymousUser()
    request.tenant = None
    if session is not None:
        request.session = session
    return request


def render(parent, request, cls=IdentityChild, inputs=None, *, lazy=False):
    parent.request = request
    return Template(
        "{% load live_tags %}{% live_render target sticky=True inputs=inputs lazy=lazy %}"
    ).render(
        Context(
            {
                "view": parent,
                "request": request,
                "target": cls.__module__ + "." + cls.__name__,
                "inputs": inputs,
                "lazy": lazy,
            }
        )
    )


def stage(parent, child, reuse):
    if reuse == "preserved":
        parent = IdentityParent()
        parent._ws_consumer = SimpleNamespace(_sticky_preserved={"identity": child})
    return parent


@pytest.mark.parametrize("cls", [IdentityChild, StoredChild])
@pytest.mark.parametrize("reuse", ["registered", "preserved"])
def test_matching_identity_retains_state(rf, cls, reuse):
    session = SessionStore()
    session.create()
    parent = IdentityParent()
    render(parent, request_for(rf, session), cls, {"a": 1, "b": 2})
    child = parent._get_child_view("identity")
    child.count = 8
    parent = stage(parent, child, reuse)
    html = render(parent, request_for(rf, SessionStore(session.session_key)), cls, {"b": 2, "a": 1})
    assert parent._get_child_view("identity") is child
    assert child.count == 8
    assert child._unmounts == 0
    assert ("dj-sticky-slot" in html) is (reuse == "preserved")


@pytest.mark.parametrize("cls", [IdentityChild, StoredChild])
@pytest.mark.parametrize(
    "reuse,change",
    [
        (reuse, change)
        for reuse in ("registered", "preserved")
        for change in ("inputs", "user", "tenant", "route", "session", "class", "parent")
        # A child preserved across live_redirect carries to another route and
        # parent class (#4149): test_preserved_child_carries_across_route_and_parent.
        if not (reuse == "preserved" and change in ("route", "parent"))
    ],
)
def test_changed_identity_remounts_in_same_slot(rf, cls, reuse, change):
    from djust.tenants.resolvers import TenantInfo

    session = SessionStore()
    session.create()
    parent = IdentityParent()
    request = request_for(rf, session)
    render(parent, request, cls, {"object_id": 1})
    child = parent._get_child_view("identity")
    assert child._explicit_child_carry_identity is not None
    child.count = 8
    old_parent = parent
    parent = stage(parent, child, reuse)
    current = request_for(rf, SessionStore(session.session_key))
    inputs = {"object_id": 1}
    if change == "inputs":
        inputs = {"object_id": 2}
    elif change == "user":
        current.user = SimpleNamespace(is_authenticated=True, pk=42)
    elif change == "tenant":
        current.tenant = TenantInfo(tenant_id="other")
    elif change == "route":
        current.path = "/other/"
    elif change == "session":
        current.session = SessionStore()
        current.session.create()
    elif change == "class":
        cls = OtherChild
    elif change == "parent":
        parent.__class__ = OtherParent
    html = render(parent, current, cls, inputs)
    replacement = parent._get_child_view("identity")
    assert replacement is not child
    assert replacement.count == 1
    assert list(parent._get_all_child_views()) == ["identity"]
    assert child._unmounts == 1
    assert child._parent_view is None
    # A disposed child keeps no identity it could be carried by (#4149).
    assert child._explicit_child_carry_identity is None
    assert "dj-sticky-slot" not in html
    if reuse == "preserved":
        assert old_parent._get_all_child_views() == {}
        assert not parent._ws_consumer._sticky_preserved
        assert not getattr(parent._ws_consumer, "_sticky_auto_reattached", set())


@pytest.mark.parametrize("cls", [IdentityChild, StoredChild])
@pytest.mark.parametrize("change", ["route", "parent", "both"])
def test_preserved_child_carries_across_route_and_parent(rf, cls, change):
    """#4149: a sticky child preserved across ``live_redirect`` reattaches under
    a destination with another route or parent class when its class, slot,
    inputs, session, user and tenant match; its reuse identity is re-recorded
    for the destination so later reuse there matches."""
    session = SessionStore()
    session.create()
    parent = IdentityParent()
    render(parent, request_for(rf, session), cls, {"object_id": 1})
    child = parent._get_child_view("identity")
    child.count = 8
    parent = stage(parent, child, "preserved")
    current = request_for(rf, SessionStore(session.session_key))
    if change in ("route", "both"):
        current.path = "/other/"
    if change in ("parent", "both"):
        parent.__class__ = OtherParent
    html = render(parent, current, cls, {"object_id": 1})
    assert parent._get_child_view("identity") is child
    assert child.count == 8
    assert child._unmounts == 0
    assert "dj-sticky-slot" in html
    # Re-rendered on the destination, the child is reused as an ordinary
    # registered child: its identity now names the destination.
    parent._ws_consumer._sticky_preserved = {}
    html = render(parent, current, cls, {"object_id": 1})
    assert "dj-sticky-slot" not in html
    assert parent._get_child_view("identity") is child
    assert child._unmounts == 0


@pytest.mark.parametrize("failure", ["refused", "state_move"])
def test_failed_carry_disposes_the_child_and_forgets_it(rf, monkeypatch, failure):
    """#4149 review: commit-or-rollback at the tag. A carried child the
    destination refuses, or whose state cannot be moved, is disposed and
    dropped from the preserved map (and from the new parent), without the
    socket's own clean-up after a failed redirect."""
    from django.core.exceptions import PermissionDenied

    from djust import _exposure_children

    session = SessionStore()
    session.create()
    parent = IdentityParent()
    render(parent, request_for(rf, session), StoredChild, {"a": 1})
    child = parent._get_child_view("identity")
    child.count = 8
    before = child._explicit_child_reuse_identity
    parent = stage(parent, child, "preserved")
    current = request_for(rf, SessionStore(session.session_key), path="/other/")
    if failure == "refused":
        monkeypatch.setattr(
            StoredChild,
            "has_object_permission",
            lambda self, request, obj: False,
            raising=False,
        )
        monkeypatch.setattr(StoredChild, "get_object", lambda self: object(), raising=False)
        expected = (PermissionDenied, TemplateSyntaxError)
    else:

        def broken(*args, **kwargs):
            raise RuntimeError("store down")

        monkeypatch.setattr(_exposure_children, "rebind_child_state", broken)
        expected = (RuntimeError,)
    with pytest.raises(expected):
        render(parent, current, StoredChild, {"a": 1})
    assert child._explicit_child_reuse_identity is not before
    assert child._djust_child_disposed is True
    assert child._parent_view is None
    assert parent._get_all_child_views() == {}
    assert parent._ws_consumer._sticky_preserved == {}


def test_transient_without_session_reuses_only_in_same_root(rf):
    parent = IdentityParent()
    render(parent, request_for(rf))
    child = parent._get_child_view("identity")
    child.count = 8
    render(parent, request_for(rf))
    assert parent._get_child_view("identity") is child
    new_parent = stage(parent, child, "preserved")
    render(new_parent, request_for(rf))
    assert new_parent._get_child_view("identity") is not child
    assert child._unmounts == 1


def test_preservation_does_not_bypass_lazy_sticky_validation(rf):
    parent = IdentityParent()
    render(parent, request_for(rf))
    child = parent._get_child_view("identity")
    parent = stage(parent, child, "preserved")
    with pytest.raises(TemplateSyntaxError, match="mutually"):
        render(parent, request_for(rf), lazy=True)
    assert not parent._get_all_child_views()


@pytest.mark.parametrize("reuse", ["registered", "preserved"])
def test_changed_schema_remounts_without_reusing_old_values(rf, monkeypatch, reuse):
    session = SessionStore()
    session.create()
    parent = IdentityParent()
    render(parent, request_for(rf, session))
    child = parent._get_child_view("identity")
    child.count = 8
    parent = stage(parent, child, reuse)
    descriptor = state("new", client=True)
    descriptor.__set_name__(IdentityChild, "count")
    monkeypatch.setattr(IdentityChild, "count", descriptor)
    render(parent, request_for(rf, session))
    replacement = parent._get_child_view("identity")
    assert replacement is not child
    assert replacement.count == "new"


@pytest.mark.parametrize("reuse", ["registered", "preserved"])
def test_cleanup_failure_does_not_resurrect_or_log_private_values(rf, monkeypatch, caplog, reuse):
    session = SessionStore()
    session.create()
    parent = IdentityParent()
    render(parent, request_for(rf, session), inputs={"id": 1})
    child = parent._get_child_view("identity")
    parent = stage(parent, child, reuse)

    def broken():
        raise RuntimeError("PRIVATE_CLEANUP_SENTINEL")

    monkeypatch.setattr(child, "_on_sticky_unmount", broken)
    html = render(parent, request_for(rf, session), inputs={"id": 2})
    assert parent._get_child_view("identity") is not child
    assert "dj-sticky-slot" not in html
    assert "PRIVATE_CLEANUP_SENTINEL" not in caplog.text
    assert "cleanup failed" in caplog.text


@pytest.mark.parametrize("change", ["route", "user", "inputs"])
def test_mount_identity_changes_are_rejected_before_registration(rf, monkeypatch, change):
    original = IdentityChild.mount

    def changed(self, request, inputs):
        original(self, request, inputs)
        if change == "route":
            request.path = "/other/"
        elif change == "user":
            request.user = SimpleNamespace(is_authenticated=True, pk=99)
        else:
            # Identity is captured from a detached copy of the render inputs.
            inputs["id"] = 99

    monkeypatch.setattr(IdentityChild, "mount", changed)
    parent = IdentityParent()
    if change == "inputs":
        render(parent, request_for(rf), inputs={"id": 1})
        child = parent._get_child_view("identity")
        # The mount receives ordinary app inputs, but cannot rewrite the
        # recorded trusted original identity used for future routing.
        assert child._explicit_child_mount_inputs == '{"inputs":{"id":1}}'
    else:
        with pytest.raises(ExposureError, match="changed during mount"):
            render(parent, request_for(rf), inputs={"id": 1})
        assert not parent._get_all_child_views()


@pytest.mark.parametrize("bad", ["user", "tenant", "session", "inputs"])
def test_unavailable_identity_has_no_private_exception_or_repr_fallback(rf, bad):
    class Private:
        def __repr__(self):
            raise AssertionError("PRIVATE_REPR_SENTINEL")

    request = request_for(rf)
    inputs = {}
    if bad == "inputs":
        inputs["value"] = Private()
    else:
        setattr(request, bad, Private())
    parent = IdentityParent()
    with pytest.raises(ExposureError) as error:
        render(parent, request, inputs=inputs)
    assert "PRIVATE" not in str(error.value)
    assert not parent._get_all_child_views()


def test_transient_reuse_does_not_create_or_save_session(rf, monkeypatch):
    session = SessionStore("transient-stable-session-key")

    def forbidden(*args, **kwargs):
        raise AssertionError("Transient reuse must not use persistence")

    for name in ("create", "save", "load"):
        monkeypatch.setattr(session, name, forbidden)
    parent = IdentityParent()
    render(parent, request_for(rf, session))
    child = parent._get_child_view("identity")
    parent = stage(parent, child, "preserved")
    render(parent, request_for(rf, session))
    assert parent._get_child_view("identity") is child


@pytest.mark.parametrize("changed", [False, True])
def test_post_render_preservation_scan_cannot_resurrect_replaced_child(rf, changed):
    from asgiref.sync import async_to_sync

    from djust.runtime import WSConsumerTransport

    session = SessionStore()
    session.create()
    parent = IdentityParent()
    render(parent, request_for(rf, session), inputs={"id": 1})
    child = parent._get_child_view("identity")
    parent = stage(parent, child, "preserved")
    frames = []

    async def send_json(frame):
        frames.append(frame)

    parent._ws_consumer.send_json = send_json
    html = render(parent, request_for(rf, session), inputs={"id": 2 if changed else 1})
    async_to_sync(WSConsumerTransport(parent._ws_consumer).on_mount_render_ready)(parent, html)
    assert (parent._get_child_view("identity") is child) is not changed
    if changed:
        assert not parent._ws_consumer._sticky_preserved
        assert not any("identity" in frame.get("views", []) for frame in frames)
    else:
        assert frames == [{"type": "sticky_hold", "views": ["identity"]}]


def test_bare_slot_markup_cannot_authorize_explicit_reattachment(rf):
    from asgiref.sync import async_to_sync

    from djust.runtime import WSConsumerTransport

    session = SessionStore()
    session.create()
    parent = IdentityParent()
    render(parent, request_for(rf, session))
    child = parent._get_child_view("identity")
    parent = stage(parent, child, "preserved")
    frames = []

    async def send_json(frame):
        frames.append(frame)

    parent._ws_consumer.send_json = send_json
    async_to_sync(WSConsumerTransport(parent._ws_consumer).on_mount_render_ready)(
        parent, '<div dj-sticky-slot="identity"></div>'
    )
    assert not parent._get_all_child_views()
    assert not parent._ws_consumer._sticky_preserved
    assert frames == [{"type": "sticky_hold", "views": []}]
    assert child._unmounts == 1


@pytest.mark.parametrize("reuse", ["registered", "preserved"])
def test_authorization_cannot_change_identity_before_reuse(rf, monkeypatch, reuse):
    from django.core.exceptions import PermissionDenied

    session = SessionStore()
    session.create()
    parent = IdentityParent()
    render(parent, request_for(rf, session))
    child = parent._get_child_view("identity")
    parent = stage(parent, child, reuse)

    def changed(self, request):
        request.path = "/different/"
        return True

    monkeypatch.setattr(IdentityChild, "check_permissions", changed, raising=False)
    with pytest.raises(PermissionDenied):
        render(parent, request_for(rf, session))
    if reuse == "preserved":
        assert not parent._get_all_child_views()
        assert not getattr(parent._ws_consumer, "_sticky_auto_reattached", set())


def test_fresh_object_authorization_cannot_change_identity_before_registration(rf, monkeypatch):
    monkeypatch.setattr(IdentityChild, "get_object", lambda self: 1, raising=False)

    def changed(self, request, obj):
        request.path = "/different/"
        return True

    monkeypatch.setattr(IdentityChild, "has_object_permission", changed, raising=False)
    parent = IdentityParent()
    with pytest.raises(ExposureError, match="changed during authorization"):
        render(parent, request_for(rf))
    assert not parent._get_all_child_views()


@pytest.mark.parametrize("change", ["inputs", "slot", "schema", "registration"])
def test_nested_identity_includes_registered_ancestor_scope(rf, change, monkeypatch):
    from djust._exposure_child_identity import child_reuse_identity
    from djust._exposure_children import record_child_mount_inputs

    session = SessionStore()
    session.create()
    request = request_for(rf, session)
    root, middle = IdentityParent(), IdentityParent()
    record_child_mount_inputs(middle, {"id": 1})
    root._register_child("middle", middle)
    before = child_reuse_identity(IdentityChild, middle, request, "leaf", {})
    if change == "inputs":
        record_child_mount_inputs(middle, {"id": 2})
    elif change == "slot":
        root._child_views.pop("middle")
        root._register_child("elsewhere", middle)
    elif change == "schema":
        descriptor = state(1)
        descriptor.__set_name__(IdentityParent, "revision")
        monkeypatch.setattr(IdentityParent, "revision", descriptor, raising=False)
    else:
        root._child_views.clear()
        with pytest.raises(ExposureError):
            child_reuse_identity(IdentityChild, middle, request, "leaf", {})
        return
    after = child_reuse_identity(IdentityChild, middle, request, "leaf", {})
    assert not before.matches(after)


# "legacy" too: an ancestor is no longer accepted under it (#4206).
@pytest.mark.parametrize("policy", ["unknown", "legacy"])
@pytest.mark.parametrize("target", [IdentityChild, IdentityParent])
def test_unknown_policy_cannot_reuse_explicit_identity(rf, monkeypatch, target, policy):
    parent = IdentityParent()
    render(parent, request_for(rf))
    child = parent._get_child_view("identity")
    monkeypatch.setattr(target, "exposure_policy", policy)
    with pytest.raises(ExposureError, match="identity unavailable"):
        render(parent, request_for(rf))
    assert child._unmounts == 0  # invalid configuration, not a valid remount


def test_same_key_from_different_session_backend_is_not_same_identity(rf):
    from django.contrib.sessions.backends.file import SessionStore as FileSession

    parent = IdentityParent()
    render(parent, request_for(rf, SessionStore("transient-stable-session-key")))
    child = parent._get_child_view("identity")
    render(parent, request_for(rf, FileSession("transient-stable-session-key")))
    assert parent._get_child_view("identity") is not child
    assert child._unmounts == 1
