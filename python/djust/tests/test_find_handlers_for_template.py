"""
Tests for find_handlers_for_template: which views use a template, and how the
template's dj-* bindings resolve against each of them.

The tool is built on the ``manage.py check`` binding scan (ADR-037 D1, row
13): the template is resolved by Django's loaders, and a view uses it when its
own template is the file or includes or extends it. These tests call the
module-level ``template_handler_report`` the MCP tool returns as JSON.
"""

from __future__ import annotations

import gc
import importlib.util
import json
import sys
import textwrap

import pytest
from django.test import override_settings

from djust.mcp.server import template_handler_report

MODULE = "find_handlers_fixture"

SOURCE = """
from djust import LiveView
from djust.decorators import event_handler


class CounterView(LiveView):
    template_name = "counter/page.html"

    @event_handler
    def increment(self, **kwargs):
        pass

    @event_handler
    def reset(self, **kwargs):
        pass


class Unrelated(LiveView):
    template = "<div dj-root></div>"
"""

TEMPLATES = {
    "counter/page.html": (
        "<div dj-root>\n"
        '{% include "counter/buttons.html" %}\n'
        '<button dj-click="{{ dynamic }}">?</button>\n'
        "</div>"
    ),
    "counter/buttons.html": (
        '<button dj-click="increment">+</button>\n<button dj-click="ghost">-</button>\n'
    ),
}


@pytest.fixture
def project(tmp_path):
    templates = tmp_path / "templates"
    for name, text in TEMPLATES.items():
        target = templates / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    path = tmp_path / ("%s.py" % MODULE)
    path.write_text(textwrap.dedent(SOURCE), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(MODULE, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = module
    spec.loader.exec_module(module)
    settings = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(templates)],
            "APP_DIRS": True,
        }
    ]
    try:
        with override_settings(TEMPLATES=settings):
            yield templates
    finally:
        sys.modules.pop(MODULE, None)
        for value in vars(module).values():
            if isinstance(value, type) and value.__module__ == MODULE:
                value.abstract = True
        gc.collect()


def _view(report, name):
    return next(v for v in report["views"] if v["class"] == "%s.%s" % (MODULE, name))


def test_an_included_template_is_matched_to_the_view_that_includes_it(project):
    report = template_handler_report("counter/buttons.html")
    assert report["resolved_path"] == str(project / "counter" / "buttons.html")
    assert report["dj_handlers_in_template"] == ["ghost", "increment"]
    view = _view(report, "CounterView")
    assert view["template_name"] == "counter/page.html"
    assert view["matched_handlers"] == ["increment"]
    assert view["handlers_in_view_not_in_template"] == ["reset"]
    assert view["handlers_in_template_not_in_view"] == ["ghost"]
    assert [(b["binding"], b["line"], b["status"], b["findings"]) for b in view["bindings"]] == [
        ('dj-click="increment"', 1, "checked", []),
        ('dj-click="ghost"', 2, "checked", ["djust.T019"]),
    ]
    assert not any(v["class"].endswith(".Unrelated") for v in report["views"])


def test_the_page_itself_reports_dynamic_bindings_and_coverage(project):
    report = template_handler_report("counter/page.html")
    view = _view(report, "CounterView")
    assert [b["status"] for b in view["bindings"]] == ["dynamic"]
    counts = next(
        t["counts"] for t in report["coverage"]["templates"] if t["owner"].endswith(".CounterView")
    )
    assert counts == {"checked": 2, "dynamic": 1, "unsupported": 0}


def test_an_absolute_path_resolves_the_same_file(project):
    path = str(project / "counter" / "buttons.html")
    assert (
        template_handler_report(path)["views"]
        == template_handler_report("counter/buttons.html")["views"]
    )


def test_a_missing_template_is_an_error_not_an_exception(project):
    report = template_handler_report("counter/missing.html")
    assert report["error"].startswith("Could not resolve template 'counter/missing.html'")


def test_the_report_is_json(project):
    report = template_handler_report("counter/buttons.html")
    assert json.loads(json.dumps(report)) == report
