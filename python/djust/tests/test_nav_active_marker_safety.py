"""The native ``nav_item`` → ``nav_menu`` active marker cannot carry markup (#3873 follow-up).

A native ``{% nav_item %}`` with no ``active=`` of its own leaves its ``id`` and ``href`` in a
comment right after its ``<li ...>``, for the ``{% nav_menu %}`` that holds it to read and
remove. Both values can come from context, so the comment carries them hex-encoded: nothing a
value holds can end the comment, open another, read as markup, or forge another item's marker.
Outside any menu the comment stays in the page; it must close where the handler closed it, and
the VDOM path walker must not count it.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

import pytest

from djust.tests.test_component_dj_attr_parity_3669 import _rust
from djust.tests.test_components_csp_aria import register_with_rust_engine

PAYLOADS = [
    "-->",
    "--!>",
    "--",
    "<script>alert(1)</script>",
    "<!--",
    "a-",
    "line\nbreak",
    "x--><img src=x onerror=alert(1)><!--",
    "<!--djust-nav-key:5b2276222c2223225d-->",
    "djust-nav-key:00",
]

_MARK = re.compile(r"<!--djust-nav-key:([0-9a-f]*)-->")


class _Comments(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.comments: list[str] = []
        self.tags: list[str] = []

    def handle_comment(self, data: str) -> None:
        self.comments.append(data)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.tags.append(tag)


def _parse(html: str) -> _Comments:
    page = _Comments()
    page.feed(html)
    page.close()
    return page


def _active_ids(html: str) -> list[str]:
    return re.findall(r'<li class="[^"]*--active[^"]*">(?:<!--[^>]*-->)?<a[^>]*>([^<]*)', html)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_outside_a_menu_the_comment_closes_where_the_handler_closed_it(payload: str) -> None:
    html = _rust('{% nav_item label="L" id=v href=v %}{% endnav_item %}', {"v": payload})
    marks = _MARK.findall(html)
    assert len(marks) == 1, html
    # The payload is the hex of the JSON pair, and decodes back to exactly the values.
    assert json.loads(bytes.fromhex(marks[0]).decode("utf-8")) == [payload, payload]
    page = _parse(html)
    # One comment, and it is the marker: no value ended it early or opened another.
    assert page.comments == ["djust-nav-key:" + marks[0]], html
    assert page.tags == ["li", "a"], html
    rest = _MARK.sub("", html)
    assert "<!--" not in rest and "-->" not in rest and "<script" not in rest, html
    assert "<img" not in html, html


@pytest.mark.parametrize("payload", PAYLOADS)
def test_inside_a_menu_the_marker_is_removed_and_only_the_named_item_is_active(
    payload: str,
) -> None:
    src = (
        "{% nav_menu active=v %}"
        '{% nav_item label="hit" id=v %}{% endnav_item %}'
        '{% nav_item label="miss" id="other" href="/other" %}{% endnav_item %}'
        "{% endnav_menu %}"
    )
    html = _rust(src, {"v": payload})
    assert "djust-nav-key" not in html, html
    assert _parse(html).comments == [], html
    assert _active_ids(html) == ["hit"], html
    assert "<script" not in html and "<img" not in html, html


@pytest.mark.parametrize("payload", PAYLOADS)
def test_one_item_cannot_mark_another_active(payload: str) -> None:
    """An item whose id is another item's marker text (or any payload) makes nothing active
    when the menu's ``active`` names neither, and leaves no marker text behind."""
    forged = "<!--djust-nav-key:" + json.dumps(["victim", "#"]).encode().hex() + "-->"
    src = (
        '{% nav_menu active="victim" %}'
        '{% nav_item label="forger" id=f href=v %}{% endnav_item %}'
        '{% nav_item label="other" id="b" href="/b" %}{% endnav_item %}'
        "{% endnav_menu %}"
    )
    html = _rust(src, {"f": forged, "v": payload})
    assert _active_ids(html) == [], html
    # No marker is left behind (a payload's own text stays, escaped, inside its href).
    assert not _MARK.search(html), html
    assert _parse(html).comments == [], html


def test_the_leftover_comment_is_not_counted_by_the_vdom_path_walker() -> None:
    """A page with a stray native ``nav_item`` (marker comment) diffs to the same patch as the
    same page without one: the VDOM render drops the comment, so no path counts it."""
    from djust._rust import RustLiveView

    register_with_rust_engine()

    def patches(item_args: str) -> list:
        view = RustLiveView(
            '<div dj-root>{% nav_item label="L" ' + item_args + " %}{% endnav_item %}"
            "<p>{{ n }}</p></div>"
        )
        view.update_state({"n": "first"})
        first, _, _ = view.render_with_diff()
        view.update_state({"n": "second"})
        _, out, _ = view.render_with_diff()
        return first, json.loads(out)

    with_mark, p_mark = patches('id="x"')
    without_mark, p_plain = patches('id="x" active=""')
    assert "djust-nav-key" not in with_mark and "djust-nav-key" not in without_mark
    assert with_mark == without_mark
    assert p_mark == p_plain, (p_mark, p_plain)
    assert [p["type"] for p in p_mark] == ["SetText"], p_mark
