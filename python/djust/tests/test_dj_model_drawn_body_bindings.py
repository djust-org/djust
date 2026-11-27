"""dj-model bindings the render counts match the markup the page carries.

A block tag that renders its body and then drops it (``dropdown`` while
closed), or hands it to its parent (``page_header_actions``), counts the body's
bindings only when the body is drawn; a ``{% filter %}`` body, which its filters
rewrite, never counts. T030 flags a binding the render will not count, including
one in a template a literal ``{% include %}`` pulls into such a body, and reads
tag arguments that hold ``%``.
"""

from __future__ import annotations

import pytest

_MARK = '<input dj-model="mark_field">'


def _django_render(src: str, **context):
    """Render ``src`` with Django's engine; return (html, recorded fields)."""
    from django.template import Context, Engine

    from djust._dj_model_bindings import record_bindings

    engine = Engine(libraries={"c": "djust.components.templatetags.djust_components"})
    tpl = engine.from_string("{% load c %}" + src)
    with record_bindings() as fields:
        html = tpl.render(Context(context))
    return html, fields


def _t030(src: str, template_dirs=()) -> list:
    from djust.checks.templates import _check_dj_model_in_custom_block_tag

    errors: list = []
    _check_dj_model_in_custom_block_tag(
        src, "t.html", "/t.html", errors, template_dirs=template_dirs
    )
    return errors


def _view(src: str, **attrs):
    from djust import LiveView

    names = ["mark_field", *attrs]
    body = {
        "template": src,
        "mark_field": "",
        **attrs,
        "mount": lambda self, request, **kwargs: None,
        "get_context_data": lambda self, **kwargs: {n: getattr(self, n) for n in names},
    }
    view = type("HeldBindingsView", (LiveView,), {"exposure_policy": "explicit", **body})()
    view.mount(None)
    return view


# --- {% filter %} -----------------------------------------------------------------------------


def test_filter_body_does_not_count_under_django_engine():
    html, fields = _django_render("{% filter force_escape %}" + _MARK + "{% endfilter %}")
    assert "&lt;input" in html
    assert fields == set()


@pytest.mark.django_db
def test_filter_body_does_not_count_under_rust_engine():
    v = _view("<div dj-root>{% filter force_escape %}" + _MARK + "{% endfilter %}</div>")
    html, _, _ = v.render_with_diff(None)
    assert "&lt;input" in html
    assert v._dj_model_fields == frozenset()


def test_t030_flags_a_binding_in_a_filter_body():
    errors = _t030("{% filter lower %}" + _MARK + "{% endfilter %}")
    assert [e.id for e in errors] == ["djust.T030"]


# --- T030: includes and `%` in arguments -------------------------------------------------------


def test_t030_follows_a_literal_include_in_a_non_emitting_body(tmp_path):
    (tmp_path / "part.html").write_text('<input dj-model="inc_field">{% include "deep.html" %}')
    (tmp_path / "deep.html").write_text('<input dj-model="deep_field">')
    errors = _t030('{% modal id="m" %}{% include "part.html" %}{% endmodal %}', [str(tmp_path)])
    fields = sorted(e.msg.split('"')[1] for e in errors)
    assert fields == ["deep_field", "inc_field"]
    assert all('included "part.html"' in e.msg for e in errors)


def test_t030_does_not_flag_an_include_outside_a_non_emitting_body(tmp_path):
    (tmp_path / "part.html").write_text('<input dj-model="inc_field">')
    src = '{% include "part.html" %}{% card %}{% include "part.html" %}{% endcard %}'
    assert _t030(src, [str(tmp_path)]) == []


def test_t030_include_that_includes_itself_terminates(tmp_path):
    (tmp_path / "loop.html").write_text('<input dj-model="f">{% include "loop.html" %}')
    errors = _t030('{% modal %}{% include "loop.html" %}{% endmodal %}', [str(tmp_path)])
    assert [e.msg.split('"')[1] for e in errors] == ["f"]


def test_t030_does_not_read_outside_the_template_dirs(tmp_path):
    inner = tmp_path / "templates"
    inner.mkdir()
    (tmp_path / "outside.html").write_text('<input dj-model="f">')
    assert _t030('{% modal %}{% include "../outside.html" %}{% endmodal %}', [str(inner)]) == []


@pytest.mark.parametrize(
    "opener",
    [
        '{% modal title="50% off" %}',
        "{% modal title='100%' %}",
        '{% modal title="a %} b" %}',
    ],
)
def test_t030_reads_tag_arguments_holding_a_percent_sign(opener):
    errors = _t030(opener + '<input dj-model="y">{% endmodal %}')
    assert [e.msg.split('"')[1] for e in errors] == ["y"]


# --- dropdown / page_header_actions -----------------------------------------------------------


@pytest.mark.parametrize("is_open", [True, False])
def test_dropdown_counts_its_body_exactly_when_open(is_open):
    html, fields = _django_render(
        '{% dropdown id="d" label="L" open=is_open %}' + _MARK + "{% enddropdown %}",
        is_open=is_open,
    )
    assert ("mark_field" in html) is is_open
    assert fields == ({"mark_field"} if is_open else set())


def test_t030_does_not_flag_dropdown():
    assert _t030('{% dropdown id="d" %}' + _MARK + "{% enddropdown %}") == []


def test_page_header_actions_outside_page_header_does_not_count():
    html, fields = _django_render(
        "{% page_header_actions %}" + _MARK + "{% endpage_header_actions %}"
    )
    assert "mark_field" not in html
    assert fields == set()


def test_page_header_counts_the_actions_it_draws():
    html, fields = _django_render(
        '{% page_header title="T" %}'
        '{% page_header_actions %}<input dj-model="dropped">{% endpage_header_actions %}'
        '{% page_header_actions %}<input dj-model="drawn">{% endpage_header_actions %}'
        "{% endpage_header %}"
    )
    assert "drawn" in html and "dropped" not in html
    assert fields == {"drawn"}
