"""#4210: held-row refresh follow-ups.

1. A ``cached_property`` computed by a thread that does not carry the step's context
   (a plain ``threading.Thread``) read through no recorder, so the view's read set
   misses its table. Provenance is decided by the context that filled it, never by
   when it ran: such a value is unknown, so a later write to its table refreshes it.
   A thread that carries the context (``copy_context().run``, ``sync_to_async``) is
   recorded and stays known, so it does not refresh more than before.
2. ``QuerySet._fetch_all`` and the ``cached_property`` descriptors' ``__get__`` are
   wrapped; a changed signature fails startup instead of recording nothing.

Real rows throughout (``auth.Group``), through the harness of ``test_held_rows_4112``.
"""

from __future__ import annotations

import contextvars
import inspect
import threading

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.db.models.query import QuerySet
from django.utils.functional import cached_property

from djust import _held_rows
from djust.sync_turn import run_sync_step
from djust.tests.test_held_rows_4112 import TAG, _Probe, _rows, _world

pytestmark = pytest.mark.django_db(transaction=True)


class _Holder(_Probe):
    """A view whose rows sit behind a ``cached_property`` that reads ``auth_group``."""

    @cached_property
    def names(self):
        return [g.name for g in _rows()]


def _in_plain_thread(fn):
    worker = threading.Thread(target=fn)
    worker.start()
    worker.join()


def _in_copied_context(fn):
    context = contextvars.copy_context()
    worker = threading.Thread(target=lambda: context.run(fn))
    worker.start()
    worker.join()


def _rename_group():
    Group.objects.filter(name=TAG["value"] + "alice").update(name=TAG["value"] + "bob")


def _touch_user(pk):
    """An UPDATE of ``auth_user`` alone: the step reads nothing."""
    get_user_model().objects.filter(pk=pk).update(first_name="x")


def _recorded_view():
    """A view that has completed one render under recording that read nothing."""
    owner = _world().pk
    view = _Holder()
    view._show = lambda: []
    view.render_with_diff()
    return view, owner


def test_a_cached_property_filled_by_a_plain_thread_during_a_step_is_unknown():
    view, owner = _recorded_view()
    with _held_rows.writes_by(view):
        _in_plain_thread(lambda: view.names)  # the handler's own thread: no context copy
    assert "names" in view.__dict__
    view._show = lambda: list(view.names)

    with _held_rows.writes_by(view):
        _rename_group()

    # R cannot say the thread read auth_group, so the write refreshes the view.
    assert _held_rows.rows_written(view) is True
    shown = run_sync_step(view, "render", view.render_with_diff)
    assert shown == [TAG["value"] + "bob"]


def test_a_cached_property_filled_in_a_context_copying_thread_stays_known():
    view, owner = _recorded_view()
    with _held_rows.writes_by(view):
        _in_copied_context(lambda: view.names)  # carries the writer: its SELECT is recorded
    assert "names" in view.__dict__
    view._show = lambda: list(view.names)

    with _held_rows.writes_by(view):
        _touch_user(owner)  # a table the view never read

    # Known and not meeting: no refresh, as before the fix (no over-refresh).
    assert _held_rows.rows_written(view) is False

    with _held_rows.writes_by(view):
        _rename_group()
    assert _held_rows.rows_written(view) is True  # and a write to its table still does


def test_a_known_property_refilled_by_a_plain_thread_becomes_unknown():
    owner = _world().pk
    view = _Holder()
    view._show = lambda: list(view.names)
    view.render_with_diff()  # filled by the recorded render: known, R has auth_group
    with _held_rows.writes_by(view):
        _touch_user(owner)
    assert _held_rows.rows_written(view) is False

    del view.__dict__["names"]  # dropped (as a reset does), then refilled off the step
    _in_plain_thread(lambda: view.names)
    with _held_rows.writes_by(view):
        _touch_user(owner)
    assert _held_rows.rows_written(view) is True


def test_the_fill_stamp_wrappers_stay_out_of_the_way_of_other_cached_properties():
    """Process-wide wrappers: a value, the class access and a second read behave as before."""
    import functools

    class Plain:
        calls = 0

        @cached_property
        def django_one(self):
            Plain.calls += 1
            return 1

        @functools.cached_property
        def stdlib_one(self):
            Plain.calls += 1
            return 2

    plain = Plain()
    assert (plain.django_one, plain.django_one, plain.stdlib_one, plain.stdlib_one) == (1, 1, 2, 2)
    assert Plain.calls == 2
    assert isinstance(Plain.django_one, cached_property)
    assert isinstance(Plain.stdlib_one, functools.cached_property)


# ------------------------------------------------------------------ item 2


def test_the_fetch_all_wrapped_has_the_signature_djust_was_written_for():
    from django import get_version

    assert list(inspect.signature(QuerySet._fetch_all).parameters) == ["self"], get_version()
    assert getattr(QuerySet._fetch_all, "_djust_held_rows", False)  # the wrapper is installed
    for descriptor in (cached_property, __import__("functools").cached_property):
        assert getattr(descriptor.__get__, "_djust_held_rows", False), descriptor


def test_install_refuses_a_changed_fetch_all_signature(monkeypatch):
    def _fetch_all(self, chunk_size):  # what a Django that changed the method would define
        raise AssertionError("not called")

    monkeypatch.setattr(QuerySet, "_fetch_all", _fetch_all)
    with pytest.raises(ImproperlyConfigured, match=r"#4112\) wraps QuerySet\._fetch_all"):
        _held_rows.install_fill_stamps()
    assert QuerySet._fetch_all is _fetch_all  # nothing was wrapped on top of it


def test_install_refuses_a_changed_cached_property_signature(monkeypatch):
    import functools

    def __get__(self, instance):
        raise AssertionError("not called")

    monkeypatch.setattr(functools.cached_property, "__get__", __get__)
    with pytest.raises(ImproperlyConfigured, match=r"cached_property\.__get__"):
        _held_rows.install_fill_stamps()


def test_install_is_idempotent():
    before = (QuerySet._fetch_all, cached_property.__get__)
    _held_rows.install_fill_stamps()
    assert (QuerySet._fetch_all, cached_property.__get__) == before
