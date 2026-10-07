"""VoiceInput ships its interaction, with its privacy disclosure (#2985, batch 5).

What has to hold on every render path (the component class, the Django template
tag and the Rust-engine handler): the button the component always rendered is
still there byte for byte, the disclosure, interim and status elements are
emitted identically, the disclosure cannot be removed, and nothing a caller
passes can break out of an attribute or into markup.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template import Context, Template

from djust.components import rust_handlers as rh
from djust.components.components.voice_input import (
    DEFAULT_DISCLOSURE,
    VoiceInput,
    clean_transcript,
    voice_extras,
)

STATIC = Path(rh.__file__).resolve().parent / "static" / "djust_components"
HOSTILE = "<img src=x onerror=alert(1)>\"'&<"

BUTTON = (
    '<button type="button" class="dj-voice-input" dj-hook="VoiceInput" data-event="transcribe" '
    'data-lang="en-US" data-continuous="false" aria-label="Voice input" aria-pressed="false">'
    '<svg class="dj-voice-input__icon" viewBox="0 0 24 24" width="20" height="20" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>'
    '<path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/>'
    '<line x1="8" y1="23" x2="16" y2="23"/></svg><span class="dj-voice-input__pulse"></span></button>'
)


def _tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


def _paths(**kw) -> dict[str, str]:
    tag_args = " ".join(f"{k}={k}" for k in kw)
    return {
        "class": VoiceInput(
            **{("custom_class" if k == "class" else k): v for k, v in kw.items()}
        ).render(),
        "tag": _tag("{% voice_input " + tag_args + " %}", **kw),
        "handler": str(rh.VoiceInputHandler().render([f"{k}={k}" for k in kw], dict(kw))),
    }


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.text: dict[str, str] = {}
        self._stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        self._stack.append(attrs.get("class", ""))

    def handle_endtag(self, tag):
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._stack and self._stack[-1]:
            self.text[self._stack[-1]] = self.text.get(self._stack[-1], "") + data


def _parse(markup: str) -> _Collect:
    parser = _Collect()
    parser.feed(markup)
    return parser


class TestMarkup:
    def test_the_button_is_unchanged_inside_a_wrapper(self):
        for path, markup in _paths().items():
            inner = markup.removeprefix('<span class="dj-voice-field">')
            inner = re.sub(r' data-max-seconds="\d+"', "", inner)
            assert inner.startswith(BUTTON), path
            assert markup.startswith('<span class="dj-voice-field">') and markup.endswith(
                "</span>"
            ), path

    def test_the_disclosure_interim_and_status_elements_follow_the_button(self):
        for path, markup in _paths().items():
            tail = markup.split("</button>", 1)[1]
            assert tail.startswith('<span class="dj-voice-input__disclosure">'), path
            parser = _parse(markup)
            by_class = {a.get("class", ""): a for _t, a in parser.tags}
            assert by_class["dj-voice-input__status"]["role"] == "status", path
            assert by_class["dj-voice-input__status"]["aria-live"] == "polite", path
            assert by_class["dj-voice-input__interim"]["aria-hidden"] == "true", path

    def test_all_three_paths_are_identical_for_any_arguments(self):
        for kw in (
            {},
            {"disclosure": "Custom privacy text.", "max_seconds": 20},
            {"disclosure": HOSTILE, "event": HOSTILE, "lang": HOSTILE, "max_seconds": HOSTILE},
            {"continuous": True, "class": "extra", "lang": "fr-CA"},
        ):
            paths = _paths(**kw)
            assert paths["class"] == paths["tag"] == paths["handler"], kw

    def test_the_default_disclosure_names_the_vendor_and_limits_its_promise_to_the_component(self):
        text = _parse(VoiceInput().render()).text["dj-voice-input__disclosure"]
        assert text == DEFAULT_DISCLOSURE
        for needle in (
            "Chrome",
            "Edge",
            "Google",
            "Microsoft",
            "This component does not record or store audio",
        ):
            assert needle in DEFAULT_DISCLOSURE, needle

    def test_the_disclosure_can_be_reworded_but_not_removed(self):
        for path, markup in _paths(disclosure="Nous envoyons du texte, pas de son.").items():
            assert (
                _parse(markup).text["dj-voice-input__disclosure"]
                == "Nous envoyons du texte, pas de son."
            ), path
        for blank in ("", "   ", None):
            assert (
                _parse(VoiceInput(disclosure=blank).render()).text["dj-voice-input__disclosure"]
                == DEFAULT_DISCLOSURE
            )

    def test_the_disclosure_is_text_only(self):
        for path, markup in _paths(disclosure=HOSTILE).items():
            parser = _parse(markup)
            assert [t for t, _a in parser.tags].count("img") == 0, path
            assert parser.text["dj-voice-input__disclosure"] == HOSTILE, path

    @pytest.mark.parametrize(
        "given,expected",
        [
            (60, "60"),
            (1, "1"),
            (0, "1"),
            (-5, "1"),
            (9999, "600"),
            ("x", "60"),
            (None, "60"),
            (1e999, "60"),
            ("30", "30"),
        ],
    )
    def test_max_seconds_is_a_sane_integer(self, given, expected):
        assert voice_extras(None, given).button_attrs == f' data-max-seconds="{expected}"'

    def test_nothing_a_caller_passes_breaks_out_of_an_attribute(self):
        for path, markup in _paths(
            event=HOSTILE, lang=HOSTILE, custom_class=HOSTILE, disclosure=HOSTILE
        ).items():
            tags = _parse(markup).tags
            assert not [k for _t, a in tags for k in a if k.startswith("on")], path

    def test_the_language_round_trips_as_text(self):
        for path, markup in _paths(lang='a"b<c>').items():
            assert html.unescape(re.search(r'data-lang="([^"]*)"', markup).group(1)) == 'a"b<c>', (
                path
            )


class TestCleanTranscript:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("  hello   world  ", "hello world"),
            ("a\tb\nc\r\nd", "a b c d"),
            ("hello‮world", "hello world"),  # a bidi override
            ("a​b‏c﻿d", "a b c d"),
            ("nul\x00byte\x1b[31m", "nul byte [31m"),
            ("line sep para", "line sep para"),
            ("\x85next\x9f", "next"),
            ("héllo wörld 日本語", "héllo wörld 日本語"),
            ("", ""),
        ],
    )
    def test_removes_what_a_transcript_has_no_use_for(self, raw, expected):
        assert clean_transcript(raw) == expected

    @pytest.mark.parametrize("value", [None, 5, 1.5, b"bytes", ["a"], {"a": 1}, True])
    def test_anything_but_a_string_is_empty(self, value):
        assert clean_transcript(value) == ""

    def test_the_length_is_capped_before_the_work_is_done(self):
        assert len(clean_transcript("x" * 100_000)) == 2000
        assert clean_transcript("hello world", max_length=5) == "hello"
        assert clean_transcript("hello", max_length=0) == ""
        assert len(clean_transcript(" " * 100_000 + "text")) <= 2000

    def test_no_markup_is_interpreted(self):
        assert clean_transcript("<b>x</b> & <script>") == "<b>x</b> & <script>"


class TestScriptStylesAndCatalogue:
    SOURCE = (STATIC / "voice-input.js").read_text()
    # What the script does, not what its comments say it does not do.
    CODE = re.sub(r"//[^\n]*|/\*.*?\*/", "", SOURCE, flags=re.S)
    CSS = (STATIC / "components.css").read_text()

    def test_the_catalogue_finds_the_script_and_the_hook(self):
        from djust.theming.gallery.component_registry import component_client

        assert component_client("voice_input") == {
            "hook": "VoiceInput",
            "script": "djust_components/voice-input.js",
            "hook_shipped": True,
        }

    @pytest.mark.parametrize(
        "banned",
        [
            r"getUserMedia",
            r"MediaRecorder",
            r"AudioContext",
            r"webkitAudioContext",
            r"\bBlob\b",
            r"indexedDB",
            r"localStorage",
            r"sessionStorage",
            r"document\.cookie",
            r"\bfetch\s*\(",
            r"XMLHttpRequest",
            r"new WebSocket",
            r"sendBeacon",
            r"\binnerHTML\b",
            r"\bouterHTML\b",
            r"insertAdjacentHTML",
            r"\beval\s*\(",
            r"new Function",
            r"document\.write",
            r"createElement",
            r"\.start\(\)\s*;?\s*\n\s*\}\s*,\s*mounted",
        ],
    )
    def test_script_records_nothing_stores_nothing_and_sends_nothing_itself(self, banned):
        assert not re.search(banned, self.CODE), banned

    def test_script_registers_without_replacing_an_app_hook(self):
        assert "!appHooks.VoiceInput && !window.DjustHooks.VoiceInput" in self.SOURCE

    def test_recognition_is_started_in_exactly_one_place_the_press(self):
        assert len(re.findall(r"\.start\(\)", self.CODE)) == 1
        start = self.CODE.index("rec.start()")
        assert self.CODE.rindex("_start: function", 0, start) > self.CODE.rindex(
            "_press: function", 0, start
        )
        # ... and the only caller of _start() is the press.
        assert len(re.findall(r"this\._start\(\)", self.CODE)) == 1

    def test_the_microphone_button_shows_focus_and_disabled_states(self):
        assert ".dj-voice-input:focus-visible" in self.CSS
        assert ".dj-voice-input:disabled" in self.CSS

    def test_the_pulse_respects_reduced_motion_and_comes_after_the_animation_rule(self):
        media = self.CSS.index("prefers-reduced-motion: reduce) { .dj-voice-input[aria-pressed")
        animation = self.CSS.index(
            '.dj-voice-input[aria-pressed="true"] .dj-voice-input__pulse { animation: dj-voice-pulse'
        )
        assert media > animation

    def test_every_class_the_hook_names_has_a_rule(self):
        for cls in set(re.findall(r'"(dj-voice-(?:input__[a-z]+|field))"', self.CODE)):
            assert f".{cls}" in self.CSS, cls
        for cls in ("disclosure", "interim", "status"):
            assert f".dj-voice-input__{cls}" in self.CSS
        assert ".dj-voice-field" in self.CSS


def test_clean_transcript_preserves_astral_text_and_removes_unpaired_surrogates():
    assert clean_transcript("x" * 1999 + "😀") == "x" * 1999 + "😀"
    assert clean_transcript("a" + chr(0xD800) + "b") == "a b"
