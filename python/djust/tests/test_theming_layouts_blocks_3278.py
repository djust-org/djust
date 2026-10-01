"""Layouts can host a LiveView page template (#3278)."""

from __future__ import annotations

import re

import pytest
from django.template import engines
from django.template.loader import get_template

pytestmark = pytest.mark.theming

# The layout sources as they were before the new blocks. The new blocks must be
# invisible until a page overrides them, so each layout renders byte-for-byte
# what its old source rendered.
ORIGINAL_LAYOUTS = {
    "base": """\
{% load static theme_tags %}<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block page_title %}{% endblock %}</title>
  {% theme_head %}
  <link rel="stylesheet" href="{% static 'djust_theming/css/layouts.css' %}">
  {% block head_extra %}{% endblock %}
  {% block extra_css %}{% endblock %}
</head>
<body class="layout-base {% block body_class %}{% endblock %}">
  {% block content %}{% endblock %}
  {% block footer %}{% endblock %}
  {% block extra_js %}{% endblock %}
</body>
</html>
""",
    "sidebar": """\
{% extends "djust_theming/layouts/base.html" %}

{% block body_class %}layout-sidebar{% endblock %}

{% block content %}
<div class="layout-sidebar__container">
  <aside class="layout-sidebar__aside" role="navigation" aria-label="Sidebar">
    {% block sidebar %}{% endblock %}
  </aside>
  <main class="layout-sidebar__main">
    {% block sidebar_content %}{% endblock %}
  </main>
</div>
{% endblock %}
""",
    "topbar": """\
{% extends "djust_theming/layouts/base.html" %}

{% block body_class %}layout-topbar{% endblock %}

{% block content %}
<header class="layout-topbar__header" role="banner">
  {% block topbar %}{% endblock %}
</header>
<main class="layout-topbar__main">
  {% block topbar_content %}{% endblock %}
</main>
{% endblock %}
""",
    "sidebar_topbar": """\
{% extends "djust_theming/layouts/base.html" %}

{% block body_class %}layout-sidebar-topbar{% endblock %}

{% block content %}
<div class="layout-sidebar-topbar__container">
  <aside class="layout-sidebar-topbar__aside" role="navigation" aria-label="Sidebar">
    {% block sidebar %}{% endblock %}
  </aside>
  <div class="layout-sidebar-topbar__right">
    <header class="layout-sidebar-topbar__header" role="banner">
      {% block topbar %}{% endblock %}
    </header>
    <main class="layout-sidebar-topbar__main">
      {% block sidebar_topbar_content %}{% endblock %}
    </main>
  </div>
</div>
{% endblock %}
""",
    "centered": """\
{% extends "djust_theming/layouts/base.html" %}

{% block body_class %}layout-centered{% endblock %}

{% block content %}
<main class="layout-centered__main">
  <div class="layout-centered__container">
    {% block centered_content %}{% endblock %}
  </div>
</main>
{% endblock %}
""",
}


def _render_source(source: str) -> str:
    return engines["django"].from_string(source).render({})


def _render_file(name: str) -> str:
    return get_template(f"djust_theming/layouts/{name}.html").render({})


@pytest.mark.parametrize("name", sorted(ORIGINAL_LAYOUTS))
def test_a_layout_with_no_overrides_renders_byte_identical_to_before(name):
    assert _render_file(name) == _render_source(ORIGINAL_LAYOUTS[name])


def _page(layout: str, blocks: str) -> str:
    return _render_source('{% extends "djust_theming/layouts/' + layout + '.html" %}' + blocks)


class TestBodyAttrsAndClientConfig:
    def test_body_attrs_lands_on_the_body_tag(self):
        html = _page("base", '{% block body_attrs %} dj-hook="Shell" data-x="1"{% endblock %}')
        assert re.search(r'<body class="layout-base "\s?dj-hook="Shell" data-x="1">', html)

    def test_body_attrs_survives_a_layout_that_sets_body_class(self):
        html = _page("sidebar_topbar", '{% block body_attrs %} dj-hook="Shell"{% endblock %}')
        assert '<body class="layout-base layout-sidebar-topbar" dj-hook="Shell">' in html

    def test_djust_client_config_can_be_placed_in_head(self):
        html = _page(
            "base",
            "{% block client_config %}{% load live_tags %}{% djust_client_config %}{% endblock %}",
        )
        head = html.split("</head>")[0]
        assert '<meta name="djust-api-prefix"' in head
        assert '<meta name="djust-ws-path"' in head
        assert "<body" not in head

    def test_client_config_precedes_the_theme_script(self):
        html = _page("base", '{% block client_config %}<meta name="cfg" content="1">{% endblock %}')
        assert html.index('name="cfg"') < html.index("__djust_theme_cookie_prefix")


@pytest.mark.parametrize(
    "layout,main_block,content_block",
    [
        ("sidebar_topbar", "sidebar_topbar_main", "sidebar_topbar_content"),
        ("sidebar", "sidebar_main", "sidebar_content"),
        ("topbar", "topbar_main", "topbar_content"),
        ("centered", "centered_main", "centered_content"),
    ],
)
class TestPageSuppliesItsOwnMain:
    def test_default_layout_wraps_the_content_in_its_main(self, layout, main_block, content_block):
        html = _page(layout, "{% block " + content_block + " %}PAGE{% endblock %}")
        assert html.count("<main") == 1
        assert re.search(r'<main class="layout-[a-z-]+__main">.*PAGE.*</main>', html, re.S)

    def test_overriding_the_main_block_leaves_exactly_one_main(
        self, layout, main_block, content_block
    ):
        html = _page(
            layout,
            "{% block " + main_block + ' %}<main dj-root id="app">PAGE</main>{% endblock %}',
        )
        assert html.count("<main") == 1 and html.count("</main>") == 1
        assert '<main dj-root id="app">PAGE</main>' in html

    def test_the_layout_chrome_stays_when_main_is_replaced(self, layout, main_block, content_block):
        html = _page(layout, "{% block " + main_block + " %}<main>P</main>{% endblock %}")
        assert "layout-" in html.split("<main>")[0]
