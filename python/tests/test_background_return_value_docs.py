"""Regression test for #1288 — @background docstring documents return-value contract.

The ``@background`` decorator discards handler return values. This must be
documented in the decorator's docstring so users aren't confused when their
handler's return value silently disappears.
"""

from djust.decorators import background


class TestBackgroundReturnValueDocs:
    """#1288: @background docstring mentions return-value discard."""

    def test_docstring_mentions_return_values_are_discarded(self):
        """@background docstring must document return-value contract."""
        doc = background.__doc__
        assert doc is not None, "@background must have a docstring"
        assert "discarded" in doc, (
            "#1288: @background docstring must mention return values are discarded"
        )

    def test_docstring_mentions_action_state_alternative(self):
        """@background docstring must point users to @action for result tracking."""
        doc = background.__doc__
        assert "_action_state" in doc, (
            "#1288: @background docstring must mention @action/"
            "_action_state as alternative for result tracking"
        )
