"""A view attribute that reuses a name djust keeps its own state under.

``LiveView.__init__`` sets about forty attributes (``_streams``,
``_components``, ``_pending_navigation``, ...). A view that assigns one of
them in its own code replaces djust's state, and the failure shows up far
away: ``self._streams = {"runs": {...}}`` made every render fail with
``TypeError: 'builtin_function_or_method' object is not iterable``, raised
from the streams context.

* V022 (Warning) reports such an assignment at ``manage.py check``;
* the streams context raises an error naming the attribute.
"""

import pytest

from djust import LiveView
from djust.checks.components import check_reserved_view_attributes


class _Lists:
    """A plain mixin: its assignments run on the view too."""

    def remember(self):
        self._pending_navigation = []


class ClashingView(_Lists, LiveView):
    template = "<div dj-root></div>"

    def mount(self, request, **kwargs):
        self._streams = {"runs": {"items": []}}
        self._lists = {"runs": []}  # not a framework name
        self.request = request  # Django's own convention: allowed

    def refresh(self):
        self._components: dict = {}


class CleanView(LiveView):
    template = "<div dj-root></div>"

    def mount(self, request, **kwargs):
        self.stream("runs", [])
        self._object = None  # ADR-017's documented cache: allowed


def _v022(*classes):
    labels = ["%s.%s" % (c.__module__, c.__qualname__) for c in classes]
    return [
        m
        for m in check_reserved_view_attributes(None)
        if any(m.msg.startswith(label + " ") for label in labels)
    ]


def test_v022_reports_assignments_to_framework_attributes():
    messages = _v022(ClashingView)
    assigned = sorted(m.msg.split("self.")[1].split(",")[0] for m in messages)
    assert assigned == ["_components", "_pending_navigation", "_streams"], messages
    assert all(m.id == "djust.V022" and m.level == 30 for m in messages)
    streams = next(m for m in messages if "self._streams" in m.msg)
    assert "mount" in streams.msg
    assert "stream(" in streams.hint
    assert streams.line_number


def test_v022_ignores_allowed_and_unrelated_names():
    assert _v022(CleanView) == []


def test_v022_reserved_names_are_derived_from_the_view():
    """The names come from a bare LiveView, not a hand-kept list."""
    from djust.checks.components import _reserved_view_attributes

    reserved = _reserved_view_attributes()
    bare = set(vars(LiveView()))
    assert reserved == bare - {"request", "_object"}
    assert "_streams" in reserved


def test_v022_can_be_suppressed(settings):
    settings.DJUST_CONFIG = {"suppress_checks": ["V022"]}
    assert _v022(ClashingView) == []


def test_streams_context_names_the_reserved_attribute():
    view = LiveView()
    view._streams = {"runs": {"items": []}}
    with pytest.raises(TypeError, match=r"_streams.*reserved"):
        view._get_streams_context()
