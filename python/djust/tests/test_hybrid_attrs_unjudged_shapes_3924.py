"""URL slot shapes of a Component's hybrid template that were escaped but not judged (#3924).

* more than 64 paths: identical paths are now one (most values fall back under the cap), and a
  value still over it is judged slot by slot, fail-closed (``_coarse``);
* ``{% now %}`` before the slot: judged slot by slot as well;
* ``{% cycle %}`` with literal values: each literal is a branch; and ``{% cycle ... as c %}``,
  which PRINTS, no longer counts as silent (a ``javascript`` it printed before the slot was not
  part of the value judged, with no warning);
* by decision, still escaped and listed: a dotted, filtered, loop or ``with`` slot, and a slot
  after ``{% firstof %}`` or a ``{% cycle %}`` of variables (the value is not in the context).
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.template import Context, Template

from djust.components import _hybrid_attrs as hybrid
from djust.components.base import Component

pytestmark = pytest.mark.components

JS = "javascript:alert(1)"


@pytest.fixture(autouse=True)
def _bootstrap(settings):
    settings.DJUST_CONFIG = {"css_framework": "bootstrap5"}


def _draw(template: str, **context) -> str:
    class Box(Component):
        pass

    Box.template = template
    Box.get_context_data = lambda self: dict(context)  # type: ignore[method-assign]
    return str(Box().render())


def _django(template: str, **context) -> str:
    return Template(template).render(Context(context))


def _href(markup: str) -> str | None:
    found: dict[str, str | None] = {}

    class P(HTMLParser):
        def handle_starttag(self, t, attrs):
            if t == "a" and not found:
                found.update(attrs)

    P(convert_charrefs=True).feed(markup)
    return found.get("href")


def _listed(template: str) -> bool:
    return bool(hybrid.plan(template).unchecked_urls)


# ---- {% cycle %} -------------------------------------------------------------------------------


def test_cycle_as_var_prints_and_is_judged():
    template = "<a href=\"{% cycle 'javascript' 'x' as c %}{{ s }}\">"
    out = _draw(template, s=":alert(1)")
    assert "javascript:" not in (_href(out) or ""), out
    assert hybrid.plan(template).groups and not _listed(template)


def test_cycle_literals_are_branches():
    template = "<a href=\"{% cycle 'java' '/' %}{{ s }}\">"
    # the first cycle prints 'java': the slot that would complete the scheme becomes '#'
    assert _href(_draw(template, s="script:x")) == "java#"
    assert _draw(template, s="/ok") == _django(template, s="/ok")


def test_cycle_silent_stays_silent():
    (group,) = hybrid.plan("<a href=\"{% cycle 'a' 'b' as c silent %}{{ s }}\">").groups
    assert not group.roles
    assert group.paths == ((("slot", group.slots[0][0]),),)


# ---- more than 64 paths ----------------------------------------------------------------------

#: 7 independent blocks, each adding a different literal: 128 distinct paths.
CAPPED = "".join("{% if a" + str(i) + " %}j" + str(i) + "{% endif %}" for i in range(7))


def test_identical_branches_dedupe_under_the_cap():
    template = '<a href="' + "".join("{% if a" + str(i) + " %}/{% endif %}" for i in range(8))
    template += '{{ s }}">'
    (group,) = hybrid.plan(template).groups
    assert not group.roles and len(group.paths) <= 9
    assert _href(_draw(template, s=JS)) == "#"


@pytest.mark.parametrize("taken", [{}, {"a0": True, "a3": True}])
def test_capped_plain_name_fails_closed(taken):
    template = f'<a href="{CAPPED}{{{{ s }}}}">'
    plan = hybrid.plan(template)
    (group,) = plan.groups
    assert group.roles and not plan.unchecked_urls
    assert "javascript:" not in (_href(_draw(template, s=JS, **taken)) or "")


@pytest.mark.parametrize(
    ("value", "kept"), [("https://x/", True), ("/p", True), ("java", False), (JS, False)]
)
def test_capped_head_keeps_a_good_url(value, kept):
    template = f'<a href="{{{{ s }}}}{CAPPED}">'
    assert dict(hybrid.plan(template).groups[0].roles).popitem()[1] == "head"
    assert _href(_draw(template, s=value)) == (value if kept else "#")


@pytest.mark.parametrize(
    ("value", "kept"),
    [("/p", True), ("?q=1", True), ("#top", True), ("", True), ("docs", False),
     ("#58;x", False), (":x", False)],
)  # fmt: skip
def test_capped_tail_rules(value, kept):
    template = f'<a href="{CAPPED}{{{{ s }}}}">'
    assert dict(hybrid.plan(template).groups[0].roles).popitem()[1] == "tail"
    expected = value if kept else "#"
    assert _href(_draw(template, s=value, a2=True)) == "j2" + expected


def test_capped_free_slot_after_a_fixing_literal():
    template = f'<a href="/x/{CAPPED}{{{{ s }}}}">'
    plan = hybrid.plan(template)
    # every path is fixed by the literal: nothing to judge, nothing listed
    assert plan.groups == () and plan.unchecked_urls == ()
    assert _href(_draw(template, s="a:b")) == "/x/a:b"
    # the same before a {% now %}: the literal fixes the value, so it is not judged at all
    template = '<a href="/x/{% now "Y" %}{{ s }}">'
    plan = hybrid.plan(template)
    assert plan.groups == () and plan.unchecked_urls == ()
    assert _href(_draw(template, s="a:b")).endswith("a:b")
    # an open character reference does not fix it (#3923)
    template = '<a href="&{% now "Y" %}{{ s }}">'
    assert dict(hybrid.plan(template).groups[0].roles).popitem()[1] == "tail"


def test_capped_noop_href_is_not_kept_at_head():
    template = f'<a href="{{{{ s }}}}{CAPPED}">'
    assert _href(_draw(template, s="javascript:void(0)")) == "#"


# ---- {% now %} -------------------------------------------------------------------------------


def test_now_before_the_slot_is_judged():
    template = '<a href="{% now "Y" %}{{ u }}">'
    assert not _listed(template)
    assert "javascript" not in (_href(_draw(template, u=JS)) or "")
    assert (_href(_draw(template, u="/p")) or "").endswith("/p")


# ---- by decision, still listed ----------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        '<a href="{% firstof base "/" %}{{ path }}">',
        "<a href=\"{% cycle v 'x' %}{{ s }}\">",
        '<a href="{{ item.url }}">',
        '<a href="{{ u|default:"/x" }}">',
        '{% for l in links %}<a href="{{ l }}">{% endfor %}',
        '{% with x=u %}<a href="{{ x }}">{% endwith %}',
    ],
)
def test_firstof_and_dotted_stay_listed(template):
    plan = hybrid.plan(template)
    assert plan.groups == ()
    assert [t[:2] for t in plan.unchecked_urls] == [("a", "href")]


def test_capped_dotted_slot_stays_listed():
    template = f'<a href="{CAPPED}{{{{ a.b }}}}">'
    plan = hybrid.plan(template)
    assert plan.groups == () and [t[:2] for t in plan.unchecked_urls] == [("a", "href")]


# ---- a printed part before a fixing literal (#3923, review) ------------------------------------


def test_capped_block_before_a_fixing_literal_is_not_free():
    """``&`` printed inside a block, then ``#``: the literal alone does not fix the value."""
    head = "".join("{% if e" + str(i) + " %}{% endif %}" for i in range(7))
    tail = "".join("{% if b" + str(i) + " %}a{% else %}b{% endif %}" for i in range(7))
    template = '<a href="' + head + "{% if x %}&{% endif %}#{{ s }}" + tail + '">'
    plan = hybrid.plan(template)
    (group,) = plan.groups
    assert dict(group.roles).popitem()[1] == "tail" and not plan.unchecked_urls
    href = _href(_draw(template, s="106;avascript:alert(1)", x=True)) or ""
    assert not href.startswith("javascript:"), href


def test_now_before_a_fixing_literal_is_not_free():
    template = '<a href="{% now "&" %}#{{ s }}">'
    (group,) = hybrid.plan(template).groups
    assert dict(group.roles).popitem()[1] == "tail"
    href = _href(_draw(template, s="106;avascript:alert(1)")) or ""
    assert not href.startswith("javascript:"), href
