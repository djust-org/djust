"""Regression tests for reflective-XSS hardening in gallery views.

Covers 6 CodeQL ``py/reflective-xss`` alerts:

- 3 real XSS sites in ``djust.theming.gallery.views`` where user-controlled URL
  kwargs (``component_name``, ``category``) were echoed into
  ``HttpResponseNotFound(f"...")`` bodies unescaped.
- 3 defense-in-depth sites in ``djust.components.gallery.views`` where
  cookie-derived values flow through an allowlist before reaching HTML output.
  The allowlist guarantees safety, but we now also ``escape()`` the values so
  the taint analyzer can see sanitization explicitly.
"""

# Import conftest first to configure Django settings before we add ours.
import tests.conftest  # noqa: F401

import pytest
from django.test import RequestFactory, override_settings

pytestmark = pytest.mark.theming


@pytest.fixture
def rf():
    return RequestFactory()


# ---------------------------------------------------------------------------
# Real XSS fixes — theming.gallery.views
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_theming_storybook_unknown_component_escaped_in_404(rf):
    """Unknown component name with HTML payload must be escaped in the 404 body."""
    from djust.theming.gallery.views import storybook_detail_view

    payload = "foo<script>alert(1)</script>"
    request = rf.get(f"/storybook/{payload}/")
    request.session = {}
    response = storybook_detail_view(request, component_name=payload)

    assert response.status_code == 404
    body = response.content.decode()
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


@override_settings(DEBUG=True)
def test_theming_storybook_unknown_category_escaped_in_404(rf):
    """Unknown category with HTML payload must be escaped in the 404 body."""
    from djust.theming.gallery.views import storybook_category_view

    payload = "foo<img src=x onerror=alert(1)>"
    request = rf.get(f"/storybook/cat/{payload}/")
    request.session = {}
    response = storybook_category_view(request, category=payload)

    assert response.status_code == 404
    body = response.content.decode()
    assert "<img" not in body
    assert "&lt;img" in body


# ---------------------------------------------------------------------------
# Defense-in-depth — components.gallery.views._resolve_theme
# ---------------------------------------------------------------------------


def test_components_gallery_theme_cookies_escaped(rf):
    """Malicious cookie values must never appear raw in rendered HTML fragments.

    The allowlist already drops this payload, but the test verifies the
    contract end-to-end.
    """
    from djust.components.gallery.views import _resolve_theme

    request = rf.get("/gallery/")
    request.COOKIES["gallery_ds"] = "<script>alert(1)</script>"
    request.COOKIES["gallery_preset"] = "<img src=x onerror=alert(1)>"

    mode, theme_css, ds_options, preset_options = _resolve_theme(request)

    assert "<script>" not in ds_options
    assert "<script>" not in preset_options
    assert "<script>" not in theme_css
    assert "<img" not in ds_options
    assert "<img" not in preset_options


def test_components_gallery_unknown_category_escaped_in_404(rf):
    """Http404 in components gallery category view must not reflect raw user input.

    Mitigated in prod by the URL slug-converter regex (``[-a-zA-Z0-9_]+``) and
    Django's default 404 template not echoing exception messages, but we escape
    the slug for defense-in-depth and to protect DEBUG-mode exposure.
    """
    from django.http import Http404

    from djust.components.gallery.views import gallery_category_view

    payload = "foo<script>alert(1)</script>"
    request = rf.get(f"/gallery/{payload}/")

    with pytest.raises(Http404) as exc_info:
        gallery_category_view(request, category_slug=payload)

    msg = str(exc_info.value)
    assert "<script>" not in msg
    assert "&lt;script&gt;" in msg


def test_components_gallery_valid_cookie_still_works(rf):
    """Sanity check: a valid allowlisted cookie value still renders normally."""
    from djust.components.gallery.views import _get_theme_options, _resolve_theme

    presets, systems = _get_theme_options()
    # Use whatever value the allowlist actually offers to avoid coupling the
    # test to a specific preset bundle being installed.
    chosen_ds = systems[0]

    request = rf.get("/gallery/")
    request.COOKIES["gallery_ds"] = chosen_ds

    mode, theme_css, ds_options, preset_options = _resolve_theme(request)

    assert f'value="{chosen_ds}"' in ds_options
    assert "selected" in ds_options
