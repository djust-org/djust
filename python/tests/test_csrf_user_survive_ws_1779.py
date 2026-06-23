"""Regression for #1779 (threat model T9): {% csrf_token %} and {{ user }} used
inside the dj-root template must survive WebSocket re-renders.

The original concern: the Rust engine re-renders the dj-root subtree on every WS
update from view-instance attributes only, so request-bound tags ({% csrf_token %})
and context-processor vars ({{ user }}) would render empty and the DOM diff would
delete the logout form / CSRF'd inline form. That is FIXED in current djust:
``_sync_state_to_rust`` applies context processors on every WS update (#1722) and
injects ``csrf_token`` (#696). These tests pin that — they render the WS path
(view.render(request=...)) twice (initial + a state change) and assert csrf + user
are present in BOTH, never blanked.
"""

from unittest.mock import Mock

import pytest
from django.test import RequestFactory, override_settings

try:
    from djust import LiveView, RustLiveView
except ImportError:  # pragma: no cover
    LiveView = None
    RustLiveView = None

from djust.mixins.context import _context_processors_cache, _resolved_processors_cache
from djust.utils import clear_template_dirs_cache

pytestmark = pytest.mark.skipif(
    LiveView is None or RustLiveView is None,
    reason="djust.LiveView / RustLiveView not available",
)


class _AuthUIView(LiveView):
    template_name = "_1779_page.html"

    def mount(self, request, **kwargs):
        self.count = 0

    def bump(self):
        self.count += 1


@pytest.fixture
def mock_request():
    request = RequestFactory().get("/")
    request.session = Mock()
    request.session.session_key = "test-session-key"
    # An authenticated user, as the auth context processor would expose.
    user = Mock()
    user.username = "alice"
    user.is_authenticated = True
    request.user = user
    return request


@pytest.fixture
def template_dir(tmp_path):
    # dj-root template uses BOTH a request-bound tag ({% csrf_token %}) and a
    # context-processor var ({{ user.username }}) — the T9 surface.
    (tmp_path / "_1779_page.html").write_text(
        "<div dj-root>"
        "{% csrf_token %}"
        "<span>user={{ user.username }}|auth={{ user.is_authenticated }}|n={{ count }}</span>"
        "</div>"
    )
    yield str(tmp_path)


def _templates_setting(template_dir):
    return [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [template_dir],
            "APP_DIRS": False,
            "OPTIONS": {
                "context_processors": [
                    "django.contrib.auth.context_processors.auth",
                ],
            },
        },
    ]


def _make_view(mock_request):
    clear_template_dirs_cache()
    _resolved_processors_cache.clear()
    _context_processors_cache.clear()
    view = _AuthUIView()
    view.setup(mock_request)
    view._initialize_temporary_assigns()
    view.mount(mock_request)
    return view


def _assert_auth_ui_present(html):
    assert "csrfmiddlewaretoken" in html, f"csrf token missing/blanked: {html!r}"
    assert "user=alice" in html, f"{{ user.username }} missing/blanked: {html!r}"
    # The Rust template engine renders booleans lowercase (true/false).
    assert "auth=true" in html, f"{{ user.is_authenticated }} missing/blanked: {html!r}"


class TestCsrfUserSurviveWsRerender:
    def test_present_on_initial_ws_render(self, mock_request, template_dir):
        with override_settings(TEMPLATES=_templates_setting(template_dir)):
            view = _make_view(mock_request)
            html = view.render(request=mock_request)
        _assert_auth_ui_present(html)

    def test_present_after_state_change_rerender(self, mock_request, template_dir):
        """The WS-update re-render (after an event mutates state) must still
        carry csrf + user — they must not be deleted by the diff (#1779/T9)."""
        with override_settings(TEMPLATES=_templates_setting(template_dir)):
            view = _make_view(mock_request)
            view.render(request=mock_request)  # initial
            view.bump()  # simulate an event mutating state
            html2 = view.render(request=mock_request)  # WS-update re-render
        _assert_auth_ui_present(html2)
        assert "n=1" in html2, f"state change not rendered: {html2!r}"
