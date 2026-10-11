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
