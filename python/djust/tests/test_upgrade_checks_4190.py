"""The 2.0 upgrade checks (#4190): V033-V035, T028 and T029.

V033 and T028 report a handler under the legacy parameter policy, which only the
project key can still select at runtime (the decorator argument is refused, #4199),
so their tests set it on ``djust.config`` directly.

The fixture views live in a module written to a temporary directory and
imported under a top-level name (djust's own modules are skipped by these
checks), then unloaded so no other test's system check run sees them.
"""

from __future__ import annotations

import gc
import importlib.util
import sys
import textwrap
import types

import pytest
from django.test import override_settings

from djust._template_bindings import django_engine
from djust.checks.bindings import _messages, scan_owner
from djust.checks.parameters import check_event_parameter_contracts
from djust.checks.upgrade import (
    check_python_markup_arguments,
    check_sticky_update_hooks,
    check_target_parameter,
)
from djust.config import config

MODULE = "upgrade_check_fixture_4190"

SOURCE = """
from django.utils.html import format_html

from djust import LiveView
from djust.components.base import Component
from djust.decorators import event_handler


class OldHandlers(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    @event_handler()
    def pick(self, item_id, **kwargs):
        pass

    @event_handler()
    def save(self, value: str = "", **kwargs):
        self.last = kwargs.get("_target")


class InheritsLegacy(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    @event_handler()
    def pick(self, item_id, **kwargs):
        pass

    @event_handler()
    def declares(self, _target=None, **kwargs):
        pass

    @event_handler(parameter_policy="strict")
    def strict_declares(self, _target: str = ""):
        pass


class NewHandlers(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    @event_handler()
    def pick(self, item_id: int = 0):
        pass

    @event_handler()
    def save(self, value: str = "", field: str = ""):
        pass


class OldExtends(LiveView):
    exposure_policy = "explicit"
    template_name = "upgrade4190/old_page.html"

    @event_handler()
    def choose(self, item_id: int = 0, **kwargs):
        pass


class NewExtends(LiveView):
    exposure_policy = "explicit"
    template_name = "upgrade4190/new_page.html"

    @event_handler()
    def choose(self, item_id: int = 0):
        pass


class OldSticky(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    def _on_sticky_update(self, changed):
        self.title = changed.get("title")


class NamedSticky(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"
    sticky_update_inputs = ("title",)

    def _on_sticky_update(self, changed):
        self.title = changed.get("title")


class NewSticky(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"


class OldChip(Component):
    def render(self):
        return format_html('<button dj-click="pick" data-item-id="{}">x</button>', 3)


class NewChip(Component):
    def render(self):
        return format_html('<button dj-click="pick" dj-value-item-id="{}">x</button>', 3)
"""

TEMPLATES = {
    "upgrade4190/base.html": (
        "<html><body><main dj-root>{% block content %}{% endblock %}</main></body></html>"
    ),
    "upgrade4190/old_page.html": (
        '{% extends "upgrade4190/base.html" %}{% block content %}'
        '<button dj-click="choose" data-item-id="3">old</button>{% endblock %}'
    ),
    "upgrade4190/new_page.html": (
        '{% extends "upgrade4190/base.html" %}{% block content %}'
        '<button dj-click="choose" dj-value-item-id="3">new</button>{% endblock %}'
    ),
}


def _load(tmp_path) -> types.ModuleType:
    path = tmp_path / ("%s.py" % MODULE)
    path.write_text(textwrap.dedent(SOURCE), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(MODULE, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = module
    spec.loader.exec_module(module)
    return module


def _unload(module: types.ModuleType) -> None:
    sys.modules.pop(MODULE, None)
    for value in vars(module).values():
        if isinstance(value, type) and value.__module__ == MODULE:
            value.abstract = True
    gc.collect()


@pytest.fixture
def m(tmp_path):
    templates = tmp_path / "templates"
    for name, text in TEMPLATES.items():
        target = templates / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    module = _load(tmp_path)
    backend = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(templates)],
            "APP_DIRS": True,
        }
    ]
    try:
        with override_settings(TEMPLATES=backend):
            yield module
    finally:
        _unload(module)


def _ours(messages, check_id):
    return [m for m in messages if m.id == check_id and MODULE in m.msg]


@pytest.fixture
def legacy_project_key():
    """The project key, the one runtime route to a legacy handler left (#4205 removes it)."""
    previous = config.get("event_parameter_policy", "strict")
    config.set("event_parameter_policy", "legacy")
    try:
        yield
    finally:
        config.set("event_parameter_policy", previous)


def test_v033_reports_a_legacy_handler_strict_would_refuse(m):
    assert not _ours(check_event_parameter_contracts(None), "djust.V033")
    previous = config.get("event_parameter_policy", "strict")
    config.set("event_parameter_policy", "legacy")
    try:
        found = _ours(check_event_parameter_contracts(None), "djust.V033")
        assert not any("NewHandlers" in x.msg for x in found)
        # An unpinned handler inherits the legacy project policy: V033 names the key.
        inherited = [x for x in found if "InheritsLegacy.pick" in x.msg]
        assert len(inherited) == 1
        assert "requires an annotation" in inherited[0].msg
        assert "LIVEVIEW_CONFIG['event_parameter_policy']" in inherited[0].hint
    finally:
        config.set("event_parameter_policy", previous)


def test_t028_reports_data_arguments_of_a_legacy_handler_in_an_extends_template(
    m, legacy_project_key
):
    engine = django_engine()
    old = _messages([scan_owner(m.OldExtends, "view", engine, {})])
    t028 = [x for x in old if x.id == "djust.T028"]
    assert t028, [x.id for x in old]
    assert "data-item-id" in t028[0].msg and "choose()" in t028[0].msg
    new = _messages([scan_owner(m.NewExtends, "view", engine, {})])
    assert not [x for x in new if x.id in ("djust.T028", "djust.T020")]


def test_v034_target(m):
    found = _ours(check_target_parameter(None), "djust.V034")
    assert len(found) == 1
    assert "OldHandlers.save" in found[0].msg and "reads _target" in found[0].msg
    # A handler that declares the parameter is V016's (a reserved name).
    assert not any("declares _target" in x.msg for x in found)


def test_v035_sticky_hook(m):
    found = _ours(check_sticky_update_hooks(None), "djust.V035")
    assert len(found) == 1 and "OldSticky" in found[0].msg
    # A view that names the inputs the hook takes (#4181) is quiet.
    assert not any("NamedSticky" in x.msg for x in found)


def test_t029_python_markup(m):
    found = _ours(check_python_markup_arguments(None), "djust.T029")
    assert len(found) == 1
    assert "data-item-id" in found[0].msg
    old_line = next(
        i
        for i, text in enumerate(textwrap.dedent(SOURCE).splitlines(), 1)
        if "data-item-id" in text
    )
    assert found[0].line_number == old_line
