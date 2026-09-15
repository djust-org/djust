"""Tests for #2833 — T018's ``{% extends %}`` skip was invisible.

The skip itself is intentional and stays (block-override context is
inheritance-scoped; the conservative trade-off is documented). The two
defects fixed here:

1. ``djust_check`` printed ``All djust checks passed!`` with no indication
   that views were skipped — for an all-extends app that "passed" meant
   "T018 examined nothing", indistinguishable from "examined everything and
   found nothing". The check now emits an Info-level message when views are
   skipped, so a pass is falsifiable.
2. ``docs/system-checks.md`` claimed the two entry points "can never drift
   apart" while they covered different template shapes (T018 skips
   ``{% extends %}``; ``djust_typecheck`` analyses it for ``template_name``
   views). The doc claim is corrected in the docs commit for this PR.

Both tests pin discovery to exactly the view under test (hermetic — the
real discovery union would make the emitted skip COUNT environment-
dependent, and an absence assertion can't tolerate pollution).
"""

import pytest


def _liveview_available():
    """Return True if LiveView can be imported (Rust extension built)."""
    try:
        from djust.live_view import LiveView  # noqa: F401

        return True
    except ImportError:
        return False


def _t018(errors):
    return [e for e in errors if e.id == "djust.T018"]


@pytest.mark.skipif(not _liveview_available(), reason="Rust extension not available")
class TestT018ExtendsSkipInfo2833:
    def test_extends_skip_emits_info_message(self, monkeypatch):
        """An app whose LiveView uses an extends template gets an Info-level
        T018 message reporting the skip — the "passed" result becomes
        falsifiable (#2833)."""
        from django.core.checks import Info

        from djust.checks import templates as _t
        from djust.live_view import LiveView

        class T018SkipInfoView(LiveView):
            template = (
                '{% extends "base.html" %}'
                "{% block content %}{{ never_set_skip_info }}{% endblock %}"
            )

            def mount(self, request, **kwargs):
                pass

        monkeypatch.setattr(_t, "_routed_liveview_classes", lambda: [])
        monkeypatch.setattr(_t, "_walk_subclasses", lambda base: [T018SkipInfoView])

        errors = _t.check_undefined_template_vars(None)
        skip_infos = [e for e in _t018(errors) if isinstance(e, Info) and "skipp" in e.msg.lower()]
        assert len(skip_infos) == 1, (
            "T018 must emit exactly one Info message when an extends-template "
            "view is skipped — a silent skip makes 'passed' unfalsifiable. "
            "T018 messages seen: %r" % [e.msg for e in _t018(errors)]
        )
        assert "extends" in skip_infos[0].msg
        # Count form, not view names: "skipped N view(s)".
        assert "skipped 1 view" in skip_infos[0].msg, (
            "the Info must report the COUNT of skipped views. Got: %r" % skip_infos[0].msg
        )
        # And still no WARNING on the extends view itself (the skip
        # behaviour is unchanged — this PR only makes it visible).
        warnings = [e for e in _t018(errors) if not isinstance(e, Info)]
        assert warnings == [], (
            "extends templates must remain SKIPPED (known v1 limitation) — "
            "no warning, only the skip Info. Got: %r" % warnings
        )

    def test_no_skip_info_without_extends_views(self, monkeypatch):
        """Gate-off (#1468): with no extends template in sight the check
        emits no skip Info — the message is load-bearing on the skip path,
        not emitted unconditionally."""
        from django.core.checks import Info

        from djust.checks import templates as _t
        from djust.live_view import LiveView

        class T018NoExtendsView(LiveView):
            template = "<div>{{ never_set_no_extends }}</div>"

            def mount(self, request, **kwargs):
                pass

        monkeypatch.setattr(_t, "_routed_liveview_classes", lambda: [])
        monkeypatch.setattr(_t, "_walk_subclasses", lambda base: [T018NoExtendsView])

        errors = _t.check_undefined_template_vars(None)
        skip_infos = [e for e in _t018(errors) if isinstance(e, Info) and "skipp" in e.msg.lower()]
        assert skip_infos == [], (
            "no extends-template views in play — no skip Info may be emitted. "
            "Got: %r" % [e.msg for e in skip_infos]
        )
