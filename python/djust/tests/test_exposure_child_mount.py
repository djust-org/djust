"""Actual eager child rendering with the production construction gate bypassed."""

import json

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.template import Context, Template, TemplateSyntaxError

from djust import LiveView, event_handler
from djust._exposure import ExposureError
from djust._exposure_children import child_state_adapter
from djust.decorators import state


class MountParent(LiveView):
    exposure_policy = "explicit"


class MountChild(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "counter"
    count = state(1, persist="server")
    secret = state("SERVER_SENTINEL", persist="server")
    template = "<div>Count={{ count }}</div>"

    def mount(self, request, object_id=1, **kwargs):
        self.count = 1
        self._service = object()
        self._object_id = object_id
        self.public_note = "UNDECLARED_SENTINEL"

    def get_object(self):
        assert self._service is not None
        return self.count

    def has_object_permission(self, request, obj):
        return obj <= request.max_count

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, render_only="RENDER_SENTINEL", **kwargs)


class MiddleChild(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "middle"
    template = (
        "{% load live_tags %}{% live_render "
        '"djust.tests.test_exposure_child_mount.MountChild" sticky=True object_id=1 %}'
    )


class HTTPParent(MountParent):
    template = (
        "<div dj-root>{% load live_tags %}{% live_render "
        '"djust.tests.test_exposure_child_mount.MountChild" sticky=True object_id=1 %}</div>'
    )

    def mount(self, request, **kwargs):
        # Reconstruct an actual child before the HTTP handler, not a mock
        # registry or a manually restored object.
        render(self, request)

    @event_handler()
    def change_child(self):
        self._get_child_view("counter").count = 8


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings, db):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = ["djust.tests.test_exposure_child_mount"]


def make_request(rf, session, path="/page/"):
    request = rf.get(path)
    request.user = AnonymousUser()
    request.session = session
    request.tenant = None
    request.max_count = 100
    return request


def render(parent, request, object_id=1):
    parent.request = request
    return Template(
        "{% load live_tags %}{% live_render "
        '"djust.tests.test_exposure_child_mount.MountChild" sticky=True object_id=object_id %}'
    ).render(Context({"view": parent, "request": request, "object_id": object_id}))


def seed(parent, request, count=9):
    adapter = child_state_adapter(
        MountChild(), parent, request, "counter", {"object_id": 1}, create=True
    )
    adapter.save({"count": count, "secret": "SERVER_SENTINEL"})
    return adapter


def test_fresh_child_mounts_then_restores_only_declared_state(rf):
    parent = MountParent()
    request = make_request(rf, SessionStore())
    adapter = seed(parent, request)
    html = render(parent, request)
    assert "Count=9" in html
    assert "SENTINEL" not in html
    child = parent._get_child_view("counter")
    assert child._service is not None
    assert child._object_id == 1
    assert json.loads(child._explicit_child_mount_inputs) == {"object_id": 1}
    assert adapter.load() == {"count": 9, "secret": "SERVER_SENTINEL"}


def test_initial_render_saves_without_legacy_browser_snapshot_optin(rf):
    parent = MountParent()
    request = make_request(rf, SessionStore())
    assert not parent.enable_state_snapshot
    assert "Count=1" in render(parent, request)
    child = parent._get_child_view("counter")
    adapter = child_state_adapter(child, parent, request, "counter", {"object_id": 1})
    assert adapter is not None
    stored = SessionStore(request.session.session_key).load()
    assert stored[adapter.key]["state"]["values"] == {"count": 1, "secret": "SERVER_SENTINEL"}
    assert "UNDECLARED_SENTINEL" not in json.dumps(stored)
    assert "RENDER_SENTINEL" not in json.dumps(stored)
    assert not any(key.startswith("liveview_") for key in stored)


@pytest.mark.parametrize("mutation", ["extra", "schema", "inputs", "legacy"])
def test_rejected_state_leaves_fresh_mount_defaults(rf, mutation):
    parent = MountParent()
    request = make_request(rf, SessionStore())
    adapter = seed(parent, request)
    raw = adapter.session[adapter.key]
    if mutation == "extra":
        raw["state"]["values"]["public_note"] = "RESTORE_SENTINEL"
    elif mutation == "schema":
        raw["state"]["schema"] = "old"
    elif mutation == "legacy":
        raw = {"count": 9}
    adapter.session[adapter.key] = raw
    adapter.session.save()
    html = render(parent, request, object_id=2 if mutation == "inputs" else 1)
    child = parent._get_child_view("counter")
    assert "Count=1" in html
    assert child.public_note == "UNDECLARED_SENTINEL"
    assert child._service is not None


def test_object_authorization_sees_restored_state_before_registration(rf):
    parent = MountParent()
    request = make_request(rf, SessionStore())
    adapter = seed(parent, request)
    before = json.dumps(adapter.session.load(), sort_keys=True)
    request.max_count = 2  # mount default allowed, restored value denied
    with pytest.raises(TemplateSyntaxError, match="permission"):
        render(parent, request)
    assert parent._get_all_child_views() == {}
    assert json.dumps(adapter.session.load(), sort_keys=True) == before


def test_unregistered_ancestor_cannot_claim_another_parent_scope(rf):
    root = MountParent()
    parent = MountParent()
    parent._parent_view = root
    parent._view_id = "missing"
    request = make_request(rf, SessionStore())
    with pytest.raises(ExposureError, match="registered"):
        child_state_adapter(MountChild(), parent, request, "counter", {}, create=True)
    assert request.session.session_key is None


def test_mixed_policy_does_not_implicitly_gain_an_explicit_contract(rf):
    request = make_request(rf, SessionStore())
    with pytest.raises(ExposureError, match="ancestry"):
        child_state_adapter(MountChild(), LiveView(), request, "counter", {}, create=True)
    assert request.session.session_key is None


def test_explicit_child_does_not_receive_implicit_raw_view_context(rf, monkeypatch):
    monkeypatch.setattr(MountChild, "template", "<div>{{ count }}{{ view.secret }}</div>")
    html = render(MountParent(), make_request(rf, SessionStore()))
    assert "SERVER_SENTINEL" not in html


def test_nested_scope_comes_from_registered_ancestry_without_raw_view_context(rf):
    parent = MountParent()
    request = make_request(rf, SessionStore())
    parent.request = request
    html = Template(
        "{% load live_tags %}{% live_render "
        '"djust.tests.test_exposure_child_mount.MiddleChild" sticky=True %}'
    ).render(Context({"view": parent, "request": request}))
    assert "Count=1" in html
    middle = parent._get_child_view("middle")
    child = middle._get_child_view("counter")
    nested = child_state_adapter(child, middle, request, "counter", {"object_id": 1})
    direct = child_state_adapter(MountChild(), parent, request, "counter", {"object_id": 1})
    assert nested.key != direct.key
    assert nested.load()["count"] == 1
    assert direct.load() is None


def test_parent_save_captures_direct_and_nested_children_in_one_flush(rf, monkeypatch):
    from djust._exposure_child_persistence import save_child_states

    parent = MountParent()
    request = make_request(rf, SessionStore())
    render(parent, request)
    Template(
        "{% load live_tags %}{% live_render "
        '"djust.tests.test_exposure_child_mount.MiddleChild" sticky=True %}'
    ).render(Context({"view": parent, "request": request}))
    direct = parent._get_child_view("counter")
    middle = parent._get_child_view("middle")
    nested = middle._get_child_view("counter")
    direct.count, nested.count = 12, 13
    saved = []
    original_save = request.session.save

    def save():
        saved.append(True)
        return original_save()

    monkeypatch.setattr(request.session, "save", save)
    save_child_states(parent, request)
    assert saved == [True]
    fresh = make_request(rf, SessionStore(request.session.session_key))
    assert (
        child_state_adapter(direct, parent, fresh, "counter", {"object_id": 1}).load()["count"]
        == 12
    )
    assert (
        child_state_adapter(nested, middle, fresh, "counter", {"object_id": 1}).load()["count"]
        == 13
    )
    stored = json.dumps(fresh.session.load())
    assert "UNDECLARED_SENTINEL" not in stored
    assert "RENDER_SENTINEL" not in stored


@pytest.mark.parametrize("invalid", ["permission", "ownership", "disposed", "budget"])
def test_parent_save_validates_every_child_before_writing(rf, invalid):
    from djust._exposure_child_persistence import save_child_states

    parent = MountParent()
    request = make_request(rf, SessionStore())
    render(parent, request)
    child = parent._get_child_view("counter")
    before = dict(request.session.items())
    child.count = 20
    if invalid == "permission":
        request.max_count = 2
    elif invalid == "ownership":
        child._parent_view = MountParent()
    elif invalid == "disposed":
        child._djust_child_disposed = True
    else:
        child.secret = "x" * 65536
    with pytest.raises(ExposureError, match="persistence unavailable"):
        save_child_states(parent, request)
    assert dict(request.session.items()) == before
    assert SessionStore(request.session.session_key).load() == before


@pytest.mark.parametrize("failure", ["nested_permission", "aggregate_budget"])
def test_parent_batch_rejection_does_not_write_earlier_valid_child(rf, failure):
    from djust._exposure_child_persistence import save_child_states

    parent = MountParent()
    request = make_request(rf, SessionStore())
    render(parent, request)
    Template(
        "{% load live_tags %}{% live_render "
        '"djust.tests.test_exposure_child_mount.MiddleChild" sticky=True %}'
    ).render(Context({"view": parent, "request": request}))
    direct = parent._get_child_view("counter")
    middle = parent._get_child_view("middle")
    nested = middle._get_child_view("counter")
    before = dict(request.session.items())
    direct.count = 20
    if failure == "nested_permission":
        nested.count = 200
    else:
        direct.secret = nested.secret = "x" * 40000
        for child, owner in ((direct, parent), (nested, middle)):
            adapter = child_state_adapter(child, owner, request, "counter", {"object_id": 1})
            # Each field set fits individually; only the total must be rejected.
            adapter._capture(adapter.contract.project_view(child, "server"))
    with pytest.raises(ExposureError, match="persistence unavailable"):
        save_child_states(parent, request)
    assert dict(request.session.items()) == before
    assert SessionStore(request.session.session_key).load() == before


@pytest.mark.parametrize("mutation", ["new_slot", "disposed"])
def test_parent_batch_rechecks_tree_after_application_hooks(rf, monkeypatch, mutation):
    from djust._exposure_child_persistence import save_child_states

    parent = MountParent()
    request = make_request(rf, SessionStore())
    render(parent, request)
    child = parent._get_child_view("counter")
    before = dict(request.session.items())
    child.count = 20
    calls = []

    def permission(self, current_request, obj):
        calls.append(True)
        if len(calls) == 2:
            if mutation == "new_slot":
                parent._child_views["unseen"] = object()
            else:
                self._djust_child_disposed = True
        return True

    monkeypatch.setattr(MountChild, "has_object_permission", permission)
    with pytest.raises(ExposureError, match="persistence unavailable"):
        save_child_states(parent, request)
    assert len(calls) == 2
    assert dict(request.session.items()) == before
    assert SessionStore(request.session.session_key).load() == before


def test_http_parent_event_saves_existing_child_after_render(rf):
    initial = make_request(rf, SessionStore())
    assert HTTPParent.as_view()(initial).status_code == 200
    current = rf.post(
        "/page/", {"event": "change_child", "params": {}}, content_type="application/json"
    )
    current.user, current.tenant, current.max_count = AnonymousUser(), None, 100
    current.session = SessionStore(initial.session.session_key)
    response = HTTPParent.as_view()(current)
    assert response.status_code == 200, response.content
    fresh = make_request(rf, SessionStore(initial.session.session_key))
    response = HTTPParent.as_view()(fresh)
    assert response.status_code == 200, response.content
    assert b"Count=8" in response.content
    assert b"SENTINEL" not in response.content


def test_http_child_storage_error_does_not_disclose_backend_details(
    rf, monkeypatch, settings, caplog
):
    settings.DEBUG = True
    initial = make_request(rf, SessionStore())
    assert HTTPParent.as_view()(initial).status_code == 200
    original_save = SessionStore.save

    def failed_save(self, *args, **kwargs):
        if any(
            key.startswith("_djust_explicit_child_") and value["state"]["values"].get("count") == 8
            for key, value in self.items()
        ):
            raise OSError("STORAGE_SECRET_SENTINEL")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "save", failed_save)
    current = rf.post(
        "/page/", {"event": "change_child", "params": {}}, content_type="application/json"
    )
    current.user, current.tenant, current.max_count = AnonymousUser(), None, 100
    current.session = SessionStore(initial.session.session_key)
    response = HTTPParent.as_view()(current)
    assert response.status_code == 500
    assert b"STORAGE_SECRET_SENTINEL" not in response.content
    assert "STORAGE_SECRET_SENTINEL" not in caplog.text
    assert all(
        value["state"]["values"]["count"] == 1
        for key, value in current.session.items()
        if key.startswith("_djust_explicit_child_")
    )


def test_http_get_uses_the_actual_embedded_restore_path(rf, monkeypatch):
    monkeypatch.setattr(
        MountParent,
        "template",
        "<div dj-root>{% load live_tags %}{% live_render "
        '"djust.tests.test_exposure_child_mount.MountChild" sticky=True object_id=1 %}</div>',
    )
    first = make_request(rf, SessionStore())
    seed(MountParent(), first, count=17)
    current = make_request(rf, SessionStore(first.session.session_key))
    response = MountParent.as_view()(current)
    assert response.status_code == 200, response.content
    assert b"Count=17" in response.content
    assert b"SENTINEL" not in response.content


def test_view_denial_happens_before_mount_or_restore(rf, monkeypatch):
    request = make_request(rf, SessionStore())
    parent = MountParent()
    adapter = seed(parent, request)
    before = json.dumps(adapter.session.load(), sort_keys=True)

    def forbidden_mount(self, request, **kwargs):
        raise AssertionError("Denied child must not mount")

    monkeypatch.setattr(MountChild, "mount", forbidden_mount)
    monkeypatch.setattr(MountChild, "check_permissions", lambda self, request: False, raising=False)
    with pytest.raises(TemplateSyntaxError, match="denied"):
        render(parent, request)
    assert parent._get_all_child_views() == {}
    assert json.dumps(adapter.session.load(), sort_keys=True) == before


def test_restore_bypass_canary_changes_the_actual_render(rf, monkeypatch):
    from djust._exposure_children import ChildStateSession

    request = make_request(rf, SessionStore())
    parent = MountParent()
    seed(parent, request, count=23)
    monkeypatch.setattr(ChildStateSession, "load", lambda self: None)
    html = render(parent, request)
    assert "Count=1" in html
    assert "Count=23" not in html


def test_mount_cannot_swap_a_declared_field_before_restore(rf, monkeypatch):
    parent = MountParent()
    request = make_request(rf, SessionStore())
    seed(parent, request)
    original = MountChild.mount
    writes = []

    def changed_mount(self, request, **kwargs):
        original(self, request, **kwargs)
        monkeypatch.setattr(
            MountChild,
            "count",
            property(lambda self: 1, lambda self, value: writes.append(value)),
        )

    monkeypatch.setattr(MountChild, "mount", changed_mount)
    with pytest.raises(ExposureError):
        render(parent, request)
    assert writes == []


def test_explicit_context_failure_is_not_replaced_by_an_empty_context(rf, monkeypatch, caplog):
    def broken_context(self, **kwargs):
        raise RuntimeError("CONTEXT_SECRET_SENTINEL")

    monkeypatch.setattr(MountChild, "get_context_data", broken_context)
    with pytest.raises(ExposureError) as failure:
        render(MountParent(), make_request(rf, SessionStore()))
    assert "CONTEXT_SECRET_SENTINEL" not in str(failure.value)
    assert "CONTEXT_SECRET_SENTINEL" not in caplog.text


def test_explicit_restore_keeps_legacy_decimal_tag_shapes_as_plain_json(rf):
    from djust.serialization import STATE_DECIMAL_TAG

    request = make_request(rf, SessionStore())
    parent = MountParent()
    adapter = seed(parent, request)
    value = {STATE_DECIMAL_TAG: "19.99"}
    adapter.save({"count": 9, "secret": value})
    render(parent, request)
    assert parent._get_child_view("counter").secret == value
