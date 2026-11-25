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
            {
                "exposure_policy": "explicit",
                "__module__": "myapp.test_views",
                "template_name": "t.html",
            },
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
            {
                "exposure_policy": "explicit",
                "__module__": "myapp.test_views",
                "template_name": "t.html",
            },
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
            {
                "exposure_policy": "explicit",
                "__module__": "myapp.test_views",
                "template_name": "t.html",
            },
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
            {
                "exposure_policy": "explicit",
                "__module__": "myapp.test_views",
                "template_name": "t.html",
            },
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
            {
                "exposure_policy": "explicit",
                "__module__": "myapp.test_views",
                "template_name": "t.html",
            },
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
