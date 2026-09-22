"""ADR-038 E1: framework log lines that quote application values.

Three fail-soft catches outside any view-owned helper logged a value from the
application: component assign validation (``Cannot coerce %r``), the suspense
fallback's template error, and a stream ``dom_id=`` factory failure (the row
itself, with a traceback). They run inside a view's turn, so they consult that
turn's diagnostics: unchanged for a legacy view, value-free for a nonlegacy one.
"""

import logging

import pytest

from djust._exposure_diagnostics import diagnostic_scope, restrict_diagnostics
from djust.components.assigns import Assign
from djust.components.base import LiveComponent
from djust.components.suspense import _render_fallback
from djust.session_utils import Stream


class _Owner:
    def __init__(self, policy):
        self.exposure_policy = policy


class CoercingComponent(LiveComponent):
    template = "<span>{{ n }}</span>"
    assigns = (Assign("n", type=int),)


def _raise(item):
    raise ValueError("FACTORY_SENTINEL")


def _assign_failure():
    CoercingComponent(n="ASSIGN_SENTINEL")


def _suspense_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("SUSPENSE_SENTINEL")

    monkeypatch.setattr("django.template.loader.render_to_string", boom)
    assert "djust" in _render_fallback("fallback.html", {})


def _stream_failure():
    stream = Stream("rows", dom_id_fn=_raise)
    stream.dom_id_for({"id": 1, "secret": "ROW_SENTINEL"}, allow_factory_fallback=True)


CASES = [
    ("assign", "ASSIGN_SENTINEL"),
    ("suspense", "SUSPENSE_SENTINEL"),
    ("stream", "ROW_SENTINEL"),
]


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
@pytest.mark.parametrize("case,sentinel", CASES)
def test_value_quoting_log_lines_follow_the_turn(
    monkeypatch, settings, caplog, policy, case, sentinel
):
    settings.DEBUG = False  # DEBUG re-raises assign errors instead of logging
    with caplog.at_level(logging.DEBUG), diagnostic_scope():
        restrict_diagnostics(_Owner(policy))
        if case == "assign":
            _assign_failure()
        elif case == "suspense":
            _suspense_failure(monkeypatch)
        else:
            _stream_failure()
    if policy == "legacy":
        # Unchanged legacy behaviour; also proves the catch ran.
        assert sentinel in caplog.text
    else:
        assert sentinel not in caplog.text
        assert "FACTORY_SENTINEL" not in caplog.text
        assert "Protected view operation failed" in caplog.text
