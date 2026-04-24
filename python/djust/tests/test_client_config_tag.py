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
* "Django and Rust template engines emit byte-identical output" →
  :func:`test_django_and_rust_engines_emit_identical_output` (PR #993
  Stage 11 🟡 — locks the dual-registration invariant from Stage 5b).
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


# ---------------------------------------------------------------------------
# 6. Dual-engine parity (PR #993 Stage 11 🟡)
# ---------------------------------------------------------------------------
#
# The ``{% djust_client_config %}`` tag is registered with BOTH the Django
# template engine (``djust.templatetags.live_tags``) and the Rust template
# engine (``djust.template_tags.client_config.ClientConfigTagHandler``).
# Both call the shared ``_resolve_api_prefix()`` helper, so their outputs
# must be byte-identical. This test locks that invariant so a future edit
# to one path that isn't mirrored to the other is caught immediately.


_PARITY_CASES = [
    pytest.param(
        {"ROOT_URLCONF": "tests.api_test_urls_default"},
        None,
        id="default-prefix",
    ),
    pytest.param(
        {
            "ROOT_URLCONF": "tests.api_test_urls_default",
            "FORCE_SCRIPT_NAME": "/mysite",
        },
        "/mysite/",
        id="force-script-name",
    ),
    pytest.param(
        {"ROOT_URLCONF": "tests.api_test_urls_custom"},
        None,
        id="custom-api-prefix",
    ),
]


@pytest.mark.parametrize("settings_overrides,script_prefix", _PARITY_CASES)
def test_django_and_rust_engines_emit_identical_output(settings_overrides, script_prefix):
    """Dual-engine parity: Django-engine render == Rust-engine render.

    Both engines register the same tag name and delegate to the shared
    ``_resolve_api_prefix()`` helper. This test renders through each
    engine's code path and asserts byte-equality so drift between the
    two registrations is caught by CI.

    Parameterized over three URL-config scenarios to cover the
    resolution branches: default mount, ``FORCE_SCRIPT_NAME``, and
    custom ``api_patterns(prefix=...)``.
    """
    from djust.template_tags.client_config import ClientConfigTagHandler

    with override_settings(**settings_overrides):
        # Production Django's BaseHandler sets the script prefix from
        # FORCE_SCRIPT_NAME at the start of every request; mirror that
        # for the FORCE_SCRIPT_NAME case since RequestFactory does not
        # invoke the middleware chain.
        if script_prefix is not None:
            set_script_prefix(script_prefix)
        clear_url_caches()

        # Django-engine render: goes through live_tags.djust_client_config.
        django_output = _render_tag()

        # Rust-engine render: goes through ClientConfigTagHandler.render(),
        # which is what the Rust template engine invokes via the
        # CustomTag callback. TagHandler.render(args, context) is the
        # documented interface (see template_tags/__init__.py).
        rust_handler = ClientConfigTagHandler()
        rust_output = rust_handler.render([], {})

        # Byte-equality: neither trailing whitespace nor ordering of
        # attributes should differ. Both paths use format_html / escape
        # on the same resolved prefix and hard-code the same attribute
        # order, so a failure here means someone edited one path
        # without mirroring to the other.
        assert django_output == rust_output, (
            "Django and Rust template engines emitted different output for "
            "{% djust_client_config %} — the dual-registration invariant "
            "from PR #993 Stage 5b is broken. "
            f"Django: {django_output!r} | Rust: {rust_output!r}"
        )
