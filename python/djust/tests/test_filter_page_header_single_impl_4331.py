"""``filter_bar`` / ``filter_*`` controls and ``page_header`` are one implementation (#4331).

The Django nodes and the native handlers both draw through ``render_filter_bar`` and
``render_page_header``. Remaining differences were the bar's default id (a random one on Django)
and the escape policy for a marked-safe operand (D3: it is escaped, as native always did), plus
the empty actions ``<div>`` a ``page_header_actions`` that drew nothing used to leave on native.
"""

from __future__ import annotations

import pytest
from django.utils.safestring import mark_safe

from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust

HOSTILE = "<script>alert(\"x\")</script> 'q' & co"

CONTEXTS = {
    "typical": {"label": "Status", "opts": ["a", "b"], "title": "Products", "v": "b"},
    "hostile": {
        "label": HOSTILE,
        "opts": [HOSTILE, {"value": HOSTILE, "label": HOSTILE}],
        "title": HOSTILE,
        "v": HOSTILE,
    },
    "safe": {
        "label": mark_safe("<b>x</b>"),
        "opts": [mark_safe("<i>o</i>")],
        "title": mark_safe("<b>T</b>"),
        "v": mark_safe("<u>v</u>"),
    },
    "none": {"label": None, "opts": None, "title": None, "v": None},
    "missing": {},
}

FILTER_SRCS = {
    "full": '{% filter_bar id="fb" %}{% filter_select name="s" label=label options=opts value=v %}'
    '{% filter_date_range name="d" label=label start=v end=v %}'
    '{% filter_search name="q" placeholder=label value=v debounce=200 %}{% endfilter_bar %}',
    "default-id": '{% filter_bar %}{% filter_search name="q" %}{% endfilter_bar %}',
    "default-id-select": '{% filter_bar %}{% filter_select name="s" options=opts %}{% endfilter_bar %}',
    "literal": '{% filter_bar %}{% filter_select name="<b>" label="<i>L</i>" '
    'options="x" %}{% endfilter_bar %}',
    "event": '{% filter_bar event="e" clear_event="c" class=label %}'
    '{% filter_search name="q" value=v %}{% endfilter_bar %}',
    "extra": "{% filter_bar %}<p>{{ label }}</p>{% endfilter_bar %}",
}

HEADER_SRCS = {
    "full": "{% page_header title=title subtitle=label description=label custom_class=label %}"
    "<nav>{{ label }}</nav>{% page_header_actions %}<a>{{ label }}</a>"
    "{% endpage_header_actions %}{% endpage_header %}",
    "literal": '{% page_header title="<b>T</b>" subtitle="S" %}{% endpage_header %}',
    "title-only": "{% page_header title=title %}{% endpage_header %}",
    "none": "{% page_header %}{% endpage_header %}",
    "empty-actions": '{% page_header title="T" %}{% page_header_actions %}{% endpage_header_actions %}'
    "{% endpage_header %}",
    "whitespace-actions": '{% page_header title="T" %}{% page_header_actions %}  \n {% endpage_header_actions %}'
    "{% endpage_header %}",
    "tag-renders-empty": '{% page_header title="T" %}{% page_header_actions %}{{ missing }}'
    "{% endpage_header_actions %}{% endpage_header %}",
}


@pytest.mark.parametrize("ctx", sorted(CONTEXTS))
@pytest.mark.parametrize("name", sorted(FILTER_SRCS))
def test_filter_tags_draw_the_same_bytes(name: str, ctx: str) -> None:
    assert _django(FILTER_SRCS[name], CONTEXTS[ctx]) == _rust(FILTER_SRCS[name], CONTEXTS[ctx])


@pytest.mark.parametrize("ctx", sorted(CONTEXTS))
@pytest.mark.parametrize("name", sorted(HEADER_SRCS))
def test_page_header_draws_the_same_bytes(name: str, ctx: str) -> None:
    assert _django(HEADER_SRCS[name], CONTEXTS[ctx]) == _rust(HEADER_SRCS[name], CONTEXTS[ctx])


def test_default_filter_bar_id_is_deterministic_on_both_engines() -> None:
    src = FILTER_SRCS["default-id"]
    outs = {_django(src) for _ in range(3)} | {_rust(src) for _ in range(3)}
    assert len(outs) == 1
    assert 'id="filter-bar"' in outs.pop()


def test_marked_safe_operands_are_escaped_on_both_engines() -> None:
    ctx = CONTEXTS["safe"]
    for src in (FILTER_SRCS["full"], HEADER_SRCS["full"]):
        for out in (_django(src, ctx), _rust(src, ctx)):
            assert "<b>T</b>" not in out
            assert "&lt;b&gt;x&lt;/b&gt;" in out
    # The label of a control is never drawn raw (the page header's slot content is a body).
    for out in (_django(FILTER_SRCS["full"], ctx), _rust(FILTER_SRCS["full"], ctx)):
        assert "<b>x</b>" not in out and "<i>o</i>" not in out and "<u>v</u>" not in out


def test_hostile_values_never_reach_the_page_raw() -> None:
    for src, ctx in (
        (FILTER_SRCS["full"], CONTEXTS["hostile"]),
        (HEADER_SRCS["full"], CONTEXTS["hostile"]),
    ):
        for out in (_django(src, ctx), _rust(src, ctx)):
            assert "<script>" not in out


@pytest.mark.parametrize("engine", [_django, _rust])
def test_an_actions_slot_that_draws_nothing_draws_no_div(engine) -> None:
    for name in ("empty-actions", "whitespace-actions", "tag-renders-empty"):
        assert "dj-page-header__actions" not in engine(HEADER_SRCS[name])
    assert "dj-page-header__actions" in engine(HEADER_SRCS["full"], CONTEXTS["typical"])


def test_the_django_nodes_call_the_shared_renderers(monkeypatch) -> None:
    """Gate-off: put Python markup back in a node and this fails."""
    from djust.components import rust_handlers
    from djust.components.templatetags import djust_components as dc

    monkeypatch.setattr(rust_handlers, "render_page_header", lambda *a: "<ph-renderer>")
    assert _django(HEADER_SRCS["none"]) == "<ph-renderer>"
    monkeypatch.setattr(dc, "render_filter_bar", lambda *a, **k: "<fb-renderer>")
    assert _django(FILTER_SRCS["default-id"]) == "<fb-renderer>"


def test_a_positional_argument_is_refused_by_the_django_tags() -> None:
    from django.template import TemplateSyntaxError

    for src in (
        '{% page_header "Pos" %}{% endpage_header %}',
        '{% filter_bar "x" %}{% endfilter_bar %}',
    ):
        with pytest.raises(TemplateSyntaxError):
            _django(src)
