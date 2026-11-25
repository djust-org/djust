"""The 2.0 upgrade checks (#4190): V034, V035 and T029.

V033 and T028 (a handler under the legacy parameter policy) are retired with the
policy itself (#4205).

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

from djust.checks.upgrade import (
    check_python_markup_arguments,
    check_sticky_update_hooks,
    check_target_parameter,
)

MODULE = "upgrade_check_fixture_4190"

SOURCE = """
from typing import Any

from django.utils.html import format_html

from djust import LiveView
from djust.components.base import Component
from djust.decorators import event_handler


class OldHandlers(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    @event_handler()
    def pick(self, item_id: int = 0, **kwargs: Any):
        pass

    @event_handler()
    def save(self, value: str = "", **kwargs: Any):
        self.last = kwargs.get("_target")


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
    module = _load(tmp_path)
    try:
        yield module
    finally:
        _unload(module)


def _ours(messages, check_id):
    return [m for m in messages if m.id == check_id and MODULE in m.msg]


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
