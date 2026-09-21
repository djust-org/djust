"""Staged ADR-034 concrete bindings, not the legacy visual dropdown."""

import json

import pytest
from asgiref.sync import async_to_sync

from djust import LiveView, event_handler
from djust.components._interactive import DropdownMenu


class MenuPage(LiveView):
    template = "<div dj-root>{{ menu }}{{ other }}<p>{{ result }}</p></div>"
    enable_state_snapshot = True
    menu = DropdownMenu(
        label="Project",
        items=[
            {"label": "Edit", "value": "edit"},
            {"label": "Disabled", "value": "disabled", "disabled": True},
        ],
    )
    other = DropdownMenu(label="Account", items=[{"label": "Settings", "value": "settings"}])

    def mount(self, request, **kwargs):
        self.result = "initial"
        self.calls = 0

    @menu.on.selected
    async def project_selected(self, component: DropdownMenu, value: str) -> None:
        assert component is self.menu
        assert not component.open
        self.calls += 1
        self.result = "project:" + value
        self.other.open = True

    @other.on.selected
    def account_selected(self, component: DropdownMenu, value: str) -> None:
        assert component is self.other
        self.calls += 1
        self.result = "account:" + value

    @event_handler()
    def select(self, **kwargs):
        raise AssertionError("Unknown component must not fall back to the parent")

    @event_handler()
    def open_project(self):
        self.menu.open = True


def mounted():
    view = MenuPage()
    view.mount(None)
    return view


def test_concrete_identity_registry_and_isolation():
    first, second = mounted(), mounted()
    assert type(first.menu) is DropdownMenu
    assert first.menu is first.menu
    assert first.menu is not first.other and first.menu is not second.menu
    assert first.menu is not MenuPage.menu
    assert first._components[first.menu.component_id] is first.menu
    assert len({first.menu.component_id, first.other.component_id, second.menu.component_id}) == 3
    assert first.menu.key == "menu"
    first.menu.open = True
    assert not first.other.open and not second.menu.open
    assert "_component_bindings" not in first._get_private_state()


def test_selection_awaits_matching_callback_and_sibling_mutation():
    view = mounted()
    view.menu.open = True
    async_to_sync(view.menu.select)(value="edit")
    assert (view.calls, view.result) == (1, "project:edit")
    assert view.other.open
    async_to_sync(view.other.select)(value="settings")
    assert (view.calls, view.result) == (2, "account:settings")


@pytest.mark.parametrize("value", ["disabled", "forged", True, 1, None])
def test_invalid_selection_never_changes_state_or_emits(value):
    view = mounted()
    view.menu.open = True
    with pytest.raises((TypeError, ValueError)):
        async_to_sync(view.menu.select)(value=value)
    assert view.menu.open and view.menu.selected == ""
    assert view.calls == 0


def test_unobserved_menu_still_works_and_unbound_declaration_cannot_mutate():
    class Page(LiveView):
        menu = DropdownMenu(label="Menu", items=[{"label": "Edit", "value": "edit"}])

    view = Page()
    async_to_sync(view.menu.toggle)()
    assert view.menu.open
    async_to_sync(view.menu.select)(value="edit")
    assert not view.menu.open and view.menu.selected == "edit"
    with pytest.raises(RuntimeError):
        async_to_sync(Page.menu.toggle)()


def test_unregistered_instance_fails_before_mutation():
    view = mounted()
    menu = view.menu
    view._components.pop(menu.component_id)
    with pytest.raises(RuntimeError):
        async_to_sync(menu.select)(value="edit")
    assert menu.selected == "" and view.calls == 0


def test_config_is_copied_and_output_html_is_escaped():
    items = [{"label": '<img src=x onerror="oops">', "value": 'x" onclick="oops'}]

    class Page(LiveView):
        menu = DropdownMenu(label="<script>bad</script>", items=items)

    items[0]["value"] = "changed"
    view = Page()
    view.menu.open = True
    html = str(view.menu)
    assert "<script>" not in html and "<img " not in html
    assert 'onclick="oops' not in html
    assert 'dj-value-value="x&quot; onclick=&quot;oops"' in html
    assert f'data-component-id="{view.menu.component_id}"' in html


def test_state_restore_preserves_identity_without_restoring_callbacks_or_config():
    view = mounted()
    view.menu.open = True
    state = view._extract_component_state(view.menu)
    fresh = mounted()
    old_id = fresh.menu.component_id
    fresh._restore_component_state(fresh.menu, state)
    assert fresh.menu.component_id == view.menu.component_id
    assert old_id not in fresh._components
    assert fresh._components[fresh.menu.component_id] is fresh.menu
    assert fresh.menu.open
    assert set(state) == {"binding_id", "open", "selected"}
    async_to_sync(fresh.menu.select)(value="edit")
    assert fresh.calls == 1 and view.calls == 0


@pytest.mark.django_db
def test_real_http_fallback_preserves_id_and_awaits_callback():
    from django.test import RequestFactory
    from djust.tests.test_exposure_runtime import make_request

    request = make_request()
    view = MenuPage()
    assert view.get(request).status_code == 200
    identity = view.menu.component_id
    assert identity != "menu"
    for target, event, params, result in [
        (identity, "toggle", {}, "initial"),
        (identity, "select", {"value": "edit"}, "project:edit"),
    ]:
        post = RequestFactory().post(
            request.path,
            data=json.dumps({"event": event, "params": {"component_id": target, **params}}),
            content_type="application/json",
        )
        post.user, post.session, post.tenant = request.user, request.session, None
        restored = MenuPage()
        response = restored.post(post)
        assert response.status_code == 200, response.content
        assert restored.result == result
        if event == "select":
            assert result.encode() in response.content
            assert restored.calls == 1 and restored.other.open
        state = request.session[f"liveview_{request.path}_components"]
        assert state["menu"]["binding_id"] == identity


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_real_websocket_two_menus_and_unknown_target():
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings
    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(make_request)()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DJUST_TENANTS=None):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".MenuPage", "url": request.path}
            )
            frame = await socket.receive_json_from(timeout=3)
            assert frame["type"] == "mount", frame
            import re

            identities = re.findall('data-component-id="([^"]+)"', frame["html"])
            assert len(identities) == 2 and identities[0] != identities[1]
            await socket.send_json_to(
                {"type": "event", "event": "open_project", "params": {}, "ref": 1}
            )
            opened = await socket.receive_json_from(timeout=3)
            assert opened["type"] == "patch", opened
            assert "aria-expanded" in json.dumps(opened)
            for expected in ["html_update", "noop"]:
                await socket.send_json_to(
                    {
                        "type": "event",
                        "event": "close",
                        "params": {"component_id": identities[0]},
                        "ref": 1,
                    }
                )
                closed = await socket.receive_json_from(timeout=3)
                assert closed["type"] == expected, closed
            for target, value, expected in [
                (identities[0], "edit", "project:edit"),
                (identities[1], "settings", "account:settings"),
            ]:
                await socket.send_json_to(
                    {
                        "type": "event",
                        "event": "select",
                        "params": {"component_id": target, "value": value},
                        "ref": 1,
                    }
                )
                frame = await socket.receive_json_from(timeout=3)
                assert frame["type"] == "html_update", frame
                assert expected in frame["html"], frame
            await socket.send_json_to(
                {
                    "type": "event",
                    "event": "select",
                    "params": {"component_id": "stale", "value": "edit"},
                    "ref": 2,
                }
            )
            frame = await socket.receive_json_from(timeout=3)
            assert frame["type"] == "error", frame
        finally:
            await socket.disconnect()

        # Fresh backend-backed session and consumer, not the first view object.
        resumed = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        resumed.scope.update(
            session=type(request.session)(session_key=request.session.session_key),
            user=request.user,
            tenant=None,
        )
        assert (await resumed.connect())[0]
        await resumed.receive_json_from(timeout=3)
        try:
            await resumed.send_json_to(
                {"type": "mount", "view": __name__ + ".MenuPage", "url": request.path}
            )
            frame = await resumed.receive_json_from(timeout=3)
            assert frame["type"] == "mount", frame
            assert "account:settings" in frame["html"], frame
            assert re.findall('data-component-id="([^"]+)"', frame["html"]) == identities
        finally:
            await resumed.disconnect()


@pytest.mark.parametrize(
    "changes",
    [
        {"binding_id": "menu"},
        {"open": "true"},
        {"selected": 42},
        {"callback": "evil"},
    ],
)
def test_invalid_snapshot_is_rejected_without_partial_mutation(changes):
    view = mounted()
    before = view._extract_component_state(view.menu)
    registry = dict(view._components)
    with pytest.raises(ValueError):
        view._restore_component_state(view.menu, {**before, **changes})
    assert view._extract_component_state(view.menu) == before
    assert view._components == registry


def test_restore_identity_collision_rejected():
    view = mounted()
    state = view._extract_component_state(view.other)
    before = view._extract_component_state(view.menu)
    with pytest.raises(ValueError, match="collision"):
        view._restore_component_state(view.menu, state)
    assert view._extract_component_state(view.menu) == before


def test_restored_selection_is_checked_against_current_server_configuration():
    view = mounted()
    state = view._extract_component_state(view.menu)
    view._restore_component_state(view.menu, {**state, "selected": "disabled"})
    assert view.menu.selected == "" and view.calls == 0


def test_callback_exception_does_not_rollback_component_or_run_twice():
    class Broken(LiveView):
        menu = DropdownMenu(label="Menu", items=[{"label": "Edit", "value": "edit"}])

        @menu.on.selected
        async def selected(self, component: DropdownMenu, value: str) -> None:
            self.calls += 1
            raise ValueError("application failure")

    view = Broken()
    view.calls = 0
    view.menu.open = True
    with pytest.raises(ValueError, match="application failure"):
        async_to_sync(view.menu.select)(value="edit")
    assert view.calls == 1 and not view.menu.open and view.menu.selected == "edit"


def test_server_toggle_observation_is_actual_post_change_value():
    class Observed(LiveView):
        menu = DropdownMenu(label="Menu", items=[])

        @menu.on.toggled
        def toggled(self, component: DropdownMenu, open: bool) -> None:
            assert component is self.menu and component.open is open
            self.observations.append(open)

    view = Observed()
    view.observations = []
    async_to_sync(view.menu.toggle)()
    async_to_sync(view.menu.close)()
    async_to_sync(view.menu.close)()
    assert view.observations == [True, False]


@pytest.mark.parametrize("name", ["render", "unmount", "send_parent", "update"])
@pytest.mark.parametrize("mode", ["open", "warn", "strict"])
def test_undeclared_component_methods_are_not_transport_actions(name, mode, monkeypatch):
    from djust.websocket_utils import _check_event_security
    from djust.config import config

    view = mounted()
    monkeypatch.setattr(
        config, "get", lambda key, default=None: mode if key == "event_security" else default
    )
    assert _check_event_security(getattr(view.menu, name), view.menu, name) is not None


def test_stale_declaration_replacement_fails_closed_before_mutation():
    class Page(MenuPage):
        pass

    view = Page()
    view.mount(None)
    menu = view.menu
    Page.menu = DropdownMenu(label="Replacement", items=[])
    with pytest.raises(RuntimeError, match="stale"):
        async_to_sync(menu.select)(value="edit")
    assert view.calls == 0


def test_django_template_renders_bound_markup_and_live_state():
    from django.template import Context, Engine

    view = mounted()
    template = Engine().from_string("{{ menu }}|{{ menu.open }}|{{ other.open }}")
    before = template.render(Context({"menu": view.menu, "other": view.other}))
    assert '<button type="button"' in before
    assert 'dj-value-value="edit"' not in before
    async_to_sync(view.menu.toggle)()
    after = template.render(Context({"menu": view.menu, "other": view.other}))
    assert 'dj-value-value="edit"' in after
    assert after.endswith("|True|False")


@pytest.mark.parametrize(
    "items",
    [
        [{"label": "Edit", "event": "legacy_handler"}],
        [{"label": "Edit", "value": "edit"}, {"label": "Duplicate", "value": "edit"}],
        [{"separator": False}],
        [{"label": "Edit", "value": ""}],
        [{"label": "Edit", "value": "edit", "disabled": "false"}],
    ],
)
def test_invalid_or_legacy_constructor_configuration_rejected(items):
    with pytest.raises((ValueError, TypeError)):
        DropdownMenu(label="Menu", items=items)


def test_binding_fingerprints_change_without_replacing_object():
    from djust.change_detection import deep_fingerprint

    view = mounted()
    menu = view.menu
    before = deep_fingerprint(menu)
    menu.open = True
    assert deep_fingerprint(menu) != before
    assert view.menu is menu


def test_view_snapshot_tracks_binding_state_not_framework_cache():
    from djust.websocket import _snapshot_assigns, _compute_changed_keys

    view = mounted()
    _ = view.menu
    before = _snapshot_assigns(view)
    view.menu.open = True
    after = _snapshot_assigns(view)
    assert _compute_changed_keys(before, after) == {"menu"}
    assert "_component_bindings" not in after


def test_callback_source_cannot_be_spoofed_by_instance_override():
    view = mounted()
    view.project_selected = lambda **kwargs: pytest.fail("forged callback executed")
    with pytest.raises(RuntimeError, match="validated declaration"):
        async_to_sync(view.menu.select)(value="edit")
    assert view.calls == 0


def test_replaced_callback_descriptor_is_rejected_without_evaluation():
    class Page(MenuPage):
        pass

    view = Page()
    view.mount(None)

    def trap(self):
        pytest.fail("replacement callback property was evaluated")

    Page.project_selected = property(trap)
    with pytest.raises(TypeError, match="instance method"):
        async_to_sync(view.menu.select)(value="edit")
    assert view.calls == 0
