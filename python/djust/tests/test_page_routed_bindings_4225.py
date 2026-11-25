"""The binding check's model of page-routed directives matches the client (#4225).

``_template_bindings.DIRECTIVES`` lists the generated names the browser adds
for each directive; ``checks/bindings.py`` (T020) compares them with the
handler's signature. ``dj-mutation`` listed only ``mutation``, while the client
also sends ``attrs`` (when ``dj-mutation-attr`` names an attribute) or
``added`` / ``removed`` (otherwise), so a strict handler that declares them
was reported as requiring a name the binding never sends. The client's generated values are read from its source here,
so the two lists cannot drift apart again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust import LiveView
from djust._template_bindings import (
    DIRECTIVES,
    _MUTATION_ATTRIBUTES,
    _MUTATION_CHILDREN,
    django_engine,
)
from djust.checks.bindings import scan_owner
from djust.decorators import event_handler

SRC = Path(__file__).resolve().parents[1] / "static" / "djust" / "src"


def _object_keys(literal: str) -> set[str]:
    """The keys of a JS object literal's body: ``a, b: x`` -> {a, b}."""
    keys = set()
    for part in literal.split(","):
        part = part.strip()
        if part:
            keys.add(re.match(r"([A-Za-z_]\w*)", part).group(1))
    return keys


def _strict_binding_generated(file: str, handler_var: str) -> set[str]:
    """The generated-values object a ``_strictBinding(el, <handler_var>, {...}`` call passes."""
    source = (SRC / file).read_text(encoding="utf-8")
    calls = re.findall(r"_strictBinding\(\w+, %s, \{([^}]*)\}" % handler_var, source)
    assert len(calls) == 1, (file, calls)
    return _object_keys(calls[0])


def _mutation_payloads() -> list[set[str]]:
    source = (SRC / "37-dj-mutation.js").read_text(encoding="utf-8")
    # The strict binding is given the payload the observer built.
    assert "_strictBinding(el, handlerName, payload," in source
    assert "const payload = pending;" in source
    payloads = [_object_keys(body) for body in re.findall(r"pending = \{([^}]*)\}", source)]
    assert len(payloads) == 2, payloads
    return payloads


@pytest.mark.parametrize(
    "directive, file, handler_var",
    [
        ("dj-viewport-top", "30-infinite-scroll.js", "eventName"),
        ("dj-viewport-bottom", "30-infinite-scroll.js", "eventName"),
        ("dj-copy-event", "09-event-binding.js", "copyEvent"),
        ("dj-dialog-close-event", "35-dj-dialog.js", "eventName"),
    ],
)
def test_the_server_lists_the_generated_names_the_client_sends(directive, file, handler_var):
    assert set(DIRECTIVES[directive].generated) == _strict_binding_generated(file, handler_var)


def test_dj_mutation_lists_both_payloads_the_client_sends():
    attributes, children = sorted(_mutation_payloads(), key=lambda keys: "attrs" not in keys)
    assert set(_MUTATION_ATTRIBUTES) == attributes == {"mutation", "attrs"}
    assert set(_MUTATION_CHILDREN) == children == {"mutation", "added", "removed"}
    assert set(DIRECTIVES["dj-mutation"].generated) == attributes | children


# --- what the check reports -----------------------------------------------------


class StrictAttrsDeclared(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><div dj-mutation="h" dj-mutation-attr="class,style"></div></div>'

    @event_handler(parameter_policy="strict")
    def h(self, mutation: str, attrs: list[str]) -> None: ...


class StrictChildrenDeclared(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><div dj-mutation="h"></div></div>'

    @event_handler(parameter_policy="strict")
    def h(self, mutation: str, added: int, removed: int) -> None: ...


class StrictAddedInAttributeMode(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><div dj-mutation="h" dj-mutation-attr="class"></div></div>'

    @event_handler(parameter_policy="strict")
    def h(self, mutation: str, added: int) -> None: ...


class StrictDynamicAttributeList(LiveView):
    """The attribute list is rendered: either payload may be sent."""

    exposure_policy = "explicit"
    template = '<div dj-root><div dj-mutation="h" dj-mutation-attr="{{ watched }}"></div></div>'

    @event_handler(parameter_policy="strict")
    def h(self, mutation: str, added: int) -> None: ...


def _findings(view: type) -> list[str]:
    report = scan_owner(view, "view", django_engine(), {})
    assert not report.error, report.error
    return [f.message for result in report.results for f in result.findings]


@pytest.mark.parametrize(
    "view, expected",
    [
        (StrictAttrsDeclared, []),
        (StrictChildrenDeclared, []),
        (StrictAddedInAttributeMode, ["h() requires 'added', which this binding never sends."]),
        (StrictDynamicAttributeList, []),
    ],
    ids=lambda value: value.__name__ if isinstance(value, type) else None,
)
def test_the_check_reports_what_this_element_sends(view, expected):
    assert _findings(view) == expected
