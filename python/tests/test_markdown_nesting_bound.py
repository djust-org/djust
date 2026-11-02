"""Markdown output keeps at most ``MAX_MD_NESTING`` containers open at once.

``djust.render_markdown`` (and ``{% djust_markdown %}``, which calls it) emits
one ``<blockquote>`` per ``>`` and a ``<ul>``/``<li>`` pair per list level. A
container that starts while ``MAX_MD_NESTING`` are already open is rendered
without its own tags and its content stays in place, so the depth of the
output, and the time spent parsing and diffing it downstream, stays bounded
and proportional to the length of the source. Ordinary documents never come
near the bound and render exactly as before.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest

from djust import LiveView, _rust, render_markdown
from djust.testing import LiveViewTestClient
from djust.tests._growth import assert_scales_linearly

# Read through getattr so a build without the constant fails the assertions
# below rather than the import of this module.
MAX = getattr(_rust, "MAX_MD_NESTING", 32)
CONTAINERS = {"blockquote", "ul", "ol", "li"}


class _Shape(HTMLParser):
    """Deepest container nesting, and whether every container closed in order."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.deepest = 0
        self.balanced = True

    def handle_starttag(self, tag, attrs):
        if tag in CONTAINERS:
            self.stack.append(tag)
            self.deepest = max(self.deepest, len(self.stack))

    def handle_endtag(self, tag):
        if tag in CONTAINERS:
            if not self.stack or self.stack.pop() != tag:
                self.balanced = False


def shape(html: str) -> _Shape:
    parser = _Shape()
    parser.feed(html)
    parser.close()
    assert parser.balanced and not parser.stack, "containers not balanced"
    return parser


def nested_list(depth: int) -> str:
    return "".join(f"{'  ' * i}- item{i}\n" for i in range(depth))


def test_the_bound_is_exported():
    assert _rust.MAX_MD_NESTING == 32


def test_deep_quotes_render_quickly_and_stay_bounded():
    # Growth, not an absolute bound (#3711): 4x the quotes (25 000 -> 100 000) must cost
    # under 10x as much, which is what "proportional to the length of the source" means.
    assert_scales_linearly(
        lambda scale: render_markdown("> " * (25_000 * scale) + "bottom"),
        what="rendering nested quotes",
    )
    html = render_markdown("> " * 100_000 + "bottom")
    assert shape(html).deepest == MAX
    assert "bottom" in html


def test_quotes_past_the_bound_keep_their_text():
    html = render_markdown("> " * (MAX + 10) + "deep text")
    assert html.count("<blockquote>") == MAX
    assert html.count("</blockquote>") == MAX
    assert "<p>deep text</p>" in html


@pytest.mark.parametrize("depth", [MAX // 2 + 1, 64, 500])
def test_deep_lists_keep_items_balanced_and_text(depth):
    html = render_markdown(nested_list(depth))
    s = shape(html)
    assert s.deepest <= MAX
    assert html.count("<li>") == html.count("</li>")
    words = set(html.replace("<", " ").replace(">", " ").split())
    assert all(f"item{i}" in words for i in range(depth))


@pytest.mark.parametrize("depth", [1, 2, 5, MAX - 1, MAX])
def test_quotes_within_the_bound_are_unchanged(depth):
    html = render_markdown("> " * depth + "text")
    expected = "<blockquote>\n" * depth + "<p>text</p>\n" + "</blockquote>\n" * depth
    assert html == expected


def test_lists_within_the_bound_are_unchanged():
    # Each list level is two containers (<ul> and <li>).
    depth = MAX // 2
    html = render_markdown(nested_list(depth))
    assert html.count("<ul>") == depth
    assert html.count("<li>") == depth
    assert shape(html).deepest == MAX


@pytest.mark.django_db
def test_template_tag_bounds_nested_quotes_through_a_live_view_render():
    page = {}

    class _V(LiveView):
        template = '<div dj-root class="prose">{% djust_markdown body %}</div>'

        def mount(self, request, **kwargs):
            self.body = page["body"]

    def mount_and_render(scale):
        page["body"] = "> " * (60_000 * scale) + "tail"
        client = LiveViewTestClient(_V)
        client.mount()
        return client.render_with_patches()[0]

    # Growth, not an absolute bound (#3711): 4x the quotes (60 000 -> 240 000) must cost
    # under 10x as much. This thread's CPU time, not the wall clock: the first version used
    # the wall clock and failed 1 run in 418 with 24 busy processes on 12 cores (the big
    # run's five samples were all stretched). The fixed cost of a mount dilutes growth, so
    # the classes sit closer together than the default 10x assumes: real code measures
    # ~3x for 4x the quotes and a quadratic replay of the same site (run it ``scale`` times)
    # measures ~12x. At 20 000 quotes that replay read 9.7x against the default limit of 10,
    # a 3% margin. The input is 3x larger now and the limit is 7x, midway between the two.
    assert_scales_linearly(
        mount_and_render,
        limit=7.0,
        runs=5,
        what="mounting and rendering nested quotes",
    )
    html = mount_and_render(4)
    assert "tail" in html
    assert html.count("<blockquote") == MAX
