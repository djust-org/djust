"""``sidebar``, ``nav_menu`` and ``toolbar`` are one implementation on both engines (#4331).

Each family had two renderers: a native handler (the Rust engine) and Python markup in the Django
node. The native handlers are now the only renderers (D1: native markup wins); the Django nodes
resolve their arguments, render their body, and hand both to the handler's ``render_kwargs``, as
``data_table`` does since #3869. What changed for the Django engine:

* ``sidebar``: body content that is not an item is kept, an item's body is its submenu whatever
  it holds, and there is no ``dj-sidebar__item--level-N`` class. The sidebar's ``active=`` still
  reaches its items, on both engines now (a marker each item leaves, as ``nav_item`` does).
* ``sidebar_section`` / ``nav_menu``: a marked-safe operand is escaped (D3).
* ``toolbar``: the children between two separators form one ``dj-toolbar__group``; the Django
  node used to wrap each node on its own, splitting ``<button>{{ x }}</button>`` across three
  groups. The default id is ``toolbar-<hash of the arguments>`` on both engines.
"""

from __future__ import annotations

import re

import pytest
from django.utils.safestring import mark_safe

from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust

HOSTILE = "<script>alert(\"x\")</script> 'q' & co"

CONTEXTS = {
    "typical": {"label": "Hello", "path": "/a", "on": True},
    "hostile": {"label": HOSTILE, "path": HOSTILE, "on": HOSTILE},
    "safe": {"label": mark_safe("<b>bold</b>"), "path": mark_safe("/a?x=<i>"), "on": True},
    "missing": {},
    "none": {"label": None, "path": None, "on": None},
}

SIDEBAR = {
    "typical": (
        '{% sidebar title=label active="/a" collapsed=on class="x" component_id="sb" %}'
        "{% sidebar_section label=label %}"
        '{% sidebar_item label=label href="/a" icon="i" %}{% endsidebar_item %}'
        '{% sidebar_item label="B" event="go" id="b" %}{% endsidebar_item %}'
        "{% endsidebar %}"
    ),
    "operands-from-context": (
        "{% sidebar id=label title=label active=path toggle_event=label %}"
        "{% sidebar_item id=label label=label href=path event=label icon=label %}"
        "{% endsidebar_item %}{% endsidebar %}"
    ),
    "literal": (
        '{% sidebar title="<b>lit</b>" %}{% sidebar_section label=\'a "q" <i>\' %}'
        '{% sidebar_item label="<b>L</b>" href="javascript:alert(1)" %}{% endsidebar_item %}'
        "{% endsidebar %}"
    ),
    "nested-children": (
        '{% sidebar active="c" %}'
        '{% sidebar_item label="A" %}'
        '{% sidebar_item label="B" %}{% sidebar_item label=label id="c" %}{% endsidebar_item %}'
        "{% endsidebar_item %}{% endsidebar_item %}{% endsidebar %}"
    ),
    "non-item-body": (
        '{% sidebar %}<li class="mine">{{ label }}</li>'
        '{% sidebar_item label="A" %}<p>{{ label }}</p>{% endsidebar_item %}junk{% endsidebar %}'
    ),
    "items-in-a-loop": (
        '{% sidebar active="/b" %}{% for p in paths %}'
        "{% sidebar_item label=p href=p %}{% endsidebar_item %}{% endfor %}{% endsidebar %}"
    ),
    "own-active": (
        '{% sidebar active="/a" %}'
        '{% sidebar_item label="A" href="/a" active="/z" %}{% endsidebar_item %}'
        '{% sidebar_item label="B" href="/b" active=True %}{% endsidebar_item %}'
        '{% sidebar_item label="C" id="c" active="c" %}{% endsidebar_item %}'
        "{% endsidebar %}"
    ),
    "empty": "{% sidebar %}{% endsidebar %}",
    "item-alone": "{% sidebar_item label=label href=path %}x{% endsidebar_item %}",
    "section-alone": "{% sidebar_section label=label %}",
}

NAV = {
    "typical": (
        '{% nav_menu brand=label brand_href=path active="/a" mobile_open=on class="n" %}'
        '{% nav_item label=label href="/a" %}{% endnav_item %}'
        '{% nav_item label="E" event="go" description=label %}{% endnav_item %}'
        "{% endnav_menu %}"
    ),
    "operands-from-context": (
        "{% nav_menu id=label brand=label active=path toggle_event=label %}"
        "{% nav_item id=label label=label href=path event=label %}{% endnav_item %}"
        "{% endnav_menu %}"
    ),
    "literal": (
        '{% nav_menu brand="<b>lit</b>" %}'
        '{% nav_item label=\'a "q" <i>\' href="javascript:x" %}{% endnav_item %}'
        "{% endnav_menu %}"
    ),
    "nested-children": (
        '{% nav_menu active="deep" %}{% nav_item label="A" mega=True %}'
        '{% nav_item label="B" %}{% nav_item label=label id="deep" %}{% endnav_item %}'
        "{% endnav_item %}{% endnav_item %}{% endnav_menu %}"
    ),
    "non-item-body": (
        '{% nav_menu %}<li class="mine">{{ label }}</li>'
        '{% nav_item label="A" event="go" %}<p>{{ label }}</p>{% endnav_item %}{% endnav_menu %}'
    ),
    "items-in-a-condition": (
        '{% nav_menu active="/b" %}{% if paths %}{% nav_item label="B" href="/b" %}{% endnav_item %}'
        "{% endif %}{% endnav_menu %}"
    ),
    "item-alone": "{% nav_item label=label href=path %}{% endnav_item %}",
}

TOOLBAR = {
    "groups": (
        '{% toolbar id="t" %}<button>{{ label }}</button><button>2</button>'
        "{% toolbar_separator %}<a>{{ label }}</a>{% toolbar_separator %}"
        "{% toolbar_overflow %}<a>{{ label }}</a>{% endtoolbar_overflow %}{% endtoolbar %}"
    ),
    "default-id": '{% toolbar size="sm" variant=label %}<b>{{ label }}</b>{% endtoolbar %}',
    "default-id-from-context": "{% toolbar class=label %}x{% endtoolbar %}",
    "operands-from-context": "{% toolbar id=label class=label size=label %}x{% endtoolbar %}",
    "whitespace": (
        '{% toolbar id="t" %}\n  <button>a</button>\n  {% toolbar_separator %}\n  '
        "{% toolbar_separator %}\n{% endtoolbar %}"
    ),
    "separator-inside-markup": (
        '{% toolbar id="t" %}<span>a{% toolbar_separator %}b</span>{% toolbar_separator %}c'
        "{% endtoolbar %}"
    ),
    "overflow-with-nested-divs": (
        '{% toolbar id="t" %}a{% toolbar_overflow %}<div><div>{{ label }}</div></div>'
        "{% endtoolbar_overflow %}b{% endtoolbar %}"
    ),
    "empty": "{% toolbar %}{% endtoolbar %}",
    "literal": '{% toolbar id=\'<b>"x"\' class="<i>" %}y{% endtoolbar %}',
}

#: The Rust engine's own ``{% if %}`` boundary comments (VDOM markers, not component markup).
_IF_MARKERS = re.compile(r'<!--dj-if id="[^"]*"-->|<!--/dj-if-->')


def _rust_markup(src: str, context: dict) -> str:
    return _IF_MARKERS.sub("", _rust(src, context))


CASES = (
    [("sidebar", k) for k in sorted(SIDEBAR)]
    + [("nav", k) for k in sorted(NAV)]
    + [("toolbar", k) for k in sorted(TOOLBAR)]
)
SOURCES = {"sidebar": SIDEBAR, "nav": NAV, "toolbar": TOOLBAR}


@pytest.mark.parametrize("ctx", sorted(CONTEXTS))
@pytest.mark.parametrize("family, case", CASES, ids=[f"{f}-{c}" for f, c in CASES])
def test_both_engines_draw_the_same_bytes(family: str, case: str, ctx: str) -> None:
    src = SOURCES[family][case]
    context = {**CONTEXTS[ctx], "paths": ["/a", "/b", HOSTILE]}
    assert _django(src, context) == _rust_markup(src, context)


@pytest.mark.parametrize("family, case", CASES, ids=[f"{f}-{c}" for f, c in CASES])
def test_a_hostile_operand_or_body_is_escaped(family: str, case: str) -> None:
    out = _django(SOURCES[family][case], {**CONTEXTS["hostile"], "paths": [HOSTILE]})
    assert "<script>" not in out
    assert "djust-sidebar-key" not in out or case == "item-alone"


@pytest.mark.parametrize(
    "src",
    [
        "{% sidebar_section label=label %}",
        "{% nav_menu brand=label %}{% endnav_menu %}",
        "{% sidebar title=label %}{% sidebar_item label=label %}{% endsidebar_item %}{% endsidebar %}",
        "{% nav_menu %}{% nav_item label=label %}{% endnav_item %}{% endnav_menu %}",
    ],
)
def test_a_marked_safe_operand_is_escaped(src: str) -> None:
    """D3: the single renderer escapes a marked-safe operand (the Django tags kept it raw)."""
    out = _django(src, {"label": mark_safe("<b>bold</b>")})
    assert "<b>" not in out and "&lt;b&gt;bold&lt;/b&gt;" in out


class TestSidebar:
    def test_body_content_that_is_not_an_item_is_kept(self) -> None:
        out = _django(SIDEBAR["non-item-body"], {"label": "L"})
        assert '<li class="mine">L</li>' in out
        assert '<ul class="dj-sidebar__submenu"><p>L</p></ul>' in out
        assert out.endswith(
            'junk</ul><div class="dj-sidebar__backdrop" dj-click="toggle_sidebar"></div></nav>'
        )

    def test_a_nested_item_is_drawn_inside_its_parents_submenu(self) -> None:
        out = _django(SIDEBAR["nested-children"], {"label": "Deep"})
        assert out.count('<ul class="dj-sidebar__submenu">') == 2
        assert "dj-sidebar__item--level" not in out

    @pytest.mark.parametrize("render", [_django, _rust], ids=["django", "rust"])
    def test_the_sidebars_active_reaches_its_items_on_both_engines(self, render) -> None:
        out = render(SIDEBAR["nested-children"], {"label": "Deep"})
        assert out.count("dj-sidebar__item--active") == 1
        assert (
            '<a class="dj-sidebar__link dj-sidebar__item--active" href="#"><span class="dj-sidebar__label">Deep'
            in out
        )
        assert "djust-sidebar-key" not in out
        out = render(SIDEBAR["items-in-a-loop"], {"paths": ["/a", "/b"]})
        assert out.count("dj-sidebar__item--active") == 1
        assert 'dj-sidebar__item--active" href="/b"' in out

    def test_an_items_own_active_wins_over_the_sidebars(self) -> None:
        out = _django(SIDEBAR["own-active"])
        assert 'dj-sidebar__item--active" href="/a"' not in out
        assert 'dj-sidebar__item--active" href="/b"' in out
        assert out.count("dj-sidebar__item--active") == 2

    def test_no_active_marks_nothing(self) -> None:
        """The Django node used to compare an empty ``id`` with an empty ``active``: every item
        without an ``id`` was drawn active when the sidebar had no ``active=``."""
        out = _django(
            '{% sidebar %}{% sidebar_item label="A" %}{% endsidebar_item %}{% endsidebar %}'
        )
        assert "dj-sidebar__item--active" not in out


class TestNav:
    def test_three_levels_and_the_menus_active(self) -> None:
        out = _django(NAV["nested-children"], {"label": "Deep"})
        assert out.count('<ul class="dj-nav__dropdown-list">') == 2
        assert out.count("dj-nav__item--active") == 1
        assert "djust-nav-key" not in out

    def test_an_item_with_a_text_body_stays_a_button(self) -> None:
        out = _django(NAV["non-item-body"], {"label": "L"})
        assert 'dj-click="go"' in out


class TestToolbar:
    def test_children_between_separators_form_one_group(self) -> None:
        out = _django(TOOLBAR["groups"], {"label": "L"})
        assert (
            '<div class="dj-toolbar__group"><button>L</button><button>2</button></div>'
            '<div class="dj-toolbar__separator" role="separator"></div>'
            '<div class="dj-toolbar__group"><a>L</a></div>'
            '<div class="dj-toolbar__separator" role="separator"></div>'
            '<div class="dj-toolbar__overflow">'
        ) in out
        assert out.count("dj-toolbar__group") == 2

    def test_whitespace_only_runs_draw_no_group(self) -> None:
        out = _django(TOOLBAR["whitespace"])
        assert out.count("dj-toolbar__group") == 1

    def test_a_separator_inside_the_authors_markup_is_not_a_group_boundary(self) -> None:
        out = _django(TOOLBAR["separator-inside-markup"])
        assert out.count("dj-toolbar__group") == 2
        assert '<div class="dj-toolbar__group"><span>a<div class="dj-toolbar__separator"' in out

    def test_the_overflow_menu_stays_outside_every_group(self) -> None:
        out = _django(TOOLBAR["overflow-with-nested-divs"], {"label": "L"})
        assert out.endswith(
            '<div class="dj-toolbar__overflow-menu"><div><div>L</div></div></div></div>'
            '<div class="dj-toolbar__group">b</div></div>'
        )
        assert out.count("dj-toolbar__group") == 2

    def test_the_default_id_is_derived_from_the_arguments_on_both_engines(self) -> None:
        src = TOOLBAR["default-id"]
        first, second = _django(src, {"label": "v"}), _rust(src, {"label": "v"})
        assert first == second
        assert 'id="toolbar-' in first
        assert _django(src, {"label": "v"}) == first  # the same on every render
        assert _django(src, {"label": "w"}) != first  # other arguments, another id
        assert 'id="mine"' in _django('{% toolbar id="mine" %}x{% endtoolbar %}')

    def test_an_empty_toolbar_draws_no_group(self) -> None:
        assert "dj-toolbar__group" not in _django(TOOLBAR["empty"])


def test_regression_toolbar_wraps_a_button_with_a_variable_in_one_group() -> None:
    """#4331: the Django ``{% toolbar %}`` wrapped each child NODE in its own group, so
    ``<button>{{ x }}</button>`` (three nodes: text, variable, text) became
    ``<div class="dj-toolbar__group"><button></div><div ...>X</div><div ...></button></div>``."""
    src = '{% toolbar id="t" %}<button>{{ x }}</button>{% endtoolbar %}'
    out = _django(src, {"x": "X"})
    assert out == (
        '<div class="dj-toolbar dj-toolbar--md dj-toolbar--default" id="t" role="toolbar">'
        '<div class="dj-toolbar__group"><button>X</button></div></div>'
    )
    assert out == _rust(src, {"x": "X"})


def test_the_django_nodes_call_the_handlers(monkeypatch) -> None:
    """Gate-off: put Python markup back in a node and this fails."""
    from djust.components import rust_handlers

    for src, handler in (
        ("{% sidebar %}{% endsidebar %}", "SidebarHandler"),
        ("{% sidebar_item %}{% endsidebar_item %}", "SidebarItemHandler"),
        ("{% sidebar_section %}", "SidebarSectionHandler"),
        ("{% nav_menu %}{% endnav_menu %}", "NavMenuHandler"),
        ("{% nav_item %}{% endnav_item %}", "NavItemHandler"),
        ("{% toolbar %}{% endtoolbar %}", "ToolbarHandler"),
    ):
        monkeypatch.setattr(
            getattr(rust_handlers, handler),
            "render_kwargs",
            lambda self, *a, _h=handler: "<" + _h + ">",
        )
        assert _django(src) == "<" + handler + ">", src
