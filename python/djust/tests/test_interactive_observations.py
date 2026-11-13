"""Native visibility observations are informational, scoped and no-op by default."""

import json

from asgiref.sync import async_to_sync
import pytest

from djust import LiveView
from djust.components._interactive import DropdownMenu


observations = []


class ObserverPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ menu }}{{ quiet }}<p>{{ last }}</p></div>"
    menu = DropdownMenu(
        label="Observed", items=[{"label": "Edit", "value": "edit"}], visibility="client"
    )
    quiet = DropdownMenu(label="Local", items=[], visibility="client")

    def mount(self, request, **kwargs):
        self.last = "initial"

    @menu.on.toggled
    def on_visibility(self, component: DropdownMenu, open: bool) -> None:
        observations.append((component, open))


class RenderingObserverPage(ObserverPage):
    def on_visibility(self, component: DropdownMenu, open: bool) -> None:
        self.last = "opened" if open else "closed"


class FailingObserverPage(ObserverPage):
    def on_visibility(self, component: DropdownMenu, open: bool) -> None:
        observations.append((component, open))
        self.last = "must-not-be-persisted"
        self.request.session["failed_application_write"] = "must-not-be-persisted"
        raise RuntimeError("observer failed")


@pytest.fixture(autouse=True)
def clear_observations():
    observations.clear()


def page():
    view = ObserverPage()
    view.mount(None)
    return view


def report(menu, sequence=1, open=True, **kwargs):
    menu.render()  # A browser report must follow a rendered observation binding.
    return async_to_sync(menu.observe_toggle)(
        open=open, sequence=sequence, lifetime=kwargs.get("lifetime", menu._observation_lifetime)
    )


def test_visibility_mode_and_native_markup_are_explicit_and_opt_in():
    view = page()
    markup = view.menu.render()
    assert 'popover="auto"' in markup and "popovertarget=" in markup
    assert 'dj-click="toggle"' not in markup
    assert 'data-dj-observe-toggle="observe_toggle"' in markup
    assert "data-dj-observe-toggle" not in view.quiet.render()
    with pytest.raises(ValueError, match="client-owned"):
        _ = view.menu.open
    with pytest.raises(ValueError, match="client-owned"):
        view.menu.open = True
    with pytest.raises(ValueError, match="visibility"):
        DropdownMenu(label="Bad", items=[], visibility="automatic")


def test_observer_injects_bound_source_without_assigning_visibility_or_dirtying_state():
    from djust.websocket import _snapshot_assigns, _compute_changed_keys

    view = page()
    menu = view.menu
    before = _snapshot_assigns(view)
    report(menu)
    assert observations == [(menu, True)]
    assert menu.state == {"open": False, "selected": ""}
    assert not _compute_changed_keys(before, _snapshot_assigns(view))
    assert "observation" not in json.dumps(view._capture_snapshot_state())


def test_duplicate_reordered_and_stale_lifetime_reports_do_not_invoke_observer():
    menu = page().menu
    report(menu, 2)
    report(menu, 2, False)
    report(menu, 1, False)
    report(menu, 100, False, lifetime="obs_" + "0" * 32)
    report(menu, 3, False)
    assert observations == [(menu, True), (menu, False)]


@pytest.mark.parametrize("sequence", [0, -1, True, "1", 2**53])
def test_invalid_sequence_rejected_before_observation(sequence):
    menu = page().menu
    with pytest.raises((TypeError, ValueError)):
        report(menu, sequence)
    assert not observations and menu._observation_sequence == 0


def test_missing_subscription_and_server_owned_mode_reject_browser_observation():
    view = page()
    with pytest.raises(ValueError, match="subscription"):
        report(view.quiet)
    from djust.tests.test_interactive_bindings import mounted

    with pytest.raises(ValueError, match="client-owned"):
        report(mounted().menu)


def test_notification_exception_consumes_sequence_without_assigning_visibility():
    class Broken(ObserverPage):
        def on_visibility(self, component: DropdownMenu, open: bool) -> None:
            observations.append((component, open))
            raise RuntimeError("observer failed")

    view = Broken()
    with pytest.raises(RuntimeError, match="observer failed"):
        report(view.menu)
    report(view.menu)  # do not invoke the failed observation twice in this lifetime
    assert observations == [(view.menu, True)]
    assert view.menu.state == {"open": False, "selected": ""}


def test_storage_failure_does_not_invoke_observer(monkeypatch):
    from djust.state_backends import get_backend

    view = page()

    def fail(*args):
        raise ConnectionError("cursor unavailable")

    monkeypatch.setattr(get_backend(), "_claim_observation", fail)
    with pytest.raises(ConnectionError, match="cursor unavailable"):
        report(view.menu)
    assert observations == []


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("changes_state", [False, True])
async def test_native_ws_observation_uses_normal_noop_or_render_path(changes_state):
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings
    from djust.websocket import LiveViewConsumer
    import re

    cls = RenderingObserverPage if changes_state else ObserverPage
    slug = __name__ + "." + cls.__name__
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        assert (await socket.connect())[0]
        await socket.receive_json_from()  # the connect frame
        try:
            await socket.send_json_to({"type": "mount", "view": slug, "url": "/"})
            frame = await socket.receive_json_from()
            assert frame["type"] == "mount", frame
            # The observed menu renders first; its binding id is in its markup.
            identity = re.search('data-component-id="([^"]+)"', frame["html"])[1]
            lifetime = re.search('data-dj-observe-lifetime="([^"]+)"', frame["html"])[1]
            await socket.send_json_to(
                {
                    "type": "event",
                    "event": "observe_toggle",
                    "ref": 1,
                    "params": {
                        "component_id": identity,
                        "lifetime": lifetime,
                        "sequence": 1,
                        "open": True,
                    },
                }
            )
            response = await socket.receive_json_from()
            assert response["type"] == ("html_update" if changes_state else "noop"), response
            if changes_state:
                assert "opened" in response["html"]
            else:
                assert len(observations) == 1 and observations[0][1] is True
        finally:
            await socket.disconnect()
