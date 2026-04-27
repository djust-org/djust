"""Regression tests for #1158 — per-project cookie namespace for theming.

Background
----------
On localhost, cookies are scoped by domain only (not port). Multiple djust
projects on `localhost:80xx` share the `djust_theme*` cookie jar and overwrite
each other. PR #1013 added `enable_client_override: False` as a workaround,
but sites with a user-facing theme switcher need cookie writes ON, so the
bleed persists for them.

Fix
---
`LIVEVIEW_CONFIG['theme']['cookie_namespace']: '<ns>'` causes the four
theming cookies to be read/written as `<ns>_djust_theme`,
`<ns>_djust_theme_preset`, `<ns>_djust_theme_pack`, `<ns>_djust_theme_layout`.

Read order: namespaced first, fall back to unprefixed for one-time migration.
Write order: only namespaced (handled by `theme.js` reading
`window.__djust_theme_cookie_prefix` injected by `theme_head.html`).

These tests cover the read side (Python) and the write-side wiring (the
template emits the right `__djust_theme_cookie_prefix` value, and the JS
file's cookie-key constants resolve from that global).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template import Context, Template
from django.test import RequestFactory


# ---------------------------------------------------------------------------
# Read side — ThemeManager.get_state()
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_namespaced_cookie_wins_when_namespace_set():
    """When `cookie_namespace` is set, the namespaced cookie is read."""
    from djust.theming.manager import ThemeManager

    rf = RequestFactory()
    req = rf.get("/")
    req.COOKIES["djust_org_djust_theme_pack"] = "nyc_core"
    # Stale unprefixed cookie from another project on the same domain — must NOT win.
    req.COOKIES["djust_theme_pack"] = "djust"
    req.session = {}

    mgr = ThemeManager(request=req)
    mgr.config = dict(mgr.config, cookie_namespace="djust_org", enable_client_override=True)
    state = mgr.get_state()
    assert state.pack == "nyc_core", (
        f"namespaced cookie must win over stale unprefixed cookie; got pack={state.pack!r}"
    )


@pytest.mark.django_db
def test_namespace_set_falls_back_to_unprefixed_when_namespaced_missing():
    """Migration window: when the namespaced cookie hasn't been written yet
    (first request after upgrade), fall back to the unprefixed cookie so
    users don't lose their existing theme on upgrade."""
    from djust.theming.manager import ThemeManager

    rf = RequestFactory()
    req = rf.get("/")
    # Only the legacy unprefixed cookie is present.
    req.COOKIES["djust_theme_pack"] = "nyc_core"
    req.session = {}

    mgr = ThemeManager(request=req)
    mgr.config = dict(mgr.config, cookie_namespace="djust_org", enable_client_override=True)
    state = mgr.get_state()
    assert state.pack == "nyc_core", (
        f"unprefixed fallback must apply when namespaced cookie missing; got pack={state.pack!r}"
    )


@pytest.mark.django_db
def test_no_namespace_reads_unprefixed_default_back_compat():
    """Default behaviour (no namespace configured): read the legacy unprefixed
    cookie. Existing deployments must keep working unchanged."""
    from djust.theming.manager import ThemeManager

    rf = RequestFactory()
    req = rf.get("/")
    req.COOKIES["djust_theme_pack"] = "nyc_core"
    req.session = {}

    mgr = ThemeManager(request=req)
    # cookie_namespace not set in config (default).
    mgr.config = dict(mgr.config, enable_client_override=True)
    mgr.config.pop("cookie_namespace", None)
    state = mgr.get_state()
    assert state.pack == "nyc_core"


@pytest.mark.django_db
def test_two_namespace_isolation():
    """Project A (namespace='a') only sees its own namespaced cookie even when
    project B's namespaced cookie is also present in the cookie jar (which
    happens on a shared localhost domain)."""
    from djust.theming.manager import ThemeManager

    rf = RequestFactory()
    req = rf.get("/")
    # Both projects' cookies present in the shared jar.
    req.COOKIES["a_djust_theme_pack"] = "pack_a"
    req.COOKIES["b_djust_theme_pack"] = "pack_b"
    req.session = {}

    mgr_a = ThemeManager(request=req)
    mgr_a.config = dict(mgr_a.config, cookie_namespace="a", enable_client_override=True)
    state_a = mgr_a.get_state()
    assert state_a.pack == "pack_a", f"namespace 'a' must read 'a_*' cookies; got {state_a.pack!r}"

    # Same request, different ThemeManager configured for namespace 'b'.
    req2 = rf.get("/")
    req2.COOKIES["a_djust_theme_pack"] = "pack_a"
    req2.COOKIES["b_djust_theme_pack"] = "pack_b"
    req2.session = {}
    mgr_b = ThemeManager(request=req2)
    mgr_b.config = dict(mgr_b.config, cookie_namespace="b", enable_client_override=True)
    state_b = mgr_b.get_state()
    assert state_b.pack == "pack_b", f"namespace 'b' must read 'b_*' cookies; got {state_b.pack!r}"


@pytest.mark.django_db
def test_namespace_applies_to_all_four_cookies():
    """All four theming cookies (theme, preset, pack, layout) honour the
    namespace, not just `pack`."""
    from djust.theming.manager import ThemeManager

    rf = RequestFactory()
    req = rf.get("/")
    req.COOKIES["proj_djust_theme"] = "ios"
    req.COOKIES["proj_djust_theme_preset"] = "rose"
    req.COOKIES["proj_djust_theme_pack"] = "nyc_core"
    req.COOKIES["proj_djust_theme_layout"] = "sidebar"
    # Stale unprefixed cookies from another project — must NOT bleed in.
    req.COOKIES["djust_theme"] = "material"
    req.COOKIES["djust_theme_preset"] = "default"
    req.COOKIES["djust_theme_pack"] = "djust"
    req.COOKIES["djust_theme_layout"] = ""
    req.session = {}

    mgr = ThemeManager(request=req)
    mgr.config = dict(mgr.config, cookie_namespace="proj", enable_client_override=True)
    state = mgr.get_state()
    # Pack overrides theme + preset (existing behaviour) — assert pack wins.
    assert state.pack == "nyc_core"
    assert state.layout == "sidebar"


# ---------------------------------------------------------------------------
# Write side — theme_head.html template emits __djust_theme_cookie_prefix
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_theme_head_emits_empty_prefix_when_no_namespace(settings):
    """When no namespace is configured, the inline anti-FOUC script sets
    `window.__djust_theme_cookie_prefix = ""`. That keeps `theme.js` writing
    the legacy unprefixed cookie names — back-compat default."""
    settings.LIVEVIEW_CONFIG = {"theme": {}}

    rf = RequestFactory()
    req = rf.get("/")
    req.COOKIES = {}
    req.session = {}

    rendered = Template("{% load theme_tags %}{% theme_head %}").render(Context({"request": req}))
    assert 'window.__djust_theme_cookie_prefix = ""' in rendered, (
        f"expected empty-prefix global; got snippet:\n{rendered[:600]}"
    )


@pytest.mark.django_db
def test_theme_head_emits_namespaced_prefix_when_configured(settings):
    """When `cookie_namespace='djust_org'`, the inline script emits
    `window.__djust_theme_cookie_prefix = "djust_org_"`. `theme.js` reads
    that global to build prefixed cookie keys before writing."""
    settings.LIVEVIEW_CONFIG = {"theme": {"cookie_namespace": "djust_org"}}

    rf = RequestFactory()
    req = rf.get("/")
    req.COOKIES = {}
    req.session = {}

    rendered = Template("{% load theme_tags %}{% theme_head %}").render(Context({"request": req}))
    assert 'window.__djust_theme_cookie_prefix = "djust_org_"' in rendered, (
        f"expected 'djust_org_' prefix global; got snippet:\n{rendered[:600]}"
    )


# ---------------------------------------------------------------------------
# theme.js: cookie-key constants resolve from window.__djust_theme_cookie_prefix
# ---------------------------------------------------------------------------


def test_theme_js_reads_cookie_prefix_from_window_global():
    """The shipped `theme.js` must read `window.__djust_theme_cookie_prefix`
    when computing cookie keys. Without this, no namespacing reaches the
    write path."""
    theme_js = (
        Path(__file__).resolve().parent.parent.parent
        / "python"
        / "djust"
        / "theming"
        / "static"
        / "djust_theming"
        / "js"
        / "theme.js"
    )
    text = theme_js.read_text()
    # The prefix is read from window.
    assert "window.__djust_theme_cookie_prefix" in text, (
        "theme.js must consult window.__djust_theme_cookie_prefix"
    )
    # Cookie keys are built by concatenation, not as static literals.
    # Match e.g. `COOKIE_KEY_THEME = COOKIE_PREFIX + 'djust_theme'`
    assert re.search(r"COOKIE_KEY_THEME\s*=\s*COOKIE_PREFIX\s*\+\s*['\"]djust_theme['\"]", text), (
        "COOKIE_KEY_THEME must be derived from COOKIE_PREFIX, not a static literal"
    )
    assert re.search(
        r"COOKIE_KEY_PACK\s*=\s*COOKIE_PREFIX\s*\+\s*['\"]djust_theme_pack['\"]", text
    ), "COOKIE_KEY_PACK must be derived from COOKIE_PREFIX"
