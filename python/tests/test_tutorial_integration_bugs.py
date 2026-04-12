"""Tests for TutorialMixin integration bugs (#691, #694).

#691: System check V010 detects wrong MRO ordering.
#694: get_context_data skips non-serializable class-level attributes.
"""

from __future__ import annotations

import gc

from django.test import override_settings

from djust.tutorials import TutorialMixin, TutorialStep


# ---------------------------------------------------------------------------
# #691 — V010 system check for TutorialMixin MRO
# ---------------------------------------------------------------------------


class TestV010TutorialMixinMRO:
    """V010 detects TutorialMixin listed after LiveView in bases."""

    def _run_check_for_v010(self, cls):
        """Run the V010 check with only *cls* visible.

        Creates the class, runs the check, then cleans up the subclass
        from Python's weak-ref registry so tests are isolated.
        """
        from djust.checks import check_liveviews

        try:
            errors = check_liveviews(None)
            return [e for e in errors if e.id == "djust.V010"]
        finally:
            del cls
            gc.collect()

    def test_wrong_order_detected(self):
        """class Bad(LiveView, TutorialMixin) should produce V010."""
        from djust.live_view import LiveView

        cls = type(
            "BadView_691a",
            (LiveView, TutorialMixin),
            {"__module__": "myapp.test_views", "template_name": "t.html"},
        )
        v010 = self._run_check_for_v010(cls)
        assert len(v010) >= 1
        assert any("TutorialMixin must be listed before LiveView" in e.msg for e in v010)

    def test_correct_order_no_error(self):
        """class Good(TutorialMixin, LiveView) should NOT produce V010."""
        from djust.live_view import LiveView

        cls = type(
            "GoodView_691b",
            (TutorialMixin, LiveView),
            {"__module__": "myapp.test_views", "template_name": "t.html"},
        )
        v010 = self._run_check_for_v010(cls)
        # Filter to only this class
        relevant = [e for e in v010 if "GoodView_691b" in e.msg]
        assert len(relevant) == 0

    def test_no_tutorial_mixin_no_error(self):
        """A plain LiveView without TutorialMixin should NOT produce V010."""
        from djust.live_view import LiveView

        cls = type(
            "PlainView_691c",
            (LiveView,),
            {"__module__": "myapp.test_views", "template_name": "t.html"},
        )
        v010 = self._run_check_for_v010(cls)
        relevant = [e for e in v010 if "PlainView_691c" in e.msg]
        assert len(relevant) == 0

    def test_v010_suppressible(self):
        """V010 can be suppressed via DJUST_CONFIG.suppress_checks."""
        from djust.live_view import LiveView

        cls = type(
            "SuppressedView_691d",
            (LiveView, TutorialMixin),
            {"__module__": "myapp.test_views", "template_name": "t.html"},
        )
        try:
            with override_settings(DJUST_CONFIG={"suppress_checks": ["V010"]}):
                from djust.checks import check_liveviews

                errors = check_liveviews(None)
                v010 = [e for e in errors if e.id == "djust.V010"]
                assert len(v010) == 0
        finally:
            del cls
            gc.collect()

    def test_error_has_fix_hint(self):
        """V010 error includes a fix_hint with correct class reordering."""
        from djust.live_view import LiveView

        cls = type(
            "HintView_691e",
            (LiveView, TutorialMixin),
            {"__module__": "myapp.test_views", "template_name": "t.html"},
        )
        try:
            from djust.checks import check_liveviews

            errors = check_liveviews(None)
            v010 = [e for e in errors if e.id == "djust.V010" and "HintView_691e" in e.msg]
            assert len(v010) == 1
            assert "TutorialMixin, LiveView" in v010[0].fix_hint
        finally:
            del cls
            gc.collect()


# ---------------------------------------------------------------------------
# #694 — get_context_data skips non-serializable class attrs
# ---------------------------------------------------------------------------


class TestContextNonSerializableClassAttrs:
    """get_context_data should skip non-serializable class-level attributes."""

    def _make_view_class(self, **class_attrs):
        """Create a minimal view class with the given class-level attributes."""
        from djust.mixins.context import ContextMixin

        class FakeView(ContextMixin):
            template_name = "test.html"

            def __init__(self, **kwargs):
                # Don't call super().__init__() — mimics View.__init__
                pass

            def _register_component(self, comp):
                pass

            def _get_template_content(self):
                return None

        # Set class-level attributes
        for k, v in class_attrs.items():
            setattr(FakeView, k, v)

        return FakeView

    def test_serializable_class_attr_included(self):
        """A JSON-serializable class attr (str, int, etc.) is included."""
        ViewClass = self._make_view_class(page_title="Hello", max_items=10)
        view = ViewClass()
        ctx = view.get_context_data()
        assert ctx["page_title"] == "Hello"
        assert ctx["max_items"] == 10

    def test_nonserializable_class_attr_excluded(self):
        """A non-serializable class attr (like TutorialStep) is excluded."""
        step = TutorialStep(target="#btn", message="Click")
        ViewClass = self._make_view_class(my_steps=[step])
        view = ViewClass()
        ctx = view.get_context_data()
        assert "my_steps" not in ctx

    def test_instance_attr_always_included(self):
        """Instance attrs (set in __init__ or mount) are always included,
        even if not serializable — they are the developer's explicit intent."""
        from djust.mixins.context import ContextMixin

        class TestView(ContextMixin):
            def __init__(self):
                pass

            def _register_component(self, comp):
                pass

            def _get_template_content(self):
                return None

        view = TestView()
        view.my_data = {"key": "value"}
        ctx = view.get_context_data()
        assert ctx["my_data"] == {"key": "value"}

    def test_serializable_list_class_attr_included(self):
        """A list of primitives at class level is included."""
        ViewClass = self._make_view_class(tags=["a", "b", "c"])
        view = ViewClass()
        ctx = view.get_context_data()
        assert ctx["tags"] == ["a", "b", "c"]

    def test_serializable_dict_class_attr_included(self):
        """A dict of primitives at class level is included."""
        ViewClass = self._make_view_class(config={"debug": True, "level": 3})
        view = ViewClass()
        ctx = view.get_context_data()
        assert ctx["config"] == {"debug": True, "level": 3}

    def test_mixed_list_with_nonserializable_excluded(self):
        """A list containing non-serializable items is excluded."""
        ViewClass = self._make_view_class(items=[TutorialStep(target="#a", message="a")])
        view = ViewClass()
        ctx = view.get_context_data()
        assert "items" not in ctx

    def test_tutorial_steps_not_in_context(self):
        """TutorialMixin._tutorial_steps are private and never in context."""
        from djust.mixins.context import ContextMixin
        from djust.mixins.push_events import PushEventMixin
        from djust.mixins.waiters import WaiterMixin

        class TourView(WaiterMixin, PushEventMixin, TutorialMixin, ContextMixin):
            _tutorial_steps = [
                TutorialStep(target="#a", message="Step 1"),
            ]

            def __init__(self, **kwargs):
                super().__init__(**kwargs)

            def start_async(self, callback, *args, name=None, **kwargs):
                pass

            def _register_component(self, comp):
                pass

            def _get_template_content(self):
                return None

        view = TourView()
        ctx = view.get_context_data()
        # _tutorial_steps is private (starts with _), so not in context
        assert "_tutorial_steps" not in ctx
        # The public property 'tutorial_steps' is callable-ish (property),
        # and won't be in class __dict__ directly either
        assert "tutorial_steps" not in ctx


class TestTutorialMixinStepsProperty:
    """TutorialMixin stores steps as _tutorial_steps with a property accessor."""

    def test_init_subclass_moves_tutorial_steps(self):
        """Defining tutorial_steps on a subclass moves it to _tutorial_steps."""
        from djust.mixins.push_events import PushEventMixin
        from djust.mixins.waiters import WaiterMixin

        class MyTour(WaiterMixin, PushEventMixin, TutorialMixin):
            tutorial_steps = [
                TutorialStep(target="#a", message="Hi"),
            ]

            def __init__(self, **kwargs):
                super().__init__(**kwargs)

            def start_async(self, callback, *args, name=None, **kwargs):
                pass

        # The class should have _tutorial_steps, not tutorial_steps in __dict__
        assert "_tutorial_steps" in MyTour.__dict__ or hasattr(MyTour, "_tutorial_steps")
        # The property should still work
        view = MyTour()
        assert len(view.tutorial_steps) == 1
        assert view.tutorial_steps[0].target == "#a"

    def test_empty_steps_default(self):
        """A subclass without tutorial_steps gets an empty list."""
        from djust.mixins.push_events import PushEventMixin
        from djust.mixins.waiters import WaiterMixin

        class EmptyTour(WaiterMixin, PushEventMixin, TutorialMixin):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)

            def start_async(self, callback, *args, name=None, **kwargs):
                pass

        view = EmptyTour()
        assert view.tutorial_steps == []
        assert view.tutorial_total_steps == 0
