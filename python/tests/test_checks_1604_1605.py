"""Tests for #1604 + #1605: V001/V005 suppression and abstract opt-out.

#1604: ``DJUST_CONFIG = {"suppress_checks": ["V001", "V005"]}`` must silence
the V001/V005 system checks, matching the behavior of every other V/C/T/Y
check (C003, V008, Y001-4, T002, T012, ...) that already honors the same
``_is_check_suppressed`` mechanism.

#1605: A LiveView subclass that sets ``abstract = True`` on the class itself
must be skipped by the per-class system checks (V001/V005/etc.). The marker
is class-explicit (consulted via ``cls.__dict__.get("abstract")``) and is
NOT inherited — subclasses of an abstract base are treated as concrete
unless they also set ``abstract = True``. Mirrors Django's ``Meta.abstract``
model semantics.
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


class TestV001SuppressViaDjustConfig:
    """#1604 — DJUST_CONFIG['suppress_checks'] must silence V001."""

    def test_v001_suppressed_via_djust_config(self, settings):
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {"suppress_checks": ["V001"]}

        cls = type(
            "V001SuppressedView",
            (LiveView,),
            {"__module__": "myapp.views"},
        )
        try:
            errors = _check_liveviews_errors()
            v001 = [e for e in errors if e.id == "djust.V001" and "V001SuppressedView" in e.msg]
            assert v001 == [], "V001 should be silenced by suppress_checks but fired: %r" % v001
        finally:
            del cls
            _force_gc()

    def test_v001_fires_without_suppression(self, settings):
        """Regression: without suppress_checks, V001 still fires."""
        from djust.live_view import LiveView

        # Explicitly empty to defeat any leaked state.
        settings.DJUST_CONFIG = {}

        cls = type(
            "V001NotSuppressedView",
            (LiveView,),
            {"__module__": "myapp.views"},
        )
        try:
            errors = _check_liveviews_errors()
            v001 = [e for e in errors if e.id == "djust.V001" and "V001NotSuppressedView" in e.msg]
            assert len(v001) == 1, "V001 should fire normally: %r" % v001
        finally:
            del cls
            _force_gc()


class TestV005SuppressViaDjustConfig:
    """#1604 — DJUST_CONFIG['suppress_checks'] must silence V005."""

    def test_v005_suppressed_via_djust_config(self, settings):
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {"suppress_checks": ["V005"]}
        settings.LIVEVIEW_ALLOWED_MODULES = ["other.module"]

        cls = type(
            "V005SuppressedView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "t.html"},
        )
        try:
            errors = _check_liveviews_errors()
            v005 = [e for e in errors if e.id == "djust.V005" and "V005SuppressedView" in e.msg]
            assert v005 == [], "V005 should be silenced by suppress_checks but fired: %r" % v005
        finally:
            del cls
            _force_gc()

    def test_v005_fires_without_suppression(self, settings):
        """Regression: without suppress_checks, V005 still fires."""
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {}
        settings.LIVEVIEW_ALLOWED_MODULES = ["other.module"]

        cls = type(
            "V005NotSuppressedView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "t.html"},
        )
        try:
            errors = _check_liveviews_errors()
            v005 = [e for e in errors if e.id == "djust.V005" and "V005NotSuppressedView" in e.msg]
            assert len(v005) == 1, "V005 should fire normally: %r" % v005
        finally:
            del cls
            _force_gc()


class TestAbstractOptOut:
    """#1605 — `abstract = True` on a LiveView subclass skips per-class checks."""

    def test_v001_skipped_on_abstract_class(self, settings):
        """V001 must not fire on a class with abstract = True (no template_name)."""
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {}

        cls = type(
            "AbstractBaseV001",
            (LiveView,),
            {"__module__": "myapp.views", "abstract": True},
        )
        try:
            errors = _check_liveviews_errors()
            v001 = [e for e in errors if e.id == "djust.V001" and "AbstractBaseV001" in e.msg]
            assert v001 == [], "V001 should skip abstract class but fired: %r" % v001
        finally:
            del cls
            _force_gc()

    def test_v005_skipped_on_abstract_class(self, settings):
        """V005 must not fire on a class with abstract = True (not in allowed modules)."""
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {}
        settings.LIVEVIEW_ALLOWED_MODULES = ["other.module"]

        cls = type(
            "AbstractBaseV005",
            (LiveView,),
            {"__module__": "myapp.views", "abstract": True, "template_name": "t.html"},
        )
        try:
            errors = _check_liveviews_errors()
            v005 = [e for e in errors if e.id == "djust.V005" and "AbstractBaseV005" in e.msg]
            assert v005 == [], "V005 should skip abstract class but fired: %r" % v005
        finally:
            del cls
            _force_gc()

    def test_abstract_not_inherited(self, settings):
        """`abstract = True` on a base must NOT propagate to subclasses.

        Subclasses are concrete unless they redeclare `abstract = True`
        themselves. Mirrors Django's Meta.abstract semantics.
        """
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {}

        base = type(
            "AbstractInheritBase",
            (LiveView,),
            {"__module__": "myapp.views", "abstract": True},
        )
        # Subclass that does NOT set abstract — must be treated as concrete.
        concrete = type(
            "ConcreteFromAbstract",
            (base,),
            {"__module__": "myapp.views"},
        )
        try:
            errors = _check_liveviews_errors()
            # Base must be skipped (no V001).
            v001_base = [
                e for e in errors if e.id == "djust.V001" and "AbstractInheritBase" in e.msg
            ]
            assert v001_base == [], "Base must be skipped: %r" % v001_base
            # Concrete subclass must fire V001 (template_name missing).
            v001_concrete = [
                e for e in errors if e.id == "djust.V001" and "ConcreteFromAbstract" in e.msg
            ]
            assert len(v001_concrete) == 1, "Concrete subclass must fire V001: %r" % v001_concrete
        finally:
            del concrete
            del base
            _force_gc()

    def test_abstract_false_explicit_is_concrete(self, settings):
        """`abstract = False` explicit is treated as concrete (V001 fires)."""
        from djust.live_view import LiveView

        settings.DJUST_CONFIG = {}

        cls = type(
            "ExplicitlyNotAbstract",
            (LiveView,),
            {"__module__": "myapp.views", "abstract": False},
        )
        try:
            errors = _check_liveviews_errors()
            v001 = [e for e in errors if e.id == "djust.V001" and "ExplicitlyNotAbstract" in e.msg]
            assert len(v001) == 1, "abstract=False must be concrete: %r" % v001
        finally:
            del cls
            _force_gc()


class TestLiveViewAbstractAttrDeclared:
    """The LiveView base class itself declares `abstract: bool = False`."""

    def test_liveview_has_abstract_attribute(self):
        from djust.live_view import LiveView

        # The base class default is False (so explicit-False subclasses
        # are indistinguishable from un-redeclared subclasses for
        # `getattr` consumers — both fall through to the class's own
        # default behavior on inheritance).
        assert getattr(LiveView, "abstract", None) is False
