"""accordion, form_array and app_shell are one implementation on both engines (#4331, #4002 s4).

Each tag was drawn twice: by a native handler (the Rust engine) and by Python markup (Django's
engine), with different contracts:

* ``accordion`` / ``accordion_item``: Django opened an item from the parent's ``active``, put the
  ``event`` on the parent, ignored an item's ``open=`` and dropped body that was not an item; the
  native item opened from its own ``open=`` / ``event`` and kept the body. The merged renderer
  (``AccordionHandler`` / ``AccordionItemHandler``) takes both (D6). Its ARIA is the WAI-ARIA
  accordion pattern: the panel is always drawn (``hidden`` while closed), so ``aria-controls``
  never dangles (Django's did) and every panel has ``id`` / ``role="region"`` /
  ``aria-labelledby`` (the native one had none).
* ``form_array``: Django repeated the body once per row (#930); the native twin ignored the body.
  The native twin is gone: the tag takes the raw-body route, and ``FormArrayHandler`` draws the
  markup from the rows the Django node renders.
* ``app_shell``: Django dropped body content outside the slot tags; the native handler kept it.
  The Django node now calls ``AppShellHandler``. The Rust root-proof protocol is untouched
  (``test_app_shell_rust_engine_3448.py``, ``test_core_layout_root_provenance.py``).
"""

from __future__ import annotations

import re

import pytest
from django.utils.safestring import mark_safe

from djust import _rust as _rust_mod
from djust.components import rust_handlers as rh
from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust

HOSTILE = "<script>alert(\"x\")</script> 'q' & co"

CONTEXTS = {
    "typical": {"label": "Hello", "active": "s2", "rows": [{"value": "a"}, {"value": "b"}]},
    "hostile": {"label": HOSTILE, "active": HOSTILE, "rows": [{"value": HOSTILE, "x": HOSTILE}]},
    "safe": {
        "label": mark_safe("<b>bold</b>"),
        "active": mark_safe("s1"),
        "rows": [{"value": mark_safe('" onclick="x'), "x": mark_safe("<i>i</i>")}],
    },
    "missing": {},
    "none": {"label": None, "active": None, "rows": None},
}


def _render_template(src: str, ctx: dict) -> str:
    rh.register_with_rust_engine()
    return str(_rust_mod.render_template(src, dict(ctx)))


def _same(src: str, ctx: dict | None = None) -> str:
    """Both engines (and both Rust entry points) draw ``src`` the same; the bytes."""
    ctx = ctx or {}
    django = _django(src, ctx)
    assert django == _rust(src, ctx), src
    assert django == _render_template(src, ctx), src
    return django


# --------------------------------------------------------------------------- accordion

ITEM = '{%% accordion_item id="%s" title=label %%}<p>{{ label }}</p>{%% endaccordion_item %%}'

ACCORDIONS = {
    "typical": '{% accordion id="acc" active=active %}'
    + ITEM % "s1"
    + ITEM % "s2"
    + "{% endaccordion %}",
    "item-open": '{% accordion id="acc" %}{% accordion_item id="s1" title="A" open=True %}x'
    '{% endaccordion_item %}{% accordion_item id="s2" title="B" %}y{% endaccordion_item %}'
    "{% endaccordion %}",
    "parent-and-item-open": '{% accordion active="s2" %}'
    '{% accordion_item id="s1" title="A" open=True %}x{% endaccordion_item %}'
    '{% accordion_item id="s2" title="B" %}y{% endaccordion_item %}{% endaccordion %}',
    "events": '{% accordion event="parent_ev" component_id="c" %}'
    '{% accordion_item id="s1" title="A" %}x{% endaccordion_item %}'
    '{% accordion_item id="s2" title="B" event="own_ev" %}y{% endaccordion_item %}'
    "{% endaccordion %}",
    "no-ids": '{% accordion active="item-1" %}'
    '{% accordion_item title="A" %}x{% endaccordion_item %}'
    '{% accordion_item title="B" %}y{% endaccordion_item %}{% endaccordion %}',
    "non-item-body": '{% accordion id="acc" %}<h3>{{ label }}</h3>'
    + ITEM % "s1"
    + "tail {{ label }}{% endaccordion %}",
    "nested": '{% accordion id="outer" active="o" component_id="oc" %}'
    '{% accordion_item id="o" title="O" %}'
    '{% accordion id="inner" active="i" %}' + ITEM % "i" + "{% endaccordion %}"
    "{% endaccordion_item %}" + ITEM % "p" + "{% endaccordion %}",
    "in-for": '{% accordion id="acc" active="s2" %}{% for i in ids %}'
    "{% accordion_item id=i title=i %}{{ i }}{% endaccordion_item %}{% endfor %}"
    "{% endaccordion %}",
    "hostile-attrs": "{% accordion id=label active=label event=label %}"
    "{% accordion_item id=label title=label %}x{% endaccordion_item %}{% endaccordion %}",
    "standalone-item": ITEM % "q",
    "empty": "{% accordion %}{% endaccordion %}",
}


@pytest.mark.parametrize("ctx", sorted(CONTEXTS))
@pytest.mark.parametrize("case", sorted(ACCORDIONS))
def test_accordion_draws_the_same_bytes_on_both_engines(case: str, ctx: str) -> None:
    html = _same(ACCORDIONS[case], {**CONTEXTS[ctx], "ids": ["s1", "s2"]})
    assert "<script>" not in html


def _items(html: str) -> list[tuple[str, str, str]]:
    """``(item class, trigger aria-expanded, panel hidden?)`` per item."""
    return re.findall(
        r'<div class="(dj-accordion-item[^"]*)"><button [^>]*aria-expanded="(\w+)"[^>]*>.*?'
        r'<div class="dj-accordion__content"[^>]*?( hidden)?>',
        html,
    )


def test_the_parent_active_opens_the_item_it_names() -> None:
    html = _same(ACCORDIONS["typical"], CONTEXTS["typical"])
    assert _items(html) == [
        ("dj-accordion-item", "false", " hidden"),
        ("dj-accordion-item dj-accordion-item--open", "true", ""),
    ]


def test_an_item_open_opens_it_too() -> None:
    """D6: the native contract (item ``open=``) is kept beside the Django one (parent
    ``active``); Django's engine used to ignore ``open=``."""
    assert [i[1] for i in _items(_same(ACCORDIONS["item-open"]))] == ["true", "false"]
    assert [i[1] for i in _items(_same(ACCORDIONS["parent-and-item-open"]))] == ["true", "true"]


def test_the_parent_event_is_the_default_and_an_item_event_overrides_it() -> None:
    html = _same(ACCORDIONS["events"])
    assert re.findall(r'dj-click="(\w+)"', html) == ["parent_ev", "own_ev"]
    assert html.count('data-component-id="c"') == 2


def test_an_item_without_an_id_is_named_by_its_position() -> None:
    html = _same(ACCORDIONS["no-ids"])
    assert re.findall(r'dj-value-value="([^"]+)"', html) == ["item-0", "item-1"]
    assert [i[1] for i in _items(html)] == ["false", "true"]


def test_body_that_is_not_an_item_is_kept_in_place() -> None:
    """D6: Django's engine dropped it; the native handler kept it."""
    html = _same(ACCORDIONS["non-item-body"], CONTEXTS["typical"])
    assert html.startswith('<div class="dj-accordion" id="acc"><h3>Hello</h3><div class="dj-acc')
    assert html.endswith("</div></div>tail Hello</div>")


def test_a_nested_accordion_keeps_its_own_items() -> None:
    html = _same(ACCORDIONS["nested"], CONTEXTS["typical"])
    assert re.findall(r'id="(\w+)-trigger-(\w+)"', html) == [
        ("outer", "o"),
        ("inner", "i"),
        ("outer", "p"),
    ]
    assert html.count('data-component-id="oc"') == 2
    assert "djust-accordion-item" not in html


def test_items_inside_a_for_loop_are_the_accordion_s_items() -> None:
    html = _same(ACCORDIONS["in-for"], {"ids": ["s1", "s2"]})
    assert [i[1] for i in _items(html)] == ["false", "true"]


def test_a_marked_safe_title_is_escaped_d3() -> None:
    html = _same(ACCORDIONS["typical"], CONTEXTS["safe"])
    assert "<span>&lt;b&gt;bold&lt;/b&gt;</span>" in html
    # The body is the template's own: ``{{ label }}`` of a marked-safe value stays markup.
    assert "<p><b>bold</b></p>" in html


def test_hostile_values_cannot_leave_their_attribute() -> None:
    html = _same(ACCORDIONS["hostile-attrs"], CONTEXTS["hostile"])
    assert "<script>" not in html
    assert 'dj-click="&lt;script&gt;alert(&quot;x&quot;)' in html


# Named regressions for the two ARIA bugs.


def test_regression_django_aria_controls_named_a_panel_that_was_not_drawn() -> None:
    """Django's engine drew ``aria-controls="acc-panel-s1"`` on a closed item and no element
    with that id. Every ``aria-controls`` now names a drawn panel."""
    html = _django(ACCORDIONS["typical"], CONTEXTS["typical"])
    controls = re.findall(r'aria-controls="([^"]+)"', html)
    assert controls == ["acc-panel-s1", "acc-panel-s2"]
    for panel in controls:
        assert re.search(rf'<div class="dj-accordion__content" id="{panel}" role="region"', html)


def test_regression_native_item_had_no_aria_controls_id_or_region() -> None:
    """The native item drew no ``aria-controls``, panel ``id`` or ``role="region"``."""
    html = _rust(ACCORDIONS["typical"], CONTEXTS["typical"])
    for iid in ("s1", "s2"):
        assert f'id="acc-trigger-{iid}"' in html
        assert f'aria-controls="acc-panel-{iid}"' in html
        assert (
            f'<div class="dj-accordion__content" id="acc-panel-{iid}" role="region" '
            f'aria-labelledby="acc-trigger-{iid}"'
        ) in html


def test_the_classes_the_client_and_css_read_are_drawn() -> None:
    """``51-keyboard-nav.js`` reads ``.dj-accordion`` / ``.dj-accordion__trigger``; the gallery
    shim and ``components.css`` read ``.dj-accordion-item(--open)``, ``.dj-accordion__chevron``
    and ``.dj-accordion__content``."""
    html = _rust(ACCORDIONS["typical"], CONTEXTS["typical"])
    for cls in (
        'class="dj-accordion"',
        'class="dj-accordion__trigger"',
        "dj-accordion-item--open",
        "dj-accordion__chevron",
        'class="dj-accordion__content"',
    ):
        assert cls in html, cls


def test_a_forged_marker_is_kept_as_it_is() -> None:
    """A marker with no item markup after it (only a template author can write one: escaped
    data cannot spell ``<!--``) is left alone."""
    key = b'["x","T",false,null,3]'.hex()
    src = "{% accordion %}{{ forged }}{% endaccordion %}"
    ctx = {"forged": mark_safe(f"<!--djust-accordion-item:{key}-->abc")}
    assert _same(src, ctx) == (
        f'<div class="dj-accordion" id="accordion"><!--djust-accordion-item:{key}-->abc</div>'
    )
    assert _same(src, {"forged": f"<!--djust-accordion-item:{key}-->"}).count("&lt;!--") == 1


def test_the_django_accordion_nodes_call_the_handlers(monkeypatch) -> None:
    """Gate-off: put Python markup back in a node and this fails."""
    monkeypatch.setattr(rh.AccordionHandler, "render_kwargs", lambda self, kw, c: "<acc>" + c)
    monkeypatch.setattr(rh.AccordionItemHandler, "render_kwargs", lambda self, kw, c: "<item>")
    assert _django(ACCORDIONS["typical"], CONTEXTS["typical"]) == "<acc><item><item>"


# --------------------------------------------------------------------------- form_array

FORM_ARRAYS = {
    "default": '{% form_array name="emails" rows=rows %}{% endform_array %}',
    "blank-body": '{% form_array name="emails" rows=rows %}  \n {% endform_array %}',
    "body": '{% form_array name="items" rows=rows min=0 max=2 %}'
    '<input name="items[{{ row_index }}]" value="{{ row.value }}">{{ row.x }}'
    "[{{ forloop.counter }}{% if forloop.last %}L{% endif %}]{% endform_array %}",
    "nested-children": "{% form_array rows=rows add_label=label class=label %}"
    "{% if row.value %}<b>{{ row.value|upper }}</b>{% else %}-{% endif %}"
    "{% endform_array %}",
    "no-rows": '{% form_array name="x" %}{{ row.value }}|{% endform_array %}',
    "hostile-operands": "{% form_array name=label add_event=label remove_event=label "
    "min=label max=label %}{% endform_array %}",
}


@pytest.mark.parametrize("ctx", sorted(CONTEXTS))
@pytest.mark.parametrize("case", sorted(FORM_ARRAYS))
def test_form_array_draws_the_same_bytes_on_both_engines(case: str, ctx: str) -> None:
    html = _same(FORM_ARRAYS[case], CONTEXTS[ctx])
    assert "<script>" not in html


def test_regression_native_form_array_repeats_its_body_per_row() -> None:
    """The native twin drew a text input per row and dropped the body; it now repeats the body
    with ``row``, ``row_index`` and ``forloop`` pushed, as Django's engine did (#930, D6)."""
    html = _rust(FORM_ARRAYS["body"], CONTEXTS["typical"])
    assert '<input name="items[0]" value="a">[1]' in html
    assert '<input name="items[1]" value="b">[2L]' in html
    assert "dj-form-array__input" not in html
    assert html == _django(FORM_ARRAYS["body"], CONTEXTS["typical"])


def test_a_blank_body_draws_the_default_inputs() -> None:
    html = _same(FORM_ARRAYS["blank-body"], CONTEXTS["typical"])
    assert 'name="emails[0]" value="a"' in html and 'name="emails[1]" value="b"' in html


def test_a_marked_safe_row_value_is_escaped_in_the_default_input() -> None:
    html = _same(FORM_ARRAYS["default"], CONTEXTS["safe"])
    assert 'value="&quot; onclick=&quot;x"' in html


def test_form_array_takes_the_raw_body_route() -> None:
    assert "form_array" in rh.RAW_BODY_TAGS
    assert "form_array" not in {name for name, _end, _h in rh.NATIVE_BLOCK_HANDLERS}


def test_the_django_form_array_node_calls_the_handler(monkeypatch) -> None:
    monkeypatch.setattr(
        rh.FormArrayHandler, "render_kwargs", lambda self, kw, rows, bodies: f"<fa {bodies}>"
    )
    src = FORM_ARRAYS["body"]
    assert _django(src, CONTEXTS["typical"]).startswith('<fa [\'<input name="items[0]"')
    assert _render_template(src, CONTEXTS["typical"]) == _django(src, CONTEXTS["typical"])


# --------------------------------------------------------------------------- app_shell

SHELL_BODY = (
    "{% app_header %}<h1>{{ label }}</h1>{% endapp_header %}"
    "{% app_sidebar %}<nav>{{ label }}</nav>{% endapp_sidebar %}"
    "{% app_content %}<p>{{ label }}</p>{% endapp_content %}"
)

SHELLS = {
    "typical": '{% app_shell id="s" class=label %}' + SHELL_BODY + "{% endapp_shell %}",
    "stray": "{% app_shell %}stray {{ label }}" + SHELL_BODY + " tail{% endapp_shell %}",
    "only-stray": "{% app_shell sidebar_collapsed=True %}<b>{{ label }}</b>{% endapp_shell %}",
    "empty": "{% app_shell %}{% endapp_shell %}",
}


@pytest.mark.parametrize("ctx", sorted(CONTEXTS))
@pytest.mark.parametrize("case", sorted(SHELLS))
def test_app_shell_draws_the_same_bytes_on_both_engines(case: str, ctx: str) -> None:
    html = _same(SHELLS[case], CONTEXTS[ctx])
    assert "<script>" not in html


def test_regression_django_app_shell_dropped_body_outside_the_slots() -> None:
    html = _django(SHELLS["stray"], CONTEXTS["typical"])
    assert html.startswith(
        '<div class="dj-app-shell" id="app-shell"><aside class="dj-app-shell__sidebar">'
    )
    assert "stray Hello" in html and " tail</div></div>" in html


def test_the_django_app_shell_node_calls_the_handler(monkeypatch) -> None:
    monkeypatch.setattr(rh.AppShellHandler, "render_kwargs", lambda self, kw, c: "<shell>")
    assert _django(SHELLS["typical"], CONTEXTS["typical"]) == "<shell>"
    # The Rust engine still draws it natively (the root-proof protocol), not through the node.
    assert _rust(SHELLS["typical"], CONTEXTS["typical"]) != "<shell>"
