"""Tests for the request-scoped theming helpers.

These are the functions the theming documentation tells people to import
(``from djust.theming import set_active_pack, set_active_mode``). Before they
existed, every one of those import lines raised ImportError.

The behaviours pinned here are the ones the docs promise: state is
per-request, an unknown pack is refused without half-applying, and the
resolved mode — not the raw setting — is what callers see.
"""

from __future__ import annotations

from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust.theming import (
    ThemePack,
    get_active_mode,
    get_active_pack,
    get_theme_css_url,
    get_theme_manager,
    reset_to_defaults,
    set_active_mode,
    set_active_pack,
)


def _request():
    """A request carrying a real session, which is where theme state lives."""
    request = RequestFactory().get("/")
    request.session = SessionStore()
    return request


# ── exports ──────────────────────────────────────────────────────────────────


def test_documented_theming_types_are_importable():
    """`from djust.theming import ThemePack` is the documented import.

    The types lived in `_types` (a private module) and were never re-exported,
    so the documented model was unusable even though `register_theme_pack`
    accepts a ThemePack and `_types.__all__` advertises it.
    """
    from djust.theming import (  # noqa: F401
        AnimationStyle,
        DesignSystem,
        IconStyle,
        IllustrationStyle,
        InteractionStyle,
        LayoutStyle,
        PatternStyle,
        SurfaceStyle,
        SurfaceTreatment,
        TypographyStyle,
    )

    assert ThemePack is not None


# ── mode ─────────────────────────────────────────────────────────────────────


def test_mode_round_trips():
    request = _request()
    assert set_active_mode(request, "dark") is True
    assert get_active_mode(request) == "dark"


def test_mode_rejects_an_unknown_value():
    request = _request()
    assert set_active_mode(request, "chartreuse") is False


def test_get_active_mode_returns_the_resolved_mode():
    """A 'system' user is actually seeing light or dark, and callers overriding
    a mode need to know which."""
    request = _request()
    set_active_mode(request, "system")
    assert get_active_mode(request) in ("light", "dark")


def test_mode_is_per_request():
    a, b = _request(), _request()
    set_active_mode(a, "dark")
    assert get_active_mode(b) != "dark", "one request's mode leaked into another"


# ── pack ─────────────────────────────────────────────────────────────────────


def test_unknown_pack_is_refused_without_side_effects():
    request = _request()
    before = get_active_pack(request)
    assert set_active_pack(request, "no-such-pack") is False
    assert get_active_pack(request) == before


def test_clearing_the_pack_is_allowed():
    request = _request()
    assert set_active_pack(request, None) is True
    assert get_active_pack(request) is None


def test_get_active_pack_is_none_when_no_pack_is_selected():
    """The normal state when a project uses a design system + preset."""
    assert get_active_pack(_request()) is None


# ── reset ────────────────────────────────────────────────────────────────────


def test_reset_clears_the_stored_choice():
    request = _request()
    set_active_mode(request, "dark")
    assert get_active_mode(request) == "dark"

    reset_to_defaults(request)
    assert get_active_mode(request) != "dark"


def test_reset_removes_the_session_key_rather_than_writing_defaults():
    """So a later change to configured defaults still takes effect for this
    user instead of being pinned by an earlier reset."""
    request = _request()
    set_active_mode(request, "dark")
    manager = get_theme_manager(request)

    reset_to_defaults(request)
    assert manager.config["session_key"] not in request.session


# ── css url ──────────────────────────────────────────────────────────────────


def test_css_url_is_a_string_and_changes_with_state():
    request = _request()
    light = get_theme_css_url(request)
    set_active_mode(request, "dark")
    dark = get_theme_css_url(request)

    assert isinstance(light, str) and light
    assert light != dark, "the cache key must change when the theme does"


def test_css_url_does_not_raise_when_theming_urls_are_unmounted():
    """A helper whose whole job is to return a string should not raise."""
    request = _request()
    assert isinstance(get_theme_css_url(request), str)
