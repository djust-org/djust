"""Which built-in block tags count the ``dj-model`` bindings in their body.

A view's dj-model write allowlist holds only the bindings its last render
emitted from template text, and the body of a custom block tag never counts
unless the tag's handler declares ``EMITS_BODY``: it always puts its rendered
body into its output (the raw-body tags render exactly the parts they emit).
This pins the list of built-in tags that declare it, checks that each one emits
its body whatever its arguments, and that the Django-engine node of each one
declares it too.
"""

from __future__ import annotations

import pytest

from djust.components.rust_handlers import EMITS_BODY_TAGS

_MARK = '<input dj-model="mark_field">'

#: Argument shapes a tag might use to hide its body; none may.
_ARGS = (
    "",
    'title="T"',
    'id="x" label="L" name="n" event="e"',
    "open=False",
    "visible=False",
    "show=False",
    "active=False",
    "expanded=False",
    "collapsed=True",
    "loading=True",
    "dismissible=True",
    'variant="danger"',
)


def _body(tag: str) -> str:
    if tag == "tabs":
        return '{% tab id="a" label="A" %}' + _MARK + "{% endtab %}"
    if tag == "split_pane":
        return _MARK + "{% pane %}" + _MARK
    return _MARK


#: Library block tags served by the ``{% load djust_components %}`` bridge.
_LOADED = ("aspect_ratio", "callout", "scroll_area", "sticky_header", "tab")


def _first_party_block_tags() -> dict:
    """Every block and raw-block tag djust registers with the Rust engine."""
    from djust._rust import render_template
    from djust.components import rust_handlers as rh

    # Bridge the library's own block tags, as a template that loads it does.
    render_template("{% load djust_components %}", {})
    tags = {name: end for name, end, _handler in rh.BLOCK_HANDLERS}
    for name in (*_LOADED, "split_pane", "tabs", "call", "component", "slot", "dj_suspense"):
        tags[name] = "end" + name
    return tags


def test_emits_body_is_declared_by_exactly_the_pinned_tags():
    from djust._rust import block_tag_handler_emits_body

    tags = _first_party_block_tags()
    assert EMITS_BODY_TAGS <= set(tags)
    declared = {name for name in tags if block_tag_handler_emits_body(name)}
    assert declared == EMITS_BODY_TAGS


def test_tags_that_can_drop_or_escape_their_body_do_not_declare_it():
    from djust._rust import block_tag_handler_emits_body

    for name in (
        "modal",
        "bottom_sheet",
        "dropdown_menu",
        "form_array",
        "confirm_dialog_block",
        "await",
        "agent_step",
        "copyable_text",
        "nav_item",
        "call",
        "component",
        "slot",
        "dj_suspense",
    ):
        assert not block_tag_handler_emits_body(name), name


@pytest.mark.parametrize("tag", sorted(EMITS_BODY_TAGS))
def test_each_emits_body_tag_emits_its_body_for_any_arguments(tag):
    from djust._rust import render_template

    end = _first_party_block_tags()[tag]
    for args in _ARGS:
        html = render_template("{%% %s %s %%}%s{%% %s %%}" % (tag, args, _body(tag), end), {})
        assert _MARK.replace(">", "") in html, (tag, args, html)


@pytest.mark.parametrize("tag", sorted(EMITS_BODY_TAGS))
def test_the_django_node_of_each_emits_body_tag_declares_it(tag):
    from django.template import Engine

    engine = Engine(libraries={"c": "djust.components.templatetags.djust_components"})
    tpl = engine.from_string("{%% load c %%}{%% %s %%}%s{%% end%s %%}" % (tag, _body(tag), tag))
    node = [n for n in tpl.nodelist if type(n).__name__ != "LoadNode"][0]
    # Django's DropdownNode renders its body and drops it while closed.
    expected = tag != "dropdown"
    assert getattr(type(node), "EMITS_BODY", False) is expected, type(node)


def _view(src: str, **attrs):
    from djust import LiveView

    names = ["mark_field", "is_staff", *attrs]
    body = {
        "template": src,
        "mark_field": "",
        "is_staff": False,
        **attrs,
        "mount": lambda self, request, **kwargs: None,
        "get_context_data": lambda self, **kwargs: {n: getattr(self, n) for n in names},
    }
    view = type("EmitsBodyView", (LiveView,), {"exposure_policy": "explicit", **body})()
    view.mount(None)
    return view


@pytest.mark.django_db
@pytest.mark.parametrize(
    "inner",
    [
        '{% card title="c" %}' + _MARK + "{% endcard %}",
        '{% accordion %}{% accordion_item title="a" %}'
        + _MARK
        + "{% endaccordion_item %}{% endaccordion %}",
        '{% tabs %}{% tab id="a" label="A" %}' + _MARK + "{% endtab %}{% endtabs %}",
        "{% split_pane %}x{% pane %}" + _MARK + "{% endsplit_pane %}",
    ],
)
def test_bindings_in_an_emits_body_tag_follow_the_branch(inner):
    v = _view("<div dj-root>{% if is_staff %}" + inner + "{% endif %}</div>", is_staff=True)
    v.render_with_diff(None)
    assert v._dj_model_fields == frozenset({"mark_field"})
    v.update_model(field="mark_field", value="ok")
    assert v.mark_field == "ok"
    v.is_staff = False
    v.render_with_diff(None)
    assert v._dj_model_fields == frozenset()


@pytest.mark.django_db
def test_a_tag_that_can_hide_its_body_does_not_count():
    v = _view('<div dj-root>{% modal id="m" open=True %}' + _MARK + "{% endmodal %}</div>")
    html, _, _ = v.render_with_diff(None)
    assert "mark_field" in html
    assert v._dj_model_fields == frozenset()


@pytest.mark.django_db
def test_a_raw_body_tag_inside_a_hiding_tag_does_not_count():
    v = _view(
        '<div dj-root>{% modal id="m" open=True %}{% tabs %}{% tab id="a" label="A" %}'
        + _MARK
        + "{% endtab %}{% endtabs %}{% endmodal %}</div>"
    )
    html, _, _ = v.render_with_diff(None)
    assert "mark_field" in html
    assert v._dj_model_fields == frozenset()


def test_t030_does_not_flag_emits_body_tags():
    from djust.checks.templates import _check_dj_model_in_custom_block_tag

    errors: list = []
    src = (
        '{% card %}<input dj-model="a">{% endcard %}'
        '{% tabs %}{% tab id="t" %}<input dj-model="b">{% endtab %}{% endtabs %}'
        '{% modal %}<input dj-model="c">{% endmodal %}'
    )
    _check_dj_model_in_custom_block_tag(src, "t.html", "/t.html", errors)
    assert [e.msg.split('"')[1] for e in errors] == ["c"]
