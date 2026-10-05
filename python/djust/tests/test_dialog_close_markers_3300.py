"""Every dialog djust ships marks its close control for Escape (Part of #3300).

The client's keyboard handling presses Escape in a ``role="dialog"`` through the
dialog's explicit close control, ``.dj-modal__close`` or a ``dj-click`` control
marked ``data-dj-close``, and no longer falls back to "the first dj-click
control". These pin the marker on every render path of each shipped dialog (the
component class, the Django template tag, the Rust-engine handler), so a dialog
cannot silently lose Escape.
"""

from __future__ import annotations

import re

import pytest
from django.template import Context, Template

from djust.components import rust_handlers as rh
from djust.components.components.image_lightbox import ImageLightbox
from djust.components.components.modal import Modal
from djust.components.components.tour import Tour

IMAGES = [{"src": "/a.jpg", "alt": "A"}, {"src": "/b.jpg", "alt": "B"}]
STEPS = [
    {"target": "#a", "title": "One", "content": "First."},
    {"target": "#b", "title": "Two", "content": "Second."},
]


def _tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


def _button(html: str, cls: str) -> str:
    found = re.search(rf'<button class="{cls}"[^>]*>', html)
    assert found, (cls, html[:200])
    return found.group(0)


def _lightbox_paths() -> dict[str, str]:
    return {
        "class": ImageLightbox(images=IMAGES, open=True).render(),
        "tag": _tag("{% lightbox images=images open=True %}", images=IMAGES),
        "handler": str(
            rh.LightboxHandler().render(["images=images", "open=True"], {"images": IMAGES})
        ),
    }


def _tour_paths() -> dict[str, str]:
    return {
        "class": Tour(steps=STEPS, active=0).render(),
        "tag": _tag("{% tour steps=steps active=0 %}", steps=STEPS),
        "handler": str(rh.TourHandler().render(["steps=steps", "active=0"], {"steps": STEPS})),
    }


class TestShippedDialogsMarkTheirCloseControl:
    def test_the_lightbox_close_button_on_every_path(self):
        for path, html in _lightbox_paths().items():
            button = _button(html, "dj-lightbox__close")
            assert "data-dj-close" in button and "dj-click" in button, path

    def test_the_tour_skip_button_on_every_path(self):
        for path, html in _tour_paths().items():
            button = _button(html, "dj-tour__skip")
            assert "data-dj-close" in button and "dj-click" in button, path

    def test_the_last_tour_step_has_no_skip_and_so_no_marker(self):
        html = Tour(steps=STEPS, active=1).render()
        assert "data-dj-close" not in html

    def test_the_stock_modal_uses_dj_modal_close(self):
        html = Modal(title="T", is_open=True).render()
        assert re.search(r'class="dj-modal__close"[^>]*dj-click', html)

    def test_the_marker_is_the_only_difference_for_the_lightbox_and_tour(self):
        """Additive markup: removing the marker gives the markup the paths had before."""
        for html in [*_lightbox_paths().values(), *_tour_paths().values()]:
            assert " data-dj-close" in html
            assert html.replace(" data-dj-close", "").count("data-dj-close") == 0


@pytest.mark.django_db
class TestThemeModal:
    def _render(self, extra: str = "") -> str:
        from django.template import engines

        return (
            engines["django"]
            .from_string(
                "{% load theme_components %}{% theme_modal id='tm' title='T' is_open=True "
                + extra
                + " %}"
            )
            .render({})
        )

    def test_the_default_close_button_is_marked(self):
        html = self._render()
        button = re.search(r"<button[^>]*modal-close[^>]*>", html).group(0)
        assert "data-dj-close" in button and 'dj-click="toggle_modal"' in button

    def test_the_template_documents_the_marker_for_a_slot_close_replacement(self):
        from pathlib import Path

        import djust.theming

        template = (
            Path(djust.theming.__file__).parent / "templates/djust_theming/components/modal.html"
        ).read_text()
        assert "slot_close" in template and "data-dj-close" in template
