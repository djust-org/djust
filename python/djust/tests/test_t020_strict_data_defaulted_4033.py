"""#4033: T020 reports a data-* argument a strict handler drops, defaulted or not.

A strict handler receives only ``dj-value-*`` attributes. T020 reported a
``data-*`` attribute that fed a *required* parameter (the handler is never
called), and said nothing when the parameter had a default: the handler ran
with the default and the click did the wrong thing, with no error anywhere.

The fixture module follows ``test_adr037_binding_checks.py``: written to a
temporary directory, imported under a top-level name, unloaded afterwards.
"""

from __future__ import annotations

import gc
import importlib.util
import sys

import pytest

from djust._template_bindings import django_engine
from djust.checks.bindings import _messages, scan_owner

MODULE = "binding_check_fixture_4033"

SOURCE = '''
from djust import LiveView
from djust.decorators import event_handler


class Gap(LiveView):
    exposure_policy = "explicit"
    template = """<div dj-root>
<button dj-click="pick" data-item-id="3">defaulted</button>
<button dj-click="need" data-item-id="3">required</button>
<button dj-click="pick" dj-value-item-id:int="3">sent</button>
<button dj-click="pick" data-item-id="3" dj-value-item-id:int="3">both</button>
<button dj-click="pick(3)" data-item-id="3">positional</button>
<button dj-click="pick" data-other="3">undeclared</button>
<button dj-click="loose" data-item-id="3">legacy</button>
</div>"""

    @event_handler(parameter_policy="strict")
    def pick(self, item_id: int = 0):
        pass

    @event_handler(parameter_policy="strict")
    def need(self, item_id: int):
        pass

    @event_handler(parameter_policy="legacy")
    def loose(self, item_id: int = 0, **kwargs):
        pass
'''


@pytest.fixture
def module(tmp_path):
    path = tmp_path / ("%s.py" % MODULE)
    path.write_text(SOURCE, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(MODULE, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = loaded
    spec.loader.exec_module(loaded)
    try:
        yield loaded
    finally:
        sys.modules.pop(MODULE, None)
        del loaded
        gc.collect()


def _all(owner):
    engine = django_engine()
    assert engine is not None
    return _messages([scan_owner(owner, "view", engine, {})])


def _found(owner):
    return {m.line_number: m for m in _all(owner)}


def _line(module, text):
    lines = open(module.__file__, encoding="utf-8").read().splitlines()
    return next(i for i, line in enumerate(lines, 1) if text in line)


def test_defaulted_parameter_fed_by_data_attribute_is_reported(module):
    found = _found(module.Gap)
    message = found[_line(module, ">defaulted<")]
    assert message.id == "djust.T020"
    assert "data-item-id" in message.msg and "pick()" in message.msg
    assert "rename data-item-id to dj-value-item-id" in message.hint


def test_required_parameter_is_still_reported_once(module):
    line = _line(module, ">required<")
    (message,) = [m for m in _all(module.Gap) if m.line_number == line]
    assert message.msg.endswith("need() requires 'item_id', which this binding never sends.")


@pytest.mark.parametrize("text", [">sent<", ">both<", ">positional<", ">undeclared<", ">legacy<"])
def test_bindings_that_send_the_argument_or_are_not_strict_are_quiet(module, text):
    assert _line(module, text) not in _found(module.Gap)
