"""Completed full-page versus live-root ownership and storage reconciliation."""

import copy

import pytest
from django.contrib.sessions.backends.db import SessionStore

from djust import LiveView, event_handler
from djust._exposure import ExposureError
from djust._exposure_child_persistence import save_child_states
from djust._exposure_children import child_state_key
from djust.decorators import state
from djust.tests.test_exposure_child_mount import MountChild, make_request


class ShellChild(MountChild):
    sticky_id = "shell"
    count = state(1, persist="server")
    secret = state("SERVER_SENTINEL", persist="server")
    template = "<div>Shell={{ count }}</div>"


class RootChild(MountChild):
    sticky_id = "root"
    count = state(1, persist="server")
    secret = state("SERVER_SENTINEL", persist="server")
    template = "<div>Root={{ count }}</div>"

    @event_handler()
    def hide_leaf(self):
        self.count = 2


class LeafChild(RootChild):
    count = state(1, persist="server")
    secret = state("SERVER_SENTINEL", persist="server")
    template = "<div>Leaf={{ count }}</div>"


class Page(LiveView):
    exposure_policy = "explicit"
    template_name = "child_prune_page.html"
    show_root = state(True, persist="server")
    show_shell = state(True, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            show_root=self.show_root, show_shell=self.show_shell, **kwargs
        )


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings, tmp_path, db):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [__name__, MountChild.__module__]
    templates = copy.deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"] = [str(tmp_path), *templates[0].get("DIRS", [])]
    settings.TEMPLATES = templates
    (tmp_path / "child_prune_base.html").write_text(
        "{% load live_tags %}<html><body>{% if show_shell %}{% live_render "
        f'"{__name__}.ShellChild" sticky=True %}}'
        "{% endif %}{% block main %}{% endblock %}</body></html>"
    )
    (tmp_path / "child_prune_page.html").write_text(
        '{% extends "child_prune_base.html" %}{% load live_tags %}{% block main %}'
        "<div dj-root>{% if show_root %}{% live_render "
        f'"{__name__}.RootChild" sticky=True %}}'
        "{% endif %}</div>{% endblock %}"
    )


def mounted(rf):
    page = Page()
    request = make_request(rf, SessionStore())
    page.request = request
    page.get_template()
    html = page.render_full_template(request)
    assert "Shell=1" in html and "Root=1" in html
    page.render_with_diff(request)
    page._cached_context = None
    save_child_states(page, request)
    return page, request


def test_root_update_prunes_root_child_but_retains_shell(rf):
    page, request = mounted(rf)
    root = page._get_child_view("root")
    shell = page._get_child_view("shell")
    assert page._explicit_child_render_regions["shell"][1] == "shell"
    page.show_root = False
    html, _, _ = page.render_with_diff(request)
    assert "Root=1" not in html
    assert page._get_child_view("root") is None
    assert root._djust_child_disposed
    assert page._get_child_view("shell") is shell
    save_child_states(page, request)
    stored = SessionStore(request.session.session_key).load()
    assert child_state_key(request.path, ("root",)) not in stored
    assert child_state_key(request.path, ("shell",)) in stored


def test_fresh_root_only_render_does_not_prune_stored_shell_scope(rf):
    _, original = mounted(rf)
    request = make_request(rf, SessionStore(original.session.session_key))
    page = Page()
    page.request = request
    page.show_root = False
    page.render_with_diff(request)
    save_child_states(page, request)
    stored = SessionStore(request.session.session_key).load()
    assert child_state_key(request.path, ("root",)) not in stored
    assert child_state_key(request.path, ("shell",)) in stored


def test_explicit_unregister_of_known_shell_does_prune_its_state(rf):
    page, request = mounted(rf)
    shell = page._get_child_view("shell")
    page._unregister_child("shell")
    assert shell._djust_child_disposed
    assert "shell" not in page._explicit_child_render_regions
    save_child_states(page, request)
    stored = SessionStore(request.session.session_key).load()
    assert child_state_key(request.path, ("shell",)) not in stored
    assert child_state_key(request.path, ("root",)) in stored


def test_fragment_fallback_is_not_authority_to_prune_the_page_shell(rf, monkeypatch):
    from djust import _rust

    _, original = mounted(rf)

    def unsupported(*args, **kwargs):
        raise RuntimeError("inheritance unavailable")

    monkeypatch.setattr(_rust, "resolve_template_inheritance", unsupported)
    request = make_request(rf, SessionStore(original.session.session_key))
    page = Page()
    page.request = request
    page.show_root = False
    page.get_template()
    page.render_full_template(request)
    assert page._explicit_child_render_scope == "root"
    save_child_states(page, request)
    stored = SessionStore(request.session.session_key).load()
    assert child_state_key(request.path, ("shell",)) in stored


def test_failed_full_render_does_not_commit_nested_prune_plans(rf, monkeypatch):
    page, request = mounted(rf)
    root = page._get_child_view("root")
    before = SessionStore(request.session.session_key).load()
    page.show_root = False

    def broken(self, **kwargs):
        raise ExposureError("RENDER_SECRET_SENTINEL")

    monkeypatch.setattr(ShellChild, "get_context_data", broken)
    with pytest.raises((ExposureError, RuntimeError)):
        page.render_full_template(request)
    assert page._get_child_view("root") is root
    assert not getattr(root, "_djust_child_disposed", False)
    assert SessionStore(request.session.session_key).load() == before


def test_full_render_prunes_only_its_route(rf):
    page, request = mounted(rf)
    other = Page()
    other_request = make_request(rf, request.session, path="/other/")
    other.request = other_request
    other.get_template()
    other.render_full_template(other_request)
    save_child_states(other, other_request)
    other_keys = {child_state_key("/other/", (slot,)) for slot in ("root", "shell")}
    page.show_root = page.show_shell = False
    page.render_full_template(request)
    save_child_states(page, request)
    stored = SessionStore(request.session.session_key).load()
    assert other_keys <= stored.keys()
    assert all(child_state_key(request.path, (slot,)) not in stored for slot in ("root", "shell"))


def test_markup_without_a_child_renderer_cannot_retain_removed_slot(rf):
    page, request = mounted(rf)
    child = page._get_child_view("root")
    page._rust_view.update_template(
        '<div dj-root><div data-djust-embedded="root">not a component</div></div>'
    )
    page.render_with_diff(request)
    assert page._get_child_view("root") is None
    assert child._djust_child_disposed


def test_unregistered_markup_wrapper_cannot_hide_a_real_child(rf):
    page, request = mounted(rf)
    root = page._get_child_view("root")
    page._rust_view.update_template(
        '<div dj-root><div data-djust-embedded="not-a-component">{% load live_tags %}'
        "{% live_render " + f'"{__name__}.RootChild" sticky=True %}}' + "</div></div>"
    )
    page.render_with_diff(request)
    assert page._get_child_view("root") is root
    assert not getattr(root, "_djust_child_disposed", False)


@pytest.mark.parametrize("container", ["textarea", "template"])
def test_inert_markup_does_not_keep_a_rendered_child_alive(rf, container):
    from django.template import Context, Template

    from djust._child_rendering import reconcile_child_render

    page, request = mounted(rf)
    root = page._get_child_view("root")

    @reconcile_child_render()
    def render_inert(owner):
        child_html = Template(
            "{% load live_tags %}{% live_render " + f'"{__name__}.RootChild" sticky=True %}}'
        ).render(Context({"view": owner, "request": request}))
        return f"<div dj-root><{container}>{child_html}</{container}></div>"

    render_inert(page)
    assert page._get_child_view("root") is None
    assert root._djust_child_disposed


def test_nested_same_named_slot_is_disposed_with_its_owner(rf, monkeypatch):
    monkeypatch.setattr(
        RootChild,
        "template",
        "{% load live_tags %}{% live_render " + f'"{__name__}.LeafChild" sticky=True %}}',
    )
    page = Page()
    request = make_request(rf, SessionStore())
    page.request = request
    page.get_template()
    page.render_full_template(request)
    page.render_with_diff(request)
    page._cached_context = None
    root = page._get_child_view("root")
    leaf = root._get_child_view("root")
    assert leaf is not None
    save_child_states(page, request)
    page.show_root = False
    page.render_with_diff(request)
    save_child_states(page, request)
    assert root._djust_child_disposed and leaf._djust_child_disposed
    assert root._explicit_child_render_regions == {}
    stored = SessionStore(request.session.session_key).load()
    assert child_state_key(request.path, ("root",)) not in stored
    assert child_state_key(request.path, ("root", "root")) not in stored


def test_late_parent_render_error_leaves_pending_nested_removal_uncommitted(rf, monkeypatch):
    monkeypatch.setattr(
        RootChild,
        "template",
        "{% load live_tags %}{% if count == 1 %}{% live_render "
        + f'"{__name__}.LeafChild" sticky=True %}}'
        + "{% endif %}",
    )
    page = Page()
    request = make_request(rf, SessionStore())
    page.request = request
    page.get_template()
    page.render_full_template(request)
    root = page._get_child_view("root")
    leaf = root._get_child_view("root")
    root.count = 2

    def broken(self, **kwargs):
        raise ExposureError("RENDER_SECRET_SENTINEL")

    monkeypatch.setattr(ShellChild, "get_context_data", broken)
    with pytest.raises((ExposureError, RuntimeError)):
        page.render_full_template(request)
    assert root._get_child_view("root") is leaf
    assert not getattr(leaf, "_djust_child_disposed", False)


@pytest.mark.parametrize(
    "malformed",
    [
        {"version": 2, "paths": [], "shell_paths": []},
        {"version": 1, "paths": ["_auth_user_id"], "shell_paths": []},
        {"version": 1, "paths": [["root"]], "shell_paths": [["unknown"]]},
    ],
)
def test_invalid_index_does_not_delete_session_entries(rf, malformed):
    from djust._child_state_index import _index_key

    page, request = mounted(rf)
    request.session[_index_key(request.path)] = malformed
    request.session.save()
    before = SessionStore(request.session.session_key).load()
    page.show_root = page.show_shell = False
    page.render_full_template(request)
    with pytest.raises(ExposureError, match="persistence unavailable"):
        save_child_states(page, request)
    assert SessionStore(request.session.session_key).load() == before


def test_index_cannot_supply_an_arbitrary_deletion_key(rf):
    from djust._child_state_index import _index_key

    page, request = mounted(rf)
    unrelated = child_state_key("/other/", ("root",))
    request.session[unrelated] = "OTHER_ROUTE_SENTINEL"
    request.session["_auth_user_id"] = "AUTH_SENTINEL"
    request.session[_index_key(request.path)] = {
        "version": 1,
        "paths": [[unrelated], ["_auth_user_id"]],
        "shell_paths": [],
    }
    request.session.save()
    page.show_root = page.show_shell = False
    page.render_full_template(request)
    save_child_states(page, request)
    stored = SessionStore(request.session.session_key).load()
    assert stored[unrelated] == "OTHER_ROUTE_SENTINEL"
    assert stored["_auth_user_id"] == "AUTH_SENTINEL"


def test_failed_prune_flush_restores_local_entries_and_modified_flag(rf, monkeypatch):
    page, request = mounted(rf)
    before = dict(request.session.items())
    page.show_root = page.show_shell = False
    page.render_full_template(request)
    request.session.modified = False

    def failed_save():
        raise OSError("STORAGE_SECRET_SENTINEL")

    monkeypatch.setattr(request.session, "save", failed_save)
    with pytest.raises(ExposureError, match="persistence unavailable"):
        save_child_states(page, request)
    assert dict(request.session.items()) == before
    assert request.session.modified is False
    assert SessionStore(request.session.session_key).load() == before


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_child_routed_event_prunes_nested_storage_before_success(rf, monkeypatch):
    from asgiref.sync import sync_to_async
    from django.test import override_settings

    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    monkeypatch.setattr(
        RootChild,
        "template",
        "{% load live_tags %}{% if count == 1 %}{% live_render "
        + f'"{__name__}.LeafChild" sticky=True %}}'
        + "{% endif %}",
    )
    request = make_request(rf, SessionStore())
    transport = MockTransport()
    await sync_to_async(request.session.create)()
    transport.build_request = lambda: request

    async def fresh(view):
        return make_request(rf, SessionStore(request.session.session_key))

    transport.explicit_event_request = fresh
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".Page", "url": request.path}
        )
    assert not transport.errors, transport.errors
    root = runtime.view_instance._get_child_view("root")
    leaf = root._get_child_view("root")
    await runtime.dispatch_event(
        {"type": "event", "event": "hide_leaf", "params": {"view_id": "root"}}
    )
    assert not transport.errors, transport.errors
    assert leaf._djust_child_disposed
    assert root._get_child_view("root") is None
    assert any(frame.get("type") == "embedded_update" for frame in transport.sent)
    stored = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert child_state_key(request.path, ("root", "root")) not in stored
    assert stored[child_state_key(request.path, ("root",))]["state"]["values"]["count"] == 2
