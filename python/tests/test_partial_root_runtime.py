"""Authenticated partial roots must be transactional across render paths."""

import django
import pytest

from djust._rust import RustLiveView

pytestmark = pytest.mark.skipif(django.VERSION < (6, 0), reason="Django partials require 6.0")

SOURCE = (
    '{% partialdef card %}<main dj-root><input dj-model="name">{{ name }}</main>{% endpartialdef %}'
    "{% if shown %}{% partial card %}{% endif %}"
)


def authenticated_view():
    view = RustLiveView(SOURCE, [])
    view.set_authenticated_partial_root(True)
    return view


@pytest.mark.parametrize("binary", [False, True])
def test_invalid_root_render_does_not_advance_diff_baseline(binary):
    view = authenticated_view()
    render = view.render_binary_diff if binary else view.render_with_diff
    view.update_state({"shown": True, "name": "before"})
    initial_html, _, initial_version = render()
    assert "before" in initial_html
    view.update_state({"shown": False, "name": "missing"})
    with pytest.raises(Exception):
        render()
    view.update_state({"shown": True, "name": "after"})
    html, patches, version = render()
    assert "after" in html
    assert patches is not None
    assert version == initial_version + 1


def test_restored_view_retains_authenticated_mode_and_binding_scope():
    view = authenticated_view()
    view.update_state({"shown": True, "name": "before"})
    view.render_with_diff()
    restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
    restored.set_template_dirs([])
    assert restored.authenticated_partial_root()
    assert restored.dj_model_fields() == ["name"]


def test_mode_planner_follows_includes_and_unknown_dynamic_selectors(tmp_path):
    from djust import _rust

    (tmp_path / "root.html").write_text(
        "{% partialdef card %}<main dj-root>ok</main>{% endpartialdef %}{% partial card %}",
        encoding="utf-8",
    )
    plan = _rust.template_needs_authenticated_partial_root
    assert plan('{% include "root.html" %}', [str(tmp_path)])
    assert plan("{% include selected %}", [str(tmp_path)])
    assert not plan("<main dj-root>ordinary</main>", [str(tmp_path)])
    subdir = tmp_path / "pages"
    subdir.mkdir()
    (subdir / "local.html").write_text(
        "{% partialdef card %}<main dj-root>ok</main>{% endpartialdef %}{% partial card %}",
        encoding="utf-8",
    )
    assert plan('{% include "./local.html" %}', [str(tmp_path)], "pages/index.html")


def test_switching_to_partial_root_keeps_last_delivered_diff_baseline():
    view = RustLiveView("<main dj-root><p>{{ name }}</p></main>", [])
    view.update_state({"shown": True, "name": "before"})
    _, _, first = view.render_with_diff()
    view.update_template(SOURCE)
    view.set_authenticated_partial_root(True)
    view.update_state({"name": "after"})
    html, patches, version = view.render_with_diff()
    assert "after" in html
    assert patches is not None
    assert version == first + 1


def test_authenticated_render_retains_lazy_child_provenance():
    from djust import _rust

    source = (
        '{% partialdef p %}<main dj-root><div dj-view="child.V" dj-lazy="visible"></div></main>'
        "{% endpartialdef %}{% partial p %}"
    )
    view = RustLiveView(source, [])
    view.set_authenticated_partial_root(True)
    html, spans, origins = view.render_full_provenance()
    assert "<main dj-root>" in html
    assert spans
    assert any("container" in origin[3] for origin in origins)
    assert _rust.authenticated_root_shell(html, spans) == (0, len(html.encode()))


def test_dynamic_selector_transitions_between_fragment_and_partial_root(tmp_path):
    (tmp_path / "plain.html").write_text("<b>PLAIN</b>", encoding="utf-8")
    (tmp_path / "live.html").write_text(
        '{% partialdef p %}<main dj-root><input dj-model="name">{{ name }}</main>{% endpartialdef %}{% partial p %}',
        encoding="utf-8",
    )
    view = RustLiveView("{% include selected %}", [str(tmp_path)])
    view.set_authenticated_partial_root(True)
    view.update_state({"selected": "plain.html", "name": "A"})
    html, _, v1 = view.render_with_diff()
    assert "PLAIN" in html
    assert not view.authenticated_partial_root()
    view.update_state({"selected": "live.html"})
    html, patches, v2 = view.render_with_diff()
    assert "dj-root" in html and patches is not None and v2 == v1 + 1
    assert view.authenticated_partial_root()
    assert view.dj_model_fields() == ["name"]
    view.update_state({"selected": "plain.html"})
    html, patches, v3 = view.render_with_diff()
    assert "PLAIN" in html and patches is not None and v3 == v2 + 1
    assert not view.authenticated_partial_root()
    view.update_state({"selected": "live.html"})
    html, _, v4 = view.render_with_diff()
    assert "dj-root" in html and v4 == v3 + 1
    assert view.authenticated_partial_root()


def test_named_partial_root_resolves_relative_include_origin(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "piece.html").write_text("<b>RELATIVE</b>", encoding="utf-8")
    source = (
        '{% partialdef p %}<main dj-root>{% include "./piece.html" %}</main>'
        "{% endpartialdef %}{% partial p %}"
    )
    view = RustLiveView(source, [str(tmp_path)])
    view.set_template_origin("pages/index.html")
    view.set_authenticated_partial_root(True)
    assert "RELATIVE" in view.render_with_diff()[0]
    restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
    restored.set_template_dirs([str(tmp_path)])
    assert "RELATIVE" in restored.render_with_diff()[0]


def test_origin_change_revokes_bindings_until_new_render(tmp_path):
    for folder, field in (("first", "name"), ("second", "is_admin")):
        path = tmp_path / folder
        path.mkdir()
        (path / "piece.html").write_text(f'<input dj-model="{field}">', encoding="utf-8")
    source = (
        '{% partialdef p %}<main dj-root>{% include "./piece.html" %}</main>'
        "{% endpartialdef %}{% partial p %}"
    )
    view = RustLiveView(source, [str(tmp_path)])
    view.set_template_origin("first/page.html")
    view.set_authenticated_partial_root(True)
    view.render_with_diff()
    assert view.dj_model_fields() == ["name"]
    view.set_template_origin("second/page.html")
    assert view.dj_model_fields() == []
    view.render_with_diff()
    assert view.dj_model_fields() == ["is_admin"]


@pytest.mark.parametrize("include", ["{% include target %}", '{% include "inner.html" %}'])
def test_planner_rejects_partial_root_competing_with_direct_root(tmp_path, include):
    (tmp_path / "inner.html").write_text(
        "{% partialdef p %}<main dj-root>INNER</main>{% endpartialdef %}{% partial p %}",
        encoding="utf-8",
    )
    source = include + "<main dj-root>OUTER</main>"
    from djust import _rust

    assert _rust.template_needs_authenticated_partial_root(source, [str(tmp_path)])
    view = RustLiveView(source, [str(tmp_path)])
    view.set_authenticated_partial_root(True)
    view.update_state({"target": "inner.html"})
    with pytest.raises(Exception):
        view.render_with_diff()


def test_dynamic_selector_is_resolved_only_by_the_render(tmp_path):
    (tmp_path / "plain.html").write_text("<b>PLAIN</b>", encoding="utf-8")
    (tmp_path / "live.html").write_text(
        "{% partialdef p %}<main dj-root>LIVE</main>{% endpartialdef %}{% partial p %}",
        encoding="utf-8",
    )

    class Chooser:
        reads = 0

        @property
        def selected(self):
            self.reads += 1
            return "plain.html" if self.reads == 1 else "live.html"

    chooser = Chooser()
    view = RustLiveView("<section>{% include chooser.selected %}</section>", [str(tmp_path)])
    view.set_authenticated_partial_root(True)
    view.update_state({"chooser": {}})
    view.set_raw_py_values({"chooser": chooser})
    html, _, _ = view.render_with_diff()
    assert "PLAIN" in html and "LIVE" not in html
    assert chooser.reads == 1


def test_prepared_page_commits_only_the_validated_render():
    view = authenticated_view()
    view.update_state({"shown": True, "name": "before"})
    full, spans, origins, root = view.prepare_authenticated_page()
    assert root is not None and "before" in full and spans and origins
    assert view.dj_model_fields() == []
    _, _, version = view.commit_authenticated_page()
    assert version == 1 and view.dj_model_fields() == ["name"]
    with pytest.raises(RuntimeError, match="pending"):
        view.commit_authenticated_page()


def test_pending_page_is_invalidated_by_mutation_and_failed_prepare():
    view = authenticated_view()
    view.update_state({"shown": True, "name": "before"})
    view.prepare_authenticated_page()
    view.update_state({"shown": False})
    with pytest.raises(RuntimeError, match="pending"):
        view.commit_authenticated_page()
    with pytest.raises(Exception):
        view.prepare_authenticated_page()
    with pytest.raises(RuntimeError, match="pending"):
        view.commit_authenticated_page()
    view.update_state({"shown": True, "name": "after"})
    view.prepare_authenticated_page()
    view.discard_authenticated_page()
    with pytest.raises(RuntimeError, match="pending"):
        view.commit_authenticated_page()


def test_rootless_dynamic_fragment_uses_fields_from_executed_render(tmp_path):
    (tmp_path / "plain.html").write_text('<input dj-model="query">', encoding="utf-8")
    view = RustLiveView("{% include selected %}", [str(tmp_path)])
    view.set_authenticated_partial_root(True)
    view.update_state({"selected": "plain.html"})
    html, _, _, root = view.prepare_authenticated_page()
    assert root is None and "query" in html
    view.commit_authenticated_page()
    assert not view.authenticated_partial_root()
    assert view.dj_model_fields() == ["query"]


@pytest.mark.parametrize(
    "method", ["render", "render_full_provenance", "render_with_diff", "render_binary_diff"]
)
def test_intervening_render_invalidates_prepared_page(method):
    view = authenticated_view()
    view.update_state({"shown": True, "name": "before"})
    view.prepare_authenticated_page()
    getattr(view, method)()
    with pytest.raises(RuntimeError, match="pending"):
        view.commit_authenticated_page()


def test_lazy_render_does_not_pre_resolve_dynamic_include(tmp_path):
    (tmp_path / "plain.html").write_text("<b>PLAIN</b>", encoding="utf-8")
    (tmp_path / "live.html").write_text(
        "{% partialdef p %}<main dj-root>LIVE</main>{% endpartialdef %}{% partial p %}",
        encoding="utf-8",
    )

    class Chooser:
        reads = 0

        @property
        def selected(self):
            self.reads += 1
            return "plain.html" if self.reads == 1 else "live.html"

    chooser = Chooser()
    view = RustLiveView("<section>{% include chooser.selected %}</section>", [str(tmp_path)])
    view.set_authenticated_partial_root(True)
    view.update_state({"chooser": {}})
    view.set_raw_py_values({"chooser": chooser})
    html, _, _ = view.render_lazy_html()
    assert "PLAIN" in html and "LIVE" not in html
    assert chooser.reads == 1


def test_primed_inheritance_cache_cannot_hide_an_uncalled_root_partial(tmp_path):
    (tmp_path / "parent.html").write_text(
        "{% partialdef p %}<main dj-root>INERT</main>{% endpartialdef %}"
        "{% block body %}default{% endblock %}",
        encoding="utf-8",
    )
    source = '{% extends "parent.html" %}{% block body %}<span>fragment</span>{% endblock %}'
    # Priming the shared inheritance cache must not skip recording the parent.
    ordinary = RustLiveView(source, [str(tmp_path)])
    assert "fragment" in ordinary.render_with_diff()[0]
    protected = RustLiveView(source, [str(tmp_path)])
    protected.set_authenticated_partial_root(True)
    with pytest.raises(Exception, match="root"):
        protected.render_with_diff()


def test_included_template_can_gain_partial_root_after_cache_warmup(tmp_path):
    from djust import _rust

    piece = tmp_path / "piece.html"
    piece.write_text("<b>plain</b>", encoding="utf-8")
    source = '{% include "piece.html" %}'
    # A static dependency can change independently of its parent source.
    assert _rust.template_needs_authenticated_partial_root(source, [str(tmp_path)])
    view = RustLiveView(source, [str(tmp_path)])
    view.set_authenticated_partial_root(True)
    assert "plain" in view.render_with_diff()[0]
    piece.write_text(
        '{% partialdef page %}<main dj-root><input dj-model="name">NEW</main>{% endpartialdef %}{% partial page %}',
        encoding="utf-8",
    )
    html, patches, _ = view.render_with_diff()
    assert "NEW" in html and patches is not None
    assert view.authenticated_partial_root()
    assert view.dj_model_fields() == ["name"]


def test_lazy_registration_plan_resolves_relative_includes_per_origin(tmp_path):
    for name in ("plain", "lazy"):
        (tmp_path / name).mkdir()
    (tmp_path / "plain" / "piece.html").write_text("<p>plain</p>", encoding="utf-8")
    (tmp_path / "lazy" / "piece.html").write_text(
        '<div dj-view="child.V" dj-lazy="visible"></div>', encoding="utf-8"
    )
    source = '{% include "./piece.html" %}'
    view = RustLiveView(source, [str(tmp_path)])
    view.set_authenticated_partial_root(True)
    view.set_template_origin("plain/page.html")
    assert not view.lazy_registration_allowed()
    view.set_template_origin("lazy/page.html")
    assert view.lazy_registration_allowed()
    view.set_template_origin("plain/page.html")
    assert not view.lazy_registration_allowed()


def test_root_replacement_preserves_framework_view_path_and_snapshot():
    source = (
        "{% partialdef a %}<main dj-root>A</main>{% endpartialdef %}"
        "{% partialdef b %}<section dj-root>B</section>{% endpartialdef %}"
        "{% if first %}{% partial a %}{% else %}{% partial b %}{% endif %}"
    )
    view = RustLiveView(source, [])
    view.set_authenticated_partial_root(True)
    view.set_authenticated_view_path("app.Page")
    view.update_state({"first": True})
    assert 'dj-view="app.Page"' in view.render_with_diff()[0]
    restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
    restored.set_template_dirs([])
    restored.update_state({"first": False})
    html, patches, _ = restored.render_with_diff()
    assert 'dj-view="app.Page"' in html and "app.Page" in patches


def test_authored_view_path_is_not_overridden():
    source = '{% partialdef a %}<main dj-root dj-view="explicit.View">A</main>{% endpartialdef %}{% partial a %}'
    view = RustLiveView(source, [])
    view.set_authenticated_partial_root(True)
    view.set_authenticated_view_path("app.Page")
    html, _, _ = view.render_with_diff()
    assert 'dj-view="explicit.View"' in html and 'dj-view="app.Page"' not in html
