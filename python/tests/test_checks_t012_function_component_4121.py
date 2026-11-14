"""#4121: T012 must recognise a function component's own template.

A ``@component`` that renders a template draws its output inside the page's
``dj-root`` (it is ``{% call %}``ed from a LiveView page), so ``dj-*``
attributes in that template work. T012 could not see that and warned. A
component now names its template (``@component(template_name=...)``), and the
check treats a registered component's template as a partial.
"""

from __future__ import annotations

import pytest

from djust import Assign, component
from djust.components.function_component import _COMPONENT_REGISTRY
from python.tests.test_checks_t002_t012_3225 import _ids, _run

BODY = (
    '<section class="moves">{% for move in block.moves %}'
    '<button type="button" dj-click="move" data-to="{{ move.key }}">{{ move.label }}</button>'
    "{% endfor %}</section>"
)
NAME = "myapp/components/status_moves.html"


@pytest.fixture
def registered():
    @component(
        name="t4121_status_moves",
        assigns=[Assign("block", dict, required=True)],
        template_name=NAME,
    )
    def status_moves(assigns):
        return {"block": assigns["block"]}

    yield status_moves
    _COMPONENT_REGISTRY.pop("t4121_status_moves", None)


def test_a_registered_components_template_gets_no_t012(tmp_path, settings, registered):
    assert _ids(_run(tmp_path, settings, NAME, BODY)) == []


def test_the_same_template_without_a_component_still_gets_t012(tmp_path, settings):
    """The real case keeps firing: nothing renders it inside a root."""
    assert _ids(_run(tmp_path, settings, NAME, BODY)) == ["djust.T012"]


def test_another_template_is_not_exempted_by_the_component(tmp_path, settings, registered):
    found = _run(tmp_path, settings, "myapp/other.html", BODY)
    assert _ids(found) == ["djust.T012"]


def test_the_component_renders_its_template_with_the_returned_context(
    tmp_path, settings, registered
):
    from djust.testing import render_component

    tpl_dir = tmp_path / "templates"
    (tpl_dir / NAME).parent.mkdir(parents=True, exist_ok=True)
    (tpl_dir / NAME).write_text(BODY)
    settings.TEMPLATES = [
        {"DIRS": [str(tpl_dir)], "BACKEND": "django.template.backends.django.DjangoTemplates"}
    ]
    html = render_component(
        "t4121_status_moves", block={"moves": [{"key": "done", "label": "Done <now>"}]}
    )
    assert 'dj-click="move" data-to="done"' in html
    assert "Done &lt;now&gt;" in html


def test_none_renders_the_template_with_the_assigns(tmp_path, settings):
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()
    (tpl_dir / "hello.html").write_text("<b>{{ who }}</b>")
    settings.TEMPLATES = [
        {"DIRS": [str(tpl_dir)], "BACKEND": "django.template.backends.django.DjangoTemplates"}
    ]

    @component(name="t4121_hello", template_name="hello.html")
    def hello(assigns):
        return None

    try:
        from djust.testing import render_component

        assert render_component("t4121_hello", who="ann") == "<b>ann</b>"
    finally:
        _COMPONENT_REGISTRY.pop("t4121_hello", None)
