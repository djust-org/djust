"""ImageUploadPreview ships its interaction on djust's upload pipeline (#2985, batch 5).

What has to hold on every render path (the component class, the Django template
tag and the Rust-engine handler): the structure the hook reads is emitted, the
files go through ``dj-upload`` / ``dj-upload-drop`` (djust's own directives, not
a transport of this component's), the new attributes are identical everywhere,
the markup the component always had is unchanged apart from them, and nothing a
caller passes can break out of an attribute.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template import Context, Template

from djust.components import rust_handlers as rh
from djust.components.components.image_upload_preview import ITEMS_HTML, ImageUploadPreview

STATIC = Path(rh.__file__).resolve().parent / "static" / "djust_components"
HOSTILE = "<img src=x onerror=alert(1)>\"'&<"

ADDED = re.compile(r' (data-upload|data-max-size|data-notify|dj-upload-drop|dj-upload)="[^"]*"')
LEGACY = (
    '<div class="dj-img-upload" dj-hook="ImageUploadPreview" data-event="upload" data-max="5">'
    '<label class="dj-img-upload__dropzone">{svg}'
    '<span class="dj-img-upload__text">Drop images here or click to upload</span>'
    '<span class="dj-img-upload__hint">Max 5 images</span>'
    '<input type="file" name="images" accept="image/*" multiple class="dj-img-upload__input" '
    'aria-label="Upload images"></label></div>'
)


def _tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


def _paths(**kw) -> dict[str, str]:
    """The same component on the three render paths."""
    tag_args = " ".join(f"{k}={k}" for k in kw)
    return {
        "class": ImageUploadPreview(
            **{("custom_class" if k == "class" else k): v for k, v in kw.items()}
        ).render(),
        "tag": _tag("{% image_upload_preview " + tag_args + " %}", **kw),
        "handler": str(rh.ImageUploadPreviewHandler().render([f"{k}={k}" for k in kw], dict(kw))),
    }


def _attr(markup: str, name: str) -> str | None:
    found = re.search(rf' {name}="([^"]*)"', markup)
    return html.unescape(found.group(1)) if found else None


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _parse(markup: str) -> list[tuple[str, dict]]:
    parser = _Collect()
    parser.feed(markup)
    return parser.tags


class TestMarkup:
    def test_the_legacy_markup_is_unchanged_apart_from_the_additions(self):
        for path, markup in _paths().items():
            stripped = ADDED.sub("", markup).replace(ITEMS_HTML, "")
            svg = re.search(r"<svg.*?</svg>", markup).group(0)
            assert stripped == LEGACY.format(svg=svg), path

    def test_every_path_renders_the_two_client_owned_elements_empty(self):
        for path, markup in _paths().items():
            assert ITEMS_HTML in markup, path
            assert markup.endswith(ITEMS_HTML + "</div>"), path

    def test_all_three_paths_are_identical_for_any_arguments(self):
        for kw in (
            {},
            {"upload": "photos", "max_size": 5_000_000, "event": "photos_uploaded"},
            {"upload": "a b", "max_size": "12", "name": "gallery", "max": 3},
            {"upload": HOSTILE, "max_size": HOSTILE, "event": HOSTILE, "name": HOSTILE},
            {"previews": ["/a.png", "https://x.example/b.png"]},
        ):
            paths = _paths(**kw)
            assert paths["class"] == paths["tag"] == paths["handler"], kw

    def test_an_upload_slot_wires_djusts_own_directives(self):
        for path, markup in _paths(upload="photos", max_size=2048).items():
            tags = _parse(markup)
            by_class = {a.get("class", ""): (t, a) for t, a in tags}
            assert by_class["dj-img-upload"][1]["data-upload"] == "photos", path
            assert by_class["dj-img-upload"][1]["data-max-size"] == "2048", path
            assert by_class["dj-img-upload__dropzone"][1]["dj-upload-drop"] == "photos", path
            input_attrs = by_class["dj-img-upload__input"][1]
            assert input_attrs["dj-upload"] == "photos", path
            assert input_attrs["type"] == "file", path

    def test_without_a_slot_it_is_a_plain_input_with_previews(self):
        for path, markup in _paths(max_size=100).items():
            assert "dj-upload" not in markup, path
            assert "data-upload" not in markup, path
            assert _attr(markup, "data-max-size") == "100", path

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_a_blank_slot_is_no_slot(self, blank):
        assert "dj-upload" not in ImageUploadPreview(upload=blank).render()

    @pytest.mark.parametrize("bad", [0, -5, "x", None, float("nan"), 1e999])
    def test_a_size_that_is_not_a_positive_integer_is_no_pre_check(self, bad):
        assert "data-max-size" not in ImageUploadPreview(max_size=bad).render()

    def test_the_hook_only_notifies_for_an_event_the_app_named(self):
        for path, markup in _paths().items():
            assert "data-notify" not in markup, path
            assert _attr(markup, "data-event") == "upload", path  # unchanged default
        for path, markup in _paths(event="done").items():
            assert _attr(markup, "data-notify") == "true", path
            assert _attr(markup, "data-event") == "done", path

    def test_nothing_a_caller_passes_breaks_out_of_an_attribute(self):
        for path, markup in _paths(
            upload=HOSTILE, max_size=HOSTILE, event=HOSTILE, name=HOSTILE, accept=HOSTILE
        ).items():
            for _tag_name, attrs in _parse(markup):
                assert not [k for k in attrs if k.startswith("on")], path
            assert "<img" not in re.sub(
                r"<img[^>]*>(?=</div>)", "", markup.split("dj-img-upload__items")[0]
            ), path

    def test_the_slot_round_trips_as_text(self):
        slot = 'a"b<c>&d'
        for path, markup in _paths(upload=slot).items():
            assert _attr(markup, "dj-upload") == slot, path
            assert _attr(markup, "dj-upload-drop") == slot, path
            assert _attr(markup, "data-upload") == slot, path


class TestScriptStylesAndCatalogue:
    SOURCE = (STATIC / "image-upload-preview.js").read_text()
    CSS = (STATIC / "components.css").read_text()

    def test_the_catalogue_finds_the_script_and_the_hook(self):
        from djust.theming.gallery.component_registry import component_client

        assert component_client("image_upload_preview") == {
            "hook": "ImageUploadPreview",
            "script": "djust_components/image-upload-preview.js",
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
            r"FileReader",
            r"readAsDataURL",
            r"new WebSocket",
            r"XMLHttpRequest",
            r"\bfetch\s*\(",
        ],
    )
    def test_script_parses_no_markup_reads_no_file_and_opens_no_transport(self, banned):
        assert not re.search(banned, self.SOURCE), banned

    def test_script_sends_files_only_through_djusts_pipeline(self):
        # No upload frame of its own: it asks djust's client to cancel, nothing else.
        assert "uploads.cancelUpload" in self.SOURCE
        assert "uploadFile" not in self.SOURCE
        assert "upload_register" not in self.SOURCE

    def test_script_registers_without_replacing_an_app_hook(self):
        assert (
            "!appHooks.ImageUploadPreview && !window.DjustHooks.ImageUploadPreview" in self.SOURCE
        )

    def test_the_file_input_stays_focusable(self):
        """display:none took the picker out of the tab order; the input is now
        visually hidden but a real, focusable control, and the zone shows focus."""
        rule = re.search(r"\.dj-img-upload__input \{([^}]*)\}", self.CSS).group(1)
        assert "display: none" not in rule and "visibility: hidden" not in rule
        assert ".dj-img-upload__dropzone:focus-within" in self.CSS

    def test_every_class_the_hook_creates_has_a_rule(self):
        for cls in re.findall(r'"(dj-img-upload__[a-z-]+)"', self.SOURCE):
            assert f".{cls}" in self.CSS, cls
