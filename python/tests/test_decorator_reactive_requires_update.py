"""Regression tests for #1287 — @reactive requires host class to have update().

``@reactive`` properties should fail at class-definition time (via
``__set_name__``) when the host class lacks an ``update()`` method,
rather than silently no-opping at runtime.
"""

import pytest

from djust import LiveView
from djust.decorators import reactive


class TestReactiveRequiresUpdate:
    """#1287: @reactive raises TypeError when host class lacks update()."""

    def test_reactive_on_non_liveview_class_raises_typeerror(self):
        """A plain class without update() fails at class-definition time."""
        with pytest.raises(TypeError, match="requires.*update"):

            class BadMixin:  # noqa: F811
                @reactive
                def x(self):
                    return self._x

    def test_reactive_on_class_with_explicit_update_works(self):
        """A LiveView subclass that defines its own update() works."""
        update_calls = []

        class MyView(LiveView):
            def update(self):
                update_calls.append(True)

            @reactive
            def count(self):
                return self._count

        assert hasattr(MyView, "count")

    def test_reactive_setter_triggers_update(self):
        """Setting a @reactive property calls self.update()."""
        update_calls = []

        class MyView(LiveView):
            def update(self):
                update_calls.append(True)

            @reactive
            def count(self):
                return self._count

        view = MyView()
        view.count = 42

        assert len(update_calls) == 1, "#1287: @reactive setter must call update()"

    def test_reactive_no_update_when_value_unchanged(self):
        """Setting to the same value skips update()."""
        update_calls = []

        class MyView(LiveView):
            def update(self):
                update_calls.append(True)

            @reactive
            def count(self):
                return self._count

        view = MyView()
        view.count = 42
        assert len(update_calls) == 1

        view.count = 42  # Same value
        assert len(update_calls) == 1, "unchanged value should not trigger update()"

    def test_reactive_with_custom_setter_triggers_update(self):
        """A custom setter (via @count.setter) still triggers update() via
        the descriptor's __set__."""
        update_calls = []

        class MyView(LiveView):
            def update(self):
                update_calls.append(True)

            @reactive
            def count(self):
                return self._count_store

            @count.setter
            def count(self, value):
                self._count_store = value

        view = MyView()
        view.count = 42

        assert view._count_store == 42
        assert len(update_calls) == 1, "#1287: @reactive with custom setter must call update()"

    def test_reactive_descriptor_doc_propagates(self):
        """@reactive propagates the function's docstring."""

        class MyView(LiveView):
            def update(self):
                pass

            @reactive
            def count(self):
                """The current count."""
                return self._count

        assert MyView.__dict__["count"].__doc__ == "The current count."
