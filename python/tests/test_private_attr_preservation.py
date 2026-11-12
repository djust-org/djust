"""
Tests for the private (_-prefixed) attribute capture helpers.

2.0 stopped keeping a legacy view's private attributes in the session (the
page-POST fallback runs ``mount()`` again, which rebuilds them); the tests of
that save and of the reconnect restore were removed with it. The helpers
``_snapshot_user_private_attrs`` / ``_get_private_state`` /
``_restore_private_state`` still back the legacy ``lazy=True`` child on the
page-POST fallback (``_lazy_containers.py``) and go with that path.

Fixes: #627, #611
"""

import threading

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler


# ---------------------------------------------------------------------------
# Test views
# ---------------------------------------------------------------------------


class PrivateAttrView(LiveView):
    """View that sets private attrs in mount() and handlers."""

    exposure_policy = "legacy"
    template = "<div dj-root><span>{{ visible }}</span></div>"

    def mount(self, request, **kwargs):
        self.visible = "hello"
        self._api_token = "secret-token-123"
        self._retry_count = 0

    @event_handler()
    def do_work(self, **kwargs):
        self._retry_count += 1
        self.visible = "working"

    @event_handler()
    def set_private(self, key: str = "", val: str = "", **kwargs):
        """Set an arbitrary private attr for testing."""
        setattr(self, f"_{key}", val)


class NonSerializablePrivateView(LiveView):
    """View with private attrs that cannot be JSON-serialized."""

    exposure_policy = "legacy"
    template = "<div dj-root><span>{{ status }}</span></div>"

    def mount(self, request, **kwargs):
        self.status = "ok"
        self._lock = threading.Lock()
        self._config = {"retries": 3}  # This one IS serializable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_session(request):
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


def _setup_view(view_cls):
    """Mount a view and record its private attribute names, as the lazy child does."""
    view = view_cls()
    factory = RequestFactory()
    get_request = _add_session(factory.get("/test/"))
    view.request = get_request
    view.mount(get_request)
    view._snapshot_user_private_attrs()
    return view, factory, get_request


# ---------------------------------------------------------------------------
# Tests: _framework_attrs
# ---------------------------------------------------------------------------


class TestFrameworkAttrs:
    """_framework_attrs correctly distinguishes framework from user attrs."""

    def test_framework_attrs_set_after_init(self):
        view = LiveView()
        assert hasattr(view, "_framework_attrs")
        assert isinstance(view._framework_attrs, frozenset)

    def test_framework_attrs_contains_known_internals(self):
        view = LiveView()
        # These are set in __init__ and should be in _framework_attrs
        assert "_rust_view" in view._framework_attrs
        assert "_session_id" in view._framework_attrs
        assert "_components" in view._framework_attrs
        assert "_streams" in view._framework_attrs
        # _framework_attrs itself is NOT in the snapshot (set as last line),
        # but it's still a framework attr that _get_private_state skips
        # because it's in _framework_attrs OR because it's the sentinel itself.

    def test_user_attrs_not_in_framework_attrs(self):
        view = PrivateAttrView()
        # User attrs aren't set until mount(), not __init__
        assert "_api_token" not in view._framework_attrs
        assert "_retry_count" not in view._framework_attrs

    def test_user_private_keys_initialized_empty(self):
        """_user_private_keys starts empty before mount() runs."""
        view = LiveView()
        assert hasattr(view, "_user_private_keys")
        assert len(view._user_private_keys) == 0

    @pytest.mark.django_db
    def test_meta_attrs_excluded_from_user_private_keys(self):
        """_framework_attrs and _user_private_keys must not leak into
        _user_private_keys after snapshot."""
        view, _, _ = _setup_view(PrivateAttrView)
        assert "_framework_attrs" not in view._user_private_keys
        assert "_user_private_keys" not in view._user_private_keys

    @pytest.mark.django_db
    def test_meta_attrs_excluded_from_get_private_state(self):
        """_framework_attrs and _user_private_keys must not appear in
        serialized private state."""
        view, _, _ = _setup_view(PrivateAttrView)
        private = view._get_private_state()
        assert "_framework_attrs" not in private
        assert "_user_private_keys" not in private


# ---------------------------------------------------------------------------
# Tests: _get_private_state / _restore_private_state
# ---------------------------------------------------------------------------


class TestPrivateStateHelpers:
    """Unit tests for _get_private_state() and _restore_private_state()."""

    @pytest.mark.django_db
    def test_get_private_state_returns_user_attrs(self):
        view, _, _ = _setup_view(PrivateAttrView)
        private = view._get_private_state()
        assert "_api_token" in private
        assert private["_api_token"] == "secret-token-123"
        assert "_retry_count" in private
        assert private["_retry_count"] == 0

    @pytest.mark.django_db
    def test_get_private_state_excludes_framework_attrs(self):
        view, _, _ = _setup_view(PrivateAttrView)
        private = view._get_private_state()
        # Framework internals must NOT appear
        assert "_rust_view" not in private
        assert "_session_id" not in private
        assert "_components" not in private
        assert "_framework_attrs" not in private

    @pytest.mark.django_db
    def test_get_private_state_skips_non_serializable(self):
        view, _, _ = _setup_view(NonSerializablePrivateView)
        private = view._get_private_state()
        # Lock is not serializable — should be skipped
        assert "_lock" not in private
        # Dict IS serializable
        assert "_config" in private
        assert private["_config"] == {"retries": 3}

    def test_restore_private_state(self):
        view = PrivateAttrView()
        state = {"_api_token": "restored", "_retry_count": 5}
        view._restore_private_state(state)
        assert view._api_token == "restored"
        assert view._retry_count == 5

    def test_restore_ignores_framework_attrs(self):
        """_restore_private_state should not overwrite framework attrs."""
        view = PrivateAttrView()
        original_components = view._components
        # Try to overwrite a framework attr — it should be ignored because
        # _components IS in _framework_attrs
        view._restore_private_state({"_components": {"evil": True}})
        assert view._components is original_components


# ---------------------------------------------------------------------------
# Tests: the page-POST fallback rebuilds private attributes with mount()
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHTTPPrivateAttrsRebuiltByMount:
    """Nothing private is kept in the session; mount() rebuilds it per POST."""

    def test_nothing_private_is_saved_on_get(self):
        view = PrivateAttrView()
        get_request = _add_session(RequestFactory().get("/test/"))
        view.get(get_request)
        assert not [k for k in get_request.session.keys() if k.endswith("__private")]

    def test_private_attrs_available_to_the_handler_on_post(self):
        view = PrivateAttrView()
        factory = RequestFactory()
        get_request = _add_session(factory.get("/test/"))
        view.get(get_request)

        fresh_view = PrivateAttrView()
        post_request = factory.post(
            "/test/",
            data='{"event":"do_work","params":{}}',
            content_type="application/json",
        )
        post_request.session = get_request.session
        response = fresh_view.post(post_request)
        assert response.status_code == 200
        # mount() ran again: the handler incremented a fresh counter.
        assert fresh_view._retry_count == 1
        assert fresh_view._api_token == "secret-token-123"
        assert fresh_view.visible == "working"
        assert not [k for k in post_request.session.keys() if k.endswith("__private")]

    def test_restore_of_an_empty_private_state_is_harmless(self):
        fresh_view = PrivateAttrView()
        fresh_view._restore_private_state({})
        assert fresh_view._get_private_state() == {}
