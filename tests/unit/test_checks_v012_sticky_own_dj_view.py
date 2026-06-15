"""Tests for ``djust.V012`` — sticky-child own-dj-view footgun check (#1803).

V012 flags a sticky-child LiveView (``sticky = True``) whose own template root
carries a ``dj-view`` attribute. The framework's
``{% live_render ... sticky=True %}`` wrapper already emits
``<div dj-view dj-sticky-view="<id>" ...>``; a child that also declares its own
``dj-view`` produces a nested/duplicate ``dj-view`` inside the wrapper, which
breaks the child's client-side mount (events silently don't bind).

The check walks ``LiveView`` subclasses with ``sticky = True``, reads each
one's template source, and scans for a ``<div ... dj-view ...>`` opening tag.

The synthetic fixture classes live in ``djust.tests.checkviews_v012`` (imported
at collection time so they register in the LiveView subclass graph before
``check_sticky_child_own_dj_view`` walks it). They use inline ``template``
strings so the scan reads the root markup directly — no filesystem needed.
"""

from __future__ import annotations

# Import the fixture module at collection time so its sticky-child LiveView
# subclasses register in the LiveView subclass graph BEFORE
# ``check_sticky_child_own_dj_view`` walks ``_walk_subclasses(LiveView)``.
import djust.tests.checkviews_v012 as fixtures  # noqa: F401


def _v012(errors):
    return [e for e in errors if e.id == "djust.V012"]


def _v012_for(errors, cls):
    label = "%s.%s" % (cls.__module__, cls.__qualname__)
    return [e for e in _v012(errors) if e.msg.startswith(label + ":")]


class TestV012StickyChildOwnDjView:
    """V012 fires only for a sticky child whose own root carries dj-view."""

    def test_v012_fires_on_sticky_child_with_own_dj_view(self):
        """sticky=True + root has dj-view → exactly one V012 for that class."""
        from djust.checks import check_sticky_child_own_dj_view
        from djust.tests.checkviews_v012 import StickyChildWithOwnDjView

        errors = check_sticky_child_own_dj_view(None)
        hits = _v012_for(errors, StickyChildWithOwnDjView)
        assert len(hits) == 1, "the footgun sticky child must be flagged exactly once"
        assert "dj-view" in hits[0].msg
        assert "sticky wrapper" in hits[0].msg
        assert "live_render" in hits[0].hint

    def test_v012_silent_for_correct_sticky_child(self):
        """sticky=True + NO own dj-view → no V012 (the correct shape)."""
        from djust.checks import check_sticky_child_own_dj_view
        from djust.tests.checkviews_v012 import StickyChildNoDjView

        errors = check_sticky_child_own_dj_view(None)
        assert _v012_for(errors, StickyChildNoDjView) == []

    def test_v012_silent_for_non_sticky_view_with_dj_view(self):
        """A normal page view (NOT sticky) with dj-view → no V012.

        Page views REQUIRE dj-view to be browser-mountable; flagging them
        would be a false positive. This is the load-bearing guard.
        """
        from djust.checks import check_sticky_child_own_dj_view
        from djust.tests.checkviews_v012 import NonStickyViewWithDjView

        errors = check_sticky_child_own_dj_view(None)
        assert _v012_for(errors, NonStickyViewWithDjView) == []

    def test_v012_silent_when_dj_view_only_in_comment(self):
        """dj-view mentioned only in a {% comment %} (not on a real tag) → no V012.

        Exercises the anchored ``<div ... dj-view ...>`` regex: prose / comment
        text containing the literal ``dj-view`` must not false-match.
        """
        from djust.checks import check_sticky_child_own_dj_view
        from djust.tests.checkviews_v012 import StickyChildDjViewInComment

        errors = check_sticky_child_own_dj_view(None)
        assert _v012_for(errors, StickyChildDjViewInComment) == []

    def test_v012_warning_metadata(self):
        """The warning points at the child class source + carries the fix hint."""
        import inspect as _inspect

        from djust.checks import check_sticky_child_own_dj_view
        from djust.tests.checkviews_v012 import StickyChildWithOwnDjView

        errors = check_sticky_child_own_dj_view(None)
        hits = _v012_for(errors, StickyChildWithOwnDjView)
        assert len(hits) == 1
        warning = hits[0]
        assert warning.file_path == _inspect.getfile(StickyChildWithOwnDjView)
        assert warning.line_number is not None
        assert "dj-view" in warning.fix_hint
        assert "live_render" in warning.fix_hint

    def test_v012_suppressed_via_config(self, settings):
        """DJUST_CONFIG['suppress_checks']=['V012'] → zero V012 on a real footgun."""
        from djust.checks import check_sticky_child_own_dj_view

        settings.DJUST_CONFIG = {"suppress_checks": ["V012"]}
        errors = check_sticky_child_own_dj_view(None)
        assert _v012(errors) == [], "V012 is suppressed via DJUST_CONFIG"

    def test_v012_empirical_canary(self):
        """#1459 empirical canary — the tooling-class validation.

        Construct a synthetic sticky child whose root carries dj-view FROM
        SCRATCH (a fresh ``type()`` subclass, not the module fixtures), run
        ``check_sticky_child_own_dj_view`` against it, and confirm it reports a
        ``djust.V012`` for exactly that class. This is the "the check catches
        the bug class it claims to catch" proof — not just unit assertions.
        """
        from djust import LiveView
        from djust.checks import check_sticky_child_own_dj_view

        # --- Synthetic footgun, built fresh ---
        Synthetic = type(
            "V012CanarySyntheticChild",
            (LiveView,),
            {
                "__module__": "djust.tests.checkviews_v012",
                "sticky": True,
                "sticky_id": "v012_canary",
                "template": (
                    '<div dj-root dj-view="x.y.V012CanarySyntheticChild">'
                    '<button dj-click="go">x</button></div>'
                ),
            },
        )

        # --- Run the tool against the synthetic trigger ---
        errors = check_sticky_child_own_dj_view(None)
        hits = _v012_for(errors, Synthetic)

        # --- Confirm the tool reports the trigger ---
        assert len(hits) == 1, "the check must report the synthetic footgun"
        assert "dj-view" in hits[0].msg

    def test_v012_gate_off_self_test(self):
        """#1468 gate-off self-test — proves the positive assertion is not a
        tautology.

        Build TWO fresh sticky children that differ ONLY in whether the root
        carries dj-view. The one WITH dj-view fires V012; the one WITHOUT does
        not. If V012 fired regardless of the dj-view attribute, the silent-case
        assertion would fail — so the test exercises the real branch.
        """
        from djust import LiveView
        from djust.checks import check_sticky_child_own_dj_view

        WithDjView = type(
            "V012GateWithDjView",
            (LiveView,),
            {
                "__module__": "djust.tests.checkviews_v012",
                "sticky": True,
                "sticky_id": "v012_gate_on",
                "template": '<div dj-view="x.y.V012GateWithDjView">x</div>',
            },
        )
        WithoutDjView = type(
            "V012GateWithoutDjView",
            (LiveView,),
            {
                "__module__": "djust.tests.checkviews_v012",
                "sticky": True,
                "sticky_id": "v012_gate_off",
                "template": '<div class="widget">x</div>',
            },
        )

        errors = check_sticky_child_own_dj_view(None)
        assert len(_v012_for(errors, WithDjView)) == 1, "dj-view present → must fire"
        assert _v012_for(errors, WithoutDjView) == [], (
            "no dj-view → must be silent (gate-off case; proves the fire isn't a tautology)"
        )
