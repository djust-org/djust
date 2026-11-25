"""Render context is not permission to persist a Rust view across requests.

No view keeps its Rust view in the state backend.
"""

from types import SimpleNamespace

import pytest

from djust import LiveView
from djust._exposure import ExposureError
from djust.state_backends.memory import InMemoryStateBackend


class CachedView(LiveView):
    exposure_policy = "explicit"
    template = "<p>{{ display }}</p>"


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    backend = InMemoryStateBackend()
    monkeypatch.setattr("djust.state_backends.get_backend", lambda: backend)
    return backend


def initialize(policy, transport, rf):
    view = CachedView(exposure_policy=policy)
    request = rf.get("/same-page/")
    request.session = SimpleNamespace(session_key="same-session")
    if transport == "ws":
        view._websocket_session_id = "same-session"
    view._initialize_rust_view(request)
    return view


@pytest.mark.parametrize("transport", ["http", "ws"])
def test_explicit_render_state_never_enters_shared_backend(backend, rf, transport):
    view = initialize("explicit", transport, rf)
    view._rust_view.update_state({"display": "TRANSIENT_RENDER_SENTINEL"})
    assert "TRANSIENT_RENDER_SENTINEL" in view._rust_view.render()
    assert backend._cache == {}
    # Repeated rendering on this instance keeps its own VDOM, not a new cache.
    rust_view = view._rust_view
    view._initialize_rust_view()
    assert view._rust_view is rust_view


@pytest.mark.parametrize("transport", ["http", "ws"])
def test_explicit_rendering_does_not_even_resolve_a_shared_backend(
    backend, rf, transport, monkeypatch
):
    def forbidden():
        raise AssertionError("The shared backend must not be consulted")

    monkeypatch.setattr("djust.state_backends.get_backend", forbidden)
    view = initialize("explicit", transport, rf)
    view._rust_view.update_state({"display": "visible"})
    assert "visible" in view._rust_view.render()


@pytest.mark.parametrize("policy", [None, "invalid", True])
def test_unknown_render_policy_rejects_before_backend_access(backend, rf, policy):
    with pytest.raises(ExposureError, match="Invalid render exposure policy"):
        initialize(policy, "http", rf)
    assert backend._cache == {}


def test_renderer_policy_marker_is_framework_state(backend, rf):
    view = initialize("explicit", "http", rf)
    view._snapshot_user_private_attrs()
    assert "_rust_view_explicit" in view._framework_attrs
