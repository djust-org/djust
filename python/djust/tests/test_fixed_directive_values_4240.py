"""Fixed-payload directives collect ``dj-value-*`` on the strict path (#4240).

The client's ``_strictBinding`` adds the element's ``dj-value-*`` to the
payload of ``dj-viewport-*``, ``dj-mutation``, ``dj-copy-event``,
``dj-dialog-close-event``, ``dj-auto-recover`` and ``dj-paste`` and rejects a
key the directive generates itself. The binding check now models that.
"""

from __future__ import annotations

import pytest

from djust import LiveView
from djust._template_bindings import django_engine
from djust.checks.bindings import scan_owner
from djust.decorators import event_handler


def _findings(markup: str, handler, policy: str = "strict") -> list[str]:
    decorated = event_handler(parameter_policy=policy)(handler)
    view = type(
        "FixedValuesView",
        (LiveView,),
        {
            "exposure_policy": "explicit",
            "template": "<div dj-root>%s</div>" % markup,
            "h": decorated,
            "closed": decorated,
        },
    )
    report = scan_owner(view, "view", django_engine(), {})
    assert not report.error, report.error
    return [f.message for result in report.results for f in result.findings]


def _generic(self, **kwargs: str) -> None: ...


@pytest.mark.parametrize(
    "markup, reused",
    [
        ('<div dj-viewport-top="h" dj-value-edge="x"></div>', "edge"),
        ('<div dj-viewport-bottom="h" dj-value-edge="x"></div>', "edge"),
        ('<div dj-mutation="h" dj-mutation-attr="class" dj-value-attrs="x"></div>', "attrs"),
        ('<div dj-mutation="h" dj-value-added="x"></div>', "added"),
        ('<button dj-copy-event="h" dj-value-text="x"></button>', "text"),
        ('<form dj-auto-recover="h" dj-value-form-values="x"></form>', "form_values"),
        ('<textarea dj-paste="h" dj-value-text="x"></textarea>', "text"),
    ],
)
def test_a_generated_name_is_reported_as_reused(markup, reused):
    found = _findings(markup, _generic)
    assert any("reuses %r" % reused in message for message in found), found


def test_children_mode_may_supply_attrs():
    found = _findings('<div dj-mutation="h" dj-value-attrs="x"></div>', _generic)
    assert not any("reuses" in message for message in found), found


def test_a_required_parameter_can_come_from_dj_value():
    def closed(self, reason: str) -> None: ...

    markup = '<dialog dj-dialog-close-event="h" dj-value-reason="esc"></dialog>'
    assert _findings(markup, closed) == []


@pytest.mark.parametrize(
    "markup",
    [
        '<dialog dj-dialog-close-event="h"></dialog>',
        '<div dj-viewport-bottom="h"></div>',
    ],
)
def test_a_required_parameter_nothing_supplies_is_reported(markup):
    def closed(self, reason: str) -> None: ...

    found = _findings(markup, closed)
    assert any("requires 'reason', which this binding never sends" in m for m in found), found


def test_an_undeclared_value_is_reported():
    def closed(self) -> None: ...

    found = _findings('<dialog dj-dialog-close-event="h" dj-value-foo="x"></dialog>', closed)
    assert any("does not declare" in message for message in found), found


def test_a_legacy_handler_gets_no_new_finding():
    def closed(self, **kwargs) -> None: ...

    found = _findings('<div dj-viewport-top="h" dj-value-edge="x"></div>', closed, "legacy")
    assert not any("reuses" in message for message in found), found


def test_routing_keys_are_reported_only_under_strict():
    def closed(self, **kwargs) -> None: ...

    markup = '<dialog dj-dialog-close-event="h" dj-value-view-id="x"></dialog>'
    assert not any("routing" in m for m in _findings(markup, closed, "legacy"))
    assert any("routing" in m for m in _findings(markup, closed, "strict"))
