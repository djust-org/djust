"""Regression tests for v0.8.2 drain — Group T (theming polish).

Covers:
- #1011 — `.card` / `.alert` overflow:hidden in components.css
- #1012 — `{% theme_css_link %}` cache-busting tag
- #1013 — `enable_client_override` flag in `LIVEVIEW_CONFIG['theme']`
- #1009 — `prose.css` shipped for `@tailwindcss/typography` ↔ pack bridge
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.template import Context, Template
from django.test import RequestFactory


# Path to the package's static dir so we can inspect shipped CSS files
THEMING_PKG = Path(__file__).resolve().parent.parent / "theming"
STATIC_CSS = THEMING_PKG / "static" / "djust_theming" / "css"


# ---------------------------------------------------------------------------
# #1011 — .card / .alert overflow:hidden
# ---------------------------------------------------------------------------


def test_components_css_card_has_overflow_hidden():
    """The `.card` selector must set `overflow: hidden` so child borders
    don't poke through the rounded corners (#1011)."""
    text = (STATIC_CSS / "components.css").read_text()
    # Find the .card { ... } block (the canonical one near "Card" comment).
    start = text.find("\n.card {\n")
    assert start > 0, "could not find .card selector"
    end = text.find("\n}\n", start)
    block = text[start:end]
    assert "overflow: hidden" in block, f".card block missing overflow: hidden\n---\n{block}\n---"


def test_components_css_alert_has_overflow_hidden():
    """The `.alert` selector must set `overflow: hidden` (#1011)."""
    text = (STATIC_CSS / "components.css").read_text()
    start = text.find("\n.alert {\n")
    assert start > 0, "could not find .alert selector"
    end = text.find("\n}\n", start)
    block = text[start:end]
    assert "overflow: hidden" in block, f".alert block missing overflow: hidden\n---\n{block}\n---"


# ---------------------------------------------------------------------------
# #1012 — {% theme_css_link %} cache-busting tag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_theme_css_link_emits_url_with_pack_and_mode_params():
    """The {% theme_css_link %} tag emits a URL with `?p=<pack>&m=<mode>`
    so different pack/mode = different URL → guaranteed fresh fetch
    even when Chrome ignores Vary: Cookie (#1012)."""
    rf = RequestFactory()
    req = rf.get("/")
    # No cookies set → falls back to defaults from settings
    tmpl = Template("{% load theme_tags %}{% theme_css_link %}")
    rendered = tmpl.render(Context({"request": req})).strip()
    assert rendered.startswith("/_theming/theme.css") or "/theme.css" in rendered, (
        f"unexpected URL: {rendered!r}"
    )
    # Either pack or mode (or both) should appear as a query param
    assert "?" in rendered, (
        f"theme_css_link should emit cache-busting query string; got {rendered!r}"
    )
    # At least one of the canonical keys
    assert "p=" in rendered or "m=" in rendered, (
        f"theme_css_link should include p= or m=; got {rendered!r}"
    )


@pytest.mark.django_db
def test_theme_css_link_url_changes_with_pack_cookie():
    """Different pack cookie → different URL. This is the actual cache-bust
    contract — same URL for stale cache → guaranteed fresh fetch on switch."""
    rf = RequestFactory()
    tmpl = Template("{% load theme_tags %}{% theme_css_link %}")

    # Render with one pack cookie
    req1 = rf.get("/")
    req1.COOKIES["djust_theme_pack"] = "djust"
    url1 = tmpl.render(Context({"request": req1})).strip()

    # Render with a different pack cookie
    req2 = rf.get("/")
    req2.COOKIES["djust_theme_pack"] = "nyc_core"
    url2 = tmpl.render(Context({"request": req2})).strip()

    assert url1 != url2, (
        f"theme_css_link must produce different URLs for different packs; got {url1!r} and {url2!r}"
    )


# ---------------------------------------------------------------------------
# #1013 — enable_client_override flag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_state_reads_cookies_when_enable_client_override_true():
    """Default behavior (enable_client_override=True): cookies override
    config. Existing back-compat (#1013)."""
    from djust.theming.manager import ThemeManager

    rf = RequestFactory()
    req = rf.get("/")
    req.COOKIES["djust_theme_pack"] = "nyc_core"
    req.session = {}

    mgr = ThemeManager(request=req)
    # Pin config defaults relevant to this test (others come from settings)
    mgr.config = dict(mgr.config, pack="djust", enable_client_override=True)
    state = mgr.get_state()
    # Cookie wins over config (back-compat default)
    assert state.pack == "nyc_core", (
        f"expected cookie pack to win when enable_client_override=True; got pack={state.pack!r}"
    )


@pytest.mark.django_db
def test_get_state_ignores_cookies_when_enable_client_override_false():
    """When `enable_client_override=False`, config wins — sites without a
    user-facing switcher won't get hijacked by stale localhost cookies (#1013)."""
    from djust.theming.manager import ThemeManager

    rf = RequestFactory()
    req = rf.get("/")
    req.COOKIES["djust_theme_pack"] = "nyc_core"
    req.session = {}

    mgr = ThemeManager(request=req)
    mgr.config = dict(mgr.config, pack="djust", enable_client_override=False)
    state = mgr.get_state()
    # Config wins over cookie
    assert state.pack == "djust", (
        f"expected config pack to win when enable_client_override=False; got pack={state.pack!r}"
    )


# ---------------------------------------------------------------------------
# #1009 — prose.css shipped
# ---------------------------------------------------------------------------


def test_prose_css_file_shipped():
    """`prose.css` must exist in the package's static dir for sites that
    `@tailwindcss/typography` to pick up via the `prose-djust` opt-in
    class (#1009)."""
    prose_path = STATIC_CSS / "prose.css"
    assert prose_path.exists(), f"prose.css missing at {prose_path}"
    text = prose_path.read_text()
    assert ".prose.prose-djust" in text, (
        "prose.css must scope under .prose.prose-djust opt-in class"
    )
    # Sanity: pack-aware token references
    assert "--color-brand-rust" in text, "prose.css should reference pack tokens"
    # Sanity: dark-mode invert variables present
    assert "--tw-prose-invert-body" in text, (
        "prose.css must include the typography plugin's invert variables for dark mode"
    )
