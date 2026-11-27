"""Marked-safe values are escaped in the attributes of djust's page templates (1.3 backport of #4157).

Django's autoescape leaves a value marked safe as it is, so a value marked safe for text could
close an attribute of the admin, allauth / djust_auth, bug-capture replay and gallery pages and add
another. Every attribute slot of those templates now uses ``|force_escape``; a URL attribute uses
``djust_url_attr`` (``{% load djust_attrs %}``) and a quoted ``dj-*`` handler argument uses
``djust_handler_arg``.

main pins this with the ``marked_safe_sweep_4009`` render sweep, which 1.3 does not have; this file
keeps the static half (every ``{{ }}`` in a start tag), renders the provider and replay-style slots
on Django's engine, and checks the filters themselves.
"""

from __future__ import annotations

import importlib
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe

PKG = Path(__file__).resolve().parents[1] / "djust"

PAGE_GLOBS = (
    "admin_ext/templates/djust_admin/*.html",
    "auth/templates/allauth/elements/*.html",
    "auth/templates/djust_auth/**/*.html",
    "templates/djust/bug_capture/replay.html",
    "components/templates/djust_components/gallery/*.html",
    "theming/templates/djust_theming/gallery/*.html",
)

PAGES = sorted({p for g in PAGE_GLOBS for p in PKG.glob(g)})

_ESCAPES = re.compile(r"\|\s*(force_escape|djust_url_attr|djust_handler_arg)\b")
_START_TAG = re.compile(r"<[a-zA-Z][^<>]*>", re.S)


def _unescaped_attribute_slots(source: str, escapes: re.Pattern[str] = _ESCAPES) -> list[str]:
    """Every ``{{ }}`` inside a start tag that does not end in one of the attribute escapers.

    Comments are blanked and a ``<`` / ``>`` inside a ``{% %}`` tag (``{% if page <= 1 %}``) is
    masked first, so such a tag does not end the start tag early and hide the slots after it.
    """
    source = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
    source = re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", "", source, flags=re.S)
    source = re.sub(
        r"\{%.*?%\}", lambda m: re.sub(r"[<>]", "_", m.group(0)), source, flags=re.S
    )
    found = []
    for tag in _START_TAG.finditer(source):
        for expr in re.findall(r"\{\{(.*?)\}\}", tag.group(0), re.S):
            if not escapes.search(expr):
                found.append(expr.strip())
    return found


def test_the_inventory_covers_every_area() -> None:
    names = [p.relative_to(PKG).as_posix() for p in PAGES]
    for area in ("djust_admin/", "allauth/elements/", "bug_capture/replay.html",
                 "djust_components/gallery/", "djust_theming/gallery/", "djust_auth/"):  # fmt: skip
        assert any(area in n for n in names), area


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.relative_to(PKG).as_posix())
def test_every_attribute_slot_escapes(page: Path) -> None:
    assert _unescaped_attribute_slots(page.read_text()) == []


def test_the_scanner_reads_past_a_comparison_in_a_block_tag() -> None:
    src = '<button dj-click="{{ a }}" {% if page <= 1 %}disabled{% endif %} title="{{ b }}">'
    assert _unescaped_attribute_slots(src) == ["a", "b"]


def test_the_scanner_sees_a_raw_attribute() -> None:
    """Gate off: the provider template without its filters is found."""
    raw = (PKG / "auth/templates/allauth/elements/provider.html").read_text()
    raw = raw.replace("|djust_url_attr", "").replace("|force_escape", "")
    assert len(_unescaped_attribute_slots(raw)) == 2


def _engine() -> Engine:
    return Engine(
        libraries={"djust_attrs": "djust.templatetags.djust_attrs"},
        autoescape=True,
    )


class _Attrs(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {n: v or "" for n, v in attrs}))


def _parse(markup: str) -> list[tuple[str, dict[str, str]]]:
    parser = _Attrs()
    parser.feed(markup)
    return parser.tags


PAYLOAD = mark_safe('x" data-pwn="1')


def test_a_marked_safe_value_stays_inside_its_attribute() -> None:
    template = _engine().from_string(
        '{% load djust_attrs %}<a href="{{ u|djust_url_attr }}" title="{{ t|force_escape }}">x</a>'
    )
    out = template.render(Context({"u": mark_safe("/a/" + PAYLOAD), "t": PAYLOAD}))
    ((tag, attrs),) = _parse(out)
    assert set(attrs) == {"href", "title"}, out
    assert attrs["title"] == 'x" data-pwn="1'


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "java\tscript:alert(1)"])
def test_a_script_url_is_neutralised(bad: str) -> None:
    template = _engine().from_string('{% load djust_attrs %}<a href="{{ u|djust_url_attr }}">x</a>')
    for value in (bad, mark_safe(bad)):
        ((_tag, attrs),) = _parse(template.render(Context({"u": value})))
        assert attrs["href"] == "#"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        ("/a/?x=1&y=2", "/a/?x=1&amp;y=2"),
        ("https://example.com/a", "https://example.com/a"),
        ("javascript:alert(1)", "#"),
        (mark_safe("javascript:alert(1)"), "#"),
        (mark_safe('/a/" data-pwn="1'), "/a/&quot; data-pwn=&quot;1"),
        (mark_safe("&#x6A;avascript:x"), "&amp;#x6A;avascript:x"),
        ("javascript:void(0)", "javascript:void(0)"),
    ],
)
def test_the_url_filter(value, expected) -> None:
    from djust.templatetags.djust_attrs import djust_url_attr

    assert str(djust_url_attr(value)) == expected


def test_a_handler_argument_keeps_its_string() -> None:
    """``force_escape`` is decoded by the browser before the client parses ``dj-click``, so a
    ``'`` ended the argument string; ``djust_handler_arg`` backslash-escapes ``\\ ' "`` first."""
    template = _engine().from_string(
        "{% load djust_attrs %}<th dj-click=\"sort_by('{{ n|djust_handler_arg }}')\">x</th>"
    )
    out = template.render(Context({"n": mark_safe('a\'b\\c" x="1')}))
    ((_tag, attrs),) = _parse(out)
    assert set(attrs) == {"dj-click"}, out
    assert attrs["dj-click"] == "sort_by('a\\'b\\\\c\\\" x=\\\"1')"


def test_a_filter_library_bridged_past_the_djust_prefix_registers_no_tag() -> None:
    """``_DJUST_FILTER_LIBRARIES`` lets a ``djust.templatetags`` library's FILTERS bridge to the
    Rust engine on ``{% load %}``; that is only right while the library has no tag."""
    from djust.template_libraries import _DJUST_FILTER_LIBRARIES

    assert _DJUST_FILTER_LIBRARIES
    for module in _DJUST_FILTER_LIBRARIES:
        library = importlib.import_module(module).register
        assert library.tags == {}, (module, sorted(library.tags))
        assert library.filters, module
