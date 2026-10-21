"""Rust-engine renders that diverged from Django's, each against Django's output.

Three defects, each compared with Django's own engine rendering the same source
and context:

1. **Include planning resolved what the render never would.** When a page needs
   lazy-container provenance (a dynamic ``{% include var %}`` or a ``dj-lazy``
   element anywhere in its tree), the planner in ``crates/djust_live`` walks
   EVERY ``{% include %}`` in the source -- outside the ``{% for %}`` and
   ``{% if %}`` around it -- and resolved each include's ``with`` bindings
   against the page context with ``?``. A binding that names a loop variable
   (``fields=m|lookup:r.agent_id`` inside ``{% for r %}``), or a name only a
   false branch would ever reach, raised "argument does not resolve" and the
   page 500ed. Django resolves an include's operand and bindings only when the
   include renders. The planner now treats what it cannot resolve as
   "unknowable", which plans conservatively (tracks provenance) instead of
   failing the render.

2. **Custom filters received ``str(obj)``.** A project ``@register.filter``
   was handed the ``str()`` of any object in the context -- its value AND a
   resolved argument -- because the filter registry crossed the PyO3 boundary
   with ``IntoPyObject``, which stringifies an opaque object. It now uses the
   same live-object conversion the tag bridge uses (#2731).

3. **``{% tabs %}`` rendered every pane and no tab list.** Its Rust block
   handler was handed the already-rendered body, so it could neither read the
   ``{% tab %}`` children's ids and labels nor pick one pane. It now renders on
   the raw-block path through Django's own ``do_tabs``, like ``split_pane``.
"""

from __future__ import annotations

import dataclasses
import re

import pytest
from django import template
from django.template import Context, Engine

from djust._rust import RustLiveView, template_needs_provenance

# A Django library for Django's side of each comparison; ``Engine(libraries=)``
# imports it by this module's path. The same callables are bridged to the Rust
# registry by the ``_bridged_filters`` fixture.
register = template.Library()


@register.filter(name="gapsparity_lookup")
def _lookup(mapping, key):
    if not mapping:
        return None
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter(name="gapsparity_kind")
def _kind(value, arg=None):
    """The TYPES the filter receives -- what Django hands it, not its text."""
    if arg is None:
        return type(value).__name__
    return "%s/%s" % (type(value).__name__, type(arg).__name__)


@register.filter(name="gapsparity_server")
def _server(value):
    """An attribute walk only the object itself can answer."""
    return value.approval.server


@dataclasses.dataclass
class Approval:
    agent_id: object
    server: str = "srv"


@dataclasses.dataclass
class Row:
    name: str
    approval: Approval

    def __str__(self) -> str:
        return "STR(%s)" % self.name


def _rows() -> list:
    return [Row("a", Approval(None)), Row("b", Approval("ag1"))]


@pytest.fixture
def _bridged_filters():
    from djust._rust import unregister_custom_filter
    from djust.template_filters import register_django_filter

    for name, func in register.filters.items():
        assert register_django_filter(name, func)
    yield
    for name in register.filters:
        unregister_custom_filter(name)


def _django(source: str, ctx: dict, dirs: list | None = None) -> str:
    engine = Engine(dirs=dirs or [], libraries={"gapsparity": __name__}, builtins=[__name__])
    return engine.from_string(source).render(Context(ctx))


_DJ_IF = re.compile(r"<!--/?dj-if[^>]*-->")


def _rust(source: str, ctx: dict, dirs: list | None = None) -> str:
    view = RustLiveView(source, dirs or [])
    view.update_state(ctx)
    return _DJ_IF.sub("", view.render())


def _rust_planned(source: str, ctx: dict, dirs: list) -> str:
    """The HTTP render path: ``djust._render_provenance.render_html``.

    ``template_needs_provenance`` is its source-only gate; when it says yes the
    render goes through ``render_lazy_html``, whose resolved planning pass is
    the one that raised.
    """
    assert template_needs_provenance(source, dirs), "the case must reach planning"
    view = RustLiveView(source, dirs)
    view.update_state(ctx)
    html, _spans, _origins = view.render_lazy_html()
    return _DJ_IF.sub("", html)


# --------------------------------------------------------------------------
# 1. Include planning
# --------------------------------------------------------------------------

# Every case opens with a DYNAMIC include: that is what sends a page through
# resolved planning (a literal-only tree never reaches it). Gate-off (the
# planner's resolutions back to `?`): every case fails except
# `missing-child-in-a-false-if`, a control -- the planner already skipped a
# file that does not exist.
PLANNING = {
    "loop-variable-in-a-builtin-filter-argument": (
        "{% include side %}{% for r in rows %}"
        "{% include 'card.html' with r=r fields='p'|add:r.agent_id %}{% endfor %}"
    ),
    "loop-variable-in-a-custom-filter-argument": (
        "{% include side %}{% for r in rows %}"
        "{% include 'card.html' with r=r fields=m|gapsparity_lookup:r.agent_id %}{% endfor %}"
    ),
    "loop-variable-as-the-include-operand-filter-argument": (
        "{% include side %}{% for r in rows %}"
        "{% include 'card'|add:r.suffix with r=r %}{% endfor %}"
    ),
    "unresolvable-binding-in-a-false-if": (
        "{% include side %}{% if flag %}"
        "{% include 'card.html' with fields=absent|add:other %}{% endif %}ok"
    ),
    "unparseable-child-in-a-false-if": (
        "{% include side %}{% if flag %}{% include 'broken.html' %}{% endif %}ok"
    ),
    "missing-child-in-a-false-if": (
        "{% include side %}{% if flag %}{% include 'nope/missing.html' %}{% endif %}ok"
    ),
}


@pytest.fixture
def planning_dir(tmp_path):
    (tmp_path / "side.html").write_text("<b>side</b>")
    (tmp_path / "card.html").write_text("<i>{{ r.agent_id }}:{{ fields }}</i>")
    (tmp_path / "card.x.html").write_text("<i>x{{ r.agent_id }}</i>")
    (tmp_path / "broken.html").write_text("{% if %}")
    return str(tmp_path)


def _planning_ctx() -> dict:
    return {
        "side": "side.html",
        "flag": False,
        "m": {"x": "F"},
        "rows": [
            {"agent_id": "x", "suffix": ".x.html"},
            {"agent_id": "z", "suffix": ".x.html"},
        ],
    }


@pytest.mark.usefixtures("_bridged_filters")
class TestIncludePlanningResolvesOnlyWhatRenders:
    @pytest.mark.parametrize("case", sorted(PLANNING))
    def test_the_planned_render_matches_django(self, case, planning_dir):
        source = PLANNING[case]
        ctx = _planning_ctx()
        expected = _django(source, ctx, [planning_dir])
        assert _rust_planned(source, ctx, [planning_dir]) == expected

    def test_a_binding_that_resolves_still_selects_a_nested_lazy_child(self, tmp_path):
        """The conservative answer must not cost the precise one: a binding
        the planner CAN resolve still decides which nested template it walks.
        """
        (tmp_path / "outer.html").write_text("{% include inner %}")
        (tmp_path / "plain.html").write_text("plain")
        (tmp_path / "lazy.html").write_text('<div dj-view="x.Child" dj-lazy></div>')
        source = '{% include "outer.html" with inner=which %}'
        dirs = [str(tmp_path)]
        for which, tracked in (("plain.html", False), ("lazy.html", True)):
            view = RustLiveView(source, dirs)
            view.update_state({"which": which})
            _html, spans, _origins = view.render_lazy_html()
            assert bool(spans) is tracked, which


# --------------------------------------------------------------------------
# 2. Custom filters receive the object
# --------------------------------------------------------------------------

CUSTOM = {
    "loop-variable-value": "{% for r in rows %}[{{ r|gapsparity_kind }}]{% endfor %}",
    "nested-object-value": "{% for r in rows %}[{{ r.approval|gapsparity_kind }}]{% endfor %}",
    "list-and-element": "{{ rows|gapsparity_kind }}|{{ rows.0|gapsparity_kind }}",
    "object-argument": "{% for r in rows %}[{{ r.name|gapsparity_kind:r.approval }}]{% endfor %}",
    # Control: `None` crossed as `None` before the fix too.
    "none-argument": (
        "{% for r in rows %}[{{ r.name|gapsparity_kind:r.approval.agent_id }}]{% endfor %}"
    ),
    "attribute-walk-inside-the-filter": "{% for r in rows %}[{{ r|gapsparity_server }}]{% endfor %}",
}


@pytest.mark.usefixtures("_bridged_filters")
class TestCustomFiltersReceiveTheObject:
    @pytest.mark.parametrize("case", sorted(CUSTOM))
    def test_matches_django(self, case):
        source = CUSTOM[case]
        expected = _django(source, {"rows": _rows()})
        assert _rust(source, {"rows": _rows()}) == expected

    def test_the_object_is_the_one_in_the_context(self):
        """Not a copy, not a wrapper: ``is`` the row the view holds."""
        seen = []

        def capture(value):
            seen.append(value)
            return ""

        from djust._rust import unregister_custom_filter
        from djust.template_filters import register_django_filter

        register_django_filter("gapsparity_capture", capture)
        try:
            rows = _rows()
            _rust("{% for r in rows %}{{ r|gapsparity_capture }}{% endfor %}", {"rows": rows})
        finally:
            unregister_custom_filter("gapsparity_capture")
        assert [id(v) for v in seen] == [id(r) for r in rows]


# --------------------------------------------------------------------------
# 3. {% tabs %}
# --------------------------------------------------------------------------

TABS = (
    '{% tabs id="t" active=tab %}'
    '{% tab id="a" label="Alpha" %}<p>pane {{ x }} A</p>{% endtab %}'
    '{% tab id="b" label="Beta" icon="*" %}<p>pane B</p>{% endtab %}'
    "{% endtabs %}"
)


@pytest.fixture
def _component_tags():
    from djust.components.rust_handlers import register_with_rust_engine

    register_with_rust_engine()


def _django_tabs(source: str, ctx: dict) -> str:
    engine = Engine(
        libraries={"djust_components": "djust.components.templatetags.djust_components"}
    )
    return engine.from_string("{% load djust_components %}" + source).render(Context(ctx))


@pytest.mark.usefixtures("_component_tags")
class TestTabsRendersOnlyTheActivePane:
    @pytest.mark.parametrize("tab", ["a", "b", ""])
    def test_matches_django(self, tab):
        ctx = {"tab": tab, "x": "<x>"}
        assert _rust(TABS, ctx) == _django_tabs(TABS, ctx)

    def test_one_pane_and_a_tab_list(self):
        html = _rust(TABS, {"tab": "b", "x": ""})
        assert 'role="tablist"' in html
        assert html.count('role="tab"') == 2
        assert html.count('role="tabpanel"') == 1
        assert "pane B" in html and "pane  A" not in html

    def test_no_load_needed(self):
        """A Rust template uses component tags without ``{% load %}``; the
        body's ``{% tab %}`` blocks must still parse."""
        assert "pane B" in _rust(TABS, {"tab": "b", "x": ""})
