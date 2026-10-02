"""``PersistentLiveView``: a LiveView base class that opts in to state snapshots.

Its only behavioural difference from ``LiveView`` is
``enable_state_snapshot = True``. It is for the legacy exposure policy; an
explicit-exposure view declares ``state(..., persist=...)`` per field and is
not changed by the flag (#3211).
"""

import importlib

import pytest

import djust
from djust import LiveView, PersistentLiveView
from djust._exposure import ExposureError
from djust.checks import check_service_worker_advanced
from djust.decorators import state
from djust.live_view import PersistentLiveView as PersistentLiveViewFromModule


class Counter(PersistentLiveView):
    template = "<div dj-root>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0


class OptedOut(PersistentLiveView):
    enable_state_snapshot = False


def test_flag_is_true_and_liveview_default_unchanged():
    assert PersistentLiveView.enable_state_snapshot is True
    assert Counter.enable_state_snapshot is True
    assert LiveView.enable_state_snapshot is False
    assert issubclass(PersistentLiveView, LiveView)
    assert PersistentLiveView.exposure_policy == "legacy"


def test_subclass_can_opt_back_out():
    assert OptedOut.enable_state_snapshot is False
    assert OptedOut().enable_state_snapshot is False


def test_exported_from_package():
    assert PersistentLiveView is PersistentLiveViewFromModule
    assert "PersistentLiveView" in djust.__all__
    assert "PersistentLiveView" in dir(djust)
    assert importlib.import_module("djust").PersistentLiveView is PersistentLiveView


def test_capture_and_restore_round_trip():
    view = Counter()
    view.count = 5
    view.label = "alice"
    view._private = "secret"
    snapshot = view._capture_snapshot_state()
    assert snapshot["count"] == 5
    assert snapshot["label"] == "alice"
    assert "_private" not in snapshot

    fresh = Counter()
    fresh.count = 0
    fresh._restore_snapshot(snapshot)
    assert fresh.count == 5
    assert fresh.label == "alice"
    assert fresh._should_restore_snapshot(None) is True


class ExplicitPersistent(PersistentLiveView):
    """Explicit view that inherits the flag: the flag must not add anything."""

    exposure_policy = "explicit"
    template = "<div dj-root>{{ saved }}</div>"
    saved = state("kept", persist="server")
    scratch = state("transient")


def test_explicit_subclass_is_constructible_and_flag_is_inert():
    view = ExplicitPersistent()
    assert view.exposure_policy == "explicit"
    assert view.enable_state_snapshot is True  # inherited, but ignored
    # Snapshot capture is the explicit projection: only persist="client"
    # grants. Nothing is added by the legacy public-attribute scan.
    view.undeclared = "UNDECLARED_SENTINEL"
    captured = view._capture_snapshot_state()
    assert captured == {}
    assert "UNDECLARED_SENTINEL" not in repr(captured)
    # The legacy raw restore hook stays closed for explicit views.
    with pytest.raises(ExposureError):
        view._restore_snapshot({"saved": "x"})


def _c304_messages():
    return [m for m in check_service_worker_advanced(None) if m.id == "djust.C304"]


def test_c304_fires_for_persistent_subclass_with_pii_name():
    class LeakyPersistent(PersistentLiveView):
        password = ""

    messages = _c304_messages()
    mine = [m for m in messages if "LeakyPersistent" in m.msg]
    assert len(mine) == 1
    assert "password" in mine[0].msg


def test_c304_silent_for_opted_out_and_clean_subclasses():
    class CleanPersistent(PersistentLiveView):
        title = ""

    class OptedOutLeaky(PersistentLiveView):
        enable_state_snapshot = False
        password = ""

    text = " ".join(m.msg for m in _c304_messages())
    assert "CleanPersistent" not in text
    assert "OptedOutLeaky" not in text


def test_base_class_is_abstract_and_not_inherited():
    assert PersistentLiveView.__dict__.get("abstract") is True
    assert Counter.__dict__.get("abstract") is not True
    # The base itself is a framework class, so user-view discovery skips it.
    from djust.checks.utils import _walk_subclasses
    from djust.management._introspect import is_user_class

    assert PersistentLiveView in set(_walk_subclasses(LiveView))
    assert not is_user_class(PersistentLiveView)
    assert is_user_class(Counter)  # test modules count as user code
