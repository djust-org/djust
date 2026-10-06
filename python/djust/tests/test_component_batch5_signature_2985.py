"""SignaturePad ships its interaction (#2985, batch 5).

What has to hold on every render path (the component class, the Django template
tag and the Rust-engine handler): the structure the hook reads is emitted, the
additions are identical everywhere, the markup the component always had is
unchanged apart from them, and nothing a caller passes can break out of an
attribute.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template import Context, Template

from djust.components import rust_handlers as rh
from djust.components.components.signature_pad import SignaturePad, pad_extras

STATIC = Path(rh.__file__).resolve().parent / "static" / "djust_components"
HOSTILE = "<img src=x onerror=alert(1)>\"'&<"

STRIP = [
    re.compile(r' data-max-bytes="[^"]*"'),
    re.compile(r' data-typed="false"'),
    re.compile(r' role="img" aria-label="[^"]*"'),
    re.compile(r'<div class="dj-signature-pad__typed" hidden>.*?</div>'),
    re.compile(r'<button class="dj-signature-pad__undo-btn" type="button">Undo</button>'),
    re.compile(r'<button class="dj-signature-pad__mode-btn"[^>]*>[^<]*</button>'),
    re.compile(r'<div class="dj-signature-pad__status"[^>]*></div>'),
]
LEGACY = (
    '<div class="dj-signature-pad" dj-hook="SignaturePad" data-save-event="save_signature" '
    'data-pen-color="#000000" data-pen-width="2"><canvas class="dj-signature-pad__canvas" '
    'width="400" height="200"></canvas><input type="hidden" name="signature" '
    'class="dj-signature-pad__value"><div class="dj-signature-pad__actions">'
    '<button class="dj-signature-pad__clear-btn" type="button">Clear</button>'
    '<button class="dj-signature-pad__save-btn" type="button">Save</button></div></div>'
)


def _tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


def _paths(**kw) -> dict[str, str]:
    tag_args = " ".join(f"{k}={k}" for k in kw)
    return {
        "class": SignaturePad(
            **{("custom_class" if k == "class" else k): v for k, v in kw.items()}
        ).render(),
        "tag": _tag("{% signature_pad " + tag_args + " %}", **kw),
        "handler": str(rh.SignaturePadHandler().render([f"{k}={k}" for k in kw], dict(kw))),
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


def _by_class(markup: str) -> dict[str, dict]:
    return {a.get("class", ""): a for _t, a in _parse(markup)}


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
            {"max_bytes": 50_000, "label": "Sign here", "typed": False},
            {"label": HOSTILE, "name": HOSTILE, "pen_color": HOSTILE, "max_bytes": HOSTILE},
            {"disabled": True, "width": 600, "height": 120, "pen_width": 3},
        ):
            paths = _paths(**kw)
            assert paths["class"] == paths["tag"] == paths["handler"], kw

    def test_the_hook_finds_every_control(self):
        for path, markup in _paths().items():
            classes = _by_class(markup)
            for cls in (
                "dj-signature-pad__canvas",
                "dj-signature-pad__value",
                "dj-signature-pad__typed",
                "dj-signature-pad__typed-input",
                "dj-signature-pad__actions",
                "dj-signature-pad__clear-btn",
                "dj-signature-pad__undo-btn",
                "dj-signature-pad__mode-btn",
                "dj-signature-pad__save-btn",
                "dj-signature-pad__status",
            ):
                assert cls in classes, (path, cls)
            assert "hidden" in classes["dj-signature-pad__typed"], path
            assert classes["dj-signature-pad__status"]["role"] == "status", path
            assert classes["dj-signature-pad__canvas"]["role"] == "img", path

    def test_the_canvas_is_named_and_the_name_is_a_kwarg(self):
        for path, markup in _paths().items():
            assert (
                _by_class(markup)["dj-signature-pad__canvas"]["aria-label"]
                == "Signature drawing area"
            ), path
        for path, markup in _paths(label=HOSTILE).items():
            assert _by_class(markup)["dj-signature-pad__canvas"]["aria-label"] == HOSTILE, path
        for blank in ("", "  ", None):
            assert (
                _by_class(SignaturePad(label=blank).render())["dj-signature-pad__canvas"][
                    "aria-label"
                ]
                == "Signature drawing area"
            )

    def test_typed_can_be_turned_off(self):
        for path, markup in _paths(typed=False).items():
            assert "dj-signature-pad__typed" not in markup, path
            assert "mode-btn" not in markup, path
            assert 'data-typed="false"' in markup, path
        for off in ("false", "False", 0, "0", ""):
            assert "mode-btn" not in SignaturePad(typed=off).render(), off

    @pytest.mark.parametrize(
        "given,expected",
        [
            (204800, "204800"),
            (50_000, "50000"),
            (1, "1024"),
            (-5, "1024"),
            ("x", "204800"),
            (None, "204800"),
            (1e999, "204800"),
        ],
    )
    def test_max_bytes_is_a_sane_integer(self, given, expected):
        assert pad_extras(given).root == f' data-max-bytes="{expected}"'

    def test_disabled_state_is_carried_as_before(self):
        for path, markup in _paths(disabled=True).items():
            tags = _parse(markup)
            root = tags[0][1]
            assert "dj-signature-pad--disabled" in root["class"], path
            by_class = _by_class(markup)
            assert "disabled" in by_class["dj-signature-pad__canvas"], path
            assert "disabled" in by_class["dj-signature-pad__save-btn"], path
            assert "disabled" not in by_class["dj-signature-pad__clear-btn"], path

    def test_nothing_a_caller_passes_breaks_out_of_an_attribute(self):
        for path, markup in _paths(
            label=HOSTILE, name=HOSTILE, pen_color=HOSTILE, save_event=HOSTILE, custom_class=HOSTILE
        ).items():
            tags = _parse(markup)
            assert not [k for _t, a in tags for k in a if k.startswith("on")], path
            assert "img" not in [t for t, _a in tags], path

    def test_the_label_round_trips_as_text(self):
        label = 'a"b<c>&d'
        for path, markup in _paths(label=label).items():
            assert html.unescape(re.search(r'aria-label="([^"]*)"', markup).group(1)) == label, path


class TestScriptStylesAndCatalogue:
    SOURCE = (STATIC / "signature-pad.js").read_text()
    CSS = (STATIC / "components.css").read_text()

    def test_the_catalogue_finds_the_script_and_the_hook(self):
        from djust.theming.gallery.component_registry import component_client

        assert component_client("signature_pad") == {
            "hook": "SignaturePad",
            "script": "djust_components/signature-pad.js",
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
            r"createElement\(\"(?!canvas)",
            r"\.src\s*=",
            r"new Image",
            r"\bfetch\s*\(",
            r"XMLHttpRequest",
            r"localStorage",
        ],
    )
    def test_script_parses_no_markup_loads_nothing_and_stores_nothing(self, banned):
        assert not re.search(banned, self.SOURCE), banned

    def test_script_registers_without_replacing_an_app_hook(self):
        assert "!appHooks.SignaturePad && !window.DjustHooks.SignaturePad" in self.SOURCE

    def test_the_canvas_does_not_scroll_the_page_while_drawing(self):
        rule = re.search(r"\.dj-signature-pad__canvas \{([^}]*)\}", self.CSS).group(1)
        assert "touch-action: none" in rule
        assert "user-select: none" in rule

    def test_every_class_the_hook_names_has_a_rule(self):
        for cls in set(re.findall(r'"(dj-signature-pad__[a-z-]+)"', self.SOURCE)):
            assert f".{cls}" in self.CSS, cls
        for cls in set(re.findall(r'"dj-signature-pad__([a-z-]+)"', self.SOURCE)):
            assert f".dj-signature-pad__{cls}" in self.CSS, cls
