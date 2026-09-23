"""Theme state read from cookies only carries registered / well-formed values,
and ``{% theme_css_link %}`` emits an encoded, attribute-safe URL."""

from __future__ import annotations

import pytest
from django.template import Context, Template
from django.test import RequestFactory

from djust.theming.manager import ThemeManager

ODD_PACK = 'x"><b id=odd>'


def _request(**cookies: str):
    req = RequestFactory().get("/")
    req.session = {}
    for name, value in cookies.items():
        req.COOKIES[name] = value
    return req


def _render_link(req) -> str:
    tmpl = Template('{% load theme_tags %}<link rel="stylesheet" href="{% theme_css_link %}">')
    return tmpl.render(Context({"request": req}))


@pytest.mark.django_db
def test_unregistered_pack_cookie_resolves_to_no_pack():
    state = ThemeManager(request=_request(djust_theme_pack=ODD_PACK)).get_state()
    assert state.pack is None


@pytest.mark.django_db
def test_unregistered_pack_cookie_falls_back_to_session_pack():
    req = _request(djust_theme_pack="not_a_pack")
    manager = ThemeManager(request=req)
    req.session = {manager._session_key: {"pack": "djust"}}
    assert manager.get_state().pack == "djust"


@pytest.mark.django_db
def test_registered_pack_cookie_is_kept():
    state = ThemeManager(request=_request(djust_theme_pack="djust")).get_state()
    assert state.pack == "djust"


@pytest.mark.django_db
@pytest.mark.parametrize("value", ['side"bar', "<b>", "../x", "a b", "x" * 65])
def test_malformed_layout_cookie_resolves_to_base_layout(value):
    state = ThemeManager(request=_request(djust_theme_layout=value)).get_state()
    assert state.layout == ""


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["sidebar", "sidebar-topbar", "my_layout2"])
def test_wellformed_layout_cookie_is_kept(value):
    state = ThemeManager(request=_request(djust_theme_layout=value)).get_state()
    assert state.layout == value


@pytest.mark.django_db
def test_theme_css_link_ignores_unregistered_pack_cookie():
    out = _render_link(_request(djust_theme_pack=ODD_PACK))
    assert "<b" not in out
    assert "p=" not in out
    assert out.count('"') == 4


@pytest.mark.django_db
def test_theme_css_link_includes_registered_pack():
    out = _render_link(_request(djust_theme_pack="djust"))
    assert 'href="/_theming/theme.css?p=djust&amp;m=' in out


@pytest.mark.django_db
def test_theme_css_link_encodes_and_escapes_query_values(monkeypatch):
    from djust.theming import manager as manager_mod
    from djust.theming.templatetags import theme_tags

    odd_state = manager_mod.ThemeState(
        theme="material",
        preset="default",
        mode="light",
        resolved_mode="light",
        pack=ODD_PACK,
    )

    class _Mgr:
        def get_state(self):
            return odd_state

    monkeypatch.setattr(theme_tags, "get_theme_manager", lambda request: _Mgr())
    out = str(theme_tags.theme_css_link({"request": None}))
    assert '"' not in out and "<" not in out and ">" not in out
    assert "p=x%22%3E%3Cb+id%3Dodd%3E" in out
    assert "&amp;m=light" in out


@pytest.mark.django_db
def test_rust_tag_handler_theme_css_link_ignores_unregistered_pack_cookie():
    from djust.theming.rust_handlers import ThemeTagHandler

    out = ThemeTagHandler("theme_css_link").render(
        [], {"request": _request(djust_theme_pack=ODD_PACK)}
    )
    assert "<b" not in out and '"' not in out
    assert "p=" not in out


@pytest.mark.django_db
def test_theme_css_view_etag_uses_resolved_pack():
    from djust.theming.views import _css_etag

    assert _css_etag(_request(djust_theme_pack=ODD_PACK)).endswith("-None")
    assert _css_etag(_request(djust_theme_pack="djust")).endswith("-djust")
