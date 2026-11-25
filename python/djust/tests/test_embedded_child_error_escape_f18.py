"""Security regression (finding #18): ``_render_embedded_child`` must NOT emit a
raw, unescaped exception message into the live page HTML.

Vulnerability: ``LiveViewConsumer._render_embedded_child`` rolled its own error
string in its ``except`` block and returned it as the embedded child's subtree
HTML (sent to the client via ``_emit_full_html_update(..., "embedded_child", ...)``).
The old shape was::

    except Exception as e:
        ...
        return f"<!-- Error rendering embedded child: {e} -->"

This was (a) NOT DEBUG-gated: raw ``str(e)`` leaked into the production page
(CWE-209), and (b) NOT escaped: an attacker-influenced exception message
containing ``-->`` (e.g. ``int("--><img src=x onerror=alert(1)>")`` →
``ValueError: invalid literal for int() with base 10: '--><img ...>'``) broke
out of the HTML comment into live DOM (CWE-79 DOM XSS).

An explicit child never builds that string: its failed render raises a
value-free ``ExposureError`` that the caller turns into the protected failure,
so no exception text reaches the subtree HTML, in ``DEBUG`` or not.
"""

from __future__ import annotations

import traceback

import pytest
from django.test import override_settings

from djust._exposure import ExposureError
from djust.websocket import LiveViewConsumer

# An attacker-influenced exception message that, unescaped, would break out of
# the surrounding HTML comment (the `-->`) and inject a live element. Mirrors the
# `ValueError` text that `int("--><img ...>")` produces verbatim.
_ATTACK_PAYLOAD = "--><img src=x onerror=alert(document.domain)><script>alert(1)</script>"
_RAISING_MESSAGE = f"invalid literal for int() with base 10: '{_ATTACK_PAYLOAD}'"


class _RaisingChildView:
    """Minimal stand-in for an embedded child LiveView whose render raises with
    an attacker-influenced message. ``_render_embedded_child`` calls
    ``get_context_data()`` first, so raising there exercises the except path."""

    exposure_policy = "explicit"

    def get_context_data(self):  # noqa: D401 - test stub
        raise ValueError(_RAISING_MESSAGE)

    def get_template(self):  # pragma: no cover - never reached (raise is earlier)
        return "<div>unused</div>"


def _render_error():
    """Drive the real ``_render_embedded_child`` except path on a bare consumer.

    The method only uses ``child_view``: a no-arg ``LiveViewConsumer()`` is
    sufficient to call it (same construction pattern as
    ``tests/unit/test_arm_recovery_helper_1645.py``). Returns what it raised."""
    consumer = LiveViewConsumer()
    with pytest.raises(ExposureError) as raised:
        consumer._render_embedded_child(_RaisingChildView())
    return raised.value


@pytest.mark.parametrize("debug", [True, False], ids=["debug", "prod"])
class TestEmbeddedChildErrorEscapeF18:
    """Finding #18: an embedded child's render failure carries no exception text."""

    def test_the_failure_is_value_free(self, debug):
        with override_settings(DEBUG=debug):
            error = _render_error()
        assert str(error) == "Explicit child rendering unavailable"
        # The ValueError is suppressed, not just hidden from ``str``: a log
        # formatter or error tracker walking the chain sees none of it.
        assert error.__suppress_context__ and error.__cause__ is None
        # The control: the payload really was raised, so its absence below is the point.
        assert _ATTACK_PAYLOAD in str(error.__context__)
        text = "".join(traceback.format_exception(error))
        for leaked in ("onerror", "invalid literal", "<img", "<script", "-->"):
            assert leaked not in text, (leaked, text)

    def test_nothing_is_returned_to_be_placed_in_the_page(self, debug):
        """The method raises instead of returning an HTML comment, so there is no
        subtree string for the page to carry (the old breakout was in that string)."""
        consumer = LiveViewConsumer()
        with override_settings(DEBUG=debug):
            with pytest.raises(ExposureError):
                result = consumer._render_embedded_child(_RaisingChildView())
                pytest.fail(f"a failed render returned {result!r}")
