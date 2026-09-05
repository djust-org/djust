"""Compare real lexical scopes and upward cycle bindings with Django."""

import pytest
from django.contrib.auth.models import User
from django.template import Context, Engine
from django.utils.safestring import mark_safe
from djust.template import DjustTemplateBackend
from djust import _rust

SCOPES = [
    ("{% if yes %}", "{% endif %}"),
    ("{% with y=1 %}", "{% endwith %}"),
    ('{% with x="local" %}', "{% endwith %}"),
    ("{% for item in items %}", "{% endfor %}"),
    ("{% for x in items %}", "{% endfor %}"),
    ("{% autoescape off %}", "{% endautoescape %}"),
    ("{% filter lower %}", "{% endfilter %}"),
    ("{% spaceless %}", "{% endspaceless %}"),
    ("{% block content %}", "{% endblock %}"),
]


@pytest.fixture(params=["backend", "rust", "dirs"])
def entry(request):
    return request.param


def compare(tmp_path, source, context, entry):
    engine = Engine(dirs=[tmp_path])
    backend = DjustTemplateBackend(
        {"NAME": "djust", "DIRS": [tmp_path], "APP_DIRS": False, "OPTIONS": {}}
    )
    expected = engine.from_string(source).render(Context(context))
    if entry == "backend":
        actual = backend.from_string(source).render(context)
    elif entry == "rust":
        actual = _rust.render_template(source, context)
    else:
        actual = _rust.render_template_with_dirs(source, context, [str(tmp_path)])
    assert actual == expected


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("safe_index", [0, 1, 2])
def test_upward_cycle_scope(tmp_path, scope, safe_index, entry):
    values = ["<a>&A</a>", "<b>&B</b>", "<c>&C</c>"]
    values[safe_index] = mark_safe(values[safe_index])
    opening, closing = scope
    source = (
        "{% cycle a b c as x silent %}" + opening + "{% cycle x %}{{ x }}" + closing + "|{{ x }}"
    )
    compare(tmp_path, source, dict(zip(["a", "b", "c"], values), yes=True, items=[1, 2]), entry)


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("value", ["<em>unsafe</em>", mark_safe("<b>safe</b>")])
def test_local_assignment_scope(tmp_path, scope, value, entry):
    opening, closing = scope
    source = opening + "{% firstof value as x %}{{ x }}" + closing + "|{{ x }}"
    compare(tmp_path, source, {"x": "outer", "value": value, "yes": True, "items": [1, 2]}, entry)


@pytest.mark.parametrize(
    "source",
    [
        '{% with q=p %}{% with p="local" %}{{ q.get_full_name }}{% endwith %}{% endwith %}',
        '{% with p="local" %}{{ p.get_full_name }}{% endwith %}|{{ p.get_full_name }}',
        "{% with p=p %}{{ p.get_full_name }}{% endwith %}",
        '{% with p="local" %}{% with q=p %}{{ q.get_full_name }}{% endwith %}{% endwith %}',
    ],
)
def test_model_alias_retains_its_original_object(tmp_path, source, entry):
    compare(tmp_path, source, {"p": User(username="user", first_name="A&B", last_name="C")}, entry)


@pytest.mark.parametrize(
    "assignment",
    [
        "{% firstof value as x %}",
        "{% widthratio n 1 1 as x %}",
    ],
)
@pytest.mark.parametrize("entry", ["backend", "dirs"])
def test_parent_block_assignment_is_scoped(tmp_path, entry, assignment):
    (tmp_path / "parent.html").write_text(
        "{% block c %}" + assignment + "{{ x }}{% endblock %}|{{ x }}"
    )
    source = (
        '{% extends "parent.html" %}{% block c %}{{ x }}:{{ block.super }}:{{ x }}{% endblock %}'
    )
    compare(tmp_path, source, {"x": "outer", "value": "inner", "n": 2}, entry)


@pytest.mark.parametrize(
    "source",
    [
        "{% firstof value as x %}<p>{{ x }}</p>",
        "{% if yes %}{% firstof value as x %}{% endif %}<p>{{ x }}</p>",
    ],
)
def test_partial_render_assignment_dependency(source):
    from djust import RustLiveView

    view = RustLiveView(source)
    view.update_state({"value": "before", "yes": True})
    view.render_with_diff()
    view.update_state({"value": "after", "yes": True})
    view.set_changed_keys(["value"])
    partial, _, _ = view.render_with_diff()
    fresh = RustLiveView(source)
    fresh.update_state({"value": "after", "yes": True})
    full, _, _ = fresh.render_with_diff()
    assert partial == full


@pytest.mark.parametrize("entry", ["backend", "dirs"])
def test_inherited_child_cycle_updates_outer_binding(tmp_path, entry):
    (tmp_path / "parent.html").write_text("{% block c %}P{% endblock %}|{{ x }}")
    source = '{% extends "parent.html" %}{% block c %}{{ block.super }}{% cycle a b as x silent %}{% endblock %}'
    compare(tmp_path, source, {"a": "A", "b": "B", "x": "outer"}, entry)


@pytest.mark.parametrize(
    "source",
    [
        "{% block c %}{% firstof value as x %}{{ x }}{% endblock %}|{{ x }}",
        "{% block c %}{% widthratio n 1 1 as x %}{{ x }}{% endblock %}|{{ x }}",
    ],
)
def test_liveview_block_binding_scope(source):
    from djust import RustLiveView
    import re

    source = "<div>" + source + "</div>"
    data = {"value": "inner", "n": 2, "x": "outer"}
    expected = Engine().from_string(source).render(Context(data))
    view = RustLiveView(source)
    view.update_state(data)
    actual, _, _ = view.render_with_diff()
    assert re.sub(r' dj-id="\d+"', "", actual) == expected
