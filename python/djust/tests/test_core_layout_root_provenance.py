"""Core layout wrappers must preserve source root proof without trusting tag names."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from djust._rust import RustLiveView
from djust.components.rust_handlers import register_with_rust_engine


@pytest.fixture(autouse=True)
def layout_handlers():
    register_with_rust_engine()
    yield
    register_with_rust_engine()


@pytest.mark.parametrize("sidebar_first", [False, True])
def test_nested_core_layout_preserves_included_root_and_model_binding(tmp_path, sidebar_first):
    (tmp_path / "root.html").write_text('<section dj-root>é😀<input dj-model="query"></section>')
    sidebar = "{% app_sidebar %}NAV{% endapp_sidebar %}"
    content = '{% app_header %}HEADER{% endapp_header %}{% app_content %}{% include "root.html" %}{% endapp_content %}'
    view = RustLiveView(
        "{% app_shell %}"
        + (sidebar + content if sidebar_first else content + sidebar)
        + "{% endapp_shell %}",
        [str(tmp_path)],
    )
    view.set_authenticated_partial_root(True)
    html, _, _, _ = view.prepare_authenticated_page()
    assert "dj-app-shell__main" in html and "é😀" in html
    assert view.prepared_page_has_root()
    view.validate_prepared_page(html, view.prepared_page_root_spans())
    view.commit_authenticated_page()
    assert view.dj_model_fields() == ["query"]


def test_replaced_handler_does_not_gain_core_layout_authority():
    from djust._rust import register_block_tag_handler

    class Opaque:
        def _render_root_parts(self, args, content, context):
            return [(None, "<div>"), ((0, len(content.encode())), ""), (None, "</div>")]

        def render(self, args, content, context):
            return "<div>" + content + "</div>"

    register_block_tag_handler("app_content", "endapp_content", Opaque())
    view = RustLiveView("{% app_content %}<main dj-root>body</main>{% endapp_content %}", [])
    view.set_authenticated_partial_root(True)
    with pytest.raises(ImproperlyConfigured, match="authored"):
        view.prepare_authenticated_page()


def test_core_layout_does_not_authorize_an_opaque_root():
    view = RustLiveView("{% app_content %}{{ payload|safe }}{% endapp_content %}", [])
    view.set_authenticated_partial_root(True)
    view.update_state({"payload": "<main dj-root><input dj-model=admin></main>"})
    with pytest.raises(ImproperlyConfigured, match="authored"):
        view.prepare_authenticated_page()


def test_core_layout_cannot_activate_a_source_inert_root():
    view = RustLiveView(
        "{% app_content %}<textarea>{{ closer|safe }}<section dj-root>hidden</section></textarea>{% endapp_content %}",
        [],
    )
    view.set_authenticated_partial_root(True)
    view.update_state({"closer": "</textarea>"})
    with pytest.raises(ImproperlyConfigured, match="authored"):
        view.prepare_authenticated_page()


def test_core_layout_model_proof_stays_separate_from_opaque_and_lazy_output():
    view = RustLiveView(
        '{% app_content %}<section dj-root><input dj-model="query">{{ payload|safe }}<div dj-view="app.Child" dj-lazy></div></section>{% endapp_content %}',
        [],
    )
    view.set_authenticated_partial_root(True)
    view.update_state({"payload": "<input dj-model=admin>"})
    html, lazy_spans, _, _ = view.prepare_authenticated_page()
    assert lazy_spans == []  # Root-copy support never grants lazy-registration authority.
    view.validate_prepared_page(html, view.prepared_page_root_spans())
    view.commit_authenticated_page()
    assert view.dj_model_fields() == ["query"]


def test_core_layout_cannot_change_the_source_root_namespace():
    view = RustLiveView(
        "<svg>{% app_shell %}<g dj-root><text>body</text></g>{% endapp_shell %}</svg>", []
    )
    view.set_authenticated_partial_root(True)
    with pytest.raises(ImproperlyConfigured, match="authored"):
        view.prepare_authenticated_page()


def test_core_layout_keeps_lazy_cutoff_for_following_source():
    view = RustLiveView(
        "{% app_header %}HEADER{% endapp_header %}<main dj-root>"
        '<div dj-view="app.Child" dj-lazy></div><input dj-model="query"></main>',
        [],
    )
    view.set_authenticated_partial_root(True)
    html, lazy_spans, _, _ = view.prepare_authenticated_page()
    assert lazy_spans == []
    assert view.prepared_page_root_spans()
    view.validate_prepared_page(html, view.prepared_page_root_spans())
    view.commit_authenticated_page()
    assert view.dj_model_fields() == ["query"]


@pytest.mark.parametrize("broken", ["missing", "duplicate", "utf8"])
def test_invalid_core_body_copy_plan_is_rejected(monkeypatch, broken):
    from djust.components.rust_handlers import AppContentHandler

    def invalid(self, args, content, context):
        size = len(content.encode())
        if broken == "missing":
            return [(None, "<main dj-root>forged</main>")]
        if broken == "duplicate":
            return [((0, size), ""), ((0, size), "")]
        return [((1, size), "")]

    monkeypatch.setattr(AppContentHandler, "_render_root_parts", invalid)
    view = RustLiveView("{% app_content %}é<main dj-root>body</main>{% endapp_content %}", [])
    view.set_authenticated_partial_root(True)
    with pytest.raises(Exception, match="[Cc]ore layout"):
        view.prepare_authenticated_page()
