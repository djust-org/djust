"""ImageLightbox and ResizablePanel ship their interactions (#2985, batch 2).

Each component rendered a ``dj-hook`` that no shipped script answered; each
now has a script under ``djust_components/``. The scripts add nothing to the
markup (every attribute they need is already rendered), so what has to hold is
that each render path - the component class, the Django template tag and the
Rust-engine handler - still emits the structure the script reads, and that the
catalogue finds the script.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template import Context, Template

from djust.components import rust_handlers as rh
from djust.components.components.image_lightbox import ImageLightbox
from djust.components.components.resizable_panel import ResizablePanel

STATIC = Path(rh.__file__).resolve().parent / "static" / "djust_components"


def _tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


IMAGES = [
    {"src": "/a.jpg", "alt": "A", "caption": "Cap A"},
    {"src": "/b.jpg", "alt": "B"},
    {"src": "/c.jpg", "alt": "C"},
]


def _lightbox_paths() -> dict[str, str]:
    return {
        "class": ImageLightbox(images=IMAGES, active=1, open=True).render(),
        "tag": _tag("{% lightbox images=images active=1 open=True %}", images=IMAGES),
    }


def _panel_paths() -> dict[str, str]:
    return {
        "class": ResizablePanel(
            content="<p>x</p>", min_size="100px", max_size="600px", initial_size="300px"
        ).render(),
        "tag": _tag(
            '{% resizable_panel min_size="100px" max_size="600px" initial_size="300px" %}'
            "<p>x</p>{% endresizable_panel %}"
        ),
    }


class TestTheStructureTheScriptsRead:
    """The scripts read these classes and attributes; each path must keep emitting them."""

    def test_lightbox(self):
        for path, html in _lightbox_paths().items():
            for cls in (
                "dj-lightbox__close",
                "dj-lightbox__prev",
                "dj-lightbox__next",
                "dj-lightbox__counter",
                "dj-lightbox__image",
                "dj-lightbox__stage",
            ):
                assert f'class="{cls}"' in html, (path, cls)
            assert 'dj-hook="ImageLightbox"' in html, path
            assert 'role="dialog" aria-modal="true"' in html, path
            assert 'data-close-event="close_lightbox"' in html, path
            assert "2 of 3" in html, path

    def test_lightbox_controls_carry_the_events_the_hook_clicks(self):
        """The hook presses the rendered buttons, so each must be an event source."""
        for path, html in _lightbox_paths().items():
            for cls in ("close", "prev", "next"):
                button = re.search(rf'<button class="dj-lightbox__{cls}"[^>]*>', html)
                assert button, (path, cls)
                assert "dj-click" in button.group(0) or "data-dj-" in button.group(0), (path, cls)

    def test_a_closed_lightbox_renders_nothing_so_the_hook_ends_with_it(self):
        assert ImageLightbox(images=IMAGES, open=False).render() == ""
        assert _tag("{% lightbox images=images open=False %}", images=IMAGES) == ""

    def test_resizable_panel(self):
        for path, html in _panel_paths().items():
            assert 'dj-hook="ResizablePanel"' in html, path
            assert 'data-direction="horizontal"' in html, path
            assert "width:300px;min-width:100px;max-width:600px" in html, path
            assert re.search(r'<div class="dj-resizable-panel__handle" role="separator"', html), (
                path
            )
            assert 'tabindex="0"' in html, path

    def test_a_disabled_panel_says_so_for_the_hook(self):
        html = ResizablePanel(content="x", disabled=True).render()
        assert 'data-disabled="true"' in html


@pytest.mark.parametrize(
    "component, hook, script",
    [
        ("image_lightbox", "ImageLightbox", "image-lightbox.js"),
        ("resizable_panel", "ResizablePanel", "resizable-panel.js"),
    ],
)
class TestScriptsAreShippedAndFound:
    def test_the_catalogue_reports_the_script_and_the_hook(self, component, hook, script):
        from djust.theming.gallery.component_registry import component_client

        assert component_client(component) == {
            "hook": hook,
            "script": f"djust_components/{script}",
            "hook_shipped": True,
        }

    def test_the_script_never_writes_html_from_data(self, component, hook, script):
        """Names, captions and alt text reach the page as text or attributes only."""
        source = (STATIC / script).read_text()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert sink not in source, sink
