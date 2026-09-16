"""Theming gallery and theme-mode defaults.

Four defects, all of the same shape: a value the PROJECT configured was
ignored in favour of a hardcoded default, and in two cases the hardcoded
default existed in more than one place so fixing one copy left the other
still wrong.

1. The gallery opened on preset ``"default"`` regardless of the configured
   preset, so a themed site saw its own component gallery in a palette that
   was not its own.
2. ``theme_head.html``'s anti-FOUC script hardcoded ``'system'`` as its mode
   fallback, so ``default_mode: "dark"`` came up light on a light-preferring
   machine.
3. ``theme.js`` hardcoded the same ``'system'`` fallback. It loads ``defer``,
   so it ran *after* the inline script and overwrote the correct value.
4. Every gallery topbar hardcoded ``href="/components/"`` — a path belonging
   to whichever project hosted the gallery — which 404s elsewhere.
"""

import re
from pathlib import Path

import pytest
from django.test import RequestFactory, override_settings

GALLERY_TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "theming"
    / "templates"
    / "djust_theming"
    / "gallery"
    / "gallery.html"
)
THEME_HEAD_TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "theming"
    / "templates"
    / "djust_theming"
    / "theme_head.html"
)
THEME_JS = (
    Path(__file__).resolve().parent.parent
    / "theming"
    / "static"
    / "djust_theming"
    / "js"
    / "theme.js"
)


@pytest.fixture
def rf():
    return RequestFactory()


# ---------------------------------------------------------------------------
# 1. The configured default mode reaches the client
# ---------------------------------------------------------------------------


@override_settings(LIVEVIEW_CONFIG={"theme": {"default_mode": "dark"}})
def test_theme_head_context_publishes_configured_default_mode(rf):
    """`default_mode` must reach the anti-FOUC script, not just the config."""
    from djust.theming.templatetags.theme_tags import build_theme_head_context

    ctx = build_theme_head_context(rf.get("/"), include_js=False)

    # JSON-encoded, because it is interpolated into a <script> literal.
    assert ctx["resolved_mode_js"] == '"dark"'


@override_settings(LIVEVIEW_CONFIG={"theme": {"default_mode": "light"}})
def test_theme_head_context_tracks_the_configured_mode(rf):
    """The published value must follow the config rather than being constant."""
    from djust.theming.templatetags.theme_tags import build_theme_head_context

    ctx = build_theme_head_context(rf.get("/"), include_js=False)

    assert ctx["resolved_mode_js"] == '"light"'


def test_theme_js_reads_the_published_default_mode():
    """theme.js must consume the shared value, not resolve its own fallback.

    This is the regression that mattered: a hardcoded 'system' here ran after
    the inline anti-FOUC script and overwrote it on every page load.
    """
    source = THEME_JS.read_text(encoding="utf-8")
    mode_body = source[source.index("getMode()") : source.index("getMode()") + 900]

    assert "__djust_theme_default_mode" in mode_body, (
        "theme.js getMode() no longer reads the mode published by "
        "theme_head.html — the two implementations can drift apart again"
    )


def test_theme_head_template_falls_back_when_mode_is_absent():
    """A hand-built context must not emit invalid JavaScript.

    `theme_head.html` is rendered by callers that pass their own context dict
    (see test_theme_head_components_link_1624). Django renders a missing
    variable as empty, so an unguarded `|| {{ resolved_mode_js }}` would emit
    `storedMode || ;` — a syntax error that kills the whole script and leaves
    the page on its :root defaults.
    """
    from django.template.loader import render_to_string

    html = render_to_string(
        "djust_theming/theme_head.html",
        {
            # Deliberately omits `resolved_mode_js`.
            "loading_class": False,
            "css_block": "",
            "deferred_css_block": "",
            "component_css_block": "",
            "include_component_link": False,
            "include_components_app_link": False,
            "include_js": False,
            "direction": "ltr",
            "cookie_prefix_js": '""',
        },
    )

    assert "storedMode || ;" not in html
    assert re.search(r'__djust_theme_default_mode = "system"', html)
    assert "|| 'system';" in html


# ---------------------------------------------------------------------------
# 2. The gallery opens on the project's preset
# ---------------------------------------------------------------------------


@override_settings(LIVEVIEW_CONFIG={"theme": {"preset": "dracula"}})
def test_gallery_opens_on_the_configured_preset(rf):
    from djust.theming.gallery.views import _initial_preset

    assert _initial_preset(rf.get("/theme/gallery/")) == "dracula"


@override_settings(LIVEVIEW_CONFIG={"theme": {"preset": "dracula"}})
def test_query_param_beats_the_configured_preset(rf):
    """An explicit ?preset= is the visitor asking; it wins."""
    from djust.theming.gallery.views import _initial_preset

    assert _initial_preset(rf.get("/theme/gallery/?preset=forest")) == "forest"


@override_settings(LIVEVIEW_CONFIG={"theme": {"preset": "dracula"}})
def test_unknown_query_param_falls_back_to_the_configured_preset(rf):
    """A junk ?preset= must not drop the gallery to an unthemed default."""
    from djust.theming.gallery.views import _initial_preset

    assert _initial_preset(rf.get("/theme/gallery/?preset=not_a_preset")) == "dracula"


@override_settings(LIVEVIEW_CONFIG={})
def test_gallery_falls_back_to_default_when_nothing_is_configured(rf):
    from djust.theming.gallery.views import _initial_preset

    assert _initial_preset(rf.get("/theme/gallery/")) == "default"


# ---------------------------------------------------------------------------
# 3. Structure — guards against the layout regressions returning
# ---------------------------------------------------------------------------


def test_gallery_styles_the_section_subheading():
    """`.gallery__section h2` is pulled down to 1.25rem; h3 must be styled too.

    The theme's base rules set h3 to var(--text-2xl) (~1.95rem on the default
    scale). With a rule for h2 and none for h3, "Variants" rendered half again
    LARGER than the "Button" section it belongs to.
    """
    css = GALLERY_TEMPLATE.read_text(encoding="utf-8")

    assert re.search(r"\.gallery__section h3\s*\{", css), (
        "the gallery lost its .gallery__section h3 rule — the section "
        "sub-heading will out-size its own parent again"
    )


def test_gallery_has_no_unstyled_trigger_buttons():
    """Every bare <button> in the gallery must carry the trigger class.

    Unclassed buttons render as browser defaults next to fully themed
    components.
    """
    html = GALLERY_TEMPLATE.read_text(encoding="utf-8")

    # Scan markup only. The file's inline <style> block discusses these
    # buttons in its comments, and a `<button>` written there is prose.
    markup = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    markup = re.sub(r"\{#.*?#\}", "", markup, flags=re.DOTALL)

    bare = [
        m.group(0)
        for m in re.finditer(r"<button(\s[^>]*)?>", markup)
        if "gallery-trigger" not in (m.group(1) or "")
    ]
    assert bare == [], f"unstyled buttons in the gallery: {bare}"


# ---------------------------------------------------------------------------
# 4. The components-gallery link resolves or is omitted — never dead
# ---------------------------------------------------------------------------


def test_components_gallery_url_is_empty_when_unroutable(monkeypatch):
    """With the optional app absent, the topbar must omit the link.

    Returning "" rather than raising is what lets the template drop the link
    instead of shipping a 404.
    """
    from djust.theming import context_processors

    def _boom(*args, **kwargs):
        raise Exception("no such route")

    monkeypatch.setattr("django.urls.reverse", _boom)

    assert context_processors._components_gallery_url() == ""


def test_gallery_templates_do_not_hardcode_host_paths():
    """The topbars must not link to paths the framework does not own.

    `/components/` was baked into all four gallery templates; it belongs to
    whichever project hosted the gallery and 404s everywhere else.
    """
    gallery_dir = GALLERY_TEMPLATE.parent
    offenders = []

    for template in sorted(gallery_dir.glob("*.html")):
        text = template.read_text(encoding="utf-8")
        if 'href="/components/"' in text:
            offenders.append(template.name)

    assert offenders == [], (
        f"hardcoded /components/ link returned in {offenders}; link via "
        "components_gallery_url so it resolves or is omitted"
    )
