"""Regression tests for #1722.

Context-processor-supplied variables (e.g. djust theming's ``{{ theme_panel }}``
/ ``{{ theme_head }}``) render correctly at the TOP LEVEL of a page but render
EMPTY inside the LiveView's dj-root template and its nested ``{% include %}``
partials.

Critical discriminator from the report: a plain view-instance attribute
(e.g. ``{{ nav_items }}``) DOES reach the same include — only the
context-processor vars don't. This is "incomplete #233".

Root cause: ``render_full_template`` applies context processors only to the
outer page shell. The dj-root template — rendered by ``render()`` /
``render_with_diff()`` on the initial GET and on every WebSocket update via
``_sync_state_to_rust`` — never had context processors applied, so any
context-processor var used inside the dj-root template (or an include reached
from it) resolved to empty.

The fix applies ``_apply_context_processors`` inside ``_sync_state_to_rust``.
"""

import pytest
from unittest.mock import Mock

from django.test import RequestFactory, override_settings
from django.utils.safestring import mark_safe

try:
    from djust import LiveView, RustLiveView
except ImportError:  # pragma: no cover
    LiveView = None
    RustLiveView = None

from djust.utils import clear_template_dirs_cache
from djust.mixins.context import _resolved_processors_cache, _context_processors_cache

pytestmark = pytest.mark.skipif(
    LiveView is None or RustLiveView is None,
    reason="djust.LiveView / RustLiveView not available",
)


def theme_like_processor(request):
    """Mimic djust theming's context processor: SafeString HTML blobs."""
    return {
        "theme_panel": mark_safe('<div class="theme-panel">PANEL</div>'),
        "theme_head": mark_safe("<style>body{color:red}</style>"),
    }


class IncludeView(LiveView):
    """dj-root template includes a partial that uses a context-processor var
    AND a plain view attr (the discriminator)."""

    template_name = "_1722_page.html"

    def mount(self, request, **kwargs):
        self.count = 0
        self.nav_items = "home|about"


@pytest.fixture
def mock_request():
    factory = RequestFactory()
    request = factory.get("/")
    request.session = Mock()
    request.session.session_key = "test-session-key"
    return request


@pytest.fixture
def template_dir(tmp_path):
    # dj-root template includes a partial.
    (tmp_path / "_1722_page.html").write_text(
        '<div dj-root><p>Count: {{ count }}</p>{% include "_1722_sidebar.html" %}</div>'
    )
    # The partial uses BOTH the context-processor var and the view attr.
    (tmp_path / "_1722_sidebar.html").write_text(
        "<aside>[panel:{{ theme_panel }}][nav:{{ nav_items }}]</aside>"
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
                    "python.tests.test_context_processors_in_includes_1722.theme_like_processor",
                ],
            },
        },
    ]


def _render(mock_request):
    clear_template_dirs_cache()
    _resolved_processors_cache.clear()
    _context_processors_cache.clear()

    view = IncludeView()
    view.setup(mock_request)
    view._initialize_temporary_assigns()
    view.mount(mock_request)
    return view.render(request=mock_request)


class TestContextProcessorVarsInIncludes:
    def test_discriminator_view_attr_reaches_include(self, mock_request, template_dir):
        """Discriminator: a plain view attr DOES reach the dj-root include.

        This guards against a regression that would "fix" #1722 by breaking
        the already-working plain-var path.
        """
        with override_settings(TEMPLATES=_templates_setting(template_dir)):
            html = _render(mock_request)
        assert "[nav:home|about]" in html, html

    def test_context_processor_var_reaches_include(self, mock_request, template_dir):
        """THE BUG (#1722): {{ theme_panel }} must render non-empty inside the
        dj-root template's nested include, unescaped (it is a SafeString)."""
        with override_settings(TEMPLATES=_templates_setting(template_dir)):
            html = _render(mock_request)
        assert '[panel:<div class="theme-panel">PANEL</div>]' in html, html
        # Specifically: must NOT be the empty-render shape the bug produced.
        assert "[panel:]" not in html, html
