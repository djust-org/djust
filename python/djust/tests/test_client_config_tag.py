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
from django.test import RequestFactory, override_settings
from django.urls import clear_url_caches, set_script_prefix

from djust.routing import _reset_route_map_cache


@pytest.fixture(autouse=True)
def _reset_route_map():
    """Clear the URLconf-derived route-map cache around each test (#1733)."""
    _reset_route_map_cache()
    yield
    _reset_route_map_cache()


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


def _render_tag(request=None) -> str:
    """Render ``{% djust_client_config %}``.

    ``request`` is optional; when provided it flows into the tag's context so
    the route-map ``<script>`` can pick up ``request.csp_nonce`` (#1733).
    """
    tpl = Template("{% load live_tags %}{% djust_client_config %}")
    ctx = {"request": request} if request is not None else {}
    return tpl.render(Context(ctx))


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
# 5b. SSE prefix (#992) — mirrors the API tests above
# ---------------------------------------------------------------------------


@override_settings(ROOT_URLCONF="tests.api_test_urls_default")
def test_tag_emits_sse_meta_with_default_prefix():
    """Doc claim (#992): SSE prefix resolves to ``/djust/`` on default mount."""
    html = _render_tag()
    assert 'name="djust-sse-prefix"' in html
    assert 'content="/djust/"' in html


@override_settings(
    ROOT_URLCONF="tests.api_test_urls_default",
    FORCE_SCRIPT_NAME="/mysite",
)
def test_tag_emits_sse_meta_under_force_script_name():
    """Doc claim (#992): FORCE_SCRIPT_NAME also applies to the SSE prefix.

    ``reverse('djust-sse-stream', ...)`` honors FORCE_SCRIPT_NAME the
    same way ``djust-api-call`` does.
    """
    from django.urls import set_script_prefix

    set_script_prefix("/mysite/")
    try:
        html = _render_tag()
    finally:
        set_script_prefix("/")
    assert 'name="djust-sse-prefix"' in html
    assert 'content="/mysite/djust/"' in html


@override_settings(ROOT_URLCONF="tests.api_test_urls_unmounted")
def test_tag_sse_meta_when_not_mounted():
    """Doc claim (#992): SSE not mounted → empty content; client falls back."""
    html = _render_tag()
    assert 'name="djust-sse-prefix"' in html
    assert 'content=""' in html


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


@override_settings(ROOT_URLCONF="tests.route_map_test_urls")
def test_django_and_rust_engines_emit_identical_route_map_with_nonce():
    """Dual-engine parity for the route-map <script> AND the CSP nonce (#1733).

    The other parity case (above) uses no-LiveView URLconfs and an empty
    Rust-handler context, so it never compares the route-map <script> OR the
    nonce across engines — the exact variant this dual-registration exists to
    protect. Per the v1.0.0rc4 retro finding #1 (a coverage suite must
    enumerate every variant of the surface it covers), this case exercises:

    * a URLconf WITH LiveView routes (so the route-map <script> is emitted), and
    * a request carrying a ``csp_nonce`` (so the <script nonce="..."> attribute
      is emitted and must match across engines),

    asserting the Django-engine ``{% djust_client_config %}`` output and the
    Rust-engine ``ClientConfigTagHandler`` output are BYTE-IDENTICAL, including
    the ``<script nonce="...">window.djust._routeMap=...</script>``.
    """
    from djust.template_tags.client_config import ClientConfigTagHandler

    request = RequestFactory().get("/")
    request.csp_nonce = "parity-nonce-xyz"

    clear_url_caches()
    _reset_route_map_cache()

    # Django-engine render: request flows in via the template context
    # (takes_context=True → context.get("request")).
    django_output = _render_tag(request=request)

    # Rust-engine render: request flows in via the context dict the Rust
    # CustomTag callback passes to TagHandler.render(args, context).
    rust_handler = ClientConfigTagHandler()
    rust_output = rust_handler.render([], {"request": request})

    # The variant under test must actually be present in BOTH outputs, or the
    # parity assertion below would be vacuously true on an empty-script case.
    assert "window.djust._routeMap" in django_output
    assert 'nonce="parity-nonce-xyz"' in django_output

    assert django_output == rust_output, (
        "Django and Rust engines emitted different output for the route-map "
        "<script> + nonce variant of {% djust_client_config %} — the "
        "dual-registration invariant is broken for the #1733 surface. "
        f"Django: {django_output!r} | Rust: {rust_output!r}"
    )


# ---------------------------------------------------------------------------
# 7. Auto-emitted route map (#1733, ADR-021 Stage 1)
# ---------------------------------------------------------------------------
#
# Doc claim (navigation.md / ADR-021): the route map is auto-derived from the
# URLconf and auto-emitted via {% djust_client_config %}, so dj-navigate works
# with zero wiring. These tests encode that claim.


@override_settings(ROOT_URLCONF="tests.route_map_test_urls")
def test_client_config_emits_route_map_script():
    """Doc claim: {% djust_client_config %} auto-emits window.djust._routeMap.

    The URLconf has LiveView routes, so the tag must append a route-map
    <script> populating window.djust._routeMap with the derived entries.
    """
    html = _render_tag()
    assert "window.djust._routeMap" in html
    assert '"/dashboard/": "tests.route_map_test_urls.DashboardView"' in html
    # Parameterised route is emitted in the JS-friendly form.
    assert '"/items/:id/": "tests.route_map_test_urls.ItemDetailView"' in html
    # The api/sse meta tags are preserved (emitted alongside the route map).
    assert 'name="djust-api-prefix"' in html
    assert 'name="djust-sse-prefix"' in html


@override_settings(ROOT_URLCONF="tests.route_map_test_urls")
def test_client_config_route_map_with_nonce():
    """Doc claim: the route-map <script> carries a CSP nonce when available."""
    request = RequestFactory().get("/")
    request.csp_nonce = "abc123nonce"
    html = _render_tag(request=request)
    assert 'nonce="abc123nonce"' in html
    assert "window.djust._routeMap" in html


@override_settings(ROOT_URLCONF="tests.route_map_test_urls")
def test_client_config_route_map_without_nonce():
    """Without a nonce the route-map <script> is emitted nonce-free."""
    html = _render_tag()
    assert "window.djust._routeMap" in html
    # The route-map script must not carry a stray empty nonce attribute.
    assert 'nonce=""' not in html


@override_settings(ROOT_URLCONF="tests.api_test_urls_default")
def test_client_config_empty_safe_when_no_liveviews():
    """Doc claim: empty-safe — no route-map <script> when no LiveView routes.

    api_test_urls_default has no LiveView routes, so the derived map is empty
    and NO route-map <script> is appended. The api/sse meta tags still emit.
    """
    html = _render_tag()
    assert "window.djust._routeMap" not in html
    # Meta tags unchanged.
    assert 'name="djust-api-prefix"' in html
    assert 'name="djust-sse-prefix"' in html


@override_settings(ROOT_URLCONF="tests.route_map_test_urls")
def test_client_config_route_map_json_escaped():
    """Security (#1078): route JSON is json.dumps-escaped (no raw <)."""
    html = _render_tag()
    # json.dumps escapes < > & ; the developer-defined module paths contain no
    # HTML-special chars, but assert the script body has no unescaped angle
    # bracket from the route data (the only < should be from the surrounding
    # markup, never inside the JSON object literal).
    # Extract the route-map script body and confirm it parses as JSON-safe.
    assert "window.djust._routeMap={" in html
    assert "</script>" in html


# ---------------------------------------------------------------------------
# auto_navigate flag emit (#1734, ADR-021 Stage 2)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_djust_config():
    """Restore the config singleton after each test.

    ``LIVEVIEW_CONFIG`` is loaded once into the ``config`` singleton at import;
    tests that ``override_settings(LIVEVIEW_CONFIG=...)`` call ``config.reset()``
    inside the override, so this re-reads the restored settings afterwards to
    avoid cross-test leakage of ``auto_navigate``.
    """
    yield
    from djust.config import config

    config.reset()


def test_auto_navigate_meta_emitted_by_default():
    """Default ON as of v1.1 (ADR-021 Stage 3): the auto-navigate <meta> is
    emitted with no configuration — native dj-navigate is the canonical default."""
    from djust.config import config

    config.reset()
    html = _render_tag()
    assert '<meta name="djust-auto-navigate" content="1">' in html


@override_settings(LIVEVIEW_CONFIG={"auto_navigate": False})
def test_auto_navigate_meta_absent_when_opted_out():
    """Opt OUT with auto_navigate=False: no <meta>, no client interception."""
    from djust.config import config

    config.reset()  # pick up the overridden LIVEVIEW_CONFIG
    html = _render_tag()
    assert "djust-auto-navigate" not in html


@override_settings(LIVEVIEW_CONFIG={"auto_navigate": True})
def test_auto_navigate_meta_emitted_when_enabled():
    """LIVEVIEW_CONFIG['auto_navigate']=True (the default) emits the <meta> flag."""
    from djust.config import config

    config.reset()  # pick up the overridden LIVEVIEW_CONFIG
    html = _render_tag()
    assert '<meta name="djust-auto-navigate" content="1">' in html


@override_settings(LIVEVIEW_CONFIG={"auto_navigate": True})
def test_auto_navigate_meta_engines_identical():
    """Both template engines emit the flag (dual-registration invariant)."""
    from djust.config import config
    from djust.template_tags.client_config import ClientConfigTagHandler

    config.reset()
    django_html = _render_tag()
    rust_html = str(ClientConfigTagHandler().render([], {}))
    assert "djust-auto-navigate" in django_html
    assert django_html == rust_html
