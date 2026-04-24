"""Tests for ``{% djust_client_config %}`` template tag (#987).

Covers FORCE_SCRIPT_NAME / mounted sub-path support. Each test is
tied to a documented claim in ``docs/website/guides/server-functions.md``
and ``docs/website/guides/http-api.md`` (Action Tracker #124 / #125).

Claims under test:

* "Default-mounted deployments get ``/djust/api/``" →
  :func:`test_tag_emits_meta_with_default_prefix`.
* "``reverse()`` honors ``FORCE_SCRIPT_NAME``" →
  :func:`test_tag_emits_meta_under_force_script_name`.
* "Custom prefix via ``api_patterns(prefix=...)``" →
  :func:`test_tag_emits_meta_when_api_mounted_at_custom_prefix`.
* "Unmounted API falls back to the client default" →
  :func:`test_tag_when_api_app_not_mounted`.
* "Output is HTML-escaped" →
  :func:`test_tag_output_is_escaped`.
"""

from __future__ import annotations

import tests.conftest  # noqa: F401  -- configure Django settings

import pytest

from django.template import Context, Template
from django.test import override_settings
from django.urls import clear_url_caches, set_script_prefix


@pytest.fixture(autouse=True)
def _reset_script_prefix():
    """Reset script prefix + URL caches around each test.

    Django's ``BaseHandler`` calls :func:`set_script_prefix` at the
    start of every request to mirror ``FORCE_SCRIPT_NAME`` /
    ``SCRIPT_NAME``. In isolated pytest runs we set it manually inside
    tests that need it and restore the default here so prior test
    state does not leak.
    """
    yield
    set_script_prefix("/")
    clear_url_caches()


def _render_tag() -> str:
    """Render ``{% djust_client_config %}`` against an empty Context."""
    tpl = Template("{% load live_tags %}{% djust_client_config %}")
    return tpl.render(Context({}))


# ---------------------------------------------------------------------------
# 1. Default prefix
# ---------------------------------------------------------------------------


@override_settings(ROOT_URLCONF="tests.api_test_urls_default")
def test_tag_emits_meta_with_default_prefix():
    """Doc claim: client falls back to ``/djust/api/`` on default mount.

    The tag emits ``<meta name="djust-api-prefix" content="/djust/api/">``
    when the API is mounted at its canonical location.
    """
    html = _render_tag()
    assert 'name="djust-api-prefix"' in html
    assert 'content="/djust/api/"' in html


# ---------------------------------------------------------------------------
# 2. FORCE_SCRIPT_NAME
# ---------------------------------------------------------------------------


@override_settings(
    ROOT_URLCONF="tests.api_test_urls_default",
    FORCE_SCRIPT_NAME="/mysite",
)
def test_tag_emits_meta_under_force_script_name():
    """Doc claim: ``reverse()`` honors ``FORCE_SCRIPT_NAME``.

    With ``FORCE_SCRIPT_NAME=/mysite`` the meta tag's content must be
    ``/mysite/djust/api/``. In production Django's ``BaseHandler`` calls
    :func:`set_script_prefix` with the forced value at the start of
    every request; we mirror that here so the tag sees the same state
    it would under live traffic.
    """
    # Production Django calls set_script_prefix() from BaseHandler
    # based on FORCE_SCRIPT_NAME — mirror that manually in the test
    # since RequestFactory does not invoke the middleware chain.
    set_script_prefix("/mysite/")
    clear_url_caches()

    html = _render_tag()
    assert 'name="djust-api-prefix"' in html
    assert 'content="/mysite/djust/api/"' in html


# ---------------------------------------------------------------------------
# 3. Custom prefix via api_patterns(prefix=...)
# ---------------------------------------------------------------------------


@override_settings(ROOT_URLCONF="tests.api_test_urls_custom")
def test_tag_emits_meta_when_api_mounted_at_custom_prefix():
    """Doc claim: custom ``api_patterns(prefix='myapi/')`` is honored.

    Mounting the API under ``/myapi/`` → the client must see
    ``<meta ... content="/myapi/">``.
    """
    html = _render_tag()
    assert 'name="djust-api-prefix"' in html
    assert 'content="/myapi/"' in html


# ---------------------------------------------------------------------------
# 4. Unmounted API → NoReverseMatch → empty content
# ---------------------------------------------------------------------------


@override_settings(ROOT_URLCONF="tests.api_test_urls_unmounted")
def test_tag_when_api_app_not_mounted():
    """Doc claim: API not mounted → meta tag emitted with empty content.

    The client-side fallback (``'/djust/api/'``) kicks in when
    ``content=""``. We emit the tag (not nothing) so debugging is easier:
    a developer inspecting the rendered HTML can immediately see the
    prefix resolution failed.
    """
    html = _render_tag()
    assert 'name="djust-api-prefix"' in html
    assert 'content=""' in html


# ---------------------------------------------------------------------------
# 5. Output is HTML-escaped (defense in depth)
# ---------------------------------------------------------------------------


@override_settings(
    ROOT_URLCONF="tests.api_test_urls_default",
    FORCE_SCRIPT_NAME='/my"site<script>',
)
def test_tag_output_is_escaped():
    """Doc claim: output is HTML-escaped.

    Even though ``FORCE_SCRIPT_NAME`` is developer-controlled, the tag
    uses :func:`django.utils.html.escape` on the resolved prefix so a
    mis-configured deployment cannot introduce XSS. Tests that the
    literal ``<script>`` sequence does not appear in the emitted HTML.
    """
    html = _render_tag()
    # No raw <script> tag should appear in the emitted markup.
    assert "<script>" not in html
    # Double-quote in the value must be escaped so it can't close the
    # content="..." attribute.
    assert 'content="/my"site' not in html
