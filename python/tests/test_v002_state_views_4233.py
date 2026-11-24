"""V002 skips views whose state is declared with ``state()`` (#4233)."""

import gc

from djust._state import state
from djust.checks import check_liveviews
from djust.live_view import LiveView


def _v002_for(name, bases, extra=None):
    body = {"exposure_policy": "explicit", "__module__": "myapp.views", "template_name": "t.html"}
    body.update(extra or {})
    cls = type(name, bases, body)
    try:
        return [e for e in check_liveviews(None) if e.id == "djust.V002" and name in e.msg]
    finally:
        del cls
        gc.collect()


def test_state_field_without_mount_is_not_reported():
    assert _v002_for("V002StateOnly", (LiveView,), {"count": state(0)}) == []


def test_no_mount_no_state_is_reported():
    assert len(_v002_for("V002Bare", (LiveView,))) == 1


def test_state_on_a_plain_mixin_parent_is_not_reported():
    mixin = type("V002StateMixin", (), {"count": state(0)})
    assert _v002_for("V002MixinChild", (mixin, LiveView)) == []


def test_state_on_a_mixin_listed_after_liveview_is_not_reported():
    mixin = type("V002CounterMixin", (), {"count": state(0)})
    assert _v002_for("V002MixinAfter", (LiveView, mixin)) == []
