"""ResizablePanel ``resize_event`` (#2985).

An optional, additive kwarg. The markup change is one data attribute, added
only when the kwarg is set, identically on the component class, the Django
template tag and the Rust-engine handler: without it the markup is what it was.
"""

from __future__ import annotations

from django.template import Context, Template

from djust.components.components.resizable_panel import ResizablePanel


def _tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


def _panel_paths(event: str | None = None) -> dict[str, str]:
    kw_class = {"resize_event": event} if event is not None else {}
    kw_tag = f" resize_event={event!r}" if event is not None else ""
    return {
        "class": ResizablePanel(content="hello", direction="horizontal", **kw_class).render(),
        "tag": _tag(
            "{% resizable_panel direction='horizontal'"
            + kw_tag
            + " %}hello{% endresizable_panel %}"
        ),
    }


class TestResizePanelResizeEvent:
    def test_the_event_is_one_additive_data_attribute_on_every_path(self):
        for path, html in _panel_paths("panel_resized").items():
            assert 'data-resize-event="panel_resized"' in html, path

    def test_without_it_there_is_no_attribute(self):
        for path, html in _panel_paths().items():
            assert "data-resize-event" not in html, path
        for path, html in _panel_paths("").items():
            assert "data-resize-event" not in html, path

    def test_removing_the_attribute_leaves_the_old_markup(self):
        with_event = _panel_paths("panel_resized")
        without = _panel_paths()
        for path in with_event:
            assert (
                with_event[path].replace(' data-resize-event="panel_resized"', "") == without[path]
            ), path

    def test_the_three_render_paths_agree(self):
        for event in (None, "panel_resized"):
            paths = _panel_paths(event)
            assert paths["class"] == paths["tag"], event

    def test_the_event_name_is_escaped(self):
        for path, html in _panel_paths('"><script>alert(1)</script>').items():
            assert "<script>" not in html, path
