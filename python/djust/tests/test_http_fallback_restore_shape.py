"""The HTTP event fallback rebuilds the view the WebSocket kept alive.

Over the socket the view instance lives for the whole page; the HTTP POST
fallback builds a fresh instance per event from the state the session saved.
Two ways that rebuild produced a different view than the one the page had:

* The saved public state was written back with ``setattr`` for every key. A
  context key named like a method or property of the view replaced the method
  on the instance with the saved value (``self.images_go_out`` a bool instead
  of a method), and the next render that called it failed with
  ``TypeError: 'bool' object is not callable``. The restore now never writes
  over a callable or property the class defines, and says so.
* Private attributes are saved only when they are JSON-serializable. One that
  is not (a service client, a dataclass, a stored callable) was simply missing
  on the rebuilt instance, so a handler that read it raised AttributeError
  and the event answered 500. When the saved private state does not cover
  every private attribute, the fallback now runs ``mount()`` to rebuild them
  and applies the saved state on top, as the explicit-exposure path does.
"""

import json
import logging

import pytest
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler


class _Client:
    """A non-JSON service object a view keeps in private state."""

    def __init__(self, label):
        self.label = label

    def greet(self):
        return f"hello from {self.label}"


class ShadowingView(LiveView):
    template = "<div dj-root><p>{{ count }}</p><p>{{ status }}</p></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # A context key that names a method and one that names a property.
        ctx["images_go_out"] = self.images_go_out()
        ctx["status"] = self.status
        return ctx

    def images_go_out(self):
        return self.count > 0

    @property
    def status(self):
        return "busy" if self.count else "idle"

    @event_handler()
    def bump(self):
        self.count += 1


class ServiceView(LiveView):
    template = "<div dj-root><p>{{ count }}</p><p>{{ greeting }}</p></div>"
    mounts = 0

    def mount(self, request, **kwargs):
        type(self).mounts += 1
        self.count = 0
        self.greeting = ""
        self._client = _Client("api")  # not JSON: never reaches the session
        self._seen = []  # JSON: saved and restored

    @event_handler()
    def bump(self):
        self.count += 1
        self._seen.append(self.count)
        self.greeting = self._client.greet()


def _initial():
    from djust.tests.test_exposure_runtime import make_request

    return make_request()


def _post(view_cls, initial, event, params=None):
    request = RequestFactory().post(
        initial.path,
        data=json.dumps(params or {}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT=event,
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    view = view_cls()
    response = view.post(request)
    return view, response


@pytest.mark.django_db
def test_restore_never_replaces_a_method_or_property_with_saved_state(caplog):
    initial = _initial()
    ShadowingView().get(initial)

    view, first = _post(ShadowingView, initial, "bump")
    assert first.status_code == 200, first.content
    assert callable(view.images_go_out)
    assert isinstance(type(view).__dict__["status"], property)
    assert "images_go_out" not in view.__dict__
    assert "status" not in view.__dict__

    # A key an override adds that no attribute walk produced is no longer
    # saved (it is computed again on every render), so it cannot shadow the
    # method...
    view_key = next(
        k for k in initial.session.keys() if k.startswith("liveview_") and "__" not in k
    )
    saved = initial.session[view_key]
    assert "images_go_out" not in saved
    # ...but a session written before that change can still hold such keys,
    # and the restore must still refuse them: seed one and run the next event.
    initial.session[view_key] = {**saved, "images_go_out": True, "status": "busy"}
    initial.session.save()
    with caplog.at_level(logging.WARNING, logger="djust"):
        view, second = _post(ShadowingView, initial, "bump")
    assert second.status_code == 200, second.content
    assert callable(view.images_go_out)
    assert view.count == 2
    warned = [r.getMessage() for r in caplog.records if "images_go_out" in r.getMessage()]
    assert warned, [r.getMessage() for r in caplog.records]
    assert "ShadowingView" in warned[0]


@pytest.mark.django_db
def test_private_attributes_that_cannot_be_saved_are_rebuilt_by_mount():
    initial = _initial()
    ServiceView.mounts = 0
    ServiceView().get(initial)
    assert ServiceView.mounts == 1

    view, first = _post(ServiceView, initial, "bump")
    assert first.status_code == 200, first.content
    assert view.greeting == "hello from api"

    view, second = _post(ServiceView, initial, "bump")
    assert second.status_code == 200, second.content
    # Public and serializable private state come from the session, not mount().
    assert view.count == 2
    assert view._seen == [1, 2]
    assert isinstance(view._client, _Client)


class JsonOnlyView(LiveView):
    template = "<div dj-root><p>{{ count }}</p></div>"
    mounts = 0

    def mount(self, request, **kwargs):
        type(self).mounts += 1
        self.count = 0
        self._seen = []

    @event_handler()
    def bump(self):
        self.count += 1
        self._seen.append(self.count)


@pytest.mark.django_db
def test_fallback_does_not_remount_when_private_state_was_saved_whole():
    initial = _initial()
    JsonOnlyView.mounts = 0
    JsonOnlyView().get(initial)
    for _ in range(2):
        _view, response = _post(JsonOnlyView, initial, "bump")
        assert response.status_code == 200, response.content
    assert JsonOnlyView.mounts == 1
    assert _view._seen == [1, 2]


def test_restore_helper_skips_methods_and_properties_keeps_data():
    from djust._state_restore import restore_public_state

    class Plain:
        label = "default"

        def go(self):
            return True

        @property
        def shown(self):
            return "computed"

    target = Plain()
    applied = restore_public_state(
        target, {"go": False, "shown": "saved", "label": "saved", "extra": 3, "_x": 1}
    )
    assert applied == {"label": "saved", "extra": 3}
    assert target.go() is True
    assert target.shown == "computed"
    assert "_x" not in target.__dict__


def test_every_context_restore_site_goes_through_the_helper():
    """The rule holds on each path that writes saved context back (#1646)."""
    from pathlib import Path

    import djust

    root = Path(djust.__file__).parent
    sites = {
        "mixins/request.py": 2,  # post(): restore, and again after the rebuild mount
        "runtime.py": 1,  # socket snapshot restore
        "mixins/sticky.py": 1,  # sticky child
        "_lazy_containers.py": 1,  # lazily mounted child
        "live_view.py": 1,  # client state snapshot
    }
    found = {name: (root / name).read_text().count("restore_public_state(") for name in sites}
    assert found == sites
