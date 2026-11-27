"""Marked-safe values are escaped in the attributes of the ``djust_components`` inclusion-tag
templates (1.3-only follow-up to the #4157 / #4009 backport).

These templates (``avatar``, ``badge``, ``pagination``, ``progress``, ``table``, ``toast``,
``markdown_controls``, ``ttyd_terminal``) were deleted on main (#4331), so main's #4157 sweep never
covered them. Django's autoescape leaves a value marked safe as it is, so
``{% avatar src="/a.png" alt=p %}`` with ``p = mark_safe('x" onerror="alert(1)')`` wrote an
``onerror`` attribute. Every attribute slot now ends in ``|force_escape`` (a ``dj-click`` /
``dj-input`` slot holds a whole handler name, which ``force_escape`` keeps inside the attribute).
URL attributes keep their own checkers, which escape and judge the scheme: ``avatar``'s ``src``
(``data_url_attr(src, image=True)`` in the tag) and ``table``'s ``table_href`` / ``table_width``.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template import Context, Template
from django.utils.safestring import mark_safe

from python.tests.test_page_template_attrs_4157 import _unescaped_attribute_slots

TEMPLATES = Path(__file__).resolve().parents[1] / "djust" / "components" / "templates"
PAGES = sorted(TEMPLATES.glob("djust_components/**/*.html"))

#: URL / CSS slots escaped and judged by the tag or a filter of their own, with the reason.
CHECKED_ELSEWHERE = re.compile(r"\|\s*(force_escape|djust_url_attr|djust_handler_arg|table_href|table_width)\b")
ALLOWED = {
    # the avatar tag hands the template data_url_attr(src, image=True): escaped, scheme-judged
    ("djust_components/avatar.html", "src"),
}

PAYLOAD = mark_safe('x" onerror="alert(1)')


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.relative_to(TEMPLATES).as_posix())
def test_every_attribute_slot_escapes(page: Path) -> None:
    name = page.relative_to(TEMPLATES).as_posix()
    found = _unescaped_attribute_slots(page.read_text(), CHECKED_ELSEWHERE)
    assert [e for e in found if (name, e) not in ALLOWED] == []


def test_the_inventory_holds_the_inclusion_templates() -> None:
    names = {p.name for p in PAGES}
    assert {"avatar.html", "badge.html", "pagination.html", "progress.html", "table.html"} <= names


class _Attrs(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attrs: list[dict[str, str]] = []

    def handle_starttag(self, tag, attrs):
        self.attrs.append({n: v or "" for n, v in attrs})


def _attr_names(markup: str) -> set[str]:
    parser = _Attrs()
    parser.feed(markup)
    return {name for attrs in parser.attrs for name in attrs}


@pytest.mark.parametrize(
    "source",
    [
        '{% avatar src="/a.png" alt=p %}',
        '{% avatar alt=p initials="AB" status=p size=p %}',
        '{% badge label="l" status=p %}',
        "{% progress value=5 label=p color=p size=p %}",
        "{% pagination page=2 total_pages=3 prev_event=p next_event=p %}",
    ],
)
def test_a_marked_safe_value_adds_no_attribute(source: str) -> None:
    out = Template("{% load djust_components %}" + source).render(Context({"p": PAYLOAD}))
    assert "onerror" not in _attr_names(out), out


def test_the_avatar_alt_stays_one_attribute() -> None:
    out = Template('{% load djust_components %}{% avatar src="/a.png" alt=p %}').render(
        Context({"p": PAYLOAD})
    )
    parser = _Attrs()
    parser.feed(out)
    (img,) = [a for a in parser.attrs if "src" in a]
    assert img["alt"] == 'x" onerror="alert(1)'
    assert img["src"] == "/a.png"
