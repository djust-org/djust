"""ADR-034 C1: the public interactive import, its checks and the debug transport.

Owner decisions (2026-09-25): Q1 publishes ``DropdownMenu``, ``ActionItem`` and
``SeparatorItem`` from ``djust.components.interactive`` and adds a check for a
module importing both DropdownMenus (``djust.Q004``). Q5 keeps actor views
unsupported, reported at ``manage.py check`` (``djust.V020``). The debug panel's
time-travel jump restores interactive state over a real WebSocket without
emitting outputs or changing identities.
"""

import ast
import json

import pytest
from django.test import override_settings

from djust import LiveView
from djust.components.interactive import DropdownMenu

CALLS: list = []


class JumpPage(LiveView):
    template = "<div dj-root>{{ menu }}{{ other }}<p>{{ result }}</p></div>"
    time_travel_enabled = True
    menu = DropdownMenu(label="Project", items=[{"label": "Edit", "value": "edit"}])
    other = DropdownMenu(label="Account", items=[{"label": "Settings", "value": "settings"}])

    def mount(self, request, **kwargs):
        self.result = "initial"

    @menu.on.selected
    def on_menu_selected(self, component: DropdownMenu, value: str) -> None:
        CALLS.append(("menu", value))
        self.result = "project:" + value


@pytest.fixture(autouse=True)
def _calls():
    CALLS.clear()
    yield
    CALLS.clear()


# --------------------------------------------------------------------------
# Public import (Q1, Q3, Q4).
# --------------------------------------------------------------------------


def test_public_module_exports_exactly_the_decided_names():
    import djust.components.interactive as interactive
    from djust.components import _interactive

    assert interactive.__all__ == ["ActionItem", "DropdownMenu", "SeparatorItem"]
    assert interactive.DropdownMenu is _interactive.DropdownMenu
    assert interactive.ActionItem is _interactive.ActionItem
    assert interactive.SeparatorItem is _interactive.SeparatorItem
    # The output-authoring API stays private (Q4).
    for private in ("Outputs", "OutputContract", "subscribe"):
        assert not hasattr(interactive, private), private


def test_public_dropdown_is_not_the_legacy_renderer():
    from djust.components.components import DropdownMenu as LegacyDropdownMenu

    assert DropdownMenu is not LegacyDropdownMenu
    assert not issubclass(DropdownMenu, LegacyDropdownMenu)


def test_item_values_must_be_nonempty_and_unique():
    with pytest.raises(TypeError):
        DropdownMenu(label="x", items=[{"label": "Blank", "value": ""}])
    with pytest.raises(ValueError):
        DropdownMenu(
            label="x",
            items=[{"label": "A", "value": "a"}, {"label": "Again", "value": "a"}],
        )
    with pytest.raises(ValueError):
        DropdownMenu(label="x", items=[], visibility="browser")


# --------------------------------------------------------------------------
# V020: interactive components on actor views (Q5).
# --------------------------------------------------------------------------


def test_v020_reports_an_actor_view_that_declares_an_interactive_component():
    import gc

    from djust.checks.components import check_interactive_actor_views

    class ActorMenuPage(LiveView):
        use_actors = True
        menu = DropdownMenu(label="Project", items=[])

    class PlainMenuPage(LiveView):
        menu = DropdownMenu(label="Project", items=[])

    class ActorWithoutMenus(LiveView):
        use_actors = True

    try:
        found = [m for m in check_interactive_actor_views(None) if m.id == "djust.V020"]
        labels = [m.msg.split(" ")[0].rsplit(".", 1)[-1] for m in found]
        assert "ActorMenuPage" in labels
        assert "PlainMenuPage" not in labels and "ActorWithoutMenus" not in labels
        message = next(m for m in found if "ActorMenuPage" in m.msg)
        assert "menu" in message.msg and message.level >= 40  # Error
        with override_settings(DJUST_CONFIG={"suppress_checks": ["V020"]}):
            assert check_interactive_actor_views(None) == []
        # The runtime refusal the check front-runs is unchanged.
        with pytest.raises(NotImplementedError):
            ActorMenuPage().menu  # noqa: B018
    finally:
        del ActorMenuPage, PlainMenuPage, ActorWithoutMenus
        gc.collect()


# --------------------------------------------------------------------------
# Q004: one module importing both DropdownMenus (Q1).
# --------------------------------------------------------------------------


def _q004(source):
    from djust.checks.quality import _check_mixed_dropdown_imports

    lines = [""] + source.splitlines()
    return _check_mixed_dropdown_imports(ast.parse(source), lines, "views.py", "views.py")


@pytest.mark.parametrize(
    "source, line",
    [
        (
            "from djust.components.interactive import DropdownMenu\n"
            "from djust.components.components import DropdownMenu as Legacy\n",
            2,
        ),
        (
            "from djust.components.components.dropdown_menu import DropdownMenu\n"
            "x = 1\n"
            "from djust.components.interactive import ActionItem, DropdownMenu as Menu\n",
            3,
        ),
    ],
)
def test_q004_flags_a_module_importing_both(source, line):
    found = _q004(source)
    assert [(m.id, m.line_number) for m in found] == [("djust.Q004", line)]


@pytest.mark.parametrize(
    "source",
    [
        "from djust.components.interactive import DropdownMenu\n",
        "from djust.components.components import DropdownMenu\n",
        "from djust.components.interactive import DropdownMenu\n"
        "from djust.components.components import Button\n",
        "from djust.components.interactive import DropdownMenu\n"
        "from .components import DropdownMenu as Local\n",
        "from djust.components.interactive import DropdownMenu\n"
        "from djust.components.components import DropdownMenu as L  # noqa: Q004\n",
    ],
)
def test_q004_is_silent_otherwise(source):
    assert _q004(source) == []


# --------------------------------------------------------------------------
# Debug transport: time-travel jump over a real WebSocket.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_time_travel_jump_restores_interactive_state_over_websocket():
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator

    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(make_request)()
    with override_settings(
        DEBUG=True, LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DJUST_TENANTS=None
    ):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)

        async def receive_until(kinds):
            frames = []
            for _ in range(8):
                frame = await socket.receive_json_from(timeout=5)
                frames.append(frame)
                if frame.get("type") in kinds:
                    return frames
            return frames

        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".JumpPage", "url": "/jump/"}
            )
            mounted = await receive_until({"mount", "error"})
            html = mounted[-1]["html"]
            ids = [chunk.split('"', 1)[0] for chunk in html.split('data-component-id="')[1:]]
            assert len(ids) == 2 and ids[0] != ids[1], html
            menu_id, other_id = ids

            async def event(ref, name, **params):
                await socket.send_json_to(
                    {"type": "event", "event": name, "ref": ref, "params": params}
                )
                return await receive_until({"patch", "html_update", "noop", "error"})

            await event(1, "toggle", component_id=menu_id)
            await event(2, "toggle", component_id=other_id)
            frames = await event(3, "select", component_id=menu_id, value="edit")
            assert "project:edit" in json.dumps(frames)
            assert CALLS == [("menu", "edit")]

            # Jump to before the selection: the menu is open again, nothing
            # is selected, no output fires, and identities are unchanged.
            await socket.send_json_to({"type": "time_travel_jump", "index": 2, "which": "before"})
            frames = await receive_until({"time_travel_state", "error"})
            assert not any(f.get("type") == "error" for f in frames), frames
            jump = next(f for f in frames if f.get("event_name") == "__time_travel_jump__")
            restored = jump["html"]
            # Both menus are open as they were before the selection, the view
            # state is the pre-selection value, and the identities are unchanged.
            assert restored.count("dj-dropdown-menu--open") == 2, restored
            assert '<p dj-id="5">initial</p>' in restored or ">initial</p>" in restored
            assert [
                chunk.split('"', 1)[0] for chunk in restored.split('data-component-id="')[1:]
            ] == [menu_id, other_id]
            assert CALLS == [("menu", "edit")], "a restore must not emit an output"

            # The restored menu still dispatches to its own callback.
            frames = await event(4, "select", component_id=menu_id, value="edit")
            assert not any(f.get("type") == "error" for f in frames), frames
            assert CALLS == [("menu", "edit"), ("menu", "edit")]
            frames = await event(5, "select", component_id="stale-id", value="edit")
            assert frames[-1]["type"] == "error"
            assert CALLS == [("menu", "edit"), ("menu", "edit")]
        finally:
            await socket.disconnect()
