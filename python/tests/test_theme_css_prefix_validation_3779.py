"""``LIVEVIEW_CONFIG['theme']['css_prefix']`` is validated where it is read (#3779).

``get_css_prefix()`` handed the raw config string to ``generate_component_css`` /
``generate_css_for_state`` / the critical-CSS split, which print it into ``.{prefix}btn { ... }``
inside ``<style>`` blocks that ``theme_head.html`` renders under ``{% autoescape off %}``.
``LIVEVIEW_CONFIG={"theme": {"css_prefix": 'x</style><img src=x onerror=alert(1)>'}}`` printed a
parsed ``<img onerror>`` element in ``{% theme_head %}``, ``{% theme_css %}`` and
``{{ theme_head }}`` (the context-processor path). It is settings-level, not request-reachable.

The cure is one validator in ``get_theme_config()`` (``[A-Za-z0-9_-]*``, the same place
``cookie_namespace`` is checked), so every reader of the config is covered, plus the same validator
at the three generators that also take a prefix as an ARGUMENT (a caller that bypasses the config).

What each class proves:

* ``TestConfigLoad`` the validator itself, including the newline that ``re.match(..."$")`` lets by.
* ``TestHostilePrefixThroughTheTags`` drives the real tags through BOTH engines and reads what
  the browser would parse (``html.parser``), never the string. A tag that does not read the prefix
  passes trivially, so the tags that DO read it are asserted to refuse.
* ``TestContextProcessor`` ``{{ theme_head }}`` is a lazy chunk built from a process-wide
  ``lru_cache`` that is keyed WITHOUT the prefix: each test clears it, otherwise a benign render
  left by an earlier test would answer for the hostile config and the test would pass for nothing.
* ``TestGeneratorArguments`` the three direct-argument entry points and the CSS views.
* ``TestBenignPrefixesAreUnchanged`` hashes of 43 renderings (prefix x tag x engine), captured from
  the code BEFORE the change (``fixtures/theme_css_prefix_golden_3779.json``).

Refs #3779, #3766, #3775, #1646, #1468.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.template import engines
from django.test import RequestFactory, override_settings

pytestmark = pytest.mark.theming

FIXTURES = Path(__file__).parent / "fixtures"

#: The reviewer's measurement (#3779).
ISSUE_PAYLOAD = "x</style><img src=x onerror=alert(1)>"

#: Each is refused. The mix covers closing the block, leaving the selector, quoting out of an
#: attribute (the prefix is also printed into ``class="..."``), and the representations a
#: ``re.match(r"^...$")`` or a letters-only check would miss.
HOSTILE = {
    "issue": ISSUE_PAYLOAD,
    "close-style": "x</style>",
    "close-brace": "x}</style><script>alert(1)</script>{",
    "double-quote": 'x" onfocus="alert(1)',
    "single-quote": "x' onfocus='alert(1)",
    "space": "x y",
    "dot": ".x",
    "semicolon": "x;color:red",
    "comment": "x/*",
    "entity": "x&lt;/style&gt;",
    "trailing-newline": "dj-\n",
    "leading-newline": "\ndj-",
    "unicode-hyphen": "dj–",
    "unicode-letter": "dј-",
    "nul": "dj-\x00",
}

#: Accepted, and printed as before. ``dj`` has no trailing hyphen (a W002 warning, not an error).
BENIGN = ["", "dj-", "dj", "djt-", "myapp-", "my_app-", "App2-", "-", "_"]


def _tags(html: str) -> list[tuple[str, dict[str, str | None]]]:
    class _Tags(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.tags: list[tuple[str, dict[str, str | None]]] = []

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))

        handle_startendtag = handle_starttag

    parser = _Tags()
    parser.feed(html)
    parser.close()
    return parser.tags


def _breakout_findings(html: str, baseline: str) -> list[str]:
    """Tags and ``on*`` attributes in ``html`` that the same page with no prefix does not have.

    ``theme_head`` prints its own ``<script>`` and ``<style>`` tags, so a breakout is whatever a
    render with ``css_prefix=""`` lacks: an ``<img>``, an injected ``<script>``, an ``on*`` handler.
    """
    have = Counter(_shapes(baseline))
    return sorted((Counter(_shapes(html)) - have).elements())


def _shapes(html: str) -> list[str]:
    shapes = []
    for tag, attrs in _tags(html):
        shapes.append(tag)
        shapes += [f"{tag}[{name}]" for name in attrs if name.startswith("on")]
    return shapes


@pytest.fixture(autouse=True)
def _fresh_theme_caches():
    """The CSS and the context-processor chunk are cached by arguments that exclude the config."""
    from djust.theming.context_processors import clear_theme_context_cache
    from djust.theming.cache import clear_css_cache

    clear_theme_context_cache()
    clear_css_cache()
    yield
    clear_theme_context_cache()
    clear_css_cache()


@pytest.fixture(scope="module", params=["django", "djust"])
def engine(request):
    if request.param == "django":
        return engines["django"]
    from djust.template import DjustTemplateBackend

    return DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


def _config(prefix):
    return override_settings(LIVEVIEW_CONFIG={"theme": {"css_prefix": prefix}})


class _F(forms.Form):
    name = forms.CharField()


def _bound_form() -> forms.Form:
    form = _F(data={"name": ""})
    form.full_clean()
    form.add_error(None, "Non-field problem")
    return form


class TestConfigLoad:
    @pytest.mark.parametrize("label", sorted(HOSTILE))
    def test_a_hostile_prefix_is_refused_when_the_config_is_read(self, label):
        from djust.theming._config import get_theme_config

        with _config(HOSTILE[label]), pytest.raises(ImproperlyConfigured) as exc:
            get_theme_config()
        assert "css_prefix" in str(exc.value)

    @pytest.mark.parametrize("value", [123, 1.5, True, ["dj-"], ("dj-",), {"a": "b"}, b"dj-"])
    def test_a_prefix_that_is_not_a_string_is_refused(self, value):
        from djust.theming._config import get_theme_config

        with _config(value), pytest.raises(ImproperlyConfigured):
            get_theme_config()

    @pytest.mark.parametrize("prefix", BENIGN)
    def test_a_benign_prefix_is_returned_unchanged(self, prefix):
        from djust.theming._config import get_theme_config
        from djust.theming.manager import get_css_prefix

        with _config(prefix):
            assert get_theme_config()["css_prefix"] == prefix
            assert get_css_prefix() == prefix

    def test_unset_and_none_still_load(self):
        from djust.theming._config import get_theme_config

        with override_settings(LIVEVIEW_CONFIG={}):
            assert get_theme_config()["css_prefix"] == ""
        with _config(None):
            assert get_theme_config()["css_prefix"] is None

    def test_the_message_names_the_setting_and_the_allowed_characters(self):
        from djust.theming._config import get_theme_config

        with _config(ISSUE_PAYLOAD), pytest.raises(ImproperlyConfigured) as exc:
            get_theme_config()
        message = str(exc.value)
        assert "LIVEVIEW_CONFIG['theme']['css_prefix']" in message
        assert "letters, digits, underscores" in message


#: Every tag here prints the configured prefix, so each must REFUSE a hostile one.
TAGS = {
    "theme_head": "{% load theme_tags %}{% theme_head %}",
    "theme_head-link-css": "{% load theme_tags %}{% theme_head link_css=True %}",
    "theme_head-no-js": "{% load theme_tags %}{% theme_head include_js=False %}",
    "theme_css": "{% load theme_tags %}{% theme_css %}",
    "theme_form": "{% load theme_form_tags %}{% theme_form form %}",
    "theme_form_errors": "{% load theme_form_tags %}{% theme_form_errors form %}",
    "get_css_prefix": "{% load theme_form_tags %}{% get_css_prefix as p %}[{{ p }}]",
    "theme_button": '{% load theme_components %}{% theme_button "Go" %}',
    "theme_alert": '{% load theme_components %}{% theme_alert "Careful" %}',
    "theme_login_page": "{% load theme_pages %}{% theme_login_page %}",
}


class TestHostilePrefixThroughTheTags:
    @pytest.mark.parametrize("name", sorted(TAGS))
    def test_a_tag_refuses_the_issue_payload(self, engine, name):
        with _config(ISSUE_PAYLOAD), pytest.raises(Exception) as exc:  # noqa: B017
            engine.from_string(TAGS[name]).render({"form": _bound_form()})
        assert isinstance(exc.value, ImproperlyConfigured) or "css_prefix" in str(exc.value), repr(
            exc.value
        )

    @pytest.mark.parametrize("label", sorted(HOSTILE))
    @pytest.mark.parametrize("name", ["theme_head", "theme_css"])
    def test_theme_head_and_theme_css_never_parse_an_element_out_of_the_prefix(
        self, engine, name, label
    ):
        with _config(""):
            baseline = engine.from_string(TAGS[name]).render({})
        with _config(HOSTILE[label]):
            try:
                html = engine.from_string(TAGS[name]).render({})
            except Exception:  # noqa: BLE001 - refusing is the expected outcome
                return
        assert _breakout_findings(html, baseline) == [], html


class TestContextProcessor:
    @staticmethod
    def _chunks(prefix):
        from djust.theming.context_processors import theme_context

        request = RequestFactory().get("/")
        with _config(prefix):
            ctx = theme_context(request)
            return {key: str(ctx[key]) for key in ("theme_head", "theme_switcher")}

    def test_the_lazy_theme_head_chunk_refuses_the_issue_payload(self):
        with pytest.raises(ImproperlyConfigured):
            self._chunks(ISSUE_PAYLOAD)

    @pytest.mark.parametrize("label", sorted(HOSTILE))
    def test_no_hostile_prefix_reaches_the_chunk(self, label):
        try:
            chunks = self._chunks(HOSTILE[label])
        except ImproperlyConfigured:
            return
        assert _breakout_findings(chunks["theme_head"], self._chunks("")["theme_head"]) == []

    def test_a_refused_prefix_is_not_cached_for_the_next_render(self):
        """The cache key excludes the prefix; the refusal must not poison it, nor a benign hit mask it."""
        benign = self._chunks("dj-")["theme_head"]
        assert ".dj-btn" in benign  # the prefix is printed into the chunk: what to guard
        from djust.theming.context_processors import clear_theme_context_cache

        clear_theme_context_cache()
        with pytest.raises(ImproperlyConfigured):
            self._chunks(ISSUE_PAYLOAD)
        assert self._chunks("dj-")["theme_head"] == benign


class TestGeneratorArguments:
    """The entry points that take a prefix as an ARGUMENT, bypassing the config (#1646)."""

    @pytest.mark.parametrize("label", sorted(HOSTILE))
    def test_generate_component_css(self, label):
        from djust.theming.component_css_generator import generate_component_css

        with pytest.raises(ImproperlyConfigured):
            generate_component_css(HOSTILE[label])

    @pytest.mark.parametrize("label", sorted(HOSTILE))
    def test_complete_theme_css_generator(self, label):
        from djust.theming.theme_css_generator import CompleteThemeCSSGenerator

        with pytest.raises(ImproperlyConfigured):
            CompleteThemeCSSGenerator("material", "default", css_prefix=HOSTILE[label])

    def test_the_three_state_level_functions_and_the_cached_wrapper(self):
        from djust.theming.manager import (
            ThemeState,
            generate_critical_css_for_state,
            generate_css_for_state,
            generate_deferred_css_for_state,
        )
        from djust.theming.theme_css_generator import generate_theme_css

        state = ThemeState(
            theme="material",
            preset="default",
            mode="light",
            resolved_mode="light",
            pack=None,
            layout="",
        )
        for fn in (
            generate_css_for_state,
            generate_critical_css_for_state,
            generate_deferred_css_for_state,
        ):
            with pytest.raises(ImproperlyConfigured):
                fn(state, css_prefix=ISSUE_PAYLOAD)
        with pytest.raises(ImproperlyConfigured):
            generate_theme_css("material", "default", css_prefix=ISSUE_PAYLOAD)

    @pytest.mark.parametrize("label", ["issue", "close-style", "trailing-newline"])
    def test_design_system_css_generator(self, label):
        from djust.theming.design_system_css import DesignSystemCSSGenerator

        with pytest.raises(ImproperlyConfigured):
            DesignSystemCSSGenerator("material", css_prefix=HOSTILE[label])

    def test_a_refusal_is_not_remembered_by_the_lru_cache(self):
        from djust.theming.component_css_generator import generate_component_css

        for _ in range(2):
            with pytest.raises(ImproperlyConfigured):
                generate_component_css(ISSUE_PAYLOAD)
        assert ".dj-btn" in generate_component_css("dj-")

    @pytest.mark.parametrize("view", ["theme_css_view", "deferred_theme_css_view"])
    def test_the_css_views_refuse_the_issue_payload(self, view):
        from djust.theming import views

        request = RequestFactory().get("/theme.css")
        with _config(ISSUE_PAYLOAD), pytest.raises(ImproperlyConfigured):
            getattr(views, view)(request)

    def test_benign_arguments_still_work(self):
        from djust.theming.component_css_generator import generate_component_css
        from djust.theming.design_system_css import DesignSystemCSSGenerator
        from djust.theming.theme_css_generator import CompleteThemeCSSGenerator

        assert ".dj-btn" in generate_component_css("dj-")
        assert ".dj-btn" in CompleteThemeCSSGenerator("material", css_prefix="dj-").generate_css()
        assert (
            DesignSystemCSSGenerator("material", css_prefix="dj")._get_css_var_name("x") == "--dj-x"
        )


class TestSystemCheckStillSeesTheConfig:
    """``manage.py check`` must report a bad prefix, not die in ``get_theme_config``."""

    def test_check_css_prefix_reports_a_prefix_the_loader_refuses(self):
        from djust.theming.checks import check_css_prefix

        with _config(ISSUE_PAYLOAD):
            messages = check_css_prefix(app_configs=None)
        assert [m.id for m in messages] == ["djust_theming.E004"]

    def test_check_css_prefix_rejects_a_trailing_newline_that_dollar_let_through(self):
        """The check used ``re.match(r"^...$")``; ``$`` also matches before a final newline."""
        from djust.theming.checks import check_css_prefix

        with _config("dj-\n"):
            messages = check_css_prefix(app_configs=None)
        assert [m.id for m in messages] == ["djust_theming.E004"]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


#: (key, source) rendered once per prefix and engine for the golden.
GOLDEN_SOURCES = {
    "theme_head": TAGS["theme_head"],
    "theme_head-link": TAGS["theme_head-link-css"],
    "theme_css": TAGS["theme_css"],
    "theme_form": TAGS["theme_form"] + "|{% theme_form_errors form %}",
    "theme_button": TAGS["theme_button"],
    "get_css_prefix": TAGS["get_css_prefix"],
    "theme_login_page": TAGS["theme_login_page"],
}


def _normalise(html: str) -> str:
    """``?v=<hex>`` is the newest mtime of the theme's static files: it differs per checkout."""
    return re.sub(r"\?v=[0-9a-f]+", "?v=VERSION", html)


def render_golden_case(engine, prefix: str, key: str) -> str:
    """One rendering of the golden. Shared with the script that captured it BEFORE the change."""
    from djust.theming.context_processors import clear_theme_context_cache
    from djust.theming.cache import clear_css_cache

    clear_theme_context_cache()
    clear_css_cache()
    with _config(prefix):
        html = engine.from_string(GOLDEN_SOURCES[key]).render({"form": _bound_form()})
    return _normalise(html)


class TestBenignPrefixesAreUnchanged:
    GOLDEN = json.loads((FIXTURES / "theme_css_prefix_golden_3779.json").read_text())

    @pytest.mark.parametrize("key", sorted(GOLDEN_SOURCES))
    @pytest.mark.parametrize("prefix", ["", "dj-", "dj"])
    def test_byte_for_byte_what_the_code_before_the_change_printed(self, engine, prefix, key):
        engine_name = "django" if engine is engines["django"] else "djust"
        html = render_golden_case(engine, prefix, key)
        want = self.GOLDEN[f"{engine_name}|{prefix}|{key}"]
        assert (len(html), _digest(html)) == (want["len"], want["sha256"])

    def test_the_context_processor_chunk_is_unchanged(self):
        want = self.GOLDEN["context|dj-"]
        got = _normalise(TestContextProcessor._chunks("dj-")["theme_head"])
        assert (len(got), _digest(got)) == (want["len"], want["sha256"])
