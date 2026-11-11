"""Root proof survives trusted edits without becoming lazy-slot authority."""

import pytest

from djust._render_provenance import RenderedHTML, collapse, fold, join, restore_preserved


@pytest.mark.parametrize(
    "normalize", [lambda html: html, fold, lambda html: collapse(html, ["main", "b"])]
)
def test_root_and_lazy_channels_stay_separate_through_unicode_edits(normalize):
    source = "<main dj-root>é <b>保全</b></main>"
    size = len(source.encode())
    root = RenderedHTML(source, root_spans=((0, size),))
    page = join(("🙂", root, "tail"))
    page = normalize(page)
    child = page[1:-4]
    assert isinstance(child, RenderedHTML)
    assert child.root_spans == ((0, len(child.encode())),)
    assert child.spans == ()
    assert child.origins == ()
    replaced = child.replace("é", "new")
    assert replaced.root_spans
    assert replaced.spans == ()
    # Plain strings never recover a proof lost by application postprocessing.
    assert RenderedHTML(str(replaced)).root_spans == ()


def test_held_blocks_restore_their_own_root_proof():
    block = RenderedHTML("<main dj-root>é</main>", root_spans=((0, 23),))
    placeholder = RenderedHTML("TOKEN", held=((0, 5, block),))
    result = restore_preserved("prefix" + placeholder + "suffix")
    assert result.root_spans == ((6, 29),)
    assert result.spans == ()


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("field_after", [False, True])
def test_opaque_cutoff_never_silently_drops_authored_bindings(tmp_path, partial, field_after):
    import django
    from django.core.exceptions import ImproperlyConfigured
    from django.test import override_settings
    from djust import LiveView
    from djust._rust import register_block_tag_handler, unregister_block_tag_handler

    if django.VERSION < (6, 0):
        pytest.skip("Django partial-root dependency planning")

    class Wrapper:
        def render(self, args, content, context):
            return "<section>" + content + "</section>"

    register_block_tag_handler("rootproofwrap", "endrootproofwrap", Wrapper())
    try:
        field = '<input dj-model="name">'
        body = "{% rootproofwrap %}BODY{% endrootproofwrap %}"
        root = "<main dj-root>" + (body + field if field_after else field + body) + "</main>"
        source = (
            "{% partialdef page %}" + root + "{% endpartialdef %}{% partial page %}"
            if partial
            else root
        )
        (tmp_path / "root.html").write_text(source)

        class View(LiveView):
            template = '{% include "root.html" %}'
            name = "before"

        templates = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path)],
                "APP_DIRS": True,
            }
        ]
        with override_settings(TEMPLATES=templates):
            view = View()
            if field_after:
                with pytest.raises(ImproperlyConfigured, match="invalid template provenance"):
                    view.render_with_diff(None)
            else:
                html, _, _ = view.render_with_diff(None)
                assert "BODY" in html
                assert "name" in view._dj_model_fields
                view.update_model(field="name", value="after")
                assert view.name == "after"
    finally:
        unregister_block_tag_handler("rootproofwrap")


def test_final_wrapper_cannot_change_root_namespace(tmp_path):
    from django.core.exceptions import ImproperlyConfigured
    from djust._rust import RustLiveView

    (tmp_path / "root.html").write_text("<main dj-root><b>BODY</b></main>")
    backend = RustLiveView('{% include "root.html" %}', [str(tmp_path)])
    backend.set_authenticated_partial_root(True)
    html, _, _, _ = backend.prepare_authenticated_page()
    spans = backend.prepared_page_root_spans()
    with pytest.raises(ImproperlyConfigured, match="invalid template provenance"):
        backend.validate_prepared_page(
            "<svg>" + html + "</svg>", [(a + 5, b + 5) for a, b in spans]
        )


def test_final_wrapper_commits_the_delivered_ordinary_tree(tmp_path):
    from djust._rust import RustLiveView

    (tmp_path / "root.html").write_text("<main dj-root><tr><td>BODY</td></tr></main>")
    backend = RustLiveView('{% include "root.html" %}', [str(tmp_path)])
    backend.set_authenticated_partial_root(True)
    html, _, _, _ = backend.prepare_authenticated_page()
    spans = backend.prepared_page_root_spans()
    wrapped = "<table>" + html + "</table>"
    backend.validate_prepared_page(wrapped, [(a + 7, b + 7) for a, b in spans])
    baseline, _, _ = backend.commit_authenticated_page()
    # HTML5 foster-parenting moves the table row outside main. It cannot stay
    # in the delivered root's baseline merely because no model fields changed.
    assert "BODY" not in baseline
    assert "<main" in baseline
