"""Native visibility observations are informational, scoped and no-op by default."""

import json

from asgiref.sync import async_to_sync
import pytest

from djust import LiveView
from djust.components._interactive import DropdownMenu


observations = []


class ObserverPage(LiveView):
    template = "<div dj-root>{{ menu }}{{ quiet }}<p>{{ last }}</p></div>"
    enable_state_snapshot = True
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
        raise RuntimeError("observer failed")


@pytest.fixture(autouse=True)
def clear_observations():
    observations.clear()


def page():
    view = ObserverPage()
    view.mount(None)
    return view


def report(menu, sequence=1, open=True, **kwargs):
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
    assert "observation" not in json.dumps(view._capture_snapshot_state(strict=True))


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


def test_session_bookkeeping_is_explicit_but_signed_snapshots_do_not_replay_it():
    source = page()
    report(source.menu, 7)
    saved = source._extract_component_state(source.menu)
    assert saved["observation"] == {"lifetime": source.menu._observation_lifetime, "sequence": 7}
    target = page()
    target._restore_component_state(target.menu, saved)
    assert target.menu._observation_lifetime == source.menu._observation_lifetime
    report(target.menu, 7)
    assert len(observations) == 1
    signed = source._capture_snapshot_state(strict=True)
    fresh = ObserverPage()
    fresh._restore_snapshot(signed)
    assert fresh.menu._observation_lifetime != source.menu._observation_lifetime
    assert fresh.menu._observation_sequence == 0


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"lifetime": "bad", "sequence": 0},
        {"lifetime": "obs_" + "0" * 32, "sequence": True},
    ],
)
def test_malformed_session_observation_cannot_partially_restore_binding(bad):
    view = page()
    before = view.menu._dump_session_binding()
    candidate = {**before, "binding_id": "cmp_" + "0" * 32, "observation": bad}
    with pytest.raises(ValueError, match="snapshot"):
        view._restore_component_state(view.menu, candidate)
    assert view.menu._dump_session_binding() == before


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


@pytest.mark.django_db
@pytest.mark.parametrize("changes_state", [False, True])
def test_http_observation_noop_does_not_render_but_reactive_observer_does(
    monkeypatch, changes_state
):
    from django.test import RequestFactory
    from djust.tests.test_exposure_runtime import make_request

    cls = RenderingObserverPage if changes_state else ObserverPage
    request = make_request()
    first = cls()
    assert first.get(request).status_code == 200
    params = {
        "component_id": first.menu.component_id,
        "open": True,
        "sequence": 1,
        "lifetime": first.menu._observation_lifetime,
    }
    if not changes_state:
        from djust import websocket

        original_diff = websocket._compute_changed_keys
        changes = []

        def capture_diff(before, after):
            result = original_diff(before, after)
            changes.append(result)
            return result

        monkeypatch.setattr(websocket, "_compute_changed_keys", capture_diff)
        monkeypatch.setattr(
            cls, "render_with_diff", lambda *a, **kw: pytest.fail(f"noop rendered: {changes}")
        )
    post = RequestFactory().post(
        request.path,
        data=json.dumps({"event": "observe_toggle", "params": params}),
        content_type="application/json",
    )
    post.user, post.session, post.tenant = request.user, request.session, None
    restored = cls()
    response = restored.post(post)
    assert response.status_code == 200, response.content
    if changes_state:
        assert restored.last == "opened"
        assert b"opened" in response.content
    else:
        assert json.loads(response.content)["type"] == "noop"
        assert observations == [(restored.menu, True)]
    saved = request.session[f"liveview_{request.path}_components"]["menu"]
    assert saved["observation"]["sequence"] == 1
    if not changes_state:
        duplicate = cls().post(post)
        assert json.loads(duplicate.content)["type"] == "noop"
        assert len(observations) == 1


@pytest.mark.django_db
@pytest.mark.xfail(
    strict=True,
    reason="ADR-034 C2: failed HTTP observation cursor needs isolated persistence without saving failed application session changes",
)
def test_http_observer_error_persists_only_cursor_and_does_not_replay_same_report():
    from django.test import RequestFactory
    from djust.tests.test_exposure_runtime import make_request

    request = make_request()
    first = FailingObserverPage()
    first.get(request)
    request.session.save()  # Complete the GET's normal middleware persistence.
    params = {
        "component_id": first.menu.component_id,
        "open": True,
        "sequence": 1,
        "lifetime": first.menu._observation_lifetime,
    }
    post = RequestFactory().post(
        request.path,
        data=json.dumps({"event": "observe_toggle", "params": params}),
        content_type="application/json",
    )
    post.user, post.session, post.tenant = request.user, request.session, None
    failed = FailingObserverPage().post(post)
    assert failed.status_code == 500
    assert request.session[f"liveview_{request.path}"]["last"] == "initial"
    post.session = type(request.session)(session_key=request.session.session_key)
    repeated = FailingObserverPage().post(post)
    assert repeated.status_code == 200, repeated.content
    assert json.loads(repeated.content)["type"] == "noop"
    assert len(observations) == 1


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("changes_state", [False, True])
async def test_native_ws_observation_uses_normal_noop_or_render_path(changes_state):
    from django.test import override_settings
    from djust.security import unsign_snapshot
    from djust.tests.test_state_snapshot_signing import _connect, _make_session_key, _receive_mount
    import re

    cls = RenderingObserverPage if changes_state else ObserverPage
    slug = __name__ + "." + cls.__name__
    session = await _make_session_key()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        socket = await _connect(session)
        try:
            await socket.send_json_to({"type": "mount", "view": slug, "url": "/"})
            frame = await _receive_mount(socket)
            state = json.loads(unsign_snapshot(frame["state_snapshot_signed"], slug, session))
            identity = state["__interactive_bindings__"]["bindings"]["menu"]["binding_id"]
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
            response = await socket.receive_json_from(timeout=3)
            assert response["type"] == ("html_update" if changes_state else "noop"), response
            if changes_state:
                assert "opened" in response["html"]
            else:
                assert len(observations) == 1 and observations[0][1] is True
        finally:
            await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_signed_resume_rotates_observation_lifetime_and_first_report_stays_noop():
    import re
    from django.test import override_settings
    from djust.security import unsign_snapshot
    from djust.tests.test_state_snapshot_signing import _connect, _make_session_key, _receive_mount

    slug = __name__ + ".ObserverPage"
    session = await _make_session_key()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        socket = await _connect(session)
        await socket.send_json_to({"type": "mount", "view": slug, "url": "/"})
        first = await _receive_mount(socket)
        old = re.search('data-dj-observe-lifetime="([^"]+)"', first["html"])[1]
        blob = first["state_snapshot_signed"]
        state = json.loads(unsign_snapshot(blob, slug, session))
        identity = state["__interactive_bindings__"]["bindings"]["menu"]["binding_id"]
        await socket.disconnect()
        socket = await _connect(session)
        try:
            await socket.send_json_to(
                {
                    "type": "live_redirect_mount",
                    "view": slug,
                    "url": "/",
                    "has_prerendered": True,
                    "state_snapshot": {"view_slug": slug, "state_json": blob},
                }
            )
            restored = await _receive_mount(socket)
            current = re.search('data-dj-observe-lifetime="([^"]+)"', restored["html"])[1]
            assert current != old
            for lifetime, count in [(old, 0), (current, 1)]:
                await socket.send_json_to(
                    {
                        "type": "event",
                        "event": "observe_toggle",
                        "ref": 2,
                        "params": {
                            "component_id": identity,
                            "lifetime": lifetime,
                            "sequence": 1,
                            "open": True,
                        },
                    }
                )
                response = await socket.receive_json_from(timeout=3)
                assert response["type"] == "noop", response
                assert len(observations) == count
        finally:
            await socket.disconnect()
