"""Review findings on the #3749 theming URL hardening (PR #3765).

Four bypasses / regressions an independent adversarial review found in the first version:

1. **A callable that returns a callable.** The check resolved a callable once and wrote the
   result back; the result was still callable, so the template engine called it again when it
   printed ``{{ item.url }}``. The write-back is now the string the engine would have printed
   for the original value (one call, then ``str``), never a callable.
2. **An item that is read by ``item["url"]``.** Django resolves ``item.url`` by a subscript FIRST
   and by an attribute second, for ANY object that supports ``__getitem__``, not only a
   ``Mapping``. The check read the attribute; the engine printed the subscript. The lookup order
   is the engine's now, for every item shape.
3. **A value stringified twice.** The check called ``str(value)``; the template printed
   ``str(value)`` again. An object whose ``__str__`` returns a safe string, then
   ``javascript:...``, passed the check and printed the second string. A value is converted to
   text ONCE and that text is what is judged and printed.
4. **A name heuristic that rewrote values that are not URLs.** Any ``data-*`` / ``dj-*``
   attribute whose name contained ``link`` / ``location`` / ``image`` ... was judged, so
   ``data_action="toggle:menu"`` or ``data_location="Paris: France"`` became ``#``. The judged
   names are an EXPLICIT set now, and every other name is printed as written.
5. **An ``attrs.<name>`` read in a template that feeds a URL position** is not a sink the tags
   can see (a project template override can print any key). The in-repo set of such reads is
   derived from the templates and pinned (it is empty); see
   ``TestAttrsReadsInUrlPositionsArePinned``.

Refs #3749, #3765, #1825, #1646, #1468.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template import engines

from scripts.lib.template_library_parity import url_sweep

pytestmark = pytest.mark.theming

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "python/djust/theming/templates"


@pytest.fixture(scope="module", params=["django", "djust"])
def engine(request):
    if request.param == "django":
        return engines["django"]
    from djust.template import DjustTemplateBackend

    return DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


def _render(engine, source: str, context: dict) -> str:
    return engine.from_string("{% load theme_components theme_pages %}" + source).render(context)


LIST_TAGS = {
    "theme_nav": ("{% theme_nav items=items %}", lambda item: {"items": [item]}),
    "theme_nav_group": (
        "{% theme_nav_group 'G' items=items %}",
        lambda item: {"items": [item]},
    ),
    "theme_breadcrumb": (
        "{% theme_breadcrumb items=items %}",
        lambda item: {"items": [item, {"label": "Here"}]},
    ),
    "theme_sidebar_nav": (
        "{% theme_sidebar_nav sections=sections %}",
        lambda item: {"sections": [{"title": "S", "items": [item]}]},
    ),
}
EVIL = "javascript:alert(1)"


# --------------------------------------------------------------------------- #
# 1. A callable that returns a callable


class _Method:
    """An object item whose ``url`` is a METHOD returning whatever it was given."""

    def __init__(self, result):
        self.label = "L"
        self._result = result

    def url(self):
        return self._result


def _chain(depth: int, final):
    """``depth`` nested zero-argument callables around ``final``."""
    value = final
    for _ in range(depth):
        value = (lambda inner: lambda: inner)(value)
    return value


@pytest.mark.parametrize("tag", list(LIST_TAGS))
@pytest.mark.parametrize("depth", [0, 1, 2, 3, 12])
@pytest.mark.parametrize("shape", ["dict", "object"])
class TestACallableReturningACallable:
    def test_the_nested_callable_is_not_printed_as_a_script_url(self, engine, tag, depth, shape):
        source, context = LIST_TAGS[tag]
        final = _chain(depth, EVIL)  # ``url()`` returns a callable ``depth - 1`` deep
        item = {"label": "L", "url": final} if shape == "dict" else _Method(final)
        html = _render(engine, source, context(item))
        assert url_sweep.findings(html) == [], html
        assert "javascript" not in html, html

    def test_a_nested_callable_that_ends_in_an_allowed_url_still_prints_what_the_engine_did(
        self, engine, tag, depth, shape
    ):
        """No regression: the engine calls the url ONCE and prints ``str`` of the result, so a
        dict whose ``url`` is ONE callable prints the string it returns (an object's METHOD is
        that one call, so it needs ``depth == 0`` for the same)."""
        source, context = LIST_TAGS[tag]
        final = _chain(depth, "/safe/")
        item = {"label": "L", "url": final} if shape == "dict" else _Method(final)
        html = _render(engine, source, context(item))
        assert url_sweep.findings(html) == [], html
        if shape == "dict" and depth == 1:
            assert 'href="/safe/"' in html, html
        if shape == "object" and depth == 0:
            assert 'href="/safe/"' in html, html


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_a_callable_items_that_returns_a_callable_is_not_called_again(engine, depth):
    """``section["items"]`` is a callable whose result is a callable: the engine calls the
    ORIGINAL once. A write-back that is still callable would be called again when the template
    reads ``section.items``, and what THAT returns (a list of script urls) would be unjudged.
    (The ``url`` path is covered twice over, because the text-once conversion also turns a
    callable result into text; this is the path where the resolver is the only guard.)"""
    inner = [{"label": "L", "url": EVIL}]
    sections = [{"title": "S", "items": _chain(depth, lambda: inner)}]
    html = _render(engine, "{% theme_sidebar_nav sections=sections %}", {"sections": sections})
    assert url_sweep.findings(html) == [], html
    assert "javascript" not in html, html


# --------------------------------------------------------------------------- #
# 2. An item the engine reads with item["url"]


class _Subscriptable:
    """NOT a ``Mapping``: ``url`` the attribute is safe, ``item["url"]`` is not."""

    label = "L"
    url = "/safe/"

    def __getitem__(self, key):
        if key == "url":
            return EVIL
        raise KeyError(key)


class _SubscriptOnly:
    """Only ``__getitem__``; no attributes at all (a ``sqlite3.Row``-like item)."""

    def __getitem__(self, key):
        return {"label": "L", "url": EVIL}[key]


class _SubscriptRaisesTypeError:
    """``__getitem__`` refuses a str key (like a list): the engine falls back to the attribute."""

    label = "L"
    url = "/attr/"

    def __getitem__(self, key):
        raise TypeError("indices must be integers")


@pytest.mark.parametrize("tag", list(LIST_TAGS))
class TestAnItemTheEngineReadsBySubscript:
    def test_the_subscript_value_is_the_one_judged(self, engine, tag):
        source, context = LIST_TAGS[tag]
        html = _render(engine, source, context(_Subscriptable()))
        assert url_sweep.findings(html) == [], html
        assert "javascript" not in html, html

    def test_an_item_with_only_a_subscript(self, engine, tag):
        source, context = LIST_TAGS[tag]
        html = _render(engine, source, context(_SubscriptOnly()))
        assert url_sweep.findings(html) == [], html
        assert "javascript" not in html, html

    def test_a_subscript_that_raises_type_error_falls_back_to_the_attribute(self, engine, tag):
        source, context = LIST_TAGS[tag]
        html = _render(engine, source, context(_SubscriptRaisesTypeError()))
        assert 'href="/attr/"' in html, html

    def test_a_safe_subscript_is_printed_and_the_attribute_is_ignored(self, engine, tag):
        """The engine prints the SUBSCRIPT; the check must not judge (or print) the attribute."""

        class Item:
            label = "L"
            url = EVIL  # never printed: the subscript wins

            def __getitem__(self, key):
                if key == "url":
                    return "/from-subscript/"
                raise KeyError(key)

        source, context = LIST_TAGS[tag]
        html = _render(engine, source, context(Item()))
        assert 'href="/from-subscript/"' in html, html


# --------------------------------------------------------------------------- #
# 3. A value stringified twice


class _Flaky:
    """``__str__`` returns a safe URL ``safe_calls`` times, then a script URL."""

    def __init__(self, safe_calls: int):
        self.safe_calls = safe_calls
        self.calls = 0

    def __str__(self) -> str:
        self.calls += 1
        return "/ok/" if self.calls <= self.safe_calls else EVIL


SINKS = {
    "avatar.src": "{% theme_avatar src=v alt='me' %}",
    "button.href": "{% theme_button 'Go' href=v %}",
    "input.ping": "{% theme_input name='q' ping=v %}",
    "input.poster": "{% theme_input name='q' poster=v %}",
    "input.src": "{% theme_input name='q' src=v %}",
    "input.dj_navigate": "{% theme_input name='q' dj_navigate=v %}",
    "nav_item.url": "{% theme_nav_item 'L' v %}",
    "login.action": "{% theme_login_page action=v %}",
    "login.forgot": "{% theme_login_page forgot_password_url=v %}",
}


@pytest.mark.parametrize("sink", list(SINKS))
@pytest.mark.parametrize("safe_calls", [1, 2, 3])
def test_a_value_is_converted_to_text_once(engine, sink, safe_calls):
    flaky = _Flaky(safe_calls)
    try:
        html = _render(engine, SINKS[sink], {"v": flaky})
    except ValueError as exc:
        # ``href`` / ``src`` / ``action`` raise for a script URL (as they always have). The native
        # engine stringifies the argument once before the tag runs, so the tag's single
        # conversion can be the script one: a refusal is a correct answer, a rendered
        # ``javascript:`` is not.
        assert "refuses" in str(exc) and sink in ("button.href", "input.src"), (sink, exc)
        return
    assert url_sweep.findings(html) == [], (flaky.calls, html)
    assert "javascript" not in html, (flaky.calls, html)


@pytest.mark.parametrize("tag", ["theme_nav", "theme_breadcrumb"])
@pytest.mark.parametrize("safe_calls", [1, 2, 3])
def test_an_item_url_is_converted_to_text_once(engine, tag, safe_calls):
    source, context = LIST_TAGS[tag]
    flaky = _Flaky(safe_calls)
    html = _render(engine, source, context({"label": "L", "url": flaky}))
    assert url_sweep.findings(html) == [], (flaky.calls, html)
    assert "javascript" not in html, (flaky.calls, html)


def test_the_text_that_is_judged_is_the_text_that_is_printed():
    from djust.theming.templatetags.theme_components import _neutralise_url, _plain_url

    flaky = _Flaky(1)
    out = _neutralise_url("u", flaky)
    assert out == "/ok/" and type(out) is str and flaky.calls == 1
    flaky = _Flaky(1)
    out = _plain_url(flaky)
    assert out == "/ok/" and type(out) is str and flaky.calls == 1


# --------------------------------------------------------------------------- #
# 4. Names that are not URLs are printed as written


def _input(engine, attr: str, value: str) -> str:
    return _render(engine, "{% theme_input name='q' " + attr + "=v %}", {"v": value})


@pytest.mark.parametrize(
    "attr, value",
    [
        ("icon", "mdi:home"),
        ("data_action", "toggle:menu"),
        ("data_link", "a:b"),
        ("dj_action", "save:draft"),
        ("data_location", "Paris: France"),
        ("data_image", "hero:large"),
        ("data_url", "page:7"),
        ("data_href", "ns:id"),
        ("cite", "Smith: 2020"),
        ("longdesc", "see: appendix"),
        ("profile", "x:y"),
        ("data_imageurl", "x:y"),
        ("data_srcset", "x:y"),
        ("data_bg", "x:y"),
        ("dj_navigatetarget", "x:y"),
        ("dj_click", "save"),
        ("dj_redirect", "a:b"),
    ],
)
def test_an_attribute_that_is_not_a_url_is_printed_as_written(engine, attr, value):
    html = _input(engine, attr, value)
    assert '%s="%s"' % (attr.replace("_", "-"), value) in html, html


# --------------------------------------------------------------------------- #
# 5. attrs.<name> reads in a URL position


_ATTR_VALUE = re.compile(r"""(?<![\w:.-])([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _attrs_reads_in_url_positions() -> set[tuple[str, str, str]]:
    """``(template, attribute, attrs.<name>)`` for every ``attrs.<name>`` inside the value of
    an attribute the sweep scanner reads as a URL (or any ``href`` / ``src`` / ``action``)."""
    found = set()
    for path in sorted(TEMPLATES.rglob("*.html")):
        rel = str(path.relative_to(TEMPLATES))
        for m in _ATTR_VALUE.finditer(path.read_text(encoding="utf-8")):
            name = m.group(1).lower()
            value = m.group(2) if m.group(2) is not None else m.group(3)
            if not (
                url_sweep.is_url_attr(name)
                or name in ("href", "src", "action", "formaction", "dj-patch", "dj-patch-reload")
            ):
                continue
            for read in re.findall(r"\battrs\.\w+", value):
                found.add((rel, name, read))
    return found


class TestAttrsReadsInUrlPositionsArePinned:
    #: No in-repo template prints an ``attrs.<name>`` into a URL position. A project template
    #: override can (``components/badge.html`` is looked up in the project's templates first), and
    #: then the page's ``link=`` reaches an ``href`` unjudged: a tag cannot see which key an
    #: override reads. The set is derived from the templates and pinned so a NEW read in this
    #: repo is a decision made here, with its own check in the tag that feeds it.
    EXPECTED: set[tuple[str, str, str]] = set()

    def test_the_set_is_the_pinned_one(self):
        assert _attrs_reads_in_url_positions() == self.EXPECTED

    def test_the_scan_sees_what_it_claims_to(self):
        """Presence before absence: it finds a planted read, and the real attribute values."""
        sample = '<a href="{{ attrs.link }}" class="{{ attrs.class }}" dj-navigate="{{ url }}">'
        planted = {
            (m.group(1).lower(), read)
            for m in _ATTR_VALUE.finditer(sample)
            for read in re.findall(r"\battrs\.\w+", m.group(2) or m.group(3) or "")
            if url_sweep.is_url_attr(m.group(1).lower())
        }
        assert planted == {("href", "attrs.link")}
        real = {
            m.group(1).lower()
            for p in TEMPLATES.rglob("*.html")
            for m in _ATTR_VALUE.finditer(p.read_text(encoding="utf-8"))
            if url_sweep.is_url_attr(m.group(1).lower())
        }
        assert {"href", "dj-navigate"} <= real
