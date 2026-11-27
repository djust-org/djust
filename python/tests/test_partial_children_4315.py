"""#4315 — partial render reuses a wrapper's children and a tag that reads only its arguments.

Slice 2: ``render_nodes_partial`` decided per TOP-LEVEL node only, so a
``{% for %}`` inside ``{% if show %}`` was redrawn whenever the wrapper
re-rendered: when its condition read a changed key, or when the wrapper held
any custom tag (which makes the whole wrapper ``"*"``). The wrapper's body is
now cached child by child.

Slice 3: every inline tag had the ``"*"`` dependency and re-rendered on every
partial render. A handler that declares ``READS_ONLY_ARGS`` now depends on its
operands' root names (plus ``ALSO_READS``), so it is reused while they are
unchanged.

Each skip is observed directly, as in ``test_partial_inline_tags_4124.py``: an
input is mutated but NOT reported as changed, so a reused fragment still shows
the OLD value (gate off), and reporting it shows the NEW value (gate on). The
byte-identity tests report every change honestly and compare the partial
render with a full render of the same state.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

import pytest
from django.http import HttpResponse
from django.test import override_settings
from django.urls import path

from djust import _rust

urlpatterns = [
    path("x4315/", lambda request: HttpResponse("x"), name="x4315"),
    path("xs4315/<str:s>/", lambda request, s: HttpResponse(s), name="xs4315"),
]

LOOP = "<ul>{% for e in entries %}<li>{{ e }}</li>{% endfor %}</ul>"


def _plain(html: str) -> str:
    """Drop the VDOM ids so assertions read the markup alone."""
    return re.sub(r' dj-id="[^"]*"', "", html)


CALLS: Dict[str, int] = {}


class ComponentStandIn:
    """A component-style inline handler: plain ``str`` return, may read anything."""

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        return "<b>component</b>"


class EchoArgs:
    """Declares that its output is a function of its arguments alone."""

    READS_ONLY_ARGS = True

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        CALLS["echo"] = CALLS.get("echo", 0) + 1
        return "echo:%s" % args[0]


class EchoUndeclared:
    """The same handler without the declaration: still re-renders every time."""

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        CALLS["undeclared"] = CALLS.get("undeclared", 0) + 1
        return "echo:%s" % args[0]


class EchoTheme:
    """Reads one context name besides its arguments, and says so."""

    READS_ONLY_ARGS = True
    ALSO_READS = frozenset({"theme"})

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        return "%s:%s" % (context.get("theme"), args[0])


HANDLERS = {
    "component4315": ComponentStandIn(),
    "echo4315": EchoArgs(),
    "undeclared4315": EchoUndeclared(),
    "theme4315": EchoTheme(),
}


@pytest.fixture(autouse=True, scope="module")
def _handlers():
    for name, handler in HANDLERS.items():
        _rust.register_tag_handler(name, handler)
    with override_settings(ROOT_URLCONF=__name__):
        yield
    for name in HANDLERS:
        _rust.unregister_tag_handler(name)


TEMPLATE_DIRS: List[str] = []


@pytest.fixture(autouse=True, scope="module")
def _template_dir(tmp_path_factory):
    directory = tmp_path_factory.mktemp("tpl4315")
    (directory / "p4315.html").write_text("<s>included</s>")
    TEMPLATE_DIRS.append(str(directory))
    yield
    TEMPLATE_DIRS.clear()


def _view(body: str, state: Dict[str, Any]):
    view = _rust.RustLiveView("<div dj-root>" + body + "</div>", list(TEMPLATE_DIRS))
    view.update_state(state)
    view.render_with_diff()
    return view


def _rerender(view, update: Dict[str, Any], reported: List[str]) -> str:
    view.update_state(update)
    view.set_changed_keys(reported)
    return _plain(view.render_with_diff()[0])


def _rows_after_count_change(body: str, report_entries: bool) -> str:
    """Render ``body``, then change ``count`` AND ``entries`` while reporting
    only ``count`` (plus ``entries`` when asked)."""
    view = _view(body, {"count": 1, "show": True, "entries": ["old-a", "old-b"]})
    html = _rerender(
        view,
        {"count": 2, "entries": ["new-z"]},
        ["count", "entries"] if report_entries else ["count"],
    )
    assert "<p>2</p>" in html, html
    return html


# --- Slice 2: per-child fragment cache inside if / with / block -------------

WRAPPED = [
    # The wrapper re-renders because a child reads the changed key.
    "{% if show %}<p>{{ count }}</p>" + LOOP + "{% endif %}",
    # ... because its condition reads it.
    "{% if count %}<p>{{ count }}</p>" + LOOP + "{% endif %}",
    # ... and the false branch is cached the same way.
    "{% if not show %}x{% else %}<p>{{ count }}</p>" + LOOP + "{% endif %}",
    # ... because it holds a "*" child (a tag that may read anything).
    "{% if show %}{% component4315 %}<p>{{ count }}</p>" + LOOP + "{% endif %}",
    "{% if show %}<p>{{ count }}</p>" + LOOP + "{% now 'Y' %}{% endif %}",
    # A {% with %}: the child reading the bound name re-renders.
    "{% with c=count %}<p>{{ c }}</p>" + LOOP + "{% endwith %}",
    # A {% block %} holding a "*" child is not flattened into the top level.
    "{% block b %}{% component4315 %}<p>{{ count }}</p>" + LOOP + "{% endblock %}",
]


@pytest.mark.parametrize("body", WRAPPED)
def test_a_nested_loop_whose_inputs_did_not_change_is_reused(body):
    html = _rows_after_count_change(body, report_entries=False)
    # The loop's child fragment was reused: its input was not reported.
    assert "old-a" in html and "new-z" not in html, html


@pytest.mark.parametrize("body", WRAPPED)
def test_a_nested_loop_whose_inputs_changed_is_redrawn(body):
    html = _rows_after_count_change(body, report_entries=True)
    assert "new-z" in html and "old-a" not in html, html


BINDING = [
    # A child that may bind re-renders the children after it ...
    "{% if show %}{% url 'x4315' as u %}<p>{{ count }}</p>" + LOOP + "{% endif %}",
    "{% if show %}{% firstof count as f %}<p>{{ count }}</p>" + LOOP + "{% endif %}",
    "{% if show %}{% include 'p4315.html' %}<p>{{ count }}</p>" + LOOP + "{% endif %}",
    # ... and so does a top-level node before the wrapper.
    "{% firstof count as f %}{% if show %}<p>{{ count }}</p>" + LOOP + "{% endif %}",
    "{% firstof count as f %}{% with c=count %}<p>{{ c }}</p>" + LOOP + "{% endwith %}",
]


@pytest.mark.parametrize("body", BINDING)
def test_a_binding_before_the_loop_still_redraws_it(body):
    html = _rows_after_count_change(body, report_entries=False)
    assert "new-z" in html and "old-a" not in html, html


def test_a_binding_after_the_loop_leaves_it_reusable():
    body = (
        "{% if show %}<p>{{ count }}</p>"
        + LOOP
        + "{% firstof count as f %}<i>{{ f }}</i>{% endif %}"
    )
    html = _rows_after_count_change(body, report_entries=False)
    assert "old-a" in html and "new-z" not in html, html
    assert "<i>2</i>" in html, html


def test_a_branch_flip_renders_the_other_branch_fresh():
    body = (
        "{% if count > 1 %}<p>{{ count }}</p>" + LOOP + "{% else %}<p>{{ count }}</p>x{% endif %}"
    )
    html = _rows_after_count_change(body, report_entries=False)
    # The true branch had no cached children: everything in it renders.
    assert "new-z" in html and "old-a" not in html, html


# --- Slice 3: a handler that reads only its arguments ------------------------


def _tag_after_label_change(tag: str, report_label: bool) -> str:
    view = _view("<p>{{ count }}</p><i>" + tag + "</i>", {"count": 1, "label": "old"})
    html = _rerender(
        view, {"count": 2, "label": "new"}, ["count", "label"] if report_label else ["count"]
    )
    assert "<p>2</p>" in html, html
    return html


def test_a_reads_only_args_tag_is_reused_while_its_operands_are_unchanged():
    CALLS.clear()
    html = _tag_after_label_change("{% echo4315 label %}", report_label=False)
    assert "<i>echo:old</i>" in html, html
    assert CALLS == {"echo": 1}, CALLS  # the first render only


def test_a_reads_only_args_tag_is_redrawn_when_an_operand_changed():
    CALLS.clear()
    html = _tag_after_label_change("{% echo4315 label %}", report_label=True)
    assert "<i>echo:new</i>" in html, html
    assert CALLS == {"echo": 2}, CALLS


def test_an_undeclared_tag_still_re_renders_on_every_partial_render():
    CALLS.clear()
    html = _tag_after_label_change("{% undeclared4315 label %}", report_label=False)
    assert "<i>echo:new</i>" in html, html
    assert CALLS == {"undeclared": 2}, CALLS


def test_also_reads_names_a_dependency_besides_the_operands():
    view = _view("<p>{{ count }}</p><i>{% theme4315 'a' %}</i>", {"count": 1, "theme": "dark"})
    html = _rerender(view, {"count": 2, "theme": "light"}, ["count"])
    assert "<i>dark:a</i>" in html, html
    html = _rerender(view, {"count": 3}, ["count", "theme"])
    assert "<i>light:a</i>" in html, html


def test_a_reads_only_args_tag_inside_a_loop_leaves_the_loop_reusable():
    body = "<p>{{ count }}</p><ul>{% for e in entries %}<li>{% echo4315 e %}</li>{% endfor %}</ul>"
    html = _rows_after_count_change(body, report_entries=False)
    assert "echo:old-a" in html and "new-z" not in html, html


def test_an_as_binding_of_a_reads_only_args_tag_still_reaches_later_siblings():
    # `{% url %}` declares READS_ONLY_ARGS; its `as` form still binds.
    body = "<p>{{ count }}</p>{% url 'x4315' as u %}<i>{{ u }}</i>" + LOOP
    html = _rows_after_count_change(body, report_entries=False)
    assert "<i>/x4315/</i>" in html, html
    assert "new-z" in html and "old-a" not in html, html


def test_builtin_declarations():
    from djust.template_tags import now, static, url

    assert url.UrlTagHandler.READS_ONLY_ARGS
    assert url.UrlTagHandler.ALSO_READS == frozenset({"request"})
    assert static.StaticTagHandler.READS_ONLY_ARGS
    # The clock is not an operand.
    assert not now.NowTagHandler.READS_ONLY_ARGS


def test_the_jit_path_extraction_keeps_the_wildcard():
    # Only the partial renderer's dependency walk honours READS_ONLY_ARGS.
    assert "*" in _rust.extract_template_variables("{% url 'x4315' %}")


# --- Byte identity: partial render == full render ----------------------------

IDENTITY = WRAPPED + [
    "{% if show %}{% url 'x4315' %}<p>{{ count }}</p>" + LOOP + "{% endif %}",
    "{% if count > 2 %}<p>{{ count }}</p>" + LOOP + "{% else %}<em>{{ count }}</em>{% endif %}",
    "{% if show %}{% firstof count as f %}<i>{{ f }}</i>" + LOOP + "{% endif %}",
    "{% if show %}<i>{% echo4315 count %}</i>{% if count %}" + LOOP + "{% endif %}{% endif %}",
    "<p>{{ count }}</p>{% echo4315 label %}{% static 'a.css' %}" + LOOP,
    # Operands whose filter literal holds `=` or that start with a literal
    # (#4315 review): their roots must still be dependencies.
    "<i>{% url 'xs4315' label|default:'a=b' %}</i><b>{{ count }}</b>",
    "{% if show %}<i>{% url 'xs4315' label|default:'a=b' %}</i><b>{{ count }}</b>{% endif %}",
    "<i>{% url 'xs4315' s=\"p\"|add:label %}</i><b>{{ count }}</b>",
    "<i>{% static label|add:'?v=2' %}</i><b>{{ count }}</b>",
    "{% if show %}<i>{% static label|add:'?v=2' %}</i>" + LOOP + "{% endif %}",
]

STEPS = [
    ({"count": 2}, ["count"]),
    ({"entries": ["b", "c"]}, ["entries"]),
    ({"show": False}, ["show"]),
    ({"show": True, "count": 3}, ["show", "count"]),
    ({"label": "L2"}, ["label"]),
    ({"count": 4, "entries": ["d"]}, ["count", "entries"]),
    ({}, []),
]


@pytest.mark.parametrize("body", IDENTITY)
def test_partial_render_equals_a_full_render(body):
    state: Dict[str, Any] = {"count": 1, "show": True, "entries": ["a"], "label": "L"}
    view = _view(body, state)
    for update, reported in STEPS:
        state.update(update)
        html = _rerender(view, update, reported)
        full = _plain(_view(body, dict(state)).render_with_diff()[0])
        assert html == full, (update, html, full)
