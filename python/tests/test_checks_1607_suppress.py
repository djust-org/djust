"""Tests for #1607: V002/V003/V004/V007/Q007 must honor suppress_checks.

Follow-up to PR #1606 (which fixed V001/V005). The same per-class loop in
`python/djust/checks.py::check_liveviews` emits V002, V003, V004, V007, and
Q007 without consulting the `_is_check_suppressed()` helper that every other
V/C/T/Y check in the file uses. This file pins the fix.
"""

import pytest


def _liveview_available():
    try:
        from djust.live_view import LiveView  # noqa: F401

        return True
    except ImportError:
        return False


def _force_gc():
    import gc

    gc.collect()


def _check_liveviews_errors():
    from djust.checks import check_liveviews

    return check_liveviews(None)


@pytest.fixture(autouse=True)
def _skip_if_no_liveview():
    if not _liveview_available():
        pytest.skip("Rust extension not available")


class TestV002Suppress:
    """V002 = LiveView missing mount(); DjustInfo severity."""

    def test_v002_suppressed_via_djust_config(self, settings):
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {"suppress_checks": ["V002"]}
        cls = type(
            "V002SuppressedView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "t.html"},
        )
        try:
            errors = _check_liveviews_errors()
            v002 = [e for e in errors if e.id == "djust.V002" and "V002SuppressedView" in e.msg]
            assert v002 == [], "V002 should be silenced by suppress_checks: %r" % v002
        finally:
            del cls
            _force_gc()

    def test_v002_fires_without_suppression(self, settings):
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {}
        cls = type(
            "V002NotSuppressedView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "t.html"},
        )
        try:
            errors = _check_liveviews_errors()
            v002 = [e for e in errors if e.id == "djust.V002" and "V002NotSuppressedView" in e.msg]
            assert len(v002) == 1, "V002 should fire normally: %r" % v002
        finally:
            del cls
            _force_gc()


class TestV003Suppress:
    """V003 = wrong mount() signature; DjustError severity."""

    def test_v003_suppressed_via_djust_config(self, settings):
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {"suppress_checks": ["V003"]}

        def bad_mount(self):  # missing request param
            pass

        cls = type(
            "V003SuppressedView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "t.html", "mount": bad_mount},
        )
        try:
            errors = _check_liveviews_errors()
            v003 = [e for e in errors if e.id == "djust.V003" and "V003SuppressedView" in e.msg]
            assert v003 == [], "V003 should be silenced: %r" % v003
        finally:
            del cls
            _force_gc()

    def test_v003_fires_without_suppression(self, settings):
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {}

        def bad_mount(self):
            pass

        cls = type(
            "V003NotSuppressedView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "t.html", "mount": bad_mount},
        )
        try:
            errors = _check_liveviews_errors()
            v003 = [e for e in errors if e.id == "djust.V003" and "V003NotSuppressedView" in e.msg]
            assert len(v003) == 1, "V003 should fire normally: %r" % v003
        finally:
            del cls
            _force_gc()


class TestV004Suppress:
    """V004 = public method name matches handler heuristic but missing @event_handler."""

    def test_v004_suppressed_via_djust_config(self, settings):
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {"suppress_checks": ["V004"]}

        def submit_form(self):  # event-handler-like name, no decorator
            pass

        def mount(self, request, **kwargs):
            pass

        cls = type(
            "V004SuppressedView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "submit_form": submit_form,
            },
        )
        try:
            errors = _check_liveviews_errors()
            v004 = [e for e in errors if e.id == "djust.V004" and "V004SuppressedView" in e.msg]
            assert v004 == [], "V004 should be silenced: %r" % v004
        finally:
            del cls
            _force_gc()

    def test_v004_fires_without_suppression(self, settings):
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {}

        def submit_form(self):
            pass

        def mount(self, request, **kwargs):
            pass

        cls = type(
            "V004NotSuppressedView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "submit_form": submit_form,
            },
        )
        try:
            errors = _check_liveviews_errors()
            v004 = [e for e in errors if e.id == "djust.V004" and "V004NotSuppressedView" in e.msg]
            assert len(v004) >= 1, "V004 should fire normally: %r" % v004
        finally:
            del cls
            _force_gc()


class TestV007Suppress:
    """V007 = event handler missing **kwargs."""

    def test_v007_suppressed_via_djust_config(self, settings):
        from djust.live_view import LiveView
        from djust.decorators import event_handler

        settings.DJUST_CONFIG = {"suppress_checks": ["V007"]}

        @event_handler()
        def bad_handler(self):  # missing **kwargs
            pass

        def mount(self, request, **kwargs):
            pass

        cls = type(
            "V007SuppressedView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "bad_handler": bad_handler,
            },
        )
        try:
            errors = _check_liveviews_errors()
            v007 = [e for e in errors if e.id == "djust.V007" and "V007SuppressedView" in e.msg]
            assert v007 == [], "V007 should be silenced: %r" % v007
        finally:
            del cls
            _force_gc()

    def test_v007_fires_without_suppression(self, settings):
        from djust.live_view import LiveView
        from djust.decorators import event_handler

        settings.DJUST_CONFIG = {}

        @event_handler()
        def bad_handler(self):
            pass

        def mount(self, request, **kwargs):
            pass

        cls = type(
            "V007NotSuppressedView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "bad_handler": bad_handler,
            },
        )
        try:
            errors = _check_liveviews_errors()
            v007 = [e for e in errors if e.id == "djust.V007" and "V007NotSuppressedView" in e.msg]
            assert len(v007) == 1, "V007 should fire normally: %r" % v007
        finally:
            del cls
            _force_gc()


class TestQ007Suppress:
    """Q007 = overlapping static_assigns and temporary_assigns."""

    def test_q007_suppressed_via_djust_config(self, settings):
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {"suppress_checks": ["Q007"]}

        def mount(self, request, **kwargs):
            pass

        cls = type(
            "Q007SuppressedView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "static_assigns": ["foo"],
                "temporary_assigns": {"foo": None},
            },
        )
        try:
            errors = _check_liveviews_errors()
            q007 = [e for e in errors if e.id == "djust.Q007" and "Q007SuppressedView" in e.msg]
            assert q007 == [], "Q007 should be silenced: %r" % q007
        finally:
            del cls
            _force_gc()

    def test_q007_fires_without_suppression(self, settings):
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {}

        def mount(self, request, **kwargs):
            pass

        cls = type(
            "Q007NotSuppressedView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "static_assigns": ["foo"],
                "temporary_assigns": {"foo": None},
            },
        )
        try:
            errors = _check_liveviews_errors()
            q007 = [e for e in errors if e.id == "djust.Q007" and "Q007NotSuppressedView" in e.msg]
            assert len(q007) == 1, "Q007 should fire normally: %r" % q007
        finally:
            del cls
            _force_gc()
