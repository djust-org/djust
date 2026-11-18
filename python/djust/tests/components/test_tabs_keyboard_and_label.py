"""``{% tabs %}`` follows the WAI-ARIA tabs pattern in its rendered markup.

- **Roving tabindex.** Only the selected tab is in the Tab order
  (``tabindex="0"``); the others carry ``tabindex="-1"`` and are reached with
  the arrow keys (``51-keyboard-nav.js``). When ``active`` names no tab, the
  first tab is the Tab stop, so the tab list is never unreachable.
- **An accessible name** for the tab list: ``label=`` (``aria-label``) or
  ``labelledby=`` (``aria-labelledby``).
- **A URL parameter** (``param=``): each tab is a ``dj-patch`` to
  ``?<param>=<tab id>``, so the view reads the tab in ``handle_params`` and
  passes it back as ``active=``. Back/Forward and bookmarks then work.

Activation is manual: the tabs are ``<button>`` elements, so Enter and Space
activate the focused tab natively.

Each assertion runs on both engines -- Django's, and the Rust engine's raw-body
path (#3565) that renders this tag through the same ``TabsNode``.
"""

from __future__ import annotations

import re

import pytest
from django import template as django_template

pytestmark = pytest.mark.components

_ENGINE = django_template.Engine(
    libraries={"djust_components": "djust.components.templatetags.djust_components"}
)


def _django(source: str, ctx: dict) -> str:
    return _ENGINE.from_string("{% load djust_components %}" + source).render(
        django_template.Context(ctx)
    )


def _rust(source: str, ctx: dict) -> str:
    from djust._rust import RustLiveView
    from djust.components.rust_handlers import register_with_rust_engine

    register_with_rust_engine()
    view = RustLiveView(source, [])
    view.update_state(ctx)
    return re.sub(r"<!--/?dj-if[^>]*-->", "", view.render())


ENGINES = {"django": _django, "rust": _rust}


def _tabs(attrs: str = "") -> str:
    return (
        '{% tabs id="who" active=tab ' + attrs + " %}"
        '{% tab id="mine" label="Mine" %}M{% endtab %}'
        '{% tab id="team" label="Team" %}T{% endtab %}'
        '{% tab id="all" label="All" %}A{% endtab %}'
        "{% endtabs %}"
    )


def _tab_buttons(html: str) -> list[str]:
    return re.findall(r'<button[^>]*role="tab"[^>]*>', html)


@pytest.fixture(params=sorted(ENGINES))
def render(request):
    return ENGINES[request.param]


class TestRovingTabindex:
    @pytest.mark.parametrize("active,stop", [("mine", 0), ("team", 1), ("all", 2)])
    def test_only_the_selected_tab_is_a_tab_stop(self, render, active, stop):
        buttons = _tab_buttons(render(_tabs(), {"tab": active}))
        assert len(buttons) == 3
        for i, button in enumerate(buttons):
            assert ('tabindex="0"' if i == stop else 'tabindex="-1"') in button, button

    @pytest.mark.parametrize("active", ["", "nope"])
    def test_without_a_matching_tab_the_first_tab_is_the_stop(self, render, active):
        """``active=""`` selects the first tab (as before); an id that names
        no tab selects none, and the first tab stays reachable."""
        buttons = _tab_buttons(render(_tabs(), {"tab": active}))
        assert [b.count('tabindex="0"') for b in buttons] == [1, 0, 0]


class TestTabListName:
    def test_label(self, render):
        html = render(_tabs('label="Whose activity"'), {"tab": "mine"})
        assert re.search(r'<nav[^>]*role="tablist"[^>]*aria-label="Whose activity"', html)

    def test_labelledby(self, render):
        html = render(_tabs('labelledby="who-heading"'), {"tab": "mine"})
        assert re.search(r'<nav[^>]*role="tablist"[^>]*aria-labelledby="who-heading"', html)

    def test_label_is_escaped(self, render):
        html = render(_tabs("label=name"), {"tab": "mine", "name": '"><script>x</script>'})
        assert "<script>x" not in html
        assert "aria-label=" in html

    def test_no_label_no_attribute(self, render):
        html = render(_tabs(), {"tab": "mine"})
        assert "aria-label" not in html.split("</nav>")[0].replace("aria-labelledby", "")


class TestUrlParameter:
    def test_param_makes_each_tab_a_dj_patch(self, render):
        html = render(_tabs('param="who"'), {"tab": "team"})
        buttons = _tab_buttons(html)
        assert [re.search(r'dj-patch="([^"]*)"', b).group(1) for b in buttons] == [
            "?who=mine",
            "?who=team",
            "?who=all",
        ]
        assert all("dj-click" not in b for b in buttons)
        assert "T" in html.split('role="tabpanel"')[1]

    def test_param_and_id_are_url_encoded(self, render):
        source = (
            '{% tabs id="t" active=tab param="a b" %}'
            '{% tab id="x&y" label="X" %}X{% endtab %}{% endtabs %}'
        )
        button = _tab_buttons(render(source, {"tab": "x&y"}))[0]
        assert 'dj-patch="?a+b=x%26y"' in button

    def test_without_param_tabs_send_the_event(self, render):
        buttons = _tab_buttons(render(_tabs(), {"tab": "mine"}))
        assert all('dj-click="set_tab"' in b and "dj-patch" not in b for b in buttons)


@pytest.mark.parametrize(
    "attrs,tab",
    [("", "team"), ('label="L"', "all"), ('labelledby="h" param="p"', "mine"), ("", "")],
)
def test_both_engines_render_the_same(attrs, tab):
    source = _tabs(attrs)
    assert _rust(source, {"tab": tab}) == _django(source, {"tab": tab})


FIXTURE_SOURCE = (
    '{% tabs id="who" active=tab label="Whose activity" %}'
    '{% tab id="mine" label="Mine" %}<p>M</p>{% endtab %}'
    '{% tab id="team" label="Team" %}<p>T</p>{% endtab %}'
    '{% tab id="all" label="All" %}<p>A</p>{% endtab %}'
    "{% endtabs %}"
)


def test_the_js_keyboard_fixture_is_the_server_markup():
    """``tests/js/tabs_keyboard_server_markup.test.js`` drives the keyboard on
    ``tests/js/fixtures/tabs_server_markup.html``. That file must be what the
    server renders, or the JS test proves behaviour on markup no page has."""
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[4] / "tests/js/fixtures/tabs_server_markup.html"
    assert fixture.read_text() == _django(FIXTURE_SOURCE, {"tab": "team"}) + "\n"
