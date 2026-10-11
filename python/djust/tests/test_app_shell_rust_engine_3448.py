"""Exercise app_shell on the Rust backend and compare Django's slot layout."""

import pytest
from django.template import Context, Engine


@pytest.fixture(scope="module")
def rust_backend():
    from djust.components.rust_handlers import register_with_rust_engine
    from djust.template.backend import DjustTemplateBackend

    register_with_rust_engine()
    return DjustTemplateBackend(
        {"NAME": "app-shell-test", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )


@pytest.mark.parametrize(
    "sidebar", ["", "{% app_sidebar %}NAV<aside>nested</aside>{% endapp_sidebar %}"]
)
def test_rust_app_shell_matches_the_django_node(rust_backend, sidebar):
    source = (
        "{% load djust_components %}{% app_shell %}"
        + sidebar
        + "{% app_header %}HEADER{% endapp_header %}"
        "{% app_content %}CONTENT{% endapp_content %}{% endapp_shell %}"
    )
    engine = Engine(
        libraries={"djust_components": "djust.components.templatetags.djust_components"}
    )
    assert rust_backend.from_string(source).render({}) == engine.from_string(source).render(
        Context({})
    )


def test_rust_app_shell_wraps_header_and_content_in_main(rust_backend):
    source = (
        "{% load djust_components %}{% app_shell %}"
        "{% app_header %}HEADER{% endapp_header %}"
        "{% app_content %}CONTENT{% endapp_content %}{% endapp_shell %}"
    )
    html = rust_backend.from_string(source).render({})
    assert (
        '<div class="dj-app-shell__main"><header class="dj-app-shell__header">HEADER</header><main class="dj-app-shell__content">CONTENT</main></div>'
        in html
    )


def test_rust_app_shell_keeps_id_class_and_collapsed_modifier(rust_backend):
    source = (
        '{% load djust_components %}{% app_shell id="workspace" class="custom" sidebar_collapsed=True %}'
        "{% app_header %}HEADER{% endapp_header %}{% app_content %}CONTENT{% endapp_content %}{% endapp_shell %}"
    )
    html = rust_backend.from_string(source).render({})
    assert 'class="dj-app-shell dj-app-shell--sidebar-collapsed custom" id="workspace"' in html
    assert "dj-app-shell__main" in html


@pytest.mark.parametrize("outer_sidebar", ["", "{% app_sidebar %}OUTER-NAV{% endapp_sidebar %}"])
@pytest.mark.parametrize("slot", ["app_content", "app_header"])
@pytest.mark.parametrize("sidebar_first", [False, True])
def test_nested_app_shell_sidebars_match_the_django_node(
    rust_backend, outer_sidebar, slot, sidebar_first
):
    inner = (
        '{% app_shell id="inner" %}'
        "{% app_sidebar %}INNER-NAV<aside>nested</aside>{% endapp_sidebar %}"
        "{% app_content %}INNER-CONTENT{% endapp_content %}{% endapp_shell %}"
    )
    nested_slot = "{% " + slot + " %}" + inner + "{% end" + slot + " %}"
    slots = outer_sidebar + nested_slot if sidebar_first else nested_slot + outer_sidebar
    source = '{% load djust_components %}{% app_shell id="outer" %}' + slots + "{% endapp_shell %}"
    engine = Engine(
        libraries={"djust_components": "djust.components.templatetags.djust_components"}
    )
    expected = engine.from_string(source).render(Context({}))
    assert rust_backend.from_string(source).render({}) == expected


@pytest.mark.parametrize(
    "prefix",
    [
        '<main class="dj-app-shell__content">A\rB\nC</main>',
        '<main><img src="x"><br><input></main>',
        '<!-- <aside class="dj-app-shell__sidebar">fake</aside> -->',
        "<script>var html = '<aside class=\"dj-app-shell__sidebar\">fake</aside>';</script>",
    ],
)
def test_sidebar_split_preserves_rendered_bytes(prefix):
    from djust.components.rust_handlers import _split_app_shell_sidebar

    sidebar = '<aside class="dj-app-shell__sidebar">NAV<aside>nested</aside></aside>'
    assert _split_app_shell_sidebar(prefix + sidebar) == (sidebar, prefix)


@pytest.mark.parametrize(
    "content",
    [
        '<div><aside class="dj-app-shell__sidebar">nested</aside></div>',
        '<aside class="dj-app-shell__sidebar">unclosed',
    ],
)
def test_sidebar_split_leaves_non_slot_or_unbalanced_content_untouched(content):
    from djust.components.rust_handlers import _split_app_shell_sidebar

    assert _split_app_shell_sidebar(content) == ("", content)


@pytest.mark.parametrize("slot", ["app_sidebar", "app_content"])
def test_app_shell_handles_optional_html_end_tags(rust_backend, slot):
    source = (
        "{% load djust_components %}{% app_shell %}{% " + slot + " %}<p>TEXT{% end" + slot + " %}"
    )
    if slot == "app_content":
        source += "{% app_sidebar %}NAV{% endapp_sidebar %}"
    source += "{% endapp_shell %}"
    engine = Engine(
        libraries={"djust_components": "djust.components.templatetags.djust_components"}
    )
    assert rust_backend.from_string(source).render({}) == engine.from_string(source).render(
        Context({})
    )
