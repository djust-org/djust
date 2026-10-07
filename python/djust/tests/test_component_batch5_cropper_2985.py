"""ImageCropper ships its interaction (#2985, batch 5).

What has to hold on every render path (the component class, the Django template
tag and the Rust-engine handler): the structure the hook reads is emitted, the
additions are identical everywhere, the markup the component always had is
unchanged apart from them, and nothing a caller passes can break out of an
attribute.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template import Context, Template

from djust.components import rust_handlers as rh
from djust.components.components.image_cropper import ImageCropper, cropper_extras

STATIC = Path(rh.__file__).resolve().parent / "static" / "djust_components"
HOSTILE = "<img src=x onerror=alert(1)>\"'&<"

STRIP = [
    re.compile(r' tabindex="0" role="group" aria-label="Crop area" aria-description="[^"]*"'),
    re.compile(r'<span class="dj-image-cropper__handle[^>]*></span>'),
    re.compile(r'<div class="dj-image-cropper__status"[^>]*></div>'),
]
LEGACY = (
    '<div class="dj-image-cropper" dj-hook="ImageCropper" data-crop-event="save_crop" '
    'data-min-width="50" data-min-height="50"><div class="dj-image-cropper__canvas">'
    '<img class="dj-image-cropper__image" src="/p.jpg" alt="Image to crop" draggable="false">'
    '<div class="dj-image-cropper__overlay"></div><div class="dj-image-cropper__selection">'
    '</div></div><div class="dj-image-cropper__actions">'
    '<button class="dj-image-cropper__crop-btn" type="button">Crop</button>'
    '<button class="dj-image-cropper__reset-btn" type="button">Reset</button></div></div>'
)


def _tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


def _paths(**kw) -> dict[str, str]:
    kw = {"src": "/p.jpg", **kw}
    tag_args = " ".join(f"{k}={k}" for k in kw)
    return {
        "class": ImageCropper(
            **{("custom_class" if k == "class" else k): v for k, v in kw.items()}
        ).render(),
        "tag": _tag("{% image_cropper " + tag_args + " %}", **kw),
        "handler": str(rh.ImageCropperHandler().render([f"{k}={k}" for k in kw], dict(kw))),
    }


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _parse(markup: str):
    parser = _Collect()
    parser.feed(markup)
    return parser.tags


def _strip(markup: str) -> str:
    for pattern in STRIP:
        markup = pattern.sub("", markup)
    return markup


class TestMarkup:
    def test_the_legacy_markup_is_unchanged_apart_from_the_additions(self):
        for path, markup in _paths().items():
            assert _strip(markup) == LEGACY, path

    def test_all_three_paths_are_identical_for_any_arguments(self):
        for kw in (
            {},
            {"aspect_ratio": "16/9", "min_width": 20, "min_height": 30, "alt": "A photo"},
            {"alt": HOSTILE, "crop_event": HOSTILE, "aspect_ratio": HOSTILE},
            {"disabled": True, "class": "extra"},
        ):
            paths = _paths(**kw)
            assert paths["class"] == paths["tag"] == paths["handler"], kw

    def test_the_hook_finds_everything_it_needs(self):
        for path, markup in _paths(aspect_ratio="1/1").items():
            by_class = {}
            for _t, attrs in _parse(markup):
                by_class.setdefault(attrs.get("class", ""), attrs)
            for cls in (
                "dj-image-cropper__canvas",
                "dj-image-cropper__image",
                "dj-image-cropper__selection",
                "dj-image-cropper__crop-btn",
                "dj-image-cropper__reset-btn",
                "dj-image-cropper__status",
            ):
                assert cls in by_class, (path, cls)
            selection = by_class["dj-image-cropper__selection"]
            assert selection["tabindex"] == "0" and selection["role"] == "group", path
            assert selection["aria-label"] == "Crop area", path
            assert "Enter crops" in selection["aria-description"], path
            assert by_class["dj-image-cropper__status"]["role"] == "status", path
            handles = [a["data-handle"] for _t, a in _parse(markup) if "data-handle" in a]
            assert handles == ["nw", "n", "ne", "e", "se", "s", "sw", "w"], path
            assert 'data-aspect-ratio="1/1"' in markup, path

    def test_handles_are_decoration_for_assistive_technology(self):
        for path, markup in _paths().items():
            for _t, attrs in _parse(markup):
                if "data-handle" in attrs:
                    assert attrs["aria-hidden"] == "true", path

    def test_the_images_alternative_text_is_a_kwarg_and_escaped(self):
        for path, markup in _paths().items():
            assert 'alt="Image to crop"' in markup, path
        for path, markup in _paths(alt='a"b<c>').items():
            img = [a for t, a in _parse(markup) if t == "img"][0]
            assert img["alt"] == 'a"b<c>', path

    def test_nothing_a_caller_passes_breaks_out_of_an_attribute(self):
        for path, markup in _paths(
            src=HOSTILE, crop_event=HOSTILE, aspect_ratio=HOSTILE, alt=HOSTILE, custom_class=HOSTILE
        ).items():
            tags = _parse(markup)
            assert not [k for _t, a in tags for k in a if k.startswith("on")], path
            assert [t for t, _a in tags].count("img") == 1, path

    def test_the_extras_are_constant_markup(self):
        extras = cropper_extras()
        assert extras == cropper_extras()
        assert extras.handles.count("data-handle") == 8


class TestScriptStylesAndCatalogue:
    SOURCE = (STATIC / "image-cropper.js").read_text()
    CSS = (STATIC / "components.css").read_text()

    def test_the_catalogue_finds_the_script_and_the_hook(self):
        from djust.theming.gallery.component_registry import component_client

        assert component_client("image_cropper") == {
            "hook": "ImageCropper",
            "script": "djust_components/image-cropper.js",
            "hook_shipped": True,
        }

    @pytest.mark.parametrize(
        "banned",
        [
            r"\binnerHTML\b",
            r"\bouterHTML\b",
            r"insertAdjacentHTML",
            r"\beval\s*\(",
            r"new Function",
            r"document\.write",
            r"\bcreateElement\b",
            r"getContext",
            r"toDataURL|toBlob",
            r"new Image",
            r"\bfetch\s*\(",
            r"XMLHttpRequest",
            r"FileReader",
        ],
    )
    def test_script_sends_only_a_box_and_touches_no_pixels(self, banned):
        assert not re.search(banned, self.SOURCE), banned

    def test_script_registers_without_replacing_an_app_hook(self):
        assert "!appHooks.ImageCropper && !window.DjustHooks.ImageCropper" in self.SOURCE

    def test_drags_do_not_scroll_the_page(self):
        rule = re.search(r"\.dj-image-cropper__canvas \{([^}]*)\}", self.CSS).group(1)
        assert "touch-action: none" in rule

    def test_the_overlay_no_longer_dims_the_selection_itself(self):
        # The selection's own shadow dims everything around it; the full-image overlay also
        # dimmed the inside of the box.
        assert ".dj-image-cropper__overlay { display: none; }" in self.CSS

    def test_every_class_the_hook_names_has_a_rule(self):
        for cls in set(re.findall(r'"(dj-image-cropper__[a-z-]+)"', self.SOURCE)):
            assert f".{cls}" in self.CSS, cls
        for name in ("nw", "n", "ne", "e", "se", "s", "sw", "w"):
            assert f".dj-image-cropper__handle--{name}" in self.CSS
